"""Fast veto gate for candidate drafts using same-day price anomalies."""
from __future__ import annotations

from typing import Any

import pandas as pd


def evaluate_fast_veto(
    *,
    candidate_payload: dict[str, Any],
    panel: pd.DataFrame,
    as_of: str,
    max_gap_pct: float = 0.08,
    max_intraday_range_pct: float = 0.15,
    min_dollar_volume_krw: float = 10_000_000.0,
    max_prev_volatility_20d: float = 0.10,
) -> dict[str, Any]:
    orders = candidate_payload.get("orders", []) if isinstance(candidate_payload, dict) else []
    if not orders:
        return {
            "status": "SKIPPED_NO_CANDIDATES",
            "reasons": ["no_candidate_symbols"],
            "allowed_orders": [],
            "vetoed_symbols": [],
            "reasons_by_symbol": {},
            "checked_symbols": [],
        }

    data = panel.copy()
    data["Date"] = pd.to_datetime(data["Date"])
    data["code"] = data["code"].astype(str).str.zfill(6)
    data = data.sort_values(["code", "Date"]).reset_index(drop=True)
    data["prev_close"] = data.groupby("code")["Close"].shift(1)
    data["dollar_volume"] = data["Close"] * data.get("Volume", 0)
    data["ret_cc"] = data.groupby("code")["Close"].pct_change()
    data["prev_volatility_20d"] = data.groupby("code")["ret_cc"].transform(lambda s: s.shift(1).rolling(20).std())
    todays = data[data["Date"] == pd.Timestamp(as_of)].copy()

    allowed_orders: list[dict[str, Any]] = []
    vetoed_symbols: list[str] = []
    reasons_by_symbol: dict[str, list[str]] = {}
    checked_symbols: list[str] = []

    for order in orders:
        symbol = str(order.get("symbol", "")).zfill(6)
        if not symbol:
            continue
        checked_symbols.append(symbol)
        row = todays[todays["code"] == symbol]
        reasons: list[str] = []
        if row.empty:
            reasons.append("missing_asof_bar")
        else:
            rec = row.iloc[-1]
            prev_close = float(rec.get("prev_close") or 0.0)
            open_px = float(rec.get("Open") or 0.0)
            high_px = float(rec.get("High") or 0.0)
            low_px = float(rec.get("Low") or 0.0)
            dollar_volume = float(rec.get("dollar_volume") or 0.0)
            prev_volatility_20d = rec.get("prev_volatility_20d")
            if prev_close <= 0 or open_px <= 0 or high_px <= 0 or low_px <= 0:
                reasons.append("invalid_price_inputs")
            else:
                gap_pct = abs(open_px / prev_close - 1.0)
                intraday_range_pct = (high_px - low_px) / prev_close
                if gap_pct > max_gap_pct:
                    reasons.append("excessive_gap")
                if intraday_range_pct > max_intraday_range_pct:
                    reasons.append("excessive_intraday_range")
            if dollar_volume < min_dollar_volume_krw:
                reasons.append("low_dollar_volume")
            if pd.notna(prev_volatility_20d) and float(prev_volatility_20d) > max_prev_volatility_20d:
                reasons.append("excessive_prev_volatility_20d")
        if reasons:
            vetoed_symbols.append(symbol)
            reasons_by_symbol[symbol] = reasons
        else:
            allowed_orders.append(order)

    if not allowed_orders:
        status = "BLOCKED_FAST_VETO"
    elif vetoed_symbols:
        status = "READY_WITH_VETO"
    else:
        status = "READY"

    return {
        "status": status,
        "reasons": sorted({reason for reasons in reasons_by_symbol.values() for reason in reasons}),
        "allowed_orders": allowed_orders,
        "vetoed_symbols": vetoed_symbols,
        "reasons_by_symbol": reasons_by_symbol,
        "checked_symbols": checked_symbols,
        "thresholds": {
            "max_gap_pct": max_gap_pct,
            "max_intraday_range_pct": max_intraday_range_pct,
            "min_dollar_volume_krw": min_dollar_volume_krw,
            "max_prev_volatility_20d": max_prev_volatility_20d,
        },
    }
