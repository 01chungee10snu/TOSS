"""Time-budgeted ML + trade-condition frontier search for TOSS.

Research/backtest only. No broker calls and no live orders.

This runner keeps the current live-promoted loss-averse config as the baseline,
then searches whether ML tail-risk overlays plus trade-condition retuning can
improve it under strict promotion gates:
- every tested year positive;
- max drawdown above configured gate;
- nonzero trades in every tested year;
- better loss-averse score than the baseline.

It writes partial CSV/MD reports after each batch, so a 5-hour run can be
observed or interrupted safely.
"""
from __future__ import annotations

import itertools
import json
import multiprocessing as mp
import os
import random
import sys
import time
import warnings
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import loss_averse_deep_frontier as base  # noqa: E402
from toss_alpha.daily.features import compute_features  # noqa: E402
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
from toss_alpha.daily.macro_signals import fetch_macro_signals  # noqa: E402

warnings.filterwarnings("ignore", category=FutureWarning)

STAMP = time.strftime("%Y%m%dT%H%M%S")
OUT_DIR = ROOT / "reports/harness"
OUT_CSV = OUT_DIR / f"ml_trade_condition_5h_search_{STAMP}.csv"
OUT_AGG = OUT_DIR / f"ml_trade_condition_5h_search_{STAMP}_agg.csv"
OUT_MD = OUT_DIR / f"ml_trade_condition_5h_search_{STAMP}.md"
STATE_JSON = OUT_DIR / "ml_trade_condition_5h_search_latest.json"

YEARS = [2024, 2025, 2026]
MDD_GATE = float(os.environ.get("TOSS_ML_SEARCH_MDD_GATE", "-10.0"))
MIN_YEAR_RETURN = float(os.environ.get("TOSS_ML_SEARCH_MIN_YEAR_RETURN", "0.0"))
TIME_BUDGET_SEC = int(float(os.environ.get("TOSS_ML_SEARCH_HOURS", "5")) * 3600)
JOBS = int(os.environ.get("TOSS_ML_SEARCH_JOBS", os.environ.get("TOSS_FRONTIER_JOBS", "15")))
BATCH_CONFIGS = int(os.environ.get("TOSS_ML_SEARCH_BATCH_CONFIGS", "96"))
RANDOM_SEED = int(os.environ.get("TOSS_ML_SEARCH_SEED", "20260707"))

CURRENT_BASELINE = {
    "strategy": "fusion_p0",
    "max_notional": 150_000,
    "max_positions": 4,
    "cash_fraction_per_entry": 0.20,
    "stop_loss_pct": 0.05,
    "take_profit_pct": 0.10,
    "trailing_stop_pct": 0.05,
    "max_holding_steps": 10,
    "max_equity_drawdown_stop_pct": 0.06,
    "risk_cooldown_steps": 8,
}
GROUP_COLS = list(base.GROUP_COLS)


def score_agg(agg: pd.DataFrame) -> pd.DataFrame:
    if agg.empty:
        return agg
    agg = agg.copy()
    agg["all_positive"] = agg["min_return"] > MIN_YEAR_RETURN
    agg["mdd_pass"] = agg["max_mdd"] >= MDD_GATE
    agg["promotable"] = agg["all_positive"] & agg["mdd_pass"] & (agg["min_trades"] > 0)
    # Loss-averse objective: prioritize Sharpe and drawdown while still rewarding
    # return/min-year return. Slightly penalize trade sparsity.
    agg["loss_averse_score"] = (
        agg["mean_sharpe"] * 10
        + agg["mean_return"] * 0.05
        + agg["max_mdd"] * 0.9
        + agg["min_return"] * 0.15
        + agg["min_trades"].clip(upper=20) * 0.03
    )
    return agg.sort_values(
        ["promotable", "loss_averse_score", "mean_sharpe", "max_mdd", "mean_return", "min_return"],
        ascending=[False, False, False, False, False, False],
    )


