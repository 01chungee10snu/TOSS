"""Feature engineering for ML-based stock ranking.

Generates per-stock, per-date features from OHLCV panel data.
All features are computed using ONLY data available up to that date
(no look-ahead bias).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def compute_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Compute all features for each (code, date) row.

    panel: columns Date, code, Open, High, Low, Close, Volume
    Returns: panel with feature columns added, sorted by code, Date.
    """
    df = panel.copy()
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values(["code", "Date"]).reset_index(drop=True)

    grouped = df.groupby("code", group_keys=False)

    # --- Returns ---
    for period in [1, 3, 5, 10, 20, 60, 120]:
        df[f"ret_{period}d"] = grouped["Close"].pct_change(period)

    # --- Log returns (for momentum signals) ---
    df["log_ret_1d"] = grouped["Close"].transform(lambda s: np.log(s / s.shift(1)))

    # --- Volatility (rolling std of daily returns) ---
    df["vol_5d"] = grouped["log_ret_1d"].rolling(5).std().reset_index(level=0, drop=True)
    df["vol_10d"] = grouped["log_ret_1d"].rolling(10).std().reset_index(level=0, drop=True)
    df["vol_20d"] = grouped["log_ret_1d"].rolling(20).std().reset_index(level=0, drop=True)
    df["vol_60d"] = grouped["log_ret_1d"].rolling(60).std().reset_index(level=0, drop=True)

    # --- Volume features ---
    df["vol_surge_5_20"] = (
        grouped["Volume"].rolling(5).mean().reset_index(level=0, drop=True)
        / (grouped["Volume"].rolling(20).mean().reset_index(level=0, drop=True) + 1)
    )
    df["vol_surge_1_20"] = df["Volume"] / (grouped["Volume"].rolling(20).mean().reset_index(level=0, drop=True) + 1)

    # --- RSI (14-day) ---
    df["_delta"] = grouped["Close"].diff()
    df["_gain"] = df["_delta"].clip(lower=0)
    df["_loss"] = (-df["_delta"]).clip(lower=0)
    avg_gain = grouped["_gain"].rolling(14).mean().reset_index(level=0, drop=True)
    avg_loss = grouped["_loss"].rolling(14).mean().reset_index(level=0, drop=True)
    rs = avg_gain / (avg_loss + 1e-10)
    df["rsi_14"] = 100 - (100 / (1 + rs))

    # --- MACD ---
    ema12 = grouped["Close"].ewm(span=12, adjust=False).mean().reset_index(level=0, drop=True)
    ema26 = grouped["Close"].ewm(span=26, adjust=False).mean().reset_index(level=0, drop=True)
    df["macd"] = ema12 - ema26
    df["macd_signal"] = grouped["macd"].ewm(span=9, adjust=False).mean().reset_index(level=0, drop=True)
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    # --- Bollinger Bands position ---
    sma20 = grouped["Close"].rolling(20).mean().reset_index(level=0, drop=True)
    std20 = grouped["Close"].rolling(20).std().reset_index(level=0, drop=True)
    upper = sma20 + 2 * std20
    lower = sma20 - 2 * std20
    df["bb_position"] = (df["Close"] - lower) / (upper - lower + 1e-10)

    # --- Moving average crossover signals ---
    sma5 = grouped["Close"].rolling(5).mean().reset_index(level=0, drop=True)
    sma10 = grouped["Close"].rolling(10).mean().reset_index(level=0, drop=True)
    sma60 = grouped["Close"].rolling(60).mean().reset_index(level=0, drop=True)
    df["above_sma5"] = (df["Close"] > sma5).astype(float)
    df["above_sma20"] = (df["Close"] > sma20).astype(float)
    df["above_sma60"] = (df["Close"] > sma60).astype(float)
    df["sma5_above_sma20"] = (sma5 > sma20).astype(float)
    df["sma20_above_sma60"] = (sma20 > sma60).astype(float)

    # --- Price-range features ---
    df["high_low_range_5d"] = (
        grouped["High"].rolling(5).max().reset_index(level=0, drop=True)
        - grouped["Low"].rolling(5).min().reset_index(level=0, drop=True)
    ) / (df["Close"] + 1e-10)

    # --- Acceleration (change in momentum) ---
    df["mom_accel"] = df["ret_5d"] - grouped["ret_5d"].shift(5).reset_index(level=0, drop=True)

    # --- Turnover / dollar volume trend ---
    df["dollar_vol"] = df["Close"] * df["Volume"]
    df["dvol_surge"] = (
        grouped["dollar_vol"].rolling(5).mean().reset_index(level=0, drop=True)
        / (grouped["dollar_vol"].rolling(20).mean().reset_index(level=0, drop=True) + 1)
    )

    # --- Cross-sectional features (relative strength vs universe) ---
    # Rank each feature within each date
    for col in ["ret_5d", "ret_20d", "ret_60d", "vol_20d", "dvol_surge"]:
        rank_col = f"{col}_xsec_rank"
        df[rank_col] = df.groupby("Date")[col].rank(pct=True)

    # --- Forward return (LABEL) ---
    forward_days = 5
    df["label_fwd_ret_5d"] = grouped["Close"].shift(-forward_days) / df["Close"] - 1

    # --- Cleanup temp columns ---
    df = df.drop(columns=[c for c in df.columns if c.startswith("_")], errors="ignore")

    return df


FEATURE_COLUMNS = [
    "ret_1d", "ret_3d", "ret_5d", "ret_10d", "ret_20d", "ret_60d", "ret_120d",
    "vol_5d", "vol_10d", "vol_20d", "vol_60d",
    "vol_surge_5_20", "vol_surge_1_20",
    "rsi_14",
    "macd", "macd_signal", "macd_hist",
    "bb_position",
    "above_sma5", "above_sma20", "above_sma60",
    "sma5_above_sma20", "sma20_above_sma60",
    "high_low_range_5d",
    "mom_accel",
    "dvol_surge",
    "ret_5d_xsec_rank", "ret_20d_xsec_rank", "ret_60d_xsec_rank",
    "vol_20d_xsec_rank", "dvol_surge_xsec_rank",
]
