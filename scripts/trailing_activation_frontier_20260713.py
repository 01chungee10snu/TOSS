"""Trailing-stop activation frontier for the promoted loss-averse TOSS policy.

Research/paper only. No broker calls and no live orders.
Keeps the promoted exposure/risk configuration fixed and tests whether delaying
trailing-stop activation until a position has earned a minimum gain improves
walk-forward return without weakening drawdown or cost robustness.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from toss_alpha.daily.features import compute_features  # noqa: E402
from toss_alpha.daily.replay import ReplayEngine  # noqa: E402
from backtest_sentiment_overlay import ENGINE_BASE, symbols_of  # noqa: E402
from fusion_3layer_backtest import (  # noqa: E402
    PANEL_PATH, SENT_CSV, MACRO_CACHE, build_sentiment_map,
    filter_sentiment_by_year, train_ml_model, predict_ml_scores,
    compute_macro_adjusted_scores,
)

STAMP = "20260713"
OUT_CSV = ROOT / f"reports/harness/trailing_activation_frontier_{STAMP}.csv"
OUT_AGG = ROOT / f"reports/harness/trailing_activation_frontier_{STAMP}_agg.csv"
OUT_JSON = ROOT / f"reports/harness/trailing_activation_frontier_{STAMP}.json"
OUT_MD = ROOT / f"reports/harness/trailing_activation_frontier_{STAMP}.md"

YEAR_PANELS: dict[int, pd.DataFrame] = {}
YEAR_SYMBOLS: dict[int, list[str]] = {}
PRED_MAPS: dict[int, dict[str, dict[str, float]]] = {}

BASE_CONFIG = {
    "max_notional": 150_000,
    "max_positions": 4,
    "cash_fraction_per_entry": 0.20,
    "stop_loss_pct": 0.05,
    "take_profit_pct": 0.10,
    "trailing_stop_pct": 0.05,
    "max_holding_steps": 999,
    "max_holding_trading_days": 20,
    "max_equity_drawdown_stop_pct": 0.06,
    "risk_cooldown_steps": 8,
}
ACTIVATION_GAINS = [0.00, 0.03, 0.05]
HOLDING_TRADING_DAYS = [5, 10, 15, 20, 25, 30, 40, 50]
COST_BPS = [30.0, 50.0, 75.0]
YEARS = [2024, 2025, 2026]


def run_one(task: dict[str, Any]) -> dict[str, Any]:
    year = int(task["trade_year"])
    cfg = dict(ENGINE_BASE)
    step = int(cfg.pop("step"))
    cfg.update({
        "max_positions": BASE_CONFIG["max_positions"],
        "stop_loss_pct": BASE_CONFIG["stop_loss_pct"],
        "take_profit_pct": BASE_CONFIG["take_profit_pct"],
        "trailing_stop_pct": BASE_CONFIG["trailing_stop_pct"],
        "trailing_stop_activation_gain_pct": float(task["activation_gain_pct"]),
        "max_holding_steps": BASE_CONFIG["max_holding_steps"],
        "max_holding_trading_days": int(task["max_holding_trading_days"]),
        "cash_fraction_per_entry": BASE_CONFIG["cash_fraction_per_entry"],
        "max_equity_drawdown_stop_pct": BASE_CONFIG["max_equity_drawdown_stop_pct"],
        "risk_cooldown_steps": BASE_CONFIG["risk_cooldown_steps"],
    })
    engine = ReplayEngine(
        panel=YEAR_PANELS[year],
        symbols=YEAR_SYMBOLS[year],
        initial_cash_krw=1_000_000,
        max_notional_krw=BASE_CONFIG["max_notional"],
        transaction_cost_bps=float(task["transaction_cost_bps"]),
        prediction_map=PRED_MAPS[year],
        prediction_overlay_mode="rerank",
        prediction_alpha=10.0,
        **cfg,
    )
    result = engine.run(step=step)
    summary = dict(result["summary"])
    trades = result.get("trades", [])
    one_step = [t for t in trades if int(t.get("holding_steps", 0)) <= 1]
    summary.update(task)
    summary["trade_count"] = int(summary.get("total_trades", 0))
    summary["one_step_trade_count"] = len(one_step)
    summary["one_step_pnl_krw"] = round(sum(float(t.get("pnl_krw", 0.0)) for t in one_step), 2)
    summary["trailing_exit_count"] = sum(t.get("exit_reason") == "trailing_stop" for t in trades)
    return summary


def aggregate(rows: pd.DataFrame) -> pd.DataFrame:
    agg = rows.groupby(["max_holding_trading_days", "activation_gain_pct", "transaction_cost_bps"], as_index=False).agg(
        mean_return=("total_return_pct", "mean"),
        min_return=("total_return_pct", "min"),
        max_mdd=("max_drawdown_pct", "min"),
        mean_sharpe=("sharpe_ratio", "mean"),
        total_trades=("trade_count", "sum"),
        one_step_trades=("one_step_trade_count", "sum"),
        one_step_pnl_krw=("one_step_pnl_krw", "sum"),
        trailing_exits=("trailing_exit_count", "sum"),
    )
    baseline = agg[
        (agg["max_holding_trading_days"] == BASE_CONFIG["max_holding_trading_days"])
        & (agg["activation_gain_pct"] == 0.0)
    ].set_index("transaction_cost_bps")
    for metric in ["mean_return", "min_return", "max_mdd", "mean_sharpe"]:
        agg[f"baseline_{metric}"] = agg["transaction_cost_bps"].map(baseline[metric])
        agg[f"delta_{metric}"] = agg[metric] - agg[f"baseline_{metric}"]
    agg["all_years_positive"] = agg["min_return"] > 0.0
    agg["mdd_gate_pass"] = agg["max_mdd"] >= -10.0
    return agg.sort_values(["transaction_cost_bps", "mean_sharpe", "mean_return"], ascending=[True, False, False])


def candidate_verdict(agg: pd.DataFrame) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    candidates = []
    eligible = agg[
        ~(
            (agg["max_holding_trading_days"] == BASE_CONFIG["max_holding_trading_days"])
            & (agg["activation_gain_pct"] == 0.0)
        )
    ]
    for (holding_days, activation), group in eligible.groupby(
        ["max_holding_trading_days", "activation_gain_pct"]
    ):
        by_cost = group.set_index("transaction_cost_bps")
        if set(COST_BPS) - set(by_cost.index):
            continue
        strict = bool(
            by_cost["all_years_positive"].all()
            and by_cost["mdd_gate_pass"].all()
            and (by_cost["delta_mean_return"] > 0).all()
            and (by_cost["delta_min_return"] >= 0).all()
            and (by_cost["delta_mean_sharpe"] > 0).all()
            and (by_cost["delta_max_mdd"] >= -0.50).all()
        )
        worst = by_cost.loc[max(COST_BPS)]
        candidates.append({
            "max_holding_trading_days": int(holding_days),
            "activation_gain_pct": float(activation),
            "strict_pass": strict,
            "worst_cost_mean_return": float(worst["mean_return"]),
            "worst_cost_min_return": float(worst["min_return"]),
            "worst_cost_mdd": float(worst["max_mdd"]),
            "worst_cost_sharpe": float(worst["mean_sharpe"]),
            "worst_cost_return_delta": float(worst["delta_mean_return"]),
            "worst_cost_sharpe_delta": float(worst["delta_mean_sharpe"]),
        })
    passing = [row for row in candidates if row["strict_pass"]]
    passing.sort(key=lambda r: (r["worst_cost_sharpe"], r["worst_cost_mean_return"]), reverse=True)
    selected = passing[0] if passing else None
    return selected, {"candidates": candidates, "selected": selected}


def main() -> None:
    panel = pd.read_parquet(PANEL_PATH)
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    panel["Date"] = pd.to_datetime(panel["Date"])
    features = compute_features(panel)
    macro_path = MACRO_CACHE
    if not macro_path.exists():
        fallback = ROOT / "src/toss_alpha/reports/harness/macro_signals.parquet"
        if not fallback.exists():
            raise FileNotFoundError(f"macro cache not found: {macro_path} or {fallback}")
        macro_path = fallback
    macro = pd.read_parquet(macro_path)
    sentiment = build_sentiment_map(pd.read_csv(SENT_CSV, parse_dates=["date"]))

    for train_end, year in [(2023, 2024), (2024, 2025), (2025, 2026)]:
        year_panel = panel[panel["Date"].dt.year == year].copy()
        YEAR_PANELS[year] = year_panel
        YEAR_SYMBOLS[year] = symbols_of(year_panel)
        model = train_ml_model(features, train_end)
        ml_map = predict_ml_scores(model, features, year)
        year_sent = filter_sentiment_by_year(sentiment, year)
        PRED_MAPS[year] = compute_macro_adjusted_scores(ml_map, macro, year_sent if year_sent else None)
        print(f"ready train<={train_end} trade={year}", flush=True)

    tasks = [
        {
            "trade_year": year,
            "max_holding_trading_days": holding_days,
            "activation_gain_pct": activation,
            "transaction_cost_bps": cost,
        }
        for year in YEARS
        for holding_days in HOLDING_TRADING_DAYS
        for activation in ACTIVATION_GAINS
        for cost in COST_BPS
    ]
    jobs = min(int(os.environ.get("TOSS_FRONTIER_JOBS", "15")), os.cpu_count() or 1)
    print(f"tasks={len(tasks)} jobs={jobs}", flush=True)
    with mp.get_context("fork").Pool(jobs) as pool:
        rows = list(pool.imap_unordered(run_one, tasks, chunksize=1))

    df = pd.DataFrame(rows).sort_values(
        ["transaction_cost_bps", "max_holding_trading_days", "activation_gain_pct", "trade_year"]
    )
    agg = aggregate(df)
    selected, verdict_detail = candidate_verdict(agg)
    verdict = "PROMOTABLE" if selected is not None else "KEEP_BASELINE"
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "paper_only": True,
        "live_order_submitted": False,
        "panel": str(PANEL_PATH),
        "panel_date_max": str(panel["Date"].max().date()),
        "base_config": BASE_CONFIG,
        "activation_gains": ACTIVATION_GAINS,
        "holding_trading_days": HOLDING_TRADING_DAYS,
        "cost_bps": COST_BPS,
        "promotion_gate": {
            "all_years_positive": True,
            "mdd_at_least_pct": -10.0,
            "beat_baseline_mean_return_at_all_costs": True,
            "no_worse_baseline_min_year_return_at_all_costs": True,
            "beat_baseline_mean_sharpe_at_all_costs": True,
            "max_allowed_mdd_degradation_pct_points": 0.50,
        },
        "verdict": verdict,
        "selected_candidate": selected,
        "verdict_detail": verdict_detail,
        "files": {"rows_csv": str(OUT_CSV), "aggregate_csv": str(OUT_AGG), "report_md": str(OUT_MD)},
    }
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_CSV, index=False)
    agg.to_csv(OUT_AGG, index=False)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        f"# Trailing Activation Frontier — {STAMP}", "",
        "Research/paper only. No broker calls. No live orders.", "",
        f"- verdict: `{verdict}`",
        f"- selected candidate: `{selected}`", "",
        "## Aggregate by holding period, activation and cost", "",
    ]
    for _, r in agg.iterrows():
        lines.append(
            f"- hold={int(r['max_holding_trading_days'])}d, activation={r['activation_gain_pct']:.0%}, cost={r['transaction_cost_bps']:.0f}bp: "
            f"mean={r['mean_return']:.2f}% min={r['min_return']:.2f}% MDD={r['max_mdd']:.2f}% "
            f"Sharpe={r['mean_sharpe']:.2f} trades={int(r['total_trades'])} "
            f"delta_ret={r['delta_mean_return']:+.2f}%p delta_sharpe={r['delta_mean_sharpe']:+.2f}"
        )
    lines.extend(["", "## Promotion rule", "", "Candidate must beat activation=0 at every cost scenario, keep every year positive, keep MDD >= -10%, and degrade MDD by no more than 0.5%p."])
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": verdict, "selected": selected, "json": str(OUT_JSON)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
