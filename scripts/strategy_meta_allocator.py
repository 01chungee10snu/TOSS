#!/usr/bin/env python3
"""Build cross-strategy correlation evidence and an evidence-aware meta allocation.

Research-only.  This script never calls a broker and never submits orders.
Live allocation is fail-closed: only tournament rows already marked
LIVE_ELIGIBLE can receive non-zero live weight.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from toss_alpha.research.meta_allocator import allocate_candidates, correlation_matrix, risk_metrics

VALIDATOR_PATH = ROOT / "scripts" / "validate_executable_etf_portfolio.py"
VALIDATION = ROOT / "reports" / "validation"
TOURNAMENT_PATH = VALIDATION / "strategy_tournament_latest.json"
ETF_PANEL_PATH = ROOT / "reports" / "backtests" / "executable_etf" / "verified_etf_unadjusted_panel_2015_2026.csv"
FORWARD_PAPER_PATH = ROOT / "reports" / "harness" / "executable_etf_paper_latest.json"
OUT_JSON = ROOT / "reports" / "validation" / "strategy_meta_allocator_latest.json"
OUT_CSV = ROOT / "reports" / "validation" / "strategy_meta_allocator_latest.csv"
OUT_MD = ROOT / "reports" / "validation" / "strategy_meta_allocator_latest.md"
COST_BPS = 75
STRICT_FACTOR_CURVE_PREFIX = "hml_cma_true_pit_"
STRICT_FACTOR_CURVE_SUFFIX = "_75bp_curve.csv"
INDEPENDENT_FACTOR_CORRELATION_THRESHOLD = 0.80


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _load_validator():
    spec = importlib.util.spec_from_file_location("executable_etf_validator_for_meta", VALIDATOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load executable ETF validator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_tournament(path: Path = TOURNAMENT_PATH) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("leaderboard"), list):
        raise RuntimeError("invalid strategy tournament report")
    return value


def _load_etf_panel(path: Path = ETF_PANEL_PATH) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    panel = pd.read_csv(path, dtype={"code": str})
    required = {"date", "code", "open", "close", "dividends"}
    missing = sorted(required.difference(panel.columns))
    if missing:
        raise RuntimeError(f"ETF panel missing columns: {','.join(missing)}")
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    panel["date"] = pd.to_datetime(panel["date"], errors="coerce")
    return panel.dropna(subset=["date"])


def build_etf_return_series(panel: pd.DataFrame, tournament: dict[str, Any]) -> tuple[dict[str, pd.Series], dict[str, Any]]:
    validator = _load_validator()
    tournament_ids = {str(row.get("strategy_id")) for row in tournament.get("leaderboard", [])}
    series: dict[str, pd.Series] = {}
    coverage: dict[str, Any] = {}
    for strategy_id, weights in validator.STRATEGIES.items():
        if strategy_id not in tournament_ids:
            continue
        result = validator.run_backtest(panel, strategy_id, weights, COST_BPS)
        eq = result.equity[["date", "equity"]].copy().sort_values("date")
        eq["date"] = pd.to_datetime(eq["date"])
        ret = eq.set_index("date")["equity"].pct_change().dropna()
        series[strategy_id] = ret
        coverage[strategy_id] = {
            "source": _display_path(ETF_PANEL_PATH),
            "execution_model": "same validator run_backtest; month-end signal -> next-open; whole shares; 75bp per traded notional",
            "observations": int(len(ret)),
            "start": ret.index.min().date().isoformat() if len(ret) else None,
            "end": ret.index.max().date().isoformat() if len(ret) else None,
        }
    return series, coverage


def build_strict_factor_return_series(
    tournament: dict[str, Any],
    *,
    validation_dir: Path = VALIDATION,
) -> tuple[dict[str, pd.Series], dict[str, Any]]:
    """Load comparable 75bp daily curves only for strict PIT factor rows in the tournament."""
    series: dict[str, pd.Series] = {}
    coverage: dict[str, Any] = {}
    for row in tournament.get("leaderboard", []):
        strategy_id = str(row.get("strategy_id") or "").strip()
        if not strategy_id or str(row.get("family") or "") != "true_pit_domestic_factor":
            continue
        if str(row.get("source") or "") != "reports/validation/hml_cma_true_pit_latest.json":
            continue
        if not strategy_id.replace("_", "").isalnum():
            continue
        path = validation_dir / f"{STRICT_FACTOR_CURVE_PREFIX}{strategy_id}{STRICT_FACTOR_CURVE_SUFFIX}"
        if not path.exists():
            coverage[strategy_id] = {
                "source": _display_path(path),
                "data_class": "strict_true_pit_domestic_factor",
                "status": "MISSING_CURVE",
                "observations": 0,
            }
            continue
        try:
            frame = pd.read_csv(path)
        except Exception:
            coverage[strategy_id] = {
                "source": _display_path(path),
                "data_class": "strict_true_pit_domestic_factor",
                "status": "INVALID_CURVE",
                "observations": 0,
            }
            continue
        if not {"date", "equity"}.issubset(frame.columns):
            coverage[strategy_id] = {
                "source": _display_path(path),
                "data_class": "strict_true_pit_domestic_factor",
                "status": "INVALID_CURVE",
                "observations": 0,
            }
            continue
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame["equity"] = pd.to_numeric(frame["equity"], errors="coerce")
        frame = frame.dropna(subset=["date", "equity"]).sort_values("date").drop_duplicates("date", keep="last")
        frame = frame[frame["equity"] > 0]
        ret = frame.set_index("date")["equity"].pct_change(fill_method=None).dropna()
        if ret.empty:
            coverage[strategy_id] = {
                "source": _display_path(path),
                "data_class": "strict_true_pit_domestic_factor",
                "status": "EMPTY_RETURN_SERIES",
                "observations": 0,
            }
            continue
        series[strategy_id] = ret
        coverage[strategy_id] = {
            "source": _display_path(path),
            "data_class": "strict_true_pit_domestic_factor",
            "status": "AVAILABLE",
            "execution_model": "strict receipt-versioned PIT factor; month-end signal -> next-open; 75bp stress; fractional research sizing",
            "observations": int(len(ret)),
            "start": ret.index.min().date().isoformat(),
            "end": ret.index.max().date().isoformat(),
        }
    return series, coverage


def build_comparable_return_series(
    panel: pd.DataFrame,
    tournament: dict[str, Any],
) -> tuple[dict[str, pd.Series], dict[str, Any]]:
    etf_series, etf_coverage = build_etf_return_series(panel, tournament)
    factor_series, factor_coverage = build_strict_factor_return_series(tournament)
    overlap = set(etf_series).intersection(factor_series)
    if overlap:
        raise RuntimeError(f"duplicate comparable return series: {','.join(sorted(overlap))}")
    return etf_series | factor_series, etf_coverage | factor_coverage


def _forward_target_ids(leaderboard: list[dict[str, Any]]) -> list[str]:
    selected: list[str] = []
    for row in leaderboard:
        notes = [str(x) for x in row.get("notes", [])]
        if any("current forward-paper target" in note for note in notes):
            selected.append(str(row.get("strategy_id")))
    return selected


def _current_drawdown_evidence(protected: list[str]) -> tuple[dict[str, float], dict[str, Any]]:
    if len(protected) != 1 or not FORWARD_PAPER_PATH.exists():
        return {}, {"source": None, "status": "MISSING"}
    try:
        data = json.loads(FORWARD_PAPER_PATH.read_text(encoding="utf-8"))
        value = data.get("forward_shadow", {}).get("metrics", {}).get("max_drawdown_pct")
        dd_pct = float(value)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}, {"source": _display_path(FORWARD_PAPER_PATH), "status": "INVALID"}
    if not math.isfinite(dd_pct):
        return {}, {"source": _display_path(FORWARD_PAPER_PATH), "status": "INVALID"}
    sid = protected[0]
    return {sid: dd_pct / 100.0}, {
        "source": _display_path(FORWARD_PAPER_PATH),
        "status": "AVAILABLE",
        "strategy_id": sid,
        "max_drawdown_pct": dd_pct,
    }


def _factor_independence_vs_forward(
    leaderboard: list[dict[str, Any]],
    correlations: dict[str, Any],
    protected: list[str],
    *,
    threshold: float = INDEPENDENT_FACTOR_CORRELATION_THRESHOLD,
) -> dict[str, Any]:
    if len(protected) != 1:
        return {}
    forward = protected[0]
    pair_map = correlations.get("pairs", {})
    result: dict[str, Any] = {}
    for row in leaderboard:
        sid = str(row.get("strategy_id") or "")
        if str(row.get("family") or "") != "true_pit_domestic_factor" or sid == forward:
            continue
        pair = pair_map.get("|".join(sorted((sid, forward))), {})
        pearson = pair.get("pearson")
        downside = pair.get("downside")
        observations = int(pair.get("observations") or 0)
        passed = bool(
            observations >= 252
            and pearson is not None
            and downside is not None
            and float(pearson) < float(threshold)
            and float(downside) < float(threshold)
        )
        result[sid] = {
            "forward_strategy_id": forward,
            "observations": observations,
            "pearson": pearson,
            "downside": downside,
            "threshold": float(threshold),
            "passed_independence_check": passed,
            "allocation_effect": "none_until_tournament_status_reaches_paper_candidate",
        }
    return result


def build_report(tournament: dict[str, Any], returns: dict[str, pd.Series], coverage: dict[str, Any]) -> dict[str, Any]:
    leaderboard = list(tournament.get("leaderboard", []))
    metrics = {sid: risk_metrics(series) for sid, series in returns.items()}
    correlations = correlation_matrix(returns, min_obs=60, windows=(60, 120))
    protected = _forward_target_ids(leaderboard)
    factor_independence = _factor_independence_vs_forward(leaderboard, correlations, protected)
    current_drawdowns, drawdown_evidence = _current_drawdown_evidence(protected)

    live = allocate_candidates(
        leaderboard,
        metrics=metrics,
        correlations=correlations,
        mode="live",
        protected=protected,
        correlation_threshold=0.95,
        max_strategy_weight=0.50,
        min_observations=252,
        current_drawdowns=current_drawdowns,
    )
    shadow = allocate_candidates(
        leaderboard,
        metrics=metrics,
        correlations=correlations,
        mode="research_shadow",
        protected=protected,
        correlation_threshold=0.95,
        max_strategy_weight=0.50,
        min_observations=252,
        current_drawdowns=current_drawdowns,
    )

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "order_submission": False,
        "source_tournament": _display_path(TOURNAMENT_PATH),
        "source_tournament_decision": tournament.get("decision"),
        "daily_return_series_available": sorted(returns),
        "daily_return_series_unavailable": sorted(
            str(row.get("strategy_id")) for row in leaderboard if str(row.get("strategy_id")) not in returns
        ),
        "forward_target_protected_from_correlation_pruning": protected,
        "current_drawdown_evidence": drawdown_evidence,
        "series_coverage": coverage,
        "strategy_risk_metrics": {sid: value.to_dict() for sid, value in metrics.items()},
        "correlations": correlations,
        "factor_independence_vs_forward": factor_independence,
        "live_allocation": live,
        "research_shadow_allocation": shadow,
        "governance": {
            "live_capital_requires_tournament_live_eligible": True,
            "forward_paper_or_research_only_live_weight": 0.0,
            "performance_score_used_for_sizing": False,
            "current_forward_target_is_not_switched_by_meta_research": True,
            "research_only_strategy_weight_until_paper_candidate": 0.0,
            "strict_factor_daily_series_used_for_correlation_before_sizing": True,
            "live_execution_connected": False,
            "live_current_drawdown_evidence_required": True,
            "historical_backtest_end_drawdown_not_used_as_current_risk_state": True,
        },
    }


def write_outputs(report: dict[str, Any]) -> None:
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        fields = ["mode", "strategy_id", "weight", "cash_weight", "selected_after_correlation"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for key in ("live_allocation", "research_shadow_allocation"):
            allocation = report[key]
            selected = set(allocation.get("selected_after_correlation", []))
            for sid, weight in allocation.get("weights", {}).items():
                writer.writerow(
                    {
                        "mode": allocation["mode"],
                        "strategy_id": sid,
                        "weight": weight,
                        "cash_weight": allocation.get("cash_weight"),
                        "selected_after_correlation": sid in selected,
                    }
                )
            if not allocation.get("weights"):
                writer.writerow(
                    {
                        "mode": allocation["mode"],
                        "strategy_id": "CASH",
                        "weight": allocation.get("cash_weight"),
                        "cash_weight": allocation.get("cash_weight"),
                        "selected_after_correlation": False,
                    }
                )

    live = report["live_allocation"]
    shadow = report["research_shadow_allocation"]
    lines = [
        "# Strategy Correlation + Meta Allocator",
        "",
        f"- 생성: {report['generated_at_utc']}",
        f"- Tournament: **{report['source_tournament_decision']}**",
        "- 실행: **research only / order_submission=False**",
        f"- Live allocation: **{live['invested_weight']:.1%} invested / {live['cash_weight']:.1%} cash**",
        f"- Research shadow: **{shadow['invested_weight']:.1%} invested / {shadow['cash_weight']:.1%} cash**",
        "",
        "## Live allocation",
        "",
    ]
    if live["weights"]:
        for sid, weight in live["weights"].items():
            lines.append(f"- {sid}: {weight:.1%}")
    else:
        lines.append("- CASH: 100.0% (LIVE_ELIGIBLE 전략 없음)")
    lines.extend(["", "## Research shadow allocation", ""])
    if shadow["weights"]:
        for sid, weight in shadow["weights"].items():
            lines.append(f"- {sid}: {weight:.1%}")
        if shadow["cash_weight"] > 0:
            lines.append(f"- CASH: {shadow['cash_weight']:.1%}")
    else:
        lines.append("- CASH: 100.0%")
    lines.extend(["", "## Correlation pruning", ""])
    removed = shadow.get("correlation_pruning", {}).get("removed", {})
    if not removed:
        lines.append("- 중복 제거 없음")
    else:
        for sid, info in removed.items():
            lines.append(
                f"- {sid} → {info['duplicate_of']} 중복으로 제외 "
                f"(Pearson={info.get('pearson')}, downside={info.get('downside')})"
            )
    lines.extend(
        [
            "",
            "## Governance",
            "",
            "- LIVE_ELIGIBLE이 아니면 실자본 0%",
            "- Tournament performance score는 sizing에 사용하지 않음",
            "- 현재 Forward target은 상관 중복 제거 시 우선 보존",
            "- 비교 가능한 일별 수익률이 없는 연구전략은 0%",
            "- 본 allocator는 주문 시스템과 연결되지 않음",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    try:
        tournament = _load_tournament()
        panel = _load_etf_panel()
    except FileNotFoundError as exc:
        print(f"BLOCKED_MISSING_INPUT:{exc}")
        return 2
    returns, coverage = build_comparable_return_series(panel, tournament)
    if not returns:
        print("BLOCKED_NO_COMPARABLE_DAILY_RETURN_SERIES")
        return 2
    report = build_report(tournament, returns, coverage)
    write_outputs(report)
    print(f"order_submission={report['order_submission']}")
    print(f"daily_series={len(report['daily_return_series_available'])}")
    print(f"live_invested_weight={report['live_allocation']['invested_weight']}")
    print(f"live_cash_weight={report['live_allocation']['cash_weight']}")
    print(f"shadow_selected={','.join(report['research_shadow_allocation']['selected_after_correlation'])}")
    print(f"shadow_cash_weight={report['research_shadow_allocation']['cash_weight']}")
    print(f"output={OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
