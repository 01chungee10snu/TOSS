#!/usr/bin/env python3
"""Backtest current live strategy with actual deployed parameters.

Simulates the full daily loop:
- Situation detection (up/down/flat × low/high_vol based on KOSPI proxy)
- Multi-factor scoring (mom_5d, vol_20d, vol_ratio, rsi_14) per situation
- Relaxed fast veto (25% range, 15% vol, risk_on 1.5x)
- Entry: top-3 candidates, 250K KRW per position, max 3 concurrent
- Exit: TP 4%, SL 3%, trailing 3%, 1-day max hold
- Inverse sleeve: KODEX inverse on down/flat/risk_off days (200K)
- Round-trip cost: 24.5 bps

Research-only. No live orders.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PANEL_CSV = ROOT / "reports" / "backtests" / "practical_universe_400_2022-01-01_2026-latest_ohlcv_panel.csv"
POLICY_JSON = ROOT / "config" / "generated_policies" / "daily_multifactor_v1_practical400.json"
KOSPI_PROXY = "005930"  # Samsung Electronics as market proxy (most liquid)
INVERSE_CODE = None  # Inverse ETF not in practical400 universe; skip inverse sleeve

# ── Live parameters (from toss-ttak-loop.sh env) ──────────────────
MAX_POSITIONS = 3
MAX_NOTIONAL_PER_POS = 250_000
INVERSE_NOTIONAL = 200_000
TP_PCT = 0.04
SL_PCT = 0.03
TRAILING_PCT = 0.03
MAX_HOLD_DAYS = 1
ROUND_TRIP_BPS = 24.5
COST_RATE = ROUND_TRIP_BPS / 10_000

# ── Relaxed fast veto thresholds ──────────────────────────────────
MAX_INTRADAY_RANGE = 0.25
MAX_PREV_VOLATILITY = 0.15
RISK_ON_RELAX = 1.5


def load_data():
    df = pd.read_csv(PANEL_CSV, dtype={"code": str}, parse_dates=["Date"])
    df = df.sort_values(["code", "Date"]).reset_index(drop=True)
    return df


def compute_factors(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["code", "Date"]).reset_index(drop=True)
    df["prev_close"] = df.groupby("code")["Close"].shift(1)
    df["ret_daily"] = df.groupby("code")["Close"].pct_change()
    df["mom_5d"] = df.groupby("code")["Close"].pct_change(5)
    df["vol_20d"] = df.groupby("code")["ret_daily"].transform(lambda s: s.rolling(20).std())
    df["intraday_range"] = (df["High"] - df["Low"]) / df["prev_close"]
    df["gap"] = abs(df["Open"] / df["prev_close"] - 1.0)
    df["dollar_volume"] = df["Close"] * df["Volume"]

    delta = df.groupby("code")["Close"].diff()
    gain = delta.clip(lower=0).groupby(df["code"]).transform(lambda s: s.rolling(14).mean())
    loss = (-delta.clip(upper=0)).groupby(df["code"]).transform(lambda s: s.rolling(14).mean())
    rs = gain / (loss + 1e-8)
    df["rsi_14"] = 100 - (100 / (1 + rs))

    vol_ma5 = df.groupby("code")["Volume"].transform(lambda s: s.rolling(5).mean())
    vol_ma20 = df.groupby("code")["Volume"].transform(lambda s: s.rolling(20).mean())
    df["vol_ratio"] = vol_ma5 / (vol_ma20 + 1e-8)

    df["fwd_ret_1d"] = df.groupby("code")["Close"].shift(-1) / df["Close"] - 1
    df["fwd_ret_3d"] = df.groupby("code")["Close"].shift(-3) / df["Close"] - 1
    df["fwd_high_1d"] = df.groupby("code")["High"].shift(-1)
    df["fwd_low_1d"] = df.groupby("code")["Low"].shift(-1)
    df["fwd_close_1d"] = df.groupby("code")["Close"].shift(-1)

    # Forward bars for exit simulation (next day intraday)
    for d in range(1, MAX_HOLD_DAYS + 2):
        df[f"fwd_high_{d}d"] = df.groupby("code")["High"].shift(-d)
        df[f"fwd_low_{d}d"] = df.groupby("code")["Low"].shift(-d)
        df[f"fwd_close_{d}d"] = df.groupby("code")["Close"].shift(-d)

    return df


def detect_situation(market_row: pd.Series) -> tuple[str, str]:
    """Return (situation, market_regime) from KOSPI proxy data."""
    ret = market_row.get("ret_daily", 0)
    vol = market_row.get("vol_20d", 0)
    if pd.isna(ret) or pd.isna(vol):
        return "unknown", ""

    if ret > 0.005:
        direction = "up"
    elif ret < -0.005:
        direction = "down"
    else:
        direction = "flat"

    if vol < 0.012:
        vol_regime = "low_vol"
    else:
        vol_regime = "high_vol"

    situation = f"{direction}_{vol_regime}"
    market_regime = "risk_on" if direction in ("up", "flat") and vol_regime == "low_vol" else ""
    if direction == "down" and vol_regime == "high_vol":
        market_regime = "risk_off"
    return situation, market_regime


def score_candidates(day_data: pd.DataFrame, situation: str, policy: dict) -> pd.DataFrame:
    sit_config = policy.get("situations", {}).get(situation)
    if not sit_config or situation == "down_low_vol":
        return pd.DataFrame()

    mode = sit_config.get("mode", "momentum")
    weights = sit_config.get("weights", {})
    min_dv = sit_config.get("min_dollar_volume", 500_000_000)
    min_price = sit_config.get("min_price", 2000)
    min_mom = sit_config.get("min_mom_5d", -1)
    max_mom = sit_config.get("max_mom_5d", 1)
    min_rsi = sit_config.get("min_rsi", 0)
    max_rsi = sit_config.get("max_rsi", 100)
    min_vr = sit_config.get("min_vol_ratio", 0)
    max_vr = sit_config.get("max_vol_ratio", 100)
    max_vol = sit_config.get("max_vol_20d", 1)

    cands = day_data.copy()
    cands = cands[
        (cands["dollar_volume"] >= min_dv)
        & (cands["Close"] >= min_price)
        & (cands["mom_5d"] >= min_mom)
        & (cands["mom_5d"] <= max_mom)
        & (cands["rsi_14"] >= min_rsi)
        & (cands["rsi_14"] <= max_rsi)
        & (cands["vol_ratio"] >= min_vr)
        & (cands["vol_ratio"] <= max_vr)
        & (cands["vol_20d"] <= max_vol)
    ]

    if cands.empty:
        return cands

    if mode == "reversal":
        mom_score = (cands["mom_5d"].abs().rank(pct=True)) * np.sign(-cands["mom_5d"] + 1e-8) * 0 + (1 - cands["mom_5d"].abs().rank(pct=True))
        # For reversal: prefer oversold (negative momentum)
        mom_score = 1 - cands["mom_5d"].rank(pct=True)  # lower momentum = higher score
    else:
        mom_score = cands["mom_5d"].rank(pct=True)

    low_vol_score = (1 / (cands["vol_20d"] + 1e-8)).rank(pct=True)
    vol_norm_score = (1 / (cands["vol_ratio"].abs() + 1e-8)).rank(pct=True)
    rsi_mid_score = (1 - (cands["rsi_14"] - 50).abs() / 50).rank(pct=True)

    cands = cands.copy()
    cands["score"] = (
        weights.get("momentum", 0.4) * mom_score
        + weights.get("low_vol", 0.2) * low_vol_score
        + weights.get("vol_norm", 0.15) * vol_norm_score
        + weights.get("rsi_mid", 0.25) * rsi_mid_score
    )
    return cands.nlargest(3, "score")


def apply_fast_veto(cands: pd.DataFrame, day_data: pd.DataFrame, market_regime: str) -> pd.DataFrame:
    if cands.empty:
        return cands

    range_thresh = MAX_INTRADAY_RANGE
    vol_thresh = MAX_PREV_VOLATILITY
    gap_thresh = 0.08

    if market_regime == "risk_on":
        range_thresh *= RISK_ON_RELAX
        vol_thresh *= RISK_ON_RELAX
        gap_thresh *= RISK_ON_RELAX

    mask = (
        (cands["intraday_range"] <= range_thresh)
        & (cands["vol_20d"] <= vol_thresh)
        & (cands["gap"] <= gap_thresh)
    )
    return cands[mask].copy()


def simulate_exit(entry_price: float, row: pd.Series, hold_day: int) -> tuple[float, str]:
    """Simulate TP/SL/trailing exit for 1-day max hold.

    Returns (exit_price, reason).
    Uses next-day intraday High/Low/Close to determine exit.
    """
    high_col = f"fwd_high_{hold_day}d"
    low_col = f"fwd_low_{hold_day}d"
    close_col = f"fwd_close_{hold_day}d"

    day_high = row.get(high_col)
    day_low = row.get(low_col)
    day_close = row.get(close_col)

    if pd.isna(day_high) or pd.isna(day_low) or pd.isna(day_close):
        return entry_price, "no_data"

    tp_price = entry_price * (1 + TP_PCT)
    sl_price = entry_price * (1 - SL_PCT)

    # Trailing: track from entry; if high-water mark drops > trailing% from peak
    peak = max(entry_price, day_high)

    # Check SL first (pessimistic: assume worst case)
    if day_low <= sl_price:
        return sl_price, "stop_loss"

    # Check TP
    if day_high >= tp_price:
        return tp_price, "take_profit"

    # Trailing stop: if peak - day_low > trailing_pct * peak
    trailing_stop = peak * (1 - TRAILING_PCT)
    if day_low <= trailing_stop and peak > entry_price:
        return trailing_stop, "trailing_stop"

    # Hold to close (max_hold_days=1 → exit at next day close)
    return day_close, "max_hold_close"


def run_backtest():
    print("Loading data...")
    df = load_data()
    df = compute_factors(df)
    policy = json.loads(POLICY_JSON.read_text())

    # Get market proxy data
    market_df = df[df["code"] == KOSPI_PROXY].copy()
    market_df = market_df.set_index("Date")

    all_dates = sorted(df["Date"].unique())
    # Skip first 25 days (need rolling calculations)
    tradeable_dates = [d for d in all_dates if d >= all_dates[25]]

    print(f"Period: {tradeable_dates[0].date()} → {tradeable_dates[-1].date()}")
    print(f"Tradeable days: {len(tradeable_dates)}")
    print()

    trades = []
    equity = 0  # accumulated P&L
    inverse_trades = []

    for i, date in enumerate(tradeable_dates):
        day_data = df[df["Date"] == date].copy()
        if day_data.empty:
            continue

        # Market situation from proxy
        mkt = market_df.loc[date] if date in market_df.index else None
        if mkt is None:
            continue

        situation, market_regime = detect_situation(mkt)

        # Inverse sleeve check (separate from normal candidates)
        use_inverse = situation in ("down_high_vol", "flat_high_vol", "risk_off") or market_regime == "risk_off"

        if use_inverse and INVERSE_CODE:
            inv_row = day_data[day_data["code"] == INVERSE_CODE]
            if not inv_row.empty:
                inv = inv_row.iloc[0]
                entry = inv["Close"]
                exit_price, reason = simulate_exit(entry, inv, 1)
                if reason != "no_data" and exit_price > 0:
                    gross_ret = exit_price / entry - 1
                    net_ret = gross_ret - COST_RATE
                    pnl = net_ret * INVERSE_NOTIONAL
                    inverse_trades.append({
                        "date": str(date.date()),
                        "symbol": INVERSE_CODE,
                        "entry": entry,
                        "exit": exit_price,
                        "return_pct": net_ret * 100,
                        "pnl_krw": pnl,
                        "reason": reason,
                    })
                    equity += pnl

        # Normal candidates
        if situation == "down_low_vol":
            continue  # rejected situation

        cands = score_candidates(day_data, situation, policy)
        if cands.empty:
            continue

        cands = apply_fast_veto(cands, day_data, market_regime)
        if cands.empty:
            continue

        # Take top candidates (max 3, limited by concurrent position cap)
        for _, row in cands.head(MAX_POSITIONS).iterrows():
            entry = row["Close"]
            exit_price, reason = simulate_exit(entry, row, 1)
            if reason == "no_data" or exit_price <= 0:
                continue

            gross_ret = exit_price / entry - 1
            net_ret = gross_ret - COST_RATE
            pnl = net_ret * MAX_NOTIONAL_PER_POS

            trades.append({
                "date": str(date.date()),
                "symbol": row["code"],
                "situation": situation,
                "market_regime": market_regime,
                "score": row["score"],
                "entry": entry,
                "exit": exit_price,
                "return_pct": net_ret * 100,
                "pnl_krw": pnl,
                "reason": reason,
            })
            equity += pnl

    # ── Metrics ────────────────────────────────────────────────────
    all_trades = trades + inverse_trades

    if not all_trades:
        print("No trades generated.")
        return

    trade_df = pd.DataFrame(all_trades)
    # Sort by date
    trade_df = trade_df.sort_values("date").reset_index(drop=True)

    # Daily P&L aggregation
    daily_pnl = trade_df.groupby("date")["pnl_krw"].sum()
    cum_equity = daily_pnl.cumsum()

    total_trades = len(trade_df)
    wins = trade_df[trade_df["pnl_krw"] > 0]
    losses = trade_df[trade_df["pnl_krw"] <= 0]
    win_rate = len(wins) / total_trades * 100 if total_trades else 0
    total_pnl = trade_df["pnl_krw"].sum()
    avg_win = wins["pnl_krw"].mean() if len(wins) > 0 else 0
    avg_loss = losses["pnl_krw"].mean() if len(losses) > 0 else 0
    profit_factor = wins["pnl_krw"].sum() / abs(losses["pnl_krw"].sum()) if len(losses) > 0 and losses["pnl_krw"].sum() != 0 else float("inf")

    # Max drawdown
    peak = cum_equity.cummax()
    dd = cum_equity - peak
    max_dd = dd.min() if len(dd) > 0 else 0
    max_dd_pct = (dd / (peak + 1e-8) * 100).min() if len(dd) > 0 else 0

    # Sharpe (daily, annualized)
    daily_ret = daily_pnl / 1_000_000  # normalize to ~1M portfolio
    sharpe = daily_ret.mean() / (daily_ret.std() + 1e-8) * np.sqrt(252) if daily_ret.std() > 0 else 0

    # Average return per trade
    avg_ret = trade_df["return_pct"].mean()

    # By situation
    sit_stats = trade_df.groupby("situation").agg(
        count=("pnl_krw", "count"),
        win_rate=("pnl_krw", lambda x: (x > 0).mean() * 100),
        avg_return_pct=("return_pct", "mean"),
        total_pnl=("pnl_krw", "sum"),
    ).round(2)

    # By exit reason
    reason_stats = trade_df.groupby("reason").agg(
        count=("pnl_krw", "count"),
        avg_return_pct=("return_pct", "mean"),
        total_pnl=("pnl_krw", "sum"),
    ).round(2)

    # Normal vs inverse
    normal_pnl = sum(t["pnl_krw"] for t in trades)
    inv_pnl = sum(t["pnl_krw"] for t in inverse_trades)

    print("=" * 60)
    print("📊 현재 보유 전략 백테스트 결과")
    print("=" * 60)
    print(f"기간: {trade_df['date'].iloc[0]} → {trade_df['date'].iloc[-1]}")
    print(f"정책: daily_multifactor_v1_practical400")
    print(f"TP {TP_PCT:.0%} / SL {SL_PCT:.0%} / Trailing {TRAILING_PCT:.0%} / Max Hold {MAX_HOLD_DAYS}일")
    print(f"왕복비용: {ROUND_TRIP_BPS}bps")
    print(f"Fast Veto: range ≤{MAX_INTRADAY_RANGE:.0%} / vol ≤{MAX_PREV_VOLATILITY:.0%} / risk_on ×{RISK_ON_RELAX}")
    print()
    print(f"총 거래: {total_trades}건 (일반 {len(trades)} + 인버스 {len(inverse_trades)})")
    print(f"승률: {win_rate:.1f}%")
    print(f"총 손익: {total_pnl:+,.0f}원")
    print(f"평균 수익률/거래: {avg_ret:+.2f}%")
    print(f"Profit Factor: {profit_factor:.2f}")
    print(f"평균 승리: {avg_win:+,.0f}원 / 평균 손실: {avg_loss:+,.0f}원")
    print(f"Max Drawdown: {max_dd:+,.0f}원 ({max_dd_pct:+.1f}%)")
    print(f"Sharpe (연환산): {sharpe:.2f}")
    print()
    print(f"일반 전략 손익: {normal_pnl:+,.0f}원")
    print(f"인버스 전략 손익: {inv_pnl:+,.0f}원")
    print()
    print("── 상황별 성과 ──")
    print(sit_stats.to_string())
    print()
    print("── 청산 사유별 ──")
    print(reason_stats.to_string())
    print()
    print("── 최근 10거래 ──")
    recent = trade_df.tail(10)[["date", "symbol", "situation", "return_pct", "pnl_krw", "reason"]]
    print(recent.to_string(index=False))

    # Save full results
    out = ROOT / "reports" / "harness" / "backtest_current_live_strategy.json"
    result = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "config": {
            "tp_pct": TP_PCT, "sl_pct": SL_PCT, "trailing_pct": TRAILING_PCT,
            "max_hold_days": MAX_HOLD_DAYS, "round_trip_bps": ROUND_TRIP_BPS,
            "max_positions": MAX_POSITIONS, "max_notional_per_pos": MAX_NOTIONAL_PER_POS,
            "fast_veto_range": MAX_INTRADAY_RANGE, "fast_veto_vol": MAX_PREV_VOLATILITY,
            "risk_on_relax": RISK_ON_RELAX,
        },
        "summary": {
            "total_trades": total_trades,
            "normal_trades": len(trades),
            "inverse_trades": len(inverse_trades),
            "win_rate_pct": round(win_rate, 1),
            "total_pnl_krw": round(total_pnl, 0),
            "avg_return_pct": round(avg_ret, 2),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown_krw": round(max_dd, 0),
            "max_drawdown_pct": round(max_dd_pct, 1),
            "sharpe": round(sharpe, 2),
            "normal_pnl": round(normal_pnl, 0),
            "inverse_pnl": round(inv_pnl, 0),
        },
    }
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"\n💾 저장: {out}")


if __name__ == "__main__":
    run_backtest()
