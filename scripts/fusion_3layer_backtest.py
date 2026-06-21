"""3-Layer Fusion Strategy: Macro Gate → ML Ranker → Sentiment Filter.

Combines three signal types into a single trading decision:
  1. MACRO GATE: Risk-on/off from global indicators (VIX, USD/KRW, SOX)
  2. ML RANKER: LightGBM prediction of 5-day forward returns
  3. SENTIMENT FILTER: KLUE-RoBERTa news sentiment overlay

When macro = risk_off, position sizing is halved and new entries are restricted.
When macro = risk_on, ML predictions get full weight.
Sentiment always acts as the final tiebreaker.

Research/paper only. No live orders.
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from toss_alpha.daily.features import compute_features, FEATURE_COLUMNS
from toss_alpha.daily.macro_signals import fetch_macro_signals, get_macro_regime

warnings.filterwarnings("ignore", category=FutureWarning)

PANEL_PATH = ROOT / "reports/backtests/practical_universe_panel.parquet"
SENT_CSV = ROOT / "reports/harness/news_sentiment_panel_20260621.csv"
MACRO_CACHE = ROOT / "reports/harness/macro_signals.parquet"
OUT_DIR = ROOT / "reports/harness"


def build_sentiment_map(sent_df: pd.DataFrame) -> dict:
    sent_df = sent_df.copy()
    sent_df["code"] = sent_df["code"].astype(str).str.zfill(6)
    sent_df["date"] = pd.to_datetime(sent_df["date"])
    sent_map: dict = {}
    for date, group in sent_df.groupby(sent_df["date"].dt.date):
        daily: dict[str, float] = {}
        counts: dict[str, int] = {}
        for _, row in group.iterrows():
            code = str(row["code"]).zfill(6)
            score = float(row.get("sentiment_score", 0))
            daily[code] = daily.get(code, 0.0) + score
            counts[code] = counts.get(code, 0) + 1
        for code in daily:
            daily[code] /= counts.get(code, 1)
        sent_map[date] = daily
    return sent_map


def filter_sentiment_by_year(sent_map: dict, year: int) -> dict:
    return {d: v for d, v in sent_map.items() if d.year == year}


def train_ml_model(features_df: pd.DataFrame, train_end_year: int) -> object:
    """Train LightGBM on data up to train_end_year."""
    import lightgbm as lgb

    train = features_df[features_df["Date"].dt.year <= train_end_year].copy()
    train = train.dropna(subset=FEATURE_COLUMNS + ["label_fwd_ret_5d"])

    X = train[FEATURE_COLUMNS].values
    y = train["label_fwd_ret_5d"].values

    model = lgb.LGBMRegressor(
        n_estimators=300,
        max_depth=6,
        learning_rate=0.05,
        num_leaves=31,
        min_child_samples=50,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=0.1,
        reg_lambda=1.0,
        random_state=42,
        verbose=-1,
    )
    model.fit(X, y)
    return model


def predict_ml_scores(model, features_df: pd.DataFrame, trade_year: int) -> dict:
    """Predict ML scores for trade_year. Returns {date_str: {code: score}}."""
    trade = features_df[features_df["Date"].dt.year == trade_year].copy()
    trade = trade.dropna(subset=FEATURE_COLUMNS)
    X = trade[FEATURE_COLUMNS].values
    preds = model.predict(X)
    trade = trade.copy()
    trade["ml_pred"] = preds

    pred_map: dict[str, dict[str, float]] = {}
    for date, group in trade.groupby(trade["Date"].dt.date):
        date_str = date.isoformat()
        pred_map[date_str] = {}
        for _, row in group.iterrows():
            pred_map[date_str][str(row["code"]).zfill(6)] = float(row["ml_pred"])
    return pred_map


def compute_macro_adjusted_scores(
    prediction_map: dict,
    macro_df: pd.DataFrame,
    sentiment_map: dict | None = None,
    *,
    ml_weight: float = 0.5,
    sent_weight: float = 0.5,
) -> dict:
    """Fuse ML scores + sentiment, with macro gate adjusting confidence.

    In risk_off: scale all scores down by 0.5 (defensive).
    In risk_on: full weight.
    """
    fused_map: dict[str, dict[str, float]] = {}

    for date_str, ml_scores in prediction_map.items():
        date = pd.Timestamp(date_str)
        regime = get_macro_regime(date, macro_df)
        macro_mult = {"risk_on": 1.0, "neutral": 0.75, "risk_off": 0.5}.get(regime["status"], 0.75)

        # Get sentiment for this date
        sent_scores = {}
        if sentiment_map:
            # Find closest sentiment date
            sent_date = date.date()
            if sent_date in sentiment_map:
                sent_scores = sentiment_map[sent_date]

        # Normalize ML scores to [-1, 1] range
        if ml_scores:
            vals = list(ml_scores.values())
            ml_min, ml_max = min(vals), max(vals)
            ml_range = max(ml_max - ml_min, 1e-10)
        else:
            continue

        # Normalize sentiment to [-1, 1]
        if sent_scores:
            svals = list(sent_scores.values())
            s_min, s_max = min(svals), max(svals)
            s_range = max(s_max - s_min, 1e-10)
        else:
            sent_scores = {}

        fused_map[date_str] = {}
        all_codes = set(ml_scores.keys()) | set(sent_scores.keys())
        for code in all_codes:
            ml_norm = (ml_scores.get(code, ml_min) - ml_min) / ml_range  # 0..1
            ml_norm = ml_norm * 2 - 1  # -1..1

            if code in sent_scores:
                s_norm = (sent_scores[code] - s_min) / s_range * 2 - 1  # -1..1
            else:
                s_norm = 0.0

            fused = macro_mult * (ml_weight * ml_norm + sent_weight * s_norm)
            fused_map[date_str][code] = fused

    return fused_map


def main() -> None:
    print("=== 3-Layer Fusion Walk-Forward Backtest ===")

    # Load data
    panel = pd.read_parquet(PANEL_PATH)
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    panel["Date"] = pd.to_datetime(panel["Date"])
    print(f"Panel: {len(panel):,} rows, {panel['code'].nunique()} codes")

    print("Computing features...")
    features_df = compute_features(panel)
    print(f"  {len(FEATURE_COLUMNS)} features")

    print("Loading macro signals...")
    if MACRO_CACHE.exists():
        macro_df = pd.read_parquet(MACRO_CACHE)
    else:
        macro_df = fetch_macro_signals()

    print("Loading sentiment...")
    sent_df = pd.read_csv(SENT_CSV, parse_dates=["date"])
    sent_map = build_sentiment_map(sent_df)
    print(f"  {len(sent_map)} sentiment dates")

    # Walk-forward: import engine
    from backtest_sentiment_overlay import run_engine

    walk_forward = [(2023, 2024), (2024, 2025), (2025, 2026)]
    rows = []

    for train_end, trade_year in walk_forward:
        print(f"\n--- Train ≤{train_end}, Trade {trade_year} ---")

        # Base
        r = run_engine(panel, year=trade_year)
        s = dict(r["summary"])
        s.update({"strategy": "base", "train_end": train_end, "trade_year": trade_year})
        rows.append(s)
        print(f"  base:              ret={s['total_return_pct']:.2f}% sharpe={s['sharpe_ratio']:.2f} mdd={s['max_drawdown_pct']:.2f}%")

        # Train ML
        print(f"  training ML...", end=" ", flush=True)
        model = train_ml_model(features_df, train_end)
        ml_map = predict_ml_scores(model, features_df, trade_year)
        print(f"done ({len(ml_map)} dates)")

        # ML only
        r = run_engine(panel, year=trade_year, sentiment_map=ml_map, overlay_mode="rerank")
        s = dict(r["summary"])
        s.update({"strategy": "ml_only", "train_end": train_end, "trade_year": trade_year})
        rows.append(s)
        print(f"  ml_only:           ret={s['total_return_pct']:.2f}% sharpe={s['sharpe_ratio']:.2f} mdd={s['max_drawdown_pct']:.2f}%")

        # 3-layer fusion: ML + Sentiment + Macro Gate
        yr_sent = filter_sentiment_by_year(sent_map, trade_year)
        fused_map = compute_macro_adjusted_scores(ml_map, macro_df, yr_sent if yr_sent else None)

        r = run_engine(panel, year=trade_year, sentiment_map=fused_map, overlay_mode="rerank")
        s = dict(r["summary"])
        s.update({"strategy": "fusion_3layer", "train_end": train_end, "trade_year": trade_year})
        rows.append(s)
        print(f"  fusion_3layer:     ret={s['total_return_pct']:.2f}% sharpe={s['sharpe_ratio']:.2f} mdd={s['max_drawdown_pct']:.2f}%")

        # Fusion hybrid
        r = run_engine(panel, year=trade_year, sentiment_map=fused_map, overlay_mode="hybrid", alpha=0.5)
        s = dict(r["summary"])
        s.update({"strategy": "fusion_hybrid", "train_end": train_end, "trade_year": trade_year})
        rows.append(s)
        print(f"  fusion_hybrid:     ret={s['total_return_pct']:.2f}% sharpe={s['sharpe_ratio']:.2f} mdd={s['max_drawdown_pct']:.2f}%")

    df = pd.DataFrame(rows)
    out_csv = OUT_DIR / "fusion_3layer_walkforward_20260621.csv"
    df.to_csv(out_csv, index=False)

    # Summary
    print("\n=== RETURN (%) ===")
    pivot = df.pivot_table(index="strategy", columns="trade_year", values="total_return_pct", aggfunc="first")
    print(pivot.to_string(float_format="%.2f"))

    print("\n=== SHARPE ===")
    pivot_s = df.pivot_table(index="strategy", columns="trade_year", values="sharpe_ratio", aggfunc="first")
    print(pivot_s.to_string(float_format="%.2f"))

    print("\n=== MDD (%) ===")
    pivot_m = df.pivot_table(index="strategy", columns="trade_year", values="max_drawdown_pct", aggfunc="first")
    print(pivot_m.to_string(float_format="*.2f"))

    # Check: does any strategy achieve ~0.1% daily avg?
    print("\n=== DAILY AVG RETURN (target: 0.10%/day) ===")
    for strat in df["strategy"].unique():
        sub = df[df["strategy"] == strat]
        for _, row in sub.iterrows():
            trades = row["total_trades"]
            total_ret = row["total_return_pct"]
            # Approximate trading days in year (~120 for step=5)
            approx_days = 120
            daily_avg = total_ret / approx_days / 100
            target = "✓" if daily_avg >= 0.001 else "✗"
            print(f"  {strat:20s} {int(row['trade_year'])}: {daily_avg*100:.3f}%/day {target}")

    print(f"\nSaved: {out_csv}")


if __name__ == "__main__":
    main()
