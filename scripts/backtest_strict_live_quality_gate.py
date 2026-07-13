#!/usr/bin/env python3
"""Backtest the post-mortem live-quality rule set.

Research/backtest only. No broker calls. No live orders.

Rule set under test:
- No ordinary BUY in bad/uncertain regimes.  Historical proxy: allow entries only
  in `risk_on` for strict variants; baseline keeps existing neutral+risk_on.
- SELL exits remain active: stop-loss, take-profit, trailing, time, risk_off exit.
- Inverse hedge is treated as unavailable, so bad regimes become cash, not inverse.
- No experimental/micro-live BUY: all entries must pass the canonical score gate.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from backtest_sentiment_overlay import ENGINE_BASE, symbols_of  # noqa: E402
from fusion_3layer_backtest import (  # noqa: E402
    PANEL_PATH,
    SENT_CSV,
    MACRO_CACHE,
    build_sentiment_map,
    filter_sentiment_by_year,
    train_ml_model,
    predict_ml_scores,
    compute_macro_adjusted_scores,
)
from toss_alpha.daily.features import compute_features  # noqa: E402
from toss_alpha.daily.macro_signals import fetch_macro_signals  # noqa: E402
from toss_alpha.daily.replay import ReplayEngine  # noqa: E402

OUT_DIR = ROOT / "reports" / "harness"
STAMP = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
OUT_CSV = OUT_DIR / f"strict_live_quality_gate_backtest_{STAMP}.csv"
OUT_AGG = OUT_DIR / f"strict_live_quality_gate_backtest_{STAMP}_agg.csv"
OUT_JSON = OUT_DIR / f"strict_live_quality_gate_backtest_{STAMP}.json"
OUT_MD = OUT_DIR / f"strict_live_quality_gate_backtest_{STAMP}.md"
LATEST_JSON = OUT_DIR / "strict_live_quality_gate_backtest_latest.json"
YEARS = [2024, 2025, 2026]


class EntryRegimeReplayEngine(ReplayEngine):
    """ReplayEngine with a stricter allowed-entry-regime overlay."""

    def __init__(self, *args, allowed_entry_regimes: set[str] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.allowed_entry_regimes = allowed_entry_regimes
        self._last_regime_status: str | None = None

    def _check_exits(self, close_prices, date_str, step_idx, regime):  # type: ignore[override]
        self._last_regime_status = str((regime or {}).get("status") or "unknown")
        return super()._check_exits(close_prices, date_str, step_idx, regime)

    def _check_entries(self, candidates, close_prices, date_str, step_idx, volume_lookup=None):  # type: ignore[override]
        if self.allowed_entry_regimes is not None and self._last_regime_status not in self.allowed_entry_regimes:
            return None
        return super()._check_entries(candidates, close_prices, date_str, step_idx, volume_lookup)


def daily_avg_pct(total_return_pct: float) -> float:
    return float(total_return_pct) / 252.0


def max_drawdown_from_curve(curve: list[dict[str, Any]]) -> float:
    peak = 0.0
    mdd = 0.0
    for row in curve:
        eq = float(row.get("equity") or 0.0)
        peak = max(peak, eq)
        if peak > 0:
            mdd = min(mdd, eq / peak - 1.0)
    return mdd * 100.0


def exit_counts(trades: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in trades:
        reason = str(t.get("exit_reason") or "unknown")
        out[reason] = out.get(reason, 0) + 1
    return out


def build_prediction_maps(panel: pd.DataFrame) -> dict[int, dict[str, dict[str, float]]]:
    features_df = compute_features(panel)
    macro_df = pd.read_parquet(MACRO_CACHE) if MACRO_CACHE.exists() else fetch_macro_signals()
    sent_df = pd.read_csv(SENT_CSV, parse_dates=["date"])
    sent_map = build_sentiment_map(sent_df)
    pred_maps: dict[int, dict[str, dict[str, float]]] = {}
    for train_end, year in [(2023, 2024), (2024, 2025), (2025, 2026)]:
        edge_model = train_ml_model(features_df, train_end)
        ml_map = predict_ml_scores(edge_model, features_df, year)
        yr_sent = filter_sentiment_by_year(sent_map, year)
        pred_maps[year] = compute_macro_adjusted_scores(ml_map, macro_df, yr_sent if yr_sent else None)
    return pred_maps


VARIANTS = [
    {
        "variant": "current_promoted_baseline",
        "description": "현재 승격 기준. risk_off 신규진입 금지, neutral/risk_on 허용.",
        "allowed_entry_regimes": None,
        "max_notional_krw": 150_000,
        "max_positions": 4,
        "cash_fraction_per_entry": 0.20,
        "stop_loss_pct": 0.05,
        "take_profit_pct": 0.10,
        "trailing_stop_pct": 0.05,
        "max_holding_steps": 10,
        "max_equity_drawdown_stop_pct": 0.06,
        "risk_cooldown_steps": 8,
    },
    {
        "variant": "strict_risk_on_only_same_exits",
        "description": "위험/불확실 장세 매수 금지 강화. risk_on에서만 신규진입.",
        "allowed_entry_regimes": {"risk_on"},
        "max_notional_krw": 150_000,
        "max_positions": 4,
        "cash_fraction_per_entry": 0.20,
        "stop_loss_pct": 0.05,
        "take_profit_pct": 0.10,
        "trailing_stop_pct": 0.05,
        "max_holding_steps": 10,
        "max_equity_drawdown_stop_pct": 0.06,
        "risk_cooldown_steps": 8,
    },
    {
        "variant": "strict_fast_stop",
        "description": "risk_on만 매수 + 빠른 손절 3% + 기존 익절/트레일링.",
        "allowed_entry_regimes": {"risk_on"},
        "max_notional_krw": 150_000,
        "max_positions": 4,
        "cash_fraction_per_entry": 0.20,
        "stop_loss_pct": 0.03,
        "take_profit_pct": 0.10,
        "trailing_stop_pct": 0.05,
        "max_holding_steps": 10,
        "max_equity_drawdown_stop_pct": 0.06,
        "risk_cooldown_steps": 8,
    },
    {
        "variant": "today_watchdog_style",
        "description": "오늘 라이브 반성 기준에 가까운 초방어형: risk_on만, 3% 손절, 5% 익절, 2% 트레일링, 짧은 보유.",
        "allowed_entry_regimes": {"risk_on"},
        "max_notional_krw": 100_000,
        "max_positions": 3,
        "cash_fraction_per_entry": 0.15,
        "stop_loss_pct": 0.03,
        "take_profit_pct": 0.05,
        "trailing_stop_pct": 0.02,
        "max_holding_steps": 5,
        "max_equity_drawdown_stop_pct": 0.04,
        "risk_cooldown_steps": 12,
    },
    {
        "variant": "loss_averse_top_with_risk_on_only",
        "description": "최근 5h 탐색 상위 loss-averse 후보에 risk_on-only 매수 기준을 덧댄 보수형.",
        "allowed_entry_regimes": {"risk_on"},
        "max_notional_krw": 100_000,
        "max_positions": 3,
        "cash_fraction_per_entry": 0.15,
        "stop_loss_pct": 0.10,
        "take_profit_pct": 0.08,
        "trailing_stop_pct": 0.05,
        "max_holding_steps": 5,
        "max_equity_drawdown_stop_pct": 0.08,
        "risk_cooldown_steps": 12,
    },
]


def run_variant_year(panel: pd.DataFrame, year: int, variant: dict[str, Any], pred_map: dict[str, dict[str, float]]) -> dict[str, Any]:
    p = panel[panel["Date"].dt.year == year].copy()
    cfg = dict(ENGINE_BASE)
    step = int(cfg.pop("step"))
    cfg.update({
        "max_positions": int(variant["max_positions"]),
        "stop_loss_pct": float(variant["stop_loss_pct"]),
        "take_profit_pct": float(variant["take_profit_pct"]),
        "trailing_stop_pct": float(variant["trailing_stop_pct"]),
        "max_holding_steps": int(variant["max_holding_steps"]),
        "cash_fraction_per_entry": float(variant["cash_fraction_per_entry"]),
        "max_equity_drawdown_stop_pct": float(variant["max_equity_drawdown_stop_pct"]),
        "risk_cooldown_steps": int(variant["risk_cooldown_steps"]),
    })
    engine = EntryRegimeReplayEngine(
        panel=p,
        symbols=symbols_of(p),
        initial_cash_krw=1_000_000,
        max_notional_krw=float(variant["max_notional_krw"]),
        transaction_cost_bps=30.0,
        prediction_map=pred_map,
        prediction_overlay_mode="rerank",
        prediction_alpha=10.0,
        allowed_entry_regimes=variant.get("allowed_entry_regimes"),
        **cfg,
    )
    result = engine.run(step=step)
    s = dict(result["summary"])
    regimes = pd.DataFrame(result["equity_curve"])
    regime_counts = regimes["regime"].value_counts().to_dict() if not regimes.empty and "regime" in regimes else {}
    s.update({
        "variant": variant["variant"],
        "description": variant["description"],
        "trade_year": year,
        "daily_avg_pct_252": daily_avg_pct(float(s["total_return_pct"])),
        "trade_count": int(s.get("total_trades", 0)),
        "exit_counts": json.dumps(exit_counts(result.get("trades", [])), ensure_ascii=False, sort_keys=True),
        "risk_on_steps": int(regime_counts.get("risk_on", 0)),
        "neutral_steps": int(regime_counts.get("neutral", 0)),
        "risk_off_steps": int(regime_counts.get("risk_off", 0)),
    })
    for k, v in variant.items():
        if k not in {"description", "allowed_entry_regimes"}:
            s[k] = v
    s["allowed_entry_regimes"] = "baseline_engine" if variant.get("allowed_entry_regimes") is None else ",".join(sorted(variant["allowed_entry_regimes"]))
    return s


def aggregate(rows: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    group_cols = [
        "variant", "description", "allowed_entry_regimes", "max_notional_krw", "max_positions",
        "cash_fraction_per_entry", "stop_loss_pct", "take_profit_pct", "trailing_stop_pct",
        "max_holding_steps", "max_equity_drawdown_stop_pct", "risk_cooldown_steps",
    ]
    agg = df.groupby(group_cols, as_index=False).agg(
        mean_return=("total_return_pct", "mean"),
        min_return=("total_return_pct", "min"),
        max_return=("total_return_pct", "max"),
        mean_daily=("daily_avg_pct_252", "mean"),
        worst_mdd=("max_drawdown_pct", "min"),
        mean_sharpe=("sharpe_ratio", "mean"),
        min_sharpe=("sharpe_ratio", "min"),
        total_trades=("trade_count", "sum"),
        min_trades=("trade_count", "min"),
        risk_stops=("risk_stop_count", "sum"),
    )
    agg["all_years_positive"] = agg["min_return"] > 0
    agg["mdd_pass_10pct"] = agg["worst_mdd"] >= -10.0
    agg["promotable"] = agg["all_years_positive"] & agg["mdd_pass_10pct"] & (agg["min_trades"] > 0)
    agg["loss_averse_score"] = (
        agg["mean_sharpe"] * 10 + agg["mean_return"] * 0.05 + agg["worst_mdd"] * 0.9 + agg["min_return"] * 0.15
    )
    return agg.sort_values(["promotable", "loss_averse_score", "mean_sharpe", "worst_mdd"], ascending=[False, False, False, False])


def write_markdown(rows: pd.DataFrame, agg: pd.DataFrame, payload: dict[str, Any]) -> None:
    best = agg.iloc[0].to_dict()
    baseline = agg[agg["variant"] == "current_promoted_baseline"].iloc[0].to_dict()
    lines = [
        "# Strict Live-Quality Gate Backtest",
        "",
        "Research/backtest only. No broker calls. No live orders.",
        "",
        "## Rule under test",
        "- Bad/uncertain regime ordinary BUY is blocked. Historical proxy: strict variants allow entries only in `risk_on`.",
        "- SELL exits stay active: stop-loss, take-profit, trailing, time exit, and risk-off exit.",
        "- Inverse hedge unavailable is modeled as cash on bad regimes, not inverse exposure.",
        "- Experimental micro-live BUY is not represented; all entries pass the canonical score gate.",
        "",
        f"- Panel: `{PANEL_PATH}`",
        f"- Years: `{YEARS}`",
        f"- Cost: `30bps` per side in ReplayEngine",
        "",
        "## Verdict",
        "",
    ]
    if bool(best["promotable"]):
        lines.append(f"- Best promotable: `{best['variant']}` mean_ret={best['mean_return']:.2f}%, min_ret={best['min_return']:.2f}%, worst_mdd={best['worst_mdd']:.2f}%, mean_sharpe={best['mean_sharpe']:.2f}.")
    else:
        lines.append("- No variant passed all promotion gates.")
    lines.append(f"- Baseline: mean_ret={baseline['mean_return']:.2f}%, min_ret={baseline['min_return']:.2f}%, worst_mdd={baseline['worst_mdd']:.2f}%, mean_sharpe={baseline['mean_sharpe']:.2f}.")
    lines += ["", "## Aggregate", "", "| variant | entry | mean % | min % | worst MDD % | Sharpe | trades | promotable |", "|---|---|---:|---:|---:|---:|---:|---|"]
    for _, r in agg.iterrows():
        lines.append(f"| {r['variant']} | {r['allowed_entry_regimes']} | {r['mean_return']:.2f} | {r['min_return']:.2f} | {r['worst_mdd']:.2f} | {r['mean_sharpe']:.2f} | {int(r['total_trades'])} | {bool(r['promotable'])} |")
    lines += ["", "## Year rows", "", "| variant | year | return % | MDD % | Sharpe | trades | win % | exits |", "|---|---:|---:|---:|---:|---:|---:|---|"]
    for _, r in rows.sort_values(["variant", "trade_year"]).iterrows():
        lines.append(f"| {r['variant']} | {int(r['trade_year'])} | {r['total_return_pct']:.2f} | {r['max_drawdown_pct']:.2f} | {r['sharpe_ratio']:.2f} | {int(r['trade_count'])} | {r['win_rate_pct']:.1f} | `{r['exit_counts']}` |")
    lines += ["", "## Files", "", f"- rows: `{OUT_CSV}`", f"- aggregate: `{OUT_AGG}`", f"- json: `{OUT_JSON}`"]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    panel = pd.read_parquet(PANEL_PATH)
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    panel["Date"] = pd.to_datetime(panel["Date"])
    print(f"PANEL {PANEL_PATH} rows={len(panel):,} codes={panel['code'].nunique()} dates={panel['Date'].min().date()}..{panel['Date'].max().date()}", flush=True)
    print("BUILDING_PREDICTION_MAPS", flush=True)
    pred_maps = build_prediction_maps(panel)
    print("RUNNING_VARIANTS", flush=True)
    rows: list[dict[str, Any]] = []
    for variant in VARIANTS:
        for year in YEARS:
            row = run_variant_year(panel, year, variant, pred_maps[year])
            rows.append(row)
            print(f"{variant['variant']} {year} ret={row['total_return_pct']:.2f}% mdd={row['max_drawdown_pct']:.2f}% trades={row['trade_count']}", flush=True)
    df = pd.DataFrame(rows)
    agg = aggregate(rows)
    df.to_csv(OUT_CSV, index=False)
    agg.to_csv(OUT_AGG, index=False)
    payload = {
        "paper_only": True,
        "live_order_submitted": False,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "panel": str(PANEL_PATH),
        "years": YEARS,
        "rule_set": "post_mortem_strict_live_quality_gate",
        "files": {"rows_csv": str(OUT_CSV), "agg_csv": str(OUT_AGG), "json": str(OUT_JSON), "md": str(OUT_MD)},
        "best": agg.iloc[0].to_dict(),
        "baseline": agg[agg["variant"] == "current_promoted_baseline"].iloc[0].to_dict(),
        "aggregate": agg.to_dict(orient="records"),
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    LATEST_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    write_markdown(df, agg, payload)
    print(json.dumps({"best": payload["best"], "baseline": payload["baseline"], "files": payload["files"]}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
