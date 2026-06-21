"""Daily forward tracking report — 0.44%/day target strategy.

Generates today's Top-10 candidates using the optimal parameters found
in the sizing frontier sweep:
  - Strategy: ml_rerank
  - cash_fraction_per_entry: 0.75
  - max_notional: 300,000 KRW
  - stop_loss: 10%, take_profit: 25%, trailing_stop: 6%
  - max_holding: 10 days
  - max_positions: 8

Pipeline:
  1. Load practical universe panel (400 stocks)
  2. Compute features (no look-ahead)
  3. Train ML ranker on all history up to T-1
  4. Predict for T (latest available date)
  5. Check macro regime (risk_on / neutral / risk_off)
  6. Rank candidates by ML prediction
  7. Output Top-10 + paper trade entry

Research/paper only. No live orders.
"""
from __future__ import annotations

import json
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from toss_alpha.daily.features import compute_features, FEATURE_COLUMNS
from toss_alpha.daily.macro_signals import get_macro_regime, CACHE_PATH as MACRO_CACHE
from fusion_3layer_backtest import train_ml_model, predict_ml_scores, compute_macro_adjusted_scores, SENT_CSV, build_sentiment_map

warnings.filterwarnings("ignore")

PANEL_PATH = ROOT / "reports/backtests/practical_universe_panel.parquet"
NAME_MAP_CSV = ROOT / "reports/harness/panel_code_name_mapping.csv"
OUT_DIR = ROOT / "reports/harness/forward_tracking"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Optimal parameters from sizing + conservative frontier
OPTIMAL = {
    "strategy": "fusion_rerank",
    "overlay_mode": "rerank",
    "prediction_alpha": 10.0,
    "cash_fraction_per_entry": 0.40,
    "max_notional": 300_000,
    "stop_loss_pct": 0.06,
    "take_profit_pct": 0.25,
    "trailing_stop_pct": 0.06,
    "max_holding_steps": 20,
    "max_positions": 8,
    "transaction_cost_bps": 30.0,
}


def _now_kst() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def load_panel() -> pd.DataFrame:
    panel = pd.read_parquet(PANEL_PATH)
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    panel["Date"] = pd.to_datetime(panel["Date"])
    return panel.sort_values(["code", "Date"]).reset_index(drop=True)


def load_name_map() -> dict[str, str]:
    if not NAME_MAP_CSV.exists():
        return {}
    df = pd.read_csv(NAME_MAP_CSV, dtype={"Code": str})
    df["Code"] = df["Code"].astype(str).str.zfill(6)
    return dict(zip(df["Code"], df["Name"]))


def fetch_name_naver(code: str) -> str:
    """Fetch stock name from Naver Finance for a single code."""
    import re
    import time

    import requests as req

    try:
        url = f"https://finance.naver.com/item/main.naver?code={code}"
        resp = req.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        match = re.search(r"<title>(.+?) :", resp.text)
        return match.group(1).strip() if match else "—"
    except Exception:
        return "—"


def resolve_names(codes: list[str], static_map: dict[str, str]) -> dict[str, str]:
    """Resolve names: static map first, Naver Finance for misses."""
    import time

    result = {}
    for code in codes:
        if code in static_map and static_map[code] != "—":
            result[code] = static_map[code]
        else:
            result[code] = fetch_name_naver(code)
            time.sleep(0.15)  # Rate limit
    return result