def aggregate(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    agg = df.groupby(GROUP_COLS, as_index=False).agg(
        mean_return=("total_return_pct", "mean"),
        min_return=("total_return_pct", "min"),
        mean_daily=("daily_avg_pct_252", "mean"),
        max_mdd=("max_drawdown_pct", "min"),
        mean_sharpe=("sharpe_ratio", "mean"),
        min_sharpe=("sharpe_ratio", "min"),
        total_trades=("trade_count", "sum"),
        min_trades=("trade_count", "min"),
    )
    return score_agg(agg)


def config_key(cfg: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(cfg[c] for c in GROUP_COLS)


def make_tasks(configs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for year in YEARS:
        for cfg in configs:
            t = dict(cfg)
            t["trade_year"] = year
            tasks.append(t)
    return tasks


def build_prediction_maps() -> list[str]:
    print("Loading panel/features for prediction maps...", flush=True)
    panel = pd.read_parquet(PANEL_PATH)
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    panel["Date"] = pd.to_datetime(panel["Date"])
    print(
        f"Panel: {PANEL_PATH} rows={len(panel):,} codes={panel['code'].nunique()} "
        f"date={panel['Date'].min().date()}..{panel['Date'].max().date()}",
        flush=True,
    )
    features_df = compute_features(panel)
    macro_df = pd.read_parquet(MACRO_CACHE) if MACRO_CACHE.exists() else fetch_macro_signals()
    sent_df = pd.read_csv(SENT_CSV, parse_dates=["date"])
    sent_map = build_sentiment_map(sent_df)

    base.YEAR_PANELS.clear()
    base.YEAR_SYMBOLS.clear()
    base.PRED_MAPS.clear()

    for year in YEARS:
        base.YEAR_PANELS[year] = panel[panel["Date"].dt.year == year].copy()
        base.YEAR_SYMBOLS[year] = base.symbols_of(base.YEAR_PANELS[year])

    # Wider ML overlay family than the 2026-07-06 deep run, but still bounded.
    tail_thresholds = [-0.02, -0.03, -0.04, -0.05, -0.07]
    penalties = [0.10, 0.25, 0.40, 0.60, 0.80, 1.00]
    for train_end, year in [(2023, 2024), (2024, 2025), (2025, 2026)]:
        print(f"Training edge/tail maps train≤{train_end} trade={year}...", flush=True)
        edge_model = train_ml_model(features_df, train_end)
        ml_map = predict_ml_scores(edge_model, features_df, year)
        yr_sent = filter_sentiment_by_year(sent_map, year)
        fused_map = compute_macro_adjusted_scores(ml_map, macro_df, yr_sent if yr_sent else None)
        base.PRED_MAPS[(year, "fusion_p0")] = fused_map
        for threshold in tail_thresholds:
            tail_model = base.train_tail_model(features_df, train_end, threshold=threshold)
            tail_map = base.predict_tail_probs(tail_model, features_df, year)
            for penalty in penalties:
                label = f"fusion_tail_t{abs(int(threshold*100)):02d}_p{str(penalty).replace('.', 'p')}"
                base.PRED_MAPS[(year, label)] = base.combine_tail_penalty(fused_map, tail_map, penalty=penalty)
        print(f"  ready train≤{train_end} trade={year}", flush=True)
    strategies = sorted({key[1] for key in base.PRED_MAPS if key[0] == YEARS[0]})
    print(f"Strategies={len(strategies)}: {strategies[:8]}{'...' if len(strategies) > 8 else ''}", flush=True)
    return strategies


def build_config_pool(strategies: list[str]) -> list[dict[str, Any]]:
    # Include baseline first, then broad but loss-aware axes. These are research
    # configs only; live defaults are not changed by this script.
    pool: list[dict[str, Any]] = [dict(CURRENT_BASELINE)]
    axes = {
        "strategy": strategies,
        "max_notional": [100_000, 150_000, 200_000, 250_000],
        "max_positions": [3, 4, 5, 6],
        "cash_fraction_per_entry": [0.15, 0.20, 0.25, 0.30],
        "stop_loss_pct": [0.04, 0.05, 0.06, 0.08, 0.10],
        "take_profit_pct": [0.08, 0.10, 0.12, 0.15, 0.18, 0.20, 0.25],
        "trailing_stop_pct": [0.03, 0.04, 0.05, 0.06, 0.08, 0.10],
        "max_holding_steps": [5, 8, 10, 15, 20],
        "max_equity_drawdown_stop_pct": [0.04, 0.05, 0.06, 0.08, 0.10],
        "risk_cooldown_steps": [4, 8, 12, 16],
    }
    # Full Cartesian product is huge. Shuffle deterministically and run until the
    # time budget expires.
    combos = itertools.product(*(axes[k] for k in GROUP_COLS))
    for values in combos:
        cfg = dict(zip(GROUP_COLS, values))
        # Keep the loss-averse search sane: larger notional requires stricter
        # guard or smaller breadth; otherwise it is likely non-promotable noise.
        if cfg["max_notional"] >= 250_000 and cfg["max_equity_drawdown_stop_pct"] > 0.06:
            continue
        if cfg["max_positions"] >= 6 and cfg["cash_fraction_per_entry"] >= 0.30:
            continue
        pool.append(cfg)
    # Remove duplicates, preserve baseline at front, shuffle remainder.
    seen: set[tuple[Any, ...]] = set()
    unique: list[dict[str, Any]] = []
    for cfg in pool:
        key = config_key(cfg)
        if key not in seen:
            seen.add(key)
            unique.append(cfg)
    baseline = unique[0]
    rest = unique[1:]
    random.Random(RANDOM_SEED).shuffle(rest)
    return [baseline] + rest


def write_reports(rows: list[dict[str, Any]], *, started_at: float, completed_configs: int, total_configs: int, final: bool = False) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    if not df.empty:
        df.to_csv(OUT_CSV, index=False)
    agg = aggregate(rows)
    if not agg.empty:
        agg.to_csv(OUT_AGG, index=False)

    baseline_row = None
    if not agg.empty:
        mask = pd.Series(True, index=agg.index)
        for col, val in CURRENT_BASELINE.items():
            mask &= agg[col] == val
        if mask.any():
            baseline_row = agg[mask].iloc[0]
    promotable = agg[agg["promotable"]].copy() if not agg.empty else pd.DataFrame()
    better = pd.DataFrame()
    if baseline_row is not None and not promotable.empty:
        better = promotable[promotable["loss_averse_score"] > float(baseline_row["loss_averse_score"])]

    lines = [
        "# ML Trade-Condition 5h Search",
        "",
        "Research/backtest only. No broker calls. No live orders submitted.",
        "",
        f"- Started: `{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(started_at))}`",
        f"- Elapsed: `{(time.time() - started_at) / 3600:.2f}h` / budget `{TIME_BUDGET_SEC / 3600:.2f}h`",
        f"- Completed configs: `{completed_configs}` / candidate pool `{total_configs}`",
        f"- Completed year-runs: `{len(rows)}`",
        f"- Status: `{'FINAL' if final else 'PARTIAL'}`",
        "",
        "## Baseline",
        "",
    ]
    if baseline_row is None:
        lines.append("- Baseline not completed yet.")
    else:
        lines.append(
            f"- `fusion_p0` current live config: mean_ret={baseline_row['mean_return']:.2f}%, "
            f"min_ret={baseline_row['min_return']:.2f}%, mdd={baseline_row['max_mdd']:.2f}%, "
            f"sharpe={baseline_row['mean_sharpe']:.2f}, score={baseline_row['loss_averse_score']:.2f}"
        )
    lines.extend(["", "## Improvement verdict so far", ""])
    if baseline_row is None:
        lines.append("- `PENDING`: baseline year-runs must finish before improvement can be judged.")
    elif better.empty:
        lines.append("- `NO_IMPROVEMENT_YET`: no strict-pass candidate has beaten the baseline score so far.")
    else:
        top = better.iloc[0]
        lines.append(
            f"- `IMPROVEMENT_FOUND`: best candidate score {top['loss_averse_score']:.2f} > baseline {baseline_row['loss_averse_score']:.2f}."
        )
    lines.extend(["", "## Top strict-pass candidates", ""])
    if promotable.empty:
        lines.append("- None yet.")
    else:
        for i, (_, row) in enumerate(promotable.head(20).iterrows(), 1):
            delta = ""
            if baseline_row is not None:
                delta = f" Δscore={row['loss_averse_score'] - baseline_row['loss_averse_score']:+.2f}"
            lines.append(
                f"{i}. `{row['strategy']}` notional={int(row['max_notional']):,} maxpos={int(row['max_positions'])} "
                f"cf={row['cash_fraction_per_entry']:.2f} SL={row['stop_loss_pct']:.0%} TP={row['take_profit_pct']:.0%} "
                f"TR={row['trailing_stop_pct']:.0%} hold={int(row['max_holding_steps'])} "
                f"guard={row['max_equity_drawdown_stop_pct']:.0%}/{int(row['risk_cooldown_steps'])}step — "
                f"mean={row['mean_return']:.2f}% min={row['min_return']:.2f}% mdd={row['max_mdd']:.2f}% "
                f"sharpe={row['mean_sharpe']:.2f} trades={int(row['total_trades'])} score={row['loss_averse_score']:.2f}{delta}"
            )
    lines.extend([
        "",
        "## Files",
        f"- rows: `{OUT_CSV}`",
        f"- aggregate: `{OUT_AGG}`",
        f"- report: `{OUT_MD}`",
        f"- latest state: `{STATE_JSON}`",
    ])
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    state = {
        "status": "FINAL" if final else "PARTIAL",
        "started_at_epoch": started_at,
        "elapsed_seconds": time.time() - started_at,
        "completed_configs": completed_configs,
        "total_candidate_pool": total_configs,
        "row_count": len(rows),
        "csv": str(OUT_CSV),
        "agg_csv": str(OUT_AGG),
        "md": str(OUT_MD),
        "baseline": None if baseline_row is None else baseline_row.to_dict(),
        "top": [] if promotable.empty else promotable.head(10).to_dict(orient="records"),
    }
    STATE_JSON.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def main() -> None:
    started_at = time.time()
    deadline = started_at + TIME_BUDGET_SEC
    print("=== ML Trade-Condition Time-Budgeted Search ===", flush=True)
    print(f"budget={TIME_BUDGET_SEC/3600:.2f}h jobs={JOBS} batch_configs={BATCH_CONFIGS}", flush=True)
    strategies = build_prediction_maps()
    config_pool = build_config_pool(strategies)
    print(f"Candidate configs={len(config_pool):,}", flush=True)

    rows: list[dict[str, Any]] = []
    completed_configs = 0
    ctx = mp.get_context("fork")
    with ctx.Pool(processes=JOBS) as pool:
        while completed_configs < len(config_pool):
            if time.time() >= deadline:
                print("TIME_BUDGET_REACHED before next batch", flush=True)
                break
            batch = config_pool[completed_configs: completed_configs + BATCH_CONFIGS]
            if not batch:
                break
            tasks = make_tasks(batch)
            print(
                f"Running batch configs={completed_configs+1}-{completed_configs+len(batch)} "
                f"year_tasks={len(tasks)} elapsed={(time.time()-started_at)/3600:.2f}h",
                flush=True,
            )
            batch_rows = list(pool.imap_unordered(base.run_one, tasks, chunksize=3))
            rows.extend(batch_rows)
            completed_configs += len(batch)
            write_reports(rows, started_at=started_at, completed_configs=completed_configs, total_configs=len(config_pool), final=False)
            agg = aggregate(rows)
            if not agg.empty:
                best = agg.iloc[0]
                print(
                    "PARTIAL_BEST="
                    f"{best['strategy']} notional={int(best['max_notional'])} maxpos={int(best['max_positions'])} "
                    f"cf={best['cash_fraction_per_entry']} sl={best['stop_loss_pct']} tp={best['take_profit_pct']} "
                    f"tr={best['trailing_stop_pct']} hold={int(best['max_holding_steps'])} guard={best['max_equity_drawdown_stop_pct']} "
                    f"mean={best['mean_return']:.2f} min={best['min_return']:.2f} mdd={best['max_mdd']:.2f} "
                    f"sharpe={best['mean_sharpe']:.2f} promotable={bool(best['promotable'])}",
                    flush=True,
                )
            # Avoid starting a batch that is likely to overrun very badly after deadline.
            if time.time() + 120 > deadline:
                print("TIME_BUDGET_NEAR_DEADLINE", flush=True)
                break
    write_reports(rows, started_at=started_at, completed_configs=completed_configs, total_configs=len(config_pool), final=True)
    print(f"WROTE_CSV={OUT_CSV}")
    print(f"WROTE_AGG={OUT_AGG}")
    print(f"WROTE_MD={OUT_MD}")
    agg = aggregate(rows)
    promotable = agg[agg["promotable"]].copy() if not agg.empty else pd.DataFrame()
    print(f"COMPLETED_CONFIGS={completed_configs}")
    print(f"PROMOTABLE_COUNT={len(promotable)}")
    if not promotable.empty:
        best = promotable.iloc[0]
        print(
            "BEST="
            f"{best['strategy']} notional={int(best['max_notional'])} maxpos={int(best['max_positions'])} "
            f"cf={best['cash_fraction_per_entry']} sl={best['stop_loss_pct']} tp={best['take_profit_pct']} "
            f"tr={best['trailing_stop_pct']} hold={int(best['max_holding_steps'])} guard={best['max_equity_drawdown_stop_pct']} "
            f"mean_ret={best['mean_return']:.2f} min_ret={best['min_return']:.2f} "
            f"mdd={best['max_mdd']:.2f} sharpe={best['mean_sharpe']:.2f}"
        )


if __name__ == "__main__":
    main()
