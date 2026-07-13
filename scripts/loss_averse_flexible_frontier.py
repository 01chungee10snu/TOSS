"""Loss-averse flexible frontier for TOSS live-candidate policy.

Research/paper only. No broker calls and no live orders.

Goal: search configurations that are more active/aggressive than the current
single-position baseline while enforcing loss-averse promotion gates:
- every tested year positive
- maximum drawdown no worse than configured gate
- minimum yearly return positive
- ranking prioritizes MDD/Sharpe before raw return
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
from toss_alpha.daily.replay import ReplayEngine  # noqa: E402
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
from toss_alpha.daily.macro_signals import fetch_macro_signals  # noqa: E402

warnings.filterwarnings("ignore", category=FutureWarning)

OUT_CSV = ROOT / "reports/harness/loss_averse_flexible_frontier_20260706.csv"
OUT_MD = ROOT / "reports/harness/loss_averse_flexible_frontier_20260706.md"
MDD_GATE = float(os.environ.get("TOSS_LOSS_AVERSE_MDD_GATE", "-10.0"))
MIN_YEAR_RETURN = float(os.environ.get("TOSS_LOSS_AVERSE_MIN_YEAR_RETURN", "0.0"))

YEAR_PANELS: dict[int, pd.DataFrame] = {}
YEAR_SYMBOLS: dict[int, list[str]] = {}
PRED_MAPS: dict[tuple[int, str], dict[str, dict[str, float]] | None] = {}


def daily_avg_pct(total_return_pct: float) -> float:
    return total_return_pct / 252.0


def run_one(task: dict[str, Any]) -> dict[str, Any]:
    year = int(task["trade_year"])
    strategy = str(task["strategy"])
    p = YEAR_PANELS[year]
    cfg = dict(ENGINE_BASE)
    step = int(cfg.pop("step"))
    cfg.update(
        {
            "max_positions": int(task["max_positions"]),
            "stop_loss_pct": float(task["stop_loss_pct"]),
            "take_profit_pct": float(task["take_profit_pct"]),
            "trailing_stop_pct": float(task["trailing_stop_pct"]),
            "max_holding_steps": int(task["max_holding_steps"]),
            "cash_fraction_per_entry": float(task["cash_fraction_per_entry"]),
            "max_equity_drawdown_stop_pct": float(task["max_equity_drawdown_stop_pct"]),
            "risk_cooldown_steps": int(task["risk_cooldown_steps"]),
        }
    )
    engine = ReplayEngine(
        panel=p,
        symbols=YEAR_SYMBOLS[year],
        initial_cash_krw=1_000_000,
        max_notional_krw=float(task["max_notional"]),
        transaction_cost_bps=30.0,
        prediction_map=PRED_MAPS.get((year, strategy)),
        prediction_overlay_mode=task["overlay_mode"],
        prediction_alpha=float(task["alpha"]),
        **cfg,
    )
    r = engine.run(step=step)
    s = dict(r["summary"])
    s.update(task)
    s["daily_avg_pct_252"] = daily_avg_pct(float(s["total_return_pct"]))
    s["trade_count"] = int(s.get("total_trades", 0))
    return s


def make_report(df: pd.DataFrame, agg: pd.DataFrame, promotable: pd.DataFrame) -> str:
    lines = [
        "# Loss-Averse Flexible Frontier — 2026-07-06",
        "",
        "Research/paper only. No broker calls. No live orders submitted.",
        "",
        "## Promotion gates",
        "",
        f"- all tested years positive: `min_return > {MIN_YEAR_RETURN:.2f}%`",
        f"- drawdown gate: `max_mdd >= {MDD_GATE:.2f}%`",
        "- ranking: pass gates first, then higher Sharpe, lower drawdown, higher return",
        "- tested years: 2024, 2025, 2026 YTD/slice in current panel",
        "",
        "## Best promotable candidates",
        "",
    ]
    if promotable.empty:
        lines.append("- None passed the strict loss-averse gate. Do not promote a more aggressive setting.")
    else:
        for i, (_, row) in enumerate(promotable.head(15).iterrows(), start=1):
            lines.append(
                f"{i}. `{row['strategy']}` notional={int(row['max_notional']):,} "
                f"maxpos={int(row['max_positions'])} cf={row['cash_fraction_per_entry']:.2f} "
                f"SL={row['stop_loss_pct']:.2%} TP={row['take_profit_pct']:.2%} "
                f"TR={row['trailing_stop_pct']:.2%} hold={int(row['max_holding_steps'])} "
                f"guard={row['max_equity_drawdown_stop_pct']:.2%}/{int(row['risk_cooldown_steps'])}step — "
                f"mean_ret={row['mean_return']:.2f}%, min_ret={row['min_return']:.2f}%, "
                f"max_mdd={row['max_mdd']:.2f}%, mean_sharpe={row['mean_sharpe']:.2f}, "
                f"mean_daily={row['mean_daily']:.3f}%, total_trades={int(row['total_trades'])}"
            )
    lines.extend(["", "## Full leaderboard top 20", ""])
    for i, (_, row) in enumerate(agg.head(20).iterrows(), start=1):
        flag = "PASS" if bool(row["promotable"]) else "FAIL"
        lines.append(
            f"{i}. {flag} `{row['strategy']}` notional={int(row['max_notional']):,} "
            f"maxpos={int(row['max_positions'])} cf={row['cash_fraction_per_entry']:.2f} "
            f"SL={row['stop_loss_pct']:.2%} TP={row['take_profit_pct']:.2%} TR={row['trailing_stop_pct']:.2%} "
            f"guard={row['max_equity_drawdown_stop_pct']:.2%}/{int(row['risk_cooldown_steps'])}step: "
            f"mean_ret={row['mean_return']:.2f}%, min_ret={row['min_return']:.2f}%, "
            f"max_mdd={row['max_mdd']:.2f}%, sharpe={row['mean_sharpe']:.2f}"
        )
    lines.extend(["", "## Year-level rows for selected best", ""])
    if not promotable.empty:
        best = promotable.iloc[0]
        mask = pd.Series(True, index=df.index)
        for col in [
            "strategy", "max_notional", "max_positions", "cash_fraction_per_entry",
            "stop_loss_pct", "take_profit_pct", "trailing_stop_pct", "max_holding_steps",
            "max_equity_drawdown_stop_pct", "risk_cooldown_steps",
        ]:
            mask &= df[col] == best[col]
        for _, row in df[mask].sort_values("trade_year").iterrows():
            lines.append(
                f"- {int(row['trade_year'])}: ret={row['total_return_pct']:.2f}% "
                f"daily={row['daily_avg_pct_252']:.3f}% mdd={row['max_drawdown_pct']:.2f}% "
                f"sharpe={row['sharpe_ratio']:.2f} trades={int(row['trade_count'])}"
            )
    lines.extend(["", "## Files", "", f"- CSV: `{OUT_CSV}`", f"- Markdown: `{OUT_MD}`"])
    return "\n".join(lines) + "\n"


def main() -> None:
    print("=== Loss-Averse Flexible Frontier ===", flush=True)
    panel = pd.read_parquet(PANEL_PATH)
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    panel["Date"] = pd.to_datetime(panel["Date"])
    print(f"Panel: {PANEL_PATH} rows={len(panel):,} codes={panel['code'].nunique()} date={panel['Date'].min().date()}..{panel['Date'].max().date()}", flush=True)

    print("Computing features...", flush=True)
    features_df = compute_features(panel)
    if MACRO_CACHE.exists():
        macro_df = pd.read_parquet(MACRO_CACHE)
    else:
        macro_df = fetch_macro_signals()
    sent_df = pd.read_csv(SENT_CSV, parse_dates=["date"])
    sent_map = build_sentiment_map(sent_df)

    for year in [2024, 2025, 2026]:
        YEAR_PANELS[year] = panel[panel["Date"].dt.year == year].copy()
        YEAR_SYMBOLS[year] = symbols_of(YEAR_PANELS[year])

    print("Training expanding ML/fusion maps...", flush=True)
    for train_end, year in [(2023, 2024), (2024, 2025), (2025, 2026)]:
        model = train_ml_model(features_df, train_end)
        ml_map = predict_ml_scores(model, features_df, year)
        yr_sent = filter_sentiment_by_year(sent_map, year)
        fused_map = compute_macro_adjusted_scores(ml_map, macro_df, yr_sent if yr_sent else None)
        PRED_MAPS[(year, "fusion_rerank")] = fused_map
        PRED_MAPS[(year, "fusion_hybrid_a0p5")] = fused_map
        print(f"  ready train≤{train_end} trade {year}", flush=True)

    strategies = [
        # Keep the bounded rerun focused on the already validated strongest family.
        ("fusion_rerank", "rerank", 10.0),
    ]
    tasks: list[dict[str, Any]] = []
    for _, year in [(2023, 2024), (2024, 2025), (2025, 2026)]:
        for strategy, mode, alpha in strategies:
            for max_notional in [100_000]:
                for max_positions in [4, 6]:
                    for cash_fraction_per_entry in [0.15, 0.20]:
                        for stop_loss_pct in [0.04, 0.05]:
                            for take_profit_pct in [0.10, 0.12, 0.15, 0.20]:
                                for trailing_stop_pct in [0.04, 0.05]:
                                    for max_equity_drawdown_stop_pct in [0.08]:
                                        for risk_cooldown_steps in [4, 8]:
                                            tasks.append(
                                                {
                                                    "trade_year": year,
                                                    "strategy": strategy,
                                                    "overlay_mode": mode,
                                                    "alpha": alpha,
                                                    "max_notional": max_notional,
                                                    "max_positions": max_positions,
                                                    "cash_fraction_per_entry": cash_fraction_per_entry,
                                                    "stop_loss_pct": stop_loss_pct,
                                                    "take_profit_pct": take_profit_pct,
                                                    "trailing_stop_pct": trailing_stop_pct,
                                                    "max_holding_steps": 10,
                                                    "max_equity_drawdown_stop_pct": max_equity_drawdown_stop_pct,
                                                    "risk_cooldown_steps": risk_cooldown_steps,
                                                }
                                            )
    jobs = int(os.environ.get("TOSS_FRONTIER_JOBS", "14"))
    print(f"Running tasks={len(tasks)} jobs={jobs}...", flush=True)
    ctx = mp.get_context("fork")
    with ctx.Pool(processes=jobs) as pool:
        rows = list(pool.imap_unordered(run_one, tasks, chunksize=4))

    df = pd.DataFrame(rows)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_CSV, index=False)

    group_cols = [
        "strategy", "max_notional", "max_positions", "cash_fraction_per_entry",
        "stop_loss_pct", "take_profit_pct", "trailing_stop_pct", "max_holding_steps",
        "max_equity_drawdown_stop_pct", "risk_cooldown_steps",
    ]
    agg = df.groupby(group_cols, as_index=False).agg(
        mean_return=("total_return_pct", "mean"),
        min_return=("total_return_pct", "min"),
        mean_daily=("daily_avg_pct_252", "mean"),
        max_mdd=("max_drawdown_pct", "min"),
        mean_sharpe=("sharpe_ratio", "mean"),
        min_sharpe=("sharpe_ratio", "min"),
        total_trades=("trade_count", "sum"),
        min_trades=("trade_count", "min"),
    )
    agg["all_positive"] = agg["min_return"] > MIN_YEAR_RETURN
    agg["mdd_pass"] = agg["max_mdd"] >= MDD_GATE
    agg["promotable"] = agg["all_positive"] & agg["mdd_pass"] & (agg["min_trades"] > 0)
    agg["loss_averse_score"] = (
        agg["mean_sharpe"] * 10
        + agg["mean_return"] * 0.05
        + agg["max_mdd"] * 0.8
        + agg["min_return"] * 0.1
    )
    agg = agg.sort_values(
        ["promotable", "loss_averse_score", "mean_sharpe", "max_mdd", "mean_return"],
        ascending=[False, False, False, False, False],
    )
    promotable = agg[agg["promotable"]].copy()
    OUT_MD.write_text(make_report(df, agg, promotable), encoding="utf-8")
    print(f"WROTE_CSV={OUT_CSV}")
    print(f"WROTE_MD={OUT_MD}")
    print(f"PROMOTABLE_COUNT={len(promotable)}")
    if not promotable.empty:
        best = promotable.iloc[0]
        print(
            "BEST="
            f"{best['strategy']} notional={int(best['max_notional'])} maxpos={int(best['max_positions'])} "
            f"cf={best['cash_fraction_per_entry']} sl={best['stop_loss_pct']} tp={best['take_profit_pct']} "
            f"tr={best['trailing_stop_pct']} mean_ret={best['mean_return']:.2f} min_ret={best['min_return']:.2f} "
            f"mdd={best['max_mdd']:.2f} sharpe={best['mean_sharpe']:.2f}"
        )


if __name__ == "__main__":
    main()
