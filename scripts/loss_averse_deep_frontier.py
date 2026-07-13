"""Deep loss-averse frontier with tail-risk penalty overlays.

Research/paper only. No broker calls and no live orders.

This expands the 2026-07-06 loss-averse frontier by using hardware-parallel
search over:
- exposure: notional, max_positions, cash_fraction
- exits: stop/take/trailing
- portfolio guard: equity drawdown stop + cooldown
- signal: fusion_rerank with optional LightGBM tail-risk penalty

Promotion stays strict: every tested year positive and MDD >= configured gate.
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

from toss_alpha.daily.features import FEATURE_COLUMNS, compute_features  # noqa: E402
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

OUT_CSV = ROOT / "reports/harness/loss_averse_deep_frontier_20260706.csv"
OUT_MD = ROOT / "reports/harness/loss_averse_deep_frontier_20260706.md"
MDD_GATE = float(os.environ.get("TOSS_DEEP_MDD_GATE", "-10.0"))
MIN_YEAR_RETURN = float(os.environ.get("TOSS_DEEP_MIN_YEAR_RETURN", "0.0"))

YEAR_PANELS: dict[int, pd.DataFrame] = {}
YEAR_SYMBOLS: dict[int, list[str]] = {}
PRED_MAPS: dict[tuple[int, str], dict[str, dict[str, float]] | None] = {}


def daily_avg_pct(total_return_pct: float) -> float:
    return total_return_pct / 252.0


def train_tail_model(features_df: pd.DataFrame, train_end_year: int, *, threshold: float):
    import lightgbm as lgb

    train = features_df[features_df["Date"].dt.year <= train_end_year].copy()
    train = train.dropna(subset=FEATURE_COLUMNS + ["label_fwd_ret_5d"])
    X = train[FEATURE_COLUMNS].values
    y = (train["label_fwd_ret_5d"].astype(float) <= threshold).astype(int).values
    model = lgb.LGBMClassifier(
        n_estimators=240,
        max_depth=5,
        learning_rate=0.04,
        num_leaves=24,
        min_child_samples=80,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=0.2,
        reg_lambda=1.2,
        random_state=20260706,
        verbose=-1,
    )
    model.fit(X, y)
    return model


def predict_tail_probs(model, features_df: pd.DataFrame, trade_year: int) -> dict[str, dict[str, float]]:
    trade = features_df[features_df["Date"].dt.year == trade_year].copy()
    trade = trade.dropna(subset=FEATURE_COLUMNS)
    proba = model.predict_proba(trade[FEATURE_COLUMNS].values)[:, 1]
    trade = trade.copy()
    trade["tail_prob"] = proba
    out: dict[str, dict[str, float]] = {}
    for date, group in trade.groupby(trade["Date"].dt.date):
        date_str = date.isoformat()
        out[date_str] = {str(row["code"]).zfill(6): float(row["tail_prob"]) for _, row in group.iterrows()}
    return out


def combine_tail_penalty(
    fused_map: dict[str, dict[str, float]],
    tail_map: dict[str, dict[str, float]],
    *,
    penalty: float,
) -> dict[str, dict[str, float]]:
    if penalty <= 0:
        return fused_map
    out: dict[str, dict[str, float]] = {}
    for date_str, scores in fused_map.items():
        tails = tail_map.get(date_str, {})
        out[date_str] = {code: float(score) - penalty * float(tails.get(code, 0.0)) for code, score in scores.items()}
    return out


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
        prediction_overlay_mode="rerank",
        prediction_alpha=10.0,
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
        "# Loss-Averse Deep Frontier — 2026-07-06",
        "",
        "Research/paper only. No broker calls. No live orders submitted.",
        "",
        "## Search axes",
        "- fusion_rerank baseline plus LightGBM tail-risk penalty variants",
        "- notional/max positions/cash fraction",
        "- stop/take/trailing exits",
        "- portfolio equity drawdown stop and cooldown",
        "",
        "## Promotion gates",
        f"- all tested years positive: `min_return > {MIN_YEAR_RETURN:.2f}%`",
        f"- drawdown gate: `max_mdd >= {MDD_GATE:.2f}%`",
        "- tested years: 2024, 2025, 2026 YTD/slice in current panel",
        "",
        "## Best promotable candidates",
        "",
    ]
    if promotable.empty:
        lines.append("- None passed. Keep current loss-averse config and investigate failure attribution.")
    else:
        for i, (_, row) in enumerate(promotable.head(25).iterrows(), start=1):
            lines.append(
                f"{i}. `{row['strategy']}` notional={int(row['max_notional']):,} maxpos={int(row['max_positions'])} "
                f"cf={row['cash_fraction_per_entry']:.2f} SL={row['stop_loss_pct']:.2%} TP={row['take_profit_pct']:.2%} "
                f"TR={row['trailing_stop_pct']:.2%} guard={row['max_equity_drawdown_stop_pct']:.2%}/"
                f"{int(row['risk_cooldown_steps'])}step — mean_ret={row['mean_return']:.2f}%, "
                f"min_ret={row['min_return']:.2f}%, max_mdd={row['max_mdd']:.2f}%, "
                f"sharpe={row['mean_sharpe']:.2f}, trades={int(row['total_trades'])}"
            )
    if not promotable.empty:
        best = promotable.iloc[0]
        mask = pd.Series(True, index=df.index)
        for col in GROUP_COLS:
            mask &= df[col] == best[col]
        lines.extend(["", "## Year-level rows for best", ""])
        for _, row in df[mask].sort_values("trade_year").iterrows():
            lines.append(
                f"- {int(row['trade_year'])}: ret={row['total_return_pct']:.2f}% "
                f"daily={row['daily_avg_pct_252']:.3f}% mdd={row['max_drawdown_pct']:.2f}% "
                f"sharpe={row['sharpe_ratio']:.2f} trades={int(row['trade_count'])} "
                f"risk_stops={int(row.get('risk_stop_count', 0))}"
            )
    lines.extend(["", "## Files", "", f"- CSV: `{OUT_CSV}`", f"- Markdown: `{OUT_MD}`"])
    return "\n".join(lines) + "\n"


GROUP_COLS = [
    "strategy", "max_notional", "max_positions", "cash_fraction_per_entry",
    "stop_loss_pct", "take_profit_pct", "trailing_stop_pct", "max_holding_steps",
    "max_equity_drawdown_stop_pct", "risk_cooldown_steps",
]


def main() -> None:
    print("=== Loss-Averse Deep Frontier ===", flush=True)
    panel = pd.read_parquet(PANEL_PATH)
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    panel["Date"] = pd.to_datetime(panel["Date"])
    print(f"Panel: {PANEL_PATH} rows={len(panel):,} codes={panel['code'].nunique()} date={panel['Date'].min().date()}..{panel['Date'].max().date()}", flush=True)

    print("Computing features...", flush=True)
    features_df = compute_features(panel)
    macro_df = pd.read_parquet(MACRO_CACHE) if MACRO_CACHE.exists() else fetch_macro_signals()
    sent_df = pd.read_csv(SENT_CSV, parse_dates=["date"])
    sent_map = build_sentiment_map(sent_df)

    for year in [2024, 2025, 2026]:
        YEAR_PANELS[year] = panel[panel["Date"].dt.year == year].copy()
        YEAR_SYMBOLS[year] = symbols_of(YEAR_PANELS[year])

    print("Training expanding edge/tail maps...", flush=True)
    tail_thresholds = [-0.03, -0.05]
    penalties = [0.0, 0.25, 0.50, 0.75]
    for train_end, year in [(2023, 2024), (2024, 2025), (2025, 2026)]:
        edge_model = train_ml_model(features_df, train_end)
        ml_map = predict_ml_scores(edge_model, features_df, year)
        yr_sent = filter_sentiment_by_year(sent_map, year)
        fused_map = compute_macro_adjusted_scores(ml_map, macro_df, yr_sent if yr_sent else None)
        PRED_MAPS[(year, "fusion_p0")]=fused_map
        for threshold in tail_thresholds:
            tail_model = train_tail_model(features_df, train_end, threshold=threshold)
            tail_map = predict_tail_probs(tail_model, features_df, year)
            for penalty in penalties:
                if penalty == 0.0:
                    continue
                label = f"fusion_tail_t{abs(int(threshold*100)):02d}_p{str(penalty).replace('.', 'p')}"
                PRED_MAPS[(year, label)] = combine_tail_penalty(fused_map, tail_map, penalty=penalty)
        print(f"  ready train≤{train_end} trade {year}", flush=True)

    strategies = sorted({key[1] for key in PRED_MAPS if key[0] == 2024})
    tasks: list[dict[str, Any]] = []
    for _, year in [(2023, 2024), (2024, 2025), (2025, 2026)]:
        for strategy in strategies:
            for max_notional in [100_000, 150_000]:
                for max_positions in [4, 6]:
                    for cash_fraction_per_entry in [0.15, 0.20]:
                        for stop_loss_pct in [0.04, 0.05, 0.06]:
                            for take_profit_pct in [0.10, 0.12, 0.15, 0.20]:
                                for trailing_stop_pct in [0.04, 0.05, 0.06]:
                                    for max_equity_drawdown_stop_pct in [0.06, 0.08]:
                                        for risk_cooldown_steps in [4, 8]:
                                            tasks.append(
                                                {
                                                    "trade_year": year,
                                                    "strategy": strategy,
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
    jobs = int(os.environ.get("TOSS_FRONTIER_JOBS", "15"))
    print(f"Running tasks={len(tasks)} jobs={jobs}...", flush=True)
    ctx = mp.get_context("fork")
    with ctx.Pool(processes=jobs) as pool:
        rows = list(pool.imap_unordered(run_one, tasks, chunksize=5))

    df = pd.DataFrame(rows)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_CSV, index=False)
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
    agg["all_positive"] = agg["min_return"] > MIN_YEAR_RETURN
    agg["mdd_pass"] = agg["max_mdd"] >= MDD_GATE
    agg["promotable"] = agg["all_positive"] & agg["mdd_pass"] & (agg["min_trades"] > 0)
    agg["loss_averse_score"] = (
        agg["mean_sharpe"] * 10
        + agg["mean_return"] * 0.05
        + agg["max_mdd"] * 0.8
        + agg["min_return"] * 0.12
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
            f"tr={best['trailing_stop_pct']} guard={best['max_equity_drawdown_stop_pct']} "
            f"mean_ret={best['mean_return']:.2f} min_ret={best['min_return']:.2f} "
            f"mdd={best['max_mdd']:.2f} sharpe={best['mean_sharpe']:.2f}"
        )


if __name__ == "__main__":
    main()