def train_and_predict(features_df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Train ML on all history except the last date, predict for the last date,
    then apply fusion_rerank (ML + macro + sentiment) overlay.

    Returns (latest_predictions_df, model_info).
    """
    # Use fusion_3layer's ML pipeline
    latest_date = features_df["Date"].max()
    train_end = features_df[features_df["Date"] < latest_date]["Date"].max().year
    predict_year = latest_date.year

    model = train_ml_model(features_df, train_end)
    ml_map = predict_ml_scores(model, features_df, predict_year)

    # Load macro + sentiment
    macro_path = MACRO_CACHE
    if macro_path.exists():
        macro_df = pd.read_parquet(macro_path)
    else:
        macro_df = None

    sent_map_raw = None
    if SENT_CSV.exists():
        sent_df = pd.read_csv(SENT_CSV, parse_dates=["date"])
        sent_map_raw = build_sentiment_map(sent_df)

    # Compute fusion-adjusted scores (macro × ML, with optional sentiment)
    yr_sent = None
    if sent_map_raw:
        # Find sentiment for predict_year
        yr_keys = [k for k in sent_map_raw if pd.Timestamp(k).year == predict_year]
        yr_sent = {k: sent_map_raw[k] for k in yr_keys}

    fusion_map = compute_macro_adjusted_scores(
        ml_map,
        macro_df,
        yr_sent,
    )

    # Map fusion scores back to DataFrame rows
    predict_rows = features_df[features_df["Date"] == latest_date].copy()
    predict_rows["ml_pred"] = predict_rows["code"].astype(str).str.zfill(6).map(
        lambda c: fusion_map.get(str(latest_date.date()), {}).get(c, 0)
    )

    info = {
        "train_samples": len(ml_map),
        "predict_samples": len(predict_rows),
        "train_end_date": str(train_end),
        "predict_date": str(latest_date.date()),
        "top_features": {},
    }

    return predict_rows, info


def apply_macro_gate(macro_status: dict, candidates: pd.DataFrame) -> pd.DataFrame:
    """If risk_off, filter to only low-volatility candidates."""
    regime = macro_status.get("status", "neutral")
    if regime == "risk_off":
        # Only keep below-median volatility
        vol_median = candidates["vol_60d"].median()
        filtered = candidates[candidates["vol_60d"] <= vol_median].copy()
        print(f"  [macro gate] risk_off → filtered to {len(filtered)} low-vol candidates")
        return filtered
    return candidates


def generate_report() -> dict:
    """Main: generate today's forward tracking report."""
    now = _now_kst()
    print(f"=== Forward Tracking Report — {now.strftime('%Y-%m-%d %H:%M KST')} ===")

    # Load data
    print("Loading panel...")
    panel = load_panel()
    print(f"  {panel.shape[0]:,} rows, {panel['code'].nunique()} codes")
    print(f"  Date range: {panel['Date'].min().date()} ~ {panel['Date'].max().date()}")

    print("Computing features...")
    features = compute_features(panel)

    # Add 5-day forward return label (for training only)
    features = features.sort_values(["code", "Date"]).reset_index(drop=True)
    features["label_fwd_ret_5d"] = features.groupby("code")["Close"].shift(-5) / features["Close"] - 1

    # Train and predict
    print("Training ML ranker...")
    predictions, model_info = train_and_predict(features)
    print(f"  Train: {model_info['train_samples']:,} samples")
    print(f"  Predict date: {model_info['predict_date']}")

    # Macro regime
    print("Checking macro regime...")
    latest_date = pd.Timestamp(panel["Date"].max())
    macro_status = get_macro_regime(latest_date, macro=None)
    print(f"  Regime: {macro_status['status']}")
    print(f"  VIX: {macro_status.get('vix', 'N/A')}")
    print(f"  USD/KRW: {macro_status.get('usd_krw', 'N/A')}")
    print(f"  SOX 20d: {macro_status.get('sox_ret_20d', 'N/A')}")

    # Rank by ML prediction
    candidates = predictions.sort_values("ml_pred", ascending=False).reset_index(drop=True)
    candidates["ml_rank"] = range(1, len(candidates) + 1)

    # Apply macro gate
    candidates = apply_macro_gate(macro_status, candidates)

    # Top 10
    top10 = candidates.head(10).copy()
    # Resolve names: static map for practical universe, Naver for misses
    top10_codes = [str(c).zfill(6) for c in top10["code"].tolist()]
    name_map = resolve_names(top10_codes, load_name_map())
    top10["name"] = top10["code"].map(lambda c: name_map.get(str(c).zfill(6), "—"))

    report = {
        "generated_at": now.isoformat(),
        "predict_date": model_info["predict_date"],
        "params": OPTIMAL,
        "macro_regime": macro_status,
        "model_info": model_info,
        "top10": [],
    }

    print(f"\n{'Rank':<5} {'Code':<8} {'Name':<14} {'ML Score':>10} {'Close':>10} {'Vol60d':>8} {'RSI':>6}")
    print("-" * 70)
    for _, row in top10.iterrows():
        entry = {
            "rank": int(row["ml_rank"]),
            "code": str(row["code"]),
            "name": str(row["name"]),
            "ml_score": round(float(row["ml_pred"]), 6),
            "close": int(row["Close"]),
            "vol_60d": round(float(row.get("vol_60d", 0)), 5),
            "rsi_14": round(float(row.get("rsi_14", 50)), 1),
            "ret_60d": round(float(row.get("ret_60d", 0)), 4),
            "ret_120d": round(float(row.get("ret_120d", 0)), 4),
            "volume": int(row["Volume"]),
        }
        report["top10"].append(entry)
        print(
            f"{entry['rank']:<5} {entry['code']:<8} {entry['name']:<14} "
            f"{entry['ml_score']:>10.4f} {entry['close']:>10,} "
            f"{entry['vol_60d']:>8.4f} {entry['rsi_14']:>6.1f}"
        )

    # Save JSON
    date_str = now.strftime("%Y-%m-%d")
    json_path = OUT_DIR / f"forward_{date_str}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nSaved: {json_path}")

    return report


if __name__ == "__main__":
    report = generate_report()

    # Telegram-friendly summary
    print("\n" + "=" * 60)
    print("📊 FORWARD TRACKING — TOP 10 (Sharpe 6.14 optimal)")
    print("=" * 60)
    regime = report["macro_regime"]
    emoji = {"risk_on": "🟢", "neutral": "🟡", "risk_off": "🔴"}.get(
        regime["status"], "⚪"
    )
    print(f"{emoji} Macro: {regime['status']} | VIX {regime.get('vix', 'N/A')}")
    print(f"📅 Predict date: {report['predict_date']}")
    print()
    for entry in report["top10"][:8]:
        print(
            f"  {entry['rank']}. {entry['code']} {entry['name']} "
            f"| score {entry['ml_score']:+.4f} | ₩{entry['close']:,}"
        )
    print()
    print("⚠️ Research/paper only. No live orders.")
