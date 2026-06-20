"""Minimal end-to-end goal runner for the TOSS research harness."""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from toss_alpha.agents.execution_draft import build_manual_draft
from toss_alpha.backtest.engine import run_momentum_backtest
from toss_alpha.data.schema import Candle, OrderIntent, RiskDecision, SignalResult
from toss_alpha.execution.qual_gate import evaluate_disclosure_gate
from toss_alpha.reports.markdown_report import render_research_report
from toss_alpha.research.goal import load_goal
from toss_alpha.risk import RiskPolicy, validate_order_intent
from toss_alpha.signals import simple_momentum_signal, volatility_penalty

DEFAULT_PANEL_GLOB = "reports/backtests/*ohlcv_panel*.csv"
DEFAULT_REPORT_DIR = Path("reports/research")


def run_goal(
    goal_path: str | Path,
    *,
    panel_csv: str | Path | None = None,
    out_dir: str | Path | None = None,
) -> dict[str, Any]:
    goal_path = Path(goal_path)
    goal = load_goal(goal_path)
    raw_goal = yaml.safe_load(goal_path.read_text(encoding="utf-8")) or {}

    panel_path = Path(panel_csv) if panel_csv else discover_default_panel_csv(goal_path.parent.parent)
    report_dir = Path(out_dir) if out_dir else DEFAULT_REPORT_DIR
    report_dir.mkdir(parents=True, exist_ok=True)

    panel = _load_panel(panel_path, goal.symbols)
    candles_by_symbol = _candles_by_symbol(panel)
    strategy_params = dict(goal.strategy_params or {})
    short = int(strategy_params.get("short_window", 20))
    long = int(strategy_params.get("long_window", 60))
    vol_window = int(strategy_params.get("volatility_window", 20))
    notional_krw = float(_nested_get(raw_goal, ["risk_gates", "max_notional_krw_per_position"], 100_000))

    signal_rows: list[dict[str, Any]] = []
    for symbol in goal.symbols:
        candles = candles_by_symbol.get(symbol, [])
        closes = [c.close for c in candles]
        mom = simple_momentum_signal(closes, short=short, long=long)
        vol = volatility_penalty(closes, window=vol_window)
        combined = mom.score + vol.score
        signal_rows.append(
            {
                "symbol": symbol,
                "combined_score": combined,
                "rationale": f"momentum={mom.score:.6f}; vol_penalty={vol.score:.6f}; {mom.rationale}; {vol.rationale}",
                "candles": candles,
            }
        )

    ranked = sorted(signal_rows, key=lambda row: row["combined_score"], reverse=True)
    top = ranked[0]
    selected_symbol = str(top["symbol"])
    data_as_of = top["candles"][-1].close_time.isoformat() if top["candles"] else "unknown"

    signal_results = [
        SignalResult(name=row["symbol"], score=float(row["combined_score"]), rationale=str(row["rationale"]))
        for row in ranked
    ]
    backtest = run_momentum_backtest(
        top["candles"],
        strategy_id=goal.goal_id,
        lookback=max(long, 60),
        fee_bps=float(strategy_params.get("fee_bps", 0.0)),
        slippage_bps=float(strategy_params.get("slippage_bps", 0.0)),
    )

    qual_gate = evaluate_disclosure_gate(
        symbols=[selected_symbol],
        api_key_present=bool(os.getenv("OPENDART_API_KEY")),
        fetch_recent_filings=None,
    )

    policy = RiskPolicy(
        live_trading_enabled=bool(_nested_get(raw_goal, ["risk_gates", "live_trading_enabled"], False)),
        max_order_krw=int(_nested_get(raw_goal, ["risk_gates", "max_notional_krw_per_position"], 100_000)),
        require_manual_confirmation=bool(_nested_get(raw_goal, ["risk_gates", "require_manual_confirmation"], True)),
    )
    risk_violations = validate_order_intent(
        side="BUY",
        notional_krw=notional_krw,
        portfolio_value_krw=float(_nested_get(raw_goal, ["risk_gates", "max_total_notional_krw"], 1_000_000)),
        policy=policy,
        manual_confirmation=False,
    )
    risk_decision = RiskDecision.blocked(risk_violations) if risk_violations else RiskDecision.allowed()

    manual_draft = build_manual_draft(
        OrderIntent(
            strategy_id=goal.goal_id,
            symbol=selected_symbol,
            side="BUY",
            notional_krw=notional_krw,
            reason=f"top ranked symbol from {goal.strategy_name}",
        ),
        risk_decision,
        rationale=f"selected highest combined score among {len(ranked)} tracked symbols",
        evidence=[
            f"panel_csv={panel_path}",
            f"selected_symbol={selected_symbol}",
            f"qual_gate_status={qual_gate['status']}",
            f"backtest_status={backtest.status}",
        ],
    )

    report_text = render_research_report(
        goal=goal,
        signals=signal_results,
        backtest=backtest,
        risk_decision=risk_decision,
        manual_draft=manual_draft,
        data_as_of=data_as_of,
    )
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    stem = f"{goal.goal_id}_{timestamp}"
    report_path = report_dir / f"{stem}.md"
    json_path = report_dir / f"{stem}.json"
    report_path.write_text(report_text, encoding="utf-8")

    result = {
        "goal_id": goal.goal_id,
        "goal_path": str(goal_path),
        "panel_csv": str(panel_path),
        "selected_symbol": selected_symbol,
        "data_as_of": data_as_of,
        "signals": [_signal_to_dict(s) for s in signal_results],
        "backtest": asdict(backtest),
        "risk_decision": asdict(risk_decision),
        "qual_gate": qual_gate,
        "manual_draft": manual_draft,
        "report_path": str(report_path),
        "json_path": str(json_path),
    }
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return result


