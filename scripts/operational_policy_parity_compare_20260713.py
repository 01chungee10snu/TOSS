"""Exact-parity comparison of current, profit-lock, and stable TOSS policies.

Research/paper only. No broker calls and no live orders.
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

YEAR_PANELS: dict[int, pd.DataFrame] = {}
YEAR_SYMBOLS: dict[int, list[str]] = {}
PRED_MAPS: dict[int, dict[str, dict[str, float]]] = {}
YEARS = [2024, 2025, 2026]
COST_BPS = [30.0, 50.0, 75.0]
POLICIES = {
    "current_live_20d": {
        "max_notional": 150_000, "max_positions": 4, "cash_fraction_per_entry": 0.20,
        "stop_loss_pct": 0.05, "take_profit_pct": 0.10, "trailing_stop_pct": 0.05,
        "trailing_stop_activation_gain_pct": 0.0, "max_holding_trading_days": 20,
        "max_equity_drawdown_stop_pct": 0.06, "risk_cooldown_steps": 8,
    },
    "profit_lock_30d_act5": {
        "max_notional": 150_000, "max_positions": 4, "cash_fraction_per_entry": 0.20,
        "stop_loss_pct": 0.05, "take_profit_pct": 0.10, "trailing_stop_pct": 0.05,
        "trailing_stop_activation_gain_pct": 0.05, "max_holding_trading_days": 30,
        "max_equity_drawdown_stop_pct": 0.06, "risk_cooldown_steps": 8,
    },
    "stable_promoted_25d": {
        "max_notional": 100_000, "max_positions": 3, "cash_fraction_per_entry": 0.15,
        "stop_loss_pct": 0.10, "take_profit_pct": 0.08, "trailing_stop_pct": 0.05,
        "trailing_stop_activation_gain_pct": 0.0, "max_holding_trading_days": 25,
        "max_equity_drawdown_stop_pct": 0.08, "risk_cooldown_steps": 12,
    },
}
OUT_JSON = ROOT / "reports/harness/operational_policy_parity_compare_20260713.json"
OUT_CSV = ROOT / "reports/harness/operational_policy_parity_compare_20260713.csv"
OUT_MD = ROOT / "reports/harness/operational_policy_parity_compare_20260713.md"


def run_one(task: dict[str, Any]) -> dict[str, Any]:
    cfg_spec = POLICIES[str(task["policy_id"])]
    cfg = dict(ENGINE_BASE)
    step = int(cfg.pop("step"))
    cfg.update({
        "max_positions": cfg_spec["max_positions"],
        "stop_loss_pct": cfg_spec["stop_loss_pct"],
        "take_profit_pct": cfg_spec["take_profit_pct"],
        "trailing_stop_pct": cfg_spec["trailing_stop_pct"],
        "trailing_stop_activation_gain_pct": cfg_spec["trailing_stop_activation_gain_pct"],
        "max_holding_steps": 999,
        "max_holding_trading_days": cfg_spec["max_holding_trading_days"],
        "cash_fraction_per_entry": cfg_spec["cash_fraction_per_entry"],
        "max_equity_drawdown_stop_pct": cfg_spec["max_equity_drawdown_stop_pct"],
        "risk_cooldown_steps": cfg_spec["risk_cooldown_steps"],
    })
    year = int(task["trade_year"])
    engine = ReplayEngine(
        panel=YEAR_PANELS[year], symbols=YEAR_SYMBOLS[year],
        initial_cash_krw=1_000_000, max_notional_krw=cfg_spec["max_notional"],
        transaction_cost_bps=float(task["transaction_cost_bps"]),
        prediction_map=PRED_MAPS[year], prediction_overlay_mode="rerank",
        prediction_alpha=10.0, **cfg,
    )
    result = engine.run(step=step)
    row = dict(result["summary"])
    row.update(task)
    return row


def main() -> None:
    panel = pd.read_parquet(PANEL_PATH)
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    panel["Date"] = pd.to_datetime(panel["Date"])
    features = compute_features(panel)
    macro_path = MACRO_CACHE
    if not macro_path.exists():
        macro_path = ROOT / "src/toss_alpha/reports/harness/macro_signals.parquet"
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
        print(f"ready {year}", flush=True)
    tasks = [
        {"policy_id": policy_id, "trade_year": year, "transaction_cost_bps": cost}
        for policy_id in POLICIES for year in YEARS for cost in COST_BPS
    ]
    jobs = min(int(os.environ.get("TOSS_FRONTIER_JOBS", "15")), os.cpu_count() or 1)
    with mp.get_context("fork").Pool(jobs) as pool:
        rows = list(pool.imap_unordered(run_one, tasks, chunksize=1))
    df = pd.DataFrame(rows).sort_values(["transaction_cost_bps", "policy_id", "trade_year"])
    agg = df.groupby(["policy_id", "transaction_cost_bps"], as_index=False).agg(
        mean_return=("total_return_pct", "mean"), min_return=("total_return_pct", "min"),
        max_mdd=("max_drawdown_pct", "min"), mean_sharpe=("sharpe_ratio", "mean"),
        total_trades=("total_trades", "sum"),
    )
    base = agg[agg.policy_id == "current_live_20d"].set_index("transaction_cost_bps")
    for metric in ["mean_return", "min_return", "max_mdd", "mean_sharpe"]:
        agg[f"delta_{metric}"] = agg[metric] - agg.transaction_cost_bps.map(base[metric])
    stress = agg[agg.transaction_cost_bps == max(COST_BPS)].set_index("policy_id")
    verdict = "KEEP_CURRENT"
    selected = None
    for policy_id in ["profit_lock_30d_act5", "stable_promoted_25d"]:
        all_cost = agg[agg.policy_id == policy_id]
        row = stress.loc[policy_id]
        if (
            (all_cost.min_return > 0).all()
            and (all_cost.mean_sharpe > base.mean_sharpe.values).all()
            and row.max_mdd >= -10.0
            and row.min_return >= 5.0
            and row.mean_return >= stress.loc["current_live_20d"].mean_return * 0.85
        ):
            verdict = "PROMOTABLE"
            selected = policy_id
            break
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "paper_only": True, "live_order_submitted": False,
        "panel_date_max": str(panel.Date.max().date()),
        "policies": POLICIES, "verdict": verdict, "selected_policy": selected,
        "promotion_gate": {
            "all_years_positive_all_costs": True,
            "beat_current_mean_sharpe_all_costs": True,
            "worst_cost_mdd_at_least_pct": -10.0,
            "worst_cost_min_year_return_at_least_pct": 5.0,
            "worst_cost_mean_return_retention_vs_current": 0.85,
        },
        "aggregate": agg.to_dict(orient="records"),
    }
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_CSV, index=False)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Operational Policy Exact-Parity Compare — 20260713", "", "Research/paper only. No broker calls. No live orders.", "", f"- verdict: `{verdict}`", f"- selected: `{selected}`", "", "## Aggregate", ""]
    for _, r in agg.sort_values(["transaction_cost_bps", "mean_sharpe"], ascending=[True, False]).iterrows():
        lines.append(f"- {r.policy_id}, cost={r.transaction_cost_bps:.0f}bp: mean={r.mean_return:.2f}% min={r.min_return:.2f}% MDD={r.max_mdd:.2f}% Sharpe={r.mean_sharpe:.2f} trades={int(r.total_trades)}")
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": verdict, "selected": selected, "json": str(OUT_JSON)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
