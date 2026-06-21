"""Regime-based ensemble backtest: dynamic strategy switching.

Research/paper only. No live orders.

Key insight from sizing_frontier:
- fusion_rerank dominates in sideways/defensive years (2024: +84% vs ml +49%)
- ml_rerank dominates in trending/up years (2026: +94% vs fusion +43%)

This script switches between the two prediction maps based on macro regime,
applying ml_rerank during risk_on and fusion_rerank during neutral/risk_off.

Sweep ensemble_weight (0.0=all fusion, 1.0=all ml, 0.5=50/50) and sizing
to find the optimal blend at each daily-target level.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import sys
import warnings
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from toss_alpha.daily.features import compute_features  # noqa: E402
from toss_alpha.daily.replay import ReplayEngine
from toss_alpha.daily.macro_signals import get_macro_regime
from backtest_sentiment_overlay import ENGINE_BASE, symbols_of
from fusion_3layer_backtest import (
    PANEL_PATH,
    SENT_CSV,
    MACRO_CACHE,
    build_sentiment_map,
    filter_sentiment_by_year,
    train_ml_model,
    predict_ml_scores,
    compute_macro_adjusted_scores,
)
warnings.filterwarnings("ignore", category=FutureWarning)

OUT_CSV = ROOT / "reports/harness/ensemble_frontier_20260621.csv"
OUT_MD = ROOT / "reports/harness/ensemble_frontier_20260621.md"

YEAR_PANELS: dict[int, pd.DataFrame] = {}
YEAR_SYMBOLS: dict[int, list[str]] = {}


def daily_avg_pct(total_return_pct: float) -> float:
    return total_return_pct / 252.0


def build_ensemble_map(
    ml_map: dict,
    fusion_map: dict,
    macro_df: pd.DataFrame,
    ensemble_weight: float,
) -> dict:
    """Blend ml and fusion maps based on regime.

    ensemble_weight=1.0: use ml_map on risk_on days, fusion otherwise.
    ensemble_weight=0.0: use fusion_map always (pure fusion).
    ensemble_weight=0.5: blend 50/50 on all days.
    """
    blended: dict[str, dict[str, float]] = {}
    all_dates = set(ml_map.keys()) | set(fusion_map.keys())
    for date_str in all_dates:
        date = pd.Timestamp(date_str)
        regime = get_macro_regime(date, macro_df)
        is_risk_on = regime["status"] == "risk_on"

        ml_scores = ml_map.get(date_str, {})
        fusion_scores = fusion_map.get(date_str, {})

        if ensemble_weight >= 1.0:
            # Pure regime switch: ml on risk_on, fusion otherwise
            chosen = ml_scores if is_risk_on else fusion_scores
        else:
            # Weighted blend
            chosen = {}
            all_codes = set(ml_scores.keys()) | set(fusion_scores.keys())
            w_ml = ensemble_weight if is_risk_on else ensemble_weight * 0.5
            w_fu = (1 - ensemble_weight) if is_risk_on else (1 - ensemble_weight * 0.5)
            for code in all_codes:
                chosen[code] = ml_scores.get(code, 0) * w_ml + fusion_scores.get(code, 0) * w_fu
        if chosen:
            blended[date_str] = chosen
    return blended


def run_one(task: dict[str, Any], features_df, macro_df, sent_map) -> dict[str, Any]:
    year = int(task["trade_year"])
    p = YEAR_PANELS[year]
    train_end = int(task["train_end"])

    # Build prediction maps
    model = train_ml_model(features_df, train_end)
    ml_map = predict_ml_scores(model, features_df, year)
    yr_sent = filter_sentiment_by_year(sent_map, year)
    fusion_map = compute_macro_adjusted_scores(ml_map, macro_df, yr_sent if yr_sent else None)

    ensemble_map = build_ensemble_map(ml_map, fusion_map, macro_df, float(task["ensemble_weight"]))

    cfg = dict(ENGINE_BASE)
    step = int(cfg.pop("step"))
    cfg.update(
        {
            "max_positions": int(task["max_positions"]),
            "stop_loss_pct": float(task["stop_loss_pct"]),
            "take_profit_pct": float(task["take_profit_pct"]),
            "trailing_stop_pct": float(task["trailing_stop_pct"]),
            "max_holding_steps": int(task["max_holding_steps"]),
        }
    )
    engine = ReplayEngine(
        panel=p,
        symbols=YEAR_SYMBOLS[year],
        initial_cash_krw=1_000_000,
        max_notional_krw=float(task["max_notional"]),
        transaction_cost_bps=30.0,
        prediction_map=ensemble_map,
        prediction_overlay_mode="rerank",
        prediction_alpha=10.0,
        cash_fraction_per_entry=float(task["cash_fraction_per_entry"]),
        **cfg,
    )
    r = engine.run(step=step)
    s = dict(r["summary"])
    s.update(task)
    s["daily_avg_pct_252"] = daily_avg_pct(float(s["total_return_pct"]))
    return s


# Pre-computed maps cache (computed once in main, passed via global)
_MAPS_CACHE: dict = {}


def run_one_cached(task: dict[str, Any]) -> dict[str, Any]:
    year = int(task["trade_year"])
    p = YEAR_PANELS[year]
    ew = float(task["ensemble_weight"])

    key = (year, ew)
    if key not in _MAPS_CACHE:
        features_df = _GLOBAL["features_df"]
        macro_df = _GLOBAL["macro_df"]
        sent_map = _GLOBAL["sent_map"]
        train_end = int(task["train_end"])
        model = train_ml_model(features_df, train_end)
        ml_map = predict_ml_scores(model, features_df, year)
        yr_sent = filter_sentiment_by_year(sent_map, year)
        fusion_map = compute_macro_adjusted_scores(ml_map, macro_df, yr_sent if yr_sent else None)
        _MAPS_CACHE[key] = build_ensemble_map(ml_map, fusion_map, macro_df, ew)

    ensemble_map = _MAPS_CACHE[key]
    cfg = dict(ENGINE_BASE)
    step = int(cfg.pop("step"))
    cfg.update(
        {
            "max_positions": int(task["max_positions"]),
            "stop_loss_pct": float(task["stop_loss_pct"]),
            "take_profit_pct": float(task["take_profit_pct"]),
            "trailing_stop_pct": float(task["trailing_stop_pct"]),
            "max_holding_steps": int(task["max_holding_steps"]),
        }
    )
    engine = ReplayEngine(
        panel=p,
        symbols=YEAR_SYMBOLS[year],
        initial_cash_krw=1_000_000,
        max_notional_krw=float(task["max_notional"]),
        transaction_cost_bps=30.0,
        prediction_map=ensemble_map,
        prediction_overlay_mode="rerank",
        prediction_alpha=10.0,
        cash_fraction_per_entry=float(task["cash_fraction_per_entry"]),
        **cfg,
    )
    r = engine.run(step=step)
    s = dict(r["summary"])
    s.update(task)
    s["daily_avg_pct_252"] = daily_avg_pct(float(s["total_return_pct"]))
    return s


_GLOBAL: dict = {}


def init_worker(features_df, macro_df, sent_map, year_panels, year_symbols):
    _GLOBAL["features_df"] = features_df
    _GLOBAL["macro_df"] = macro_df
    _GLOBAL["sent_map"] = sent_map
    global YEAR_PANELS, YEAR_SYMBOLS
    YEAR_PANELS = year_panels
    YEAR_SYMBOLS = year_symbols


def main() -> None:
    print("=== Regime-Based Ensemble Frontier ===", flush=True)
    panel = pd.read_parquet(PANEL_PATH)
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    panel["Date"] = pd.to_datetime(panel["Date"])
    print(f"Panel: {len(panel):,} rows, {panel['code'].nunique()} codes", flush=True)

    print("Computing features...", flush=True)
    features_df = compute_features(panel)
    macro_path = MACRO_CACHE
    if not macro_path.exists():
        alt = ROOT / "src/toss_alpha/reports/harness/macro_signals.parquet"
        macro_path = alt if alt.exists() else macro_path
    macro_df = pd.read_parquet(macro_path)
    sent_df = pd.read_csv(SENT_CSV, parse_dates=["date"])
    sent_map = build_sentiment_map(sent_df)

    yp = {}
    ys = {}
    for year in [2024, 2025, 2026]:
        yp[year] = panel[panel["Date"].dt.year == year].copy()
        ys[year] = symbols_of(yp[year])

    # Ensemble weight: 0.0=pure fusion, 0.5=blend, 0.75=ml-biased, 1.0=pure regime-switch
    ENSEMBLE_WEIGHTS = [0.0, 0.5, 0.75, 1.0]
    # Risk presets (best from sizing_frontier)
    RISK_CONFIGS = [
        {"name": "fusion_optimal", "stop_loss_pct": 0.06, "take_profit_pct": 0.25, "trailing_stop_pct": 0.06, "max_holding_steps": 20},
        {"name": "ml_optimal", "stop_loss_pct": 0.10, "take_profit_pct": 0.25, "trailing_stop_pct": 0.06, "max_holding_steps": 10},
    ]
    MAX_POSITIONS = [4, 6, 8]
    SIZING = [
        {"cash_fraction_per_entry": 0.30, "max_notional": 200_000},
        {"cash_fraction_per_entry": 0.40, "max_notional": 300_000},
        {"cash_fraction_per_entry": 0.60, "max_notional": 300_000},
        {"cash_fraction_per_entry": 0.60, "max_notional": 500_000},
    ]

    tasks: list[dict[str, Any]] = []
    for train_end, year in [(2023, 2024), (2024, 2025), (2025, 2026)]:
        for ew in ENSEMBLE_WEIGHTS:
            for rc in RISK_CONFIGS:
                for mp_ in MAX_POSITIONS:
                    for sz in SIZING:
                        t = {"ensemble_weight": ew, "train_end": train_end, "trade_year": year}
                        t.update({k: v for k, v in rc.items() if k != "name"})
                        t["risk_config"] = rc["name"]
                        t.update(sz)
                        t["max_positions"] = mp_
                        tasks.append(t)

    print(f"Total tasks: {len(tasks)}", flush=True)

    jobs = int(os.environ.get("TOSS_FRONTIER_JOBS", "14"))
    ctx = mp.get_context("fork")
    with ctx.Pool(
        processes=jobs,
        initializer=init_worker,
        initargs=(features_df, macro_df, sent_map, yp, ys),
    ) as pool:
        rows = list(pool.imap_unordered(run_one_cached, tasks, chunksize=2))

    df = pd.DataFrame(rows).sort_values([
        "trade_year", "ensemble_weight", "risk_config", "max_positions",
        "cash_fraction_per_entry", "max_notional",
    ])
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_CSV, index=False)

    group_cols = ["ensemble_weight", "risk_config", "max_positions", "cash_fraction_per_entry", "max_notional"]
    agg = df.groupby(group_cols).agg(
        mean_return=("total_return_pct", "mean"),
        min_return=("total_return_pct", "min"),
        mean_daily=("daily_avg_pct_252", "mean"),
        max_mdd=("max_drawdown_pct", "min"),
        mean_sharpe=("sharpe_ratio", "mean"),
        years=("trade_year", "count"),
    ).reset_index()
    agg["all_positive"] = agg["min_return"] > 0
    agg = agg.sort_values(["all_positive", "mean_sharpe"], ascending=[False, False])

    lines = [
        "# Regime-Based Ensemble Frontier",
        "",
        "Research/paper only. No live orders submitted.",
        "",
        "## Interpretation",
        "",
        "- ensemble_weight=1.0: pure regime switch (ml on risk_on, fusion otherwise)",
        "- ensemble_weight=0.5: 50/50 blend (ml-biased on risk_on)",
        "- ensemble_weight=0.0: pure fusion (baseline)",
        "",
        "## Robust leaderboard (3yr all-positive, sorted by Sharpe)",
        "",
    ]
    robust = agg[agg["all_positive"]]
    for _, row in robust.head(25).iterrows():
        lines.append(
            f"- ew={row['ensemble_weight']:.2f} {row['risk_config']} maxpos={int(row['max_positions'])} "
            f"cf={row['cash_fraction_per_entry']:.2f} not={int(row['max_notional']):,}: "
            f"mean_daily={row['mean_daily']:.3f}% mean_ret={row['mean_return']:.1f}% "
            f"min_ret={row['min_return']:.1f}% mdd={row['max_mdd']:.1f}% sharpe={row['mean_sharpe']:.2f}"
        )
    lines.extend(["", "## Files", "", f"- CSV: `{OUT_CSV}`"])
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n=== Top 20 robust combos ===", flush=True)
    print(robust.head(20).to_string(index=False, float_format=lambda x: f"{x:.3f}"), flush=True)
    print(f"\nRobust combos: {len(robust)} / {len(agg)}")
    print(f"Saved: {OUT_CSV}\nReport: {OUT_MD}", flush=True)


if __name__ == "__main__":
    main()