def discover_default_panel_csv(repo_root: str | Path | None = None) -> Path:
    root = Path(repo_root or Path.cwd())
    matches = sorted(root.glob(DEFAULT_PANEL_GLOB))
    if not matches:
        raise FileNotFoundError(f"no panel CSV found matching {DEFAULT_PANEL_GLOB}")
    return matches[-1]


def _load_panel(panel_csv: str | Path, symbols: list[str]) -> pd.DataFrame:
    panel = pd.read_csv(panel_csv, dtype={"code": str}, parse_dates=["Date"])
    required = {"Date", "code", "Close"}
    missing = required - set(panel.columns)
    if missing:
        raise ValueError(f"panel missing columns: {sorted(missing)}")
    filtered = panel[panel["code"].isin([str(s).zfill(6) for s in symbols])].copy()
    if filtered.empty:
        raise ValueError("panel contains no rows for requested symbols")
    return filtered.sort_values(["code", "Date"])


def _candles_by_symbol(panel: pd.DataFrame) -> dict[str, list[Candle]]:
    by_symbol: dict[str, list[Candle]] = {}
    for symbol, group in panel.groupby("code"):
        candles: list[Candle] = []
        for row in group.sort_values("Date").itertuples(index=False):
            dt = pd.Timestamp(row.Date).to_pydatetime()
            candles.append(
                Candle(
                    symbol=str(symbol),
                    interval="1d",
                    open_time=dt,
                    close_time=dt,
                    close=float(row.Close),
                    open=float(row.Open) if hasattr(row, "Open") and pd.notna(row.Open) else None,
                    high=float(row.High) if hasattr(row, "High") and pd.notna(row.High) else None,
                    low=float(row.Low) if hasattr(row, "Low") and pd.notna(row.Low) else None,
                    volume=float(row.Volume) if hasattr(row, "Volume") and pd.notna(row.Volume) else None,
                    source="panel_csv",
                )
            )
        by_symbol[str(symbol)] = candles
    return by_symbol


def _signal_to_dict(signal: SignalResult) -> dict[str, Any]:
    return {
        "name": signal.name,
        "score": signal.score,
        "rationale": signal.rationale,
        "research_only": signal.research_only,
        "not_investment_advice": signal.not_investment_advice,
        "data_as_of": signal.data_as_of.isoformat() if signal.data_as_of else None,
        "known_limitations": list(signal.known_limitations),
    }


def _nested_get(data: dict[str, Any], keys: list[str], default: Any) -> Any:
    cur: Any = data
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur
