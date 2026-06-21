"""Walk-forward ML ranker backtest.

Trains a LightGBM model on historical data to predict 5-day forward returns,
then uses model predictions as a ranking signal for the ReplayEngine.

Walk-forward protocol:
  - Train on 2022-2023, trade 2024
  - Train on 2022-2024, trade 2025
  - Train on 2022-2025, trade 2026

This prevents look-ahead bias and tests generalization.

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
from backtest_sentiment_overlay import run_engine

warnings.filterwarnings("ignore", category=FutureWarning)

PANEL_PATH = ROOT / "reports/backtests/practical_universe_panel.parquet"
OUT_DIR = ROOT / "reports/harness"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def load_panel() -> pd.DataFrame:
    panel = pd.read_parquet(PANEL_PATH)
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    panel["Date"] = pd.to_datetime(panel["Date"])
    return panel.sort_values(["code", "Date"]).reset_index(drop=True)


def train_predict_walkforward(
    features_df: pd.DataFrame,
    train_end_year: int,
    trade_year: int,
) -> dict[str, dict[str, float]]:
    """Train on data up to train_end_year, predict for trade_year.

    Returns: prediction_map = {date_str: {code: predicted_score}}
    """
    try:
        import lightgbm as lgb
    except ImportError:
        print("  LightGBM not available, trying sklearn GradientBoosting...")
        from sklearn.ensemble import GradientBoostingRegressor as Model
        use_lgb = False
    else:
        use_lgb = True

    train = features_df[features_df["Date"].dt.year <= train_end_year].copy()
    trade = features_df[features_df["Date"].dt.year == trade_year].copy()

    # Drop rows with NaN features or labels
    train = train.dropna(subset=FEATURE_COLUMNS + ["label_fwd_ret_5d"])
    trade_features = trade.dropna(subset=FEATURE_COLUMNS)

    if train.empty or trade_features.empty:
        print(f"  Insufficient data: train={len(train)}, trade_features={len(trade_features)}")
        return {}

    X_train = train[FEATURE_COLUMNS].values
    y_train = train["label_fwd_ret_5d"].values

    print(f"  Training: {len(X_train)} samples, {len(FEATURE_COLUMNS)} features")
    print(f"  Trading: {len(trade_features)} samples in {trade_year}")

    if use_lgb:
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
    else:
        model = Model(n_estimators=200, max_depth=5, learning_rate=0.05, random_state=42)

    model.fit(X_train, y_train)

    # Predict
    X_trade = trade_features[FEATURE_COLUMNS].values
    preds = model.predict(X_trade)
    trade_features = trade_features.copy()
    trade_features["ml_pred"] = preds

    # Build prediction_map: {date_str: {code: score}}
    prediction_map: dict[str, dict[str, float]] = {}
    for date, group in trade_features.groupby(trade_features["Date"].dt.date):
        date_str = date.isoformat()
        prediction_map[date_str] = {}
        for _, row in group.iterrows():
            code = str(row["code"]).zfill(6)
            prediction_map[date_str][code] = float(row["ml_pred"])

    # Feature importance
    if use_lgb:
        imp = pd.Series(model.feature_importances_, index=FEATURE_COLUMNS).sort_values(ascending=False)
        print(f"  Top 10 features:")
        for name, val in imp.head(10).items():
            print(f"    {name}: {val}")

    return prediction_map


def main() -> None:
    print("=== ML Ranker Walk-Forward Backtest ===")
    print(f"Panel: {PANEL_PATH}")

    panel = load_panel()
    print(f"  {len(panel):,} rows, {panel['code'].nunique()} codes")
    print(f"  Date range: {panel['Date'].min()} ~ {panel['Date'].max()}")

    # Compute features (this takes a bit)
    print("Computing features...")
    features_df = compute_features(panel)
    print(f"  Features computed: {len(FEATURE_COLUMNS)} columns")
    print(f"  Rows with valid label: {features_df['label_fwd_ret_5d'].notna().sum():,}")

    # Walk-forward experiments
    rows = []
    walk_forward = [
        (2023, 2024),
        (2024, 2025),
        (2025, 2026),
    ]

    for train_end, trade_year in walk_forward:
        print(f"\n--- Walk-forward: train ≤{train_end}, trade {trade_year} ---")

        # Base (no ML)
        print(f"  base...", end=" ", flush=True)
        r = run_engine(panel, year=trade_year)
        s = dict(r["summary"])
        s.update({"experiment": "base", "train_end": train_end, "trade_year": trade_year})
        rows.append(s)
        print(f"ret={s['total_return_pct']:.2f}% mdd={s['max_drawdown_pct']:.2f}% trades={s['total_trades']}")

        # ML prediction map
        ml_map = train_predict_walkforward(features_df, train_end, trade_year)
        if not ml_map:
            print(f"  No ML predictions, skipping.")
            continue

        # ML rerank overlay
        print(f"  ml_rerank...", end=" ", flush=True)
        r = run_engine(panel, year=trade_year, sentiment_map=ml_map, overlay_mode="rerank")
        s = dict(r["summary"])
        s.update({"experiment": "ml_rerank", "train_end": train_end, "trade_year": trade_year})
        rows.append(s)
        print(f"ret={s['total_return_pct']:.2f}% mdd={s['max_drawdown_pct']:.2f}% trades={s['total_trades']}")

        # ML hybrid a0.5
        print(f"  ml_hybrid_a0.5...", end=" ", flush=True)
        r = run_engine(panel, year=trade_year, sentiment_map=ml_map, overlay_mode="hybrid", alpha=0.5)
        s = dict(r["summary"])
        s.update({"experiment": "ml_hybrid_a0p5", "train_end": train_end, "trade_year": trade_year})
        rows.append(s)
        print(f"ret={s['total_return_pct']:.2f}% mdd={s['max_drawdown_pct']:.2f}% trades={s['total_trades']}")

        # ML hybrid a1.0 (more aggressive)
        print(f"  ml_hybrid_a1.0...", end=" ", flush=True)
        r = run_engine(panel, year=trade_year, sentiment_map=ml_map, overlay_mode="hybrid", alpha=1.0)
        s = dict(r["summary"])
        s.update({"experiment": "ml_hybrid_a1p0", "train_end": train_end, "trade_year": trade_year})
        rows.append(s)
        print(f"ret={s['total_return_pct']:.2f}% mdd={s['max_drawdown_pct']:.2f}% trades={s['total_trades']}")

    df = pd.DataFrame(rows)
    out_csv = OUT_DIR / "ml_ranker_walkforward_20260621.csv"
    df.to_csv(out_csv, index=False)
    print(f"\nSaved: {out_csv}")

    # Summary
    print("\n=== WALK-FORWARD RETURN SUMMARY (%) ===")
    pivot = df.pivot_table(
        index="experiment",
        columns="trade_year",
        values="total_return_pct",
        aggfunc="first",
    )
    print(pivot.to_string(float_format="%.2f"))

    print("\n=== WALK-FORWARD SHARPE ===")
    pivot_sharpe = df.pivot_table(
        index="experiment",
        columns="trade_year",
        values="sharpe_ratio",
        aggfunc="first",
    )
    print(pivot_sharpe.to_string(float_format="%.2f"))

    print("\n=== WALK-FORWARD MDD (%) ===")
    pivot_mdd = df.pivot_table(
        index="experiment",
        columns="trade_year",
        values="max_drawdown_pct",
        aggfunc="first",
    )
    print(pivot_mdd.to_string(float_format="%.2f"))


if __name__ == "__main__":
    main()
