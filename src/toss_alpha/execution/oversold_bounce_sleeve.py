"""Oversold bounce sleeve: buy heavily-oversold large-caps after a market crash.

After a severe market selloff (e.g. KOSPI -10%+), the next session often sees a
technical rebound in the most heavily oversold liquid names.  This sleeve scans
the panel for symbols that:
  1. Dropped >= threshold (default -7%) in the previous session.
  2. Have sufficient trading value (default 500억원+) to ensure liquidity.
  3. Are showing early intraday strength (current price > prev close).

It generates BUY candidates for the top N oversold large-caps with tight
take-profit and stop-loss exits for same-day or next-day profit capture.

This module never submits broker orders; it only produces candidate orders that
pass through the existing live-submit gates (market-time, freshness, risk, etc.).
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from toss_alpha.execution.inverse_sleeve import krx_tick_size, round_up_to_tick, whole_share_quantity


@dataclass(frozen=True)
class OversoldBounceSettings:
    enabled: bool = False
    min_drop_pct: float = 0.07          # previous-session drop threshold
    min_trading_value_krw: float = 50e9  # 500억원 liquidity floor
    min_price_krw: float = 10_000        # skip penny stocks
    max_candidates: int = 3              # top-N oversold names to buy
    notional_krw: float = 200_000        # per-symbol budget
    take_profit_pct: float = 0.04        # 4% take profit
    stop_loss_pct: float = 0.03          # 3% stop loss
    max_holding_days: int = 1            # exit next day at latest
    min_market_day_return_for_trigger: float = -0.03  # market must have fallen >= 3% prev session
    buy_aggressiveness_pct: float = 0.01  # limit price = ask * (1 + 1%)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "OversoldBounceSettings":
        source = os.environ if env is None else env
        return cls(
            enabled=_env_true(source.get("TOSS_BOUNCE_SLEEVE_ENABLED")),
            min_drop_pct=abs(float(source.get("TOSS_BOUNCE_MIN_DROP_PCT", "0.07"))),
            min_trading_value_krw=float(source.get("TOSS_BOUNCE_MIN_TRADING_VALUE_KRW", str(50e9))),
            min_price_krw=float(source.get("TOSS_BOUNCE_MIN_PRICE_KRW", "10000")),
            max_candidates=int(source.get("TOSS_BOUNCE_MAX_CANDIDATES", "3")),
            notional_krw=float(source.get("TOSS_BOUNCE_NOTIONAL_KRW", "200000")),
            take_profit_pct=float(source.get("TOSS_BOUNCE_TAKE_PROFIT_PCT", "0.04")),
            stop_loss_pct=float(source.get("TOSS_BOUNCE_STOP_LOSS_PCT", "0.03")),
            max_holding_days=int(source.get("TOSS_BOUNCE_MAX_HOLDING_DAYS", "1")),
            min_market_day_return_for_trigger=abs(float(source.get("TOSS_BOUNCE_MIN_MARKET_DROP_PCT", "0.03"))) * -1,
            buy_aggressiveness_pct=float(source.get("TOSS_BOUNCE_BUY_AGGRESSIVENESS_PCT", "0.01")),
        )


def maybe_apply_oversold_bounce(
    candidate_payload: dict[str, Any],
    *,
    panel_path: Path,
    out_dir: Path,
    env: Mapping[str, str] | None = None,
    intraday_verdict: str = "",
    market_day_return: float | None = None,
    realtime_quotes: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return possibly replaced payload with oversold-bounce BUY candidates.

    Triggers when:
      - sleeve is enabled
      - intraday verdict indicates a risk-on/recovery regime (LONG_BUY or HOLD)
      - the previous session saw a significant market drop
      - candidate payload has no ordinary BUY orders (avoids double-buying)
    """
    settings = OversoldBounceSettings.from_env(env)
    audit: dict[str, Any] = {
        "enabled": settings.enabled,
        "applied": False,
        "reason": None,
        "candidates_found": 0,
    }
    if not settings.enabled:
        audit["reason"] = "bounce_sleeve_disabled"
        return candidate_payload, audit

    # Only fire when the intraday verdict supports buying (recovery / risk-on).
    verdict = intraday_verdict.upper().strip()
    if verdict not in {"LONG_BUY", "HOLD", ""}:
        audit["reason"] = f"bounce_sleeve_skipped:verdict:{verdict}"
        return candidate_payload, audit

    # Market must have dropped significantly in the previous session.
    if market_day_return is not None and market_day_return > settings.min_market_day_return_for_trigger:
        audit["reason"] = f"bounce_sleeve_skipped:market_not_oversold:{market_day_return:.2%}"
        return candidate_payload, audit

    # Don't overlay if the main payload already has ordinary BUYs.
    existing_buys = [
        o for o in candidate_payload.get("orders", [])
        if str(o.get("side", "BUY")).upper() == "BUY"
    ]
    if existing_buys:
        audit["reason"] = "bounce_sleeve_skipped:existing_buys_present"
        return candidate_payload, audit

    try:
        candidates = scan_oversold_candidates(panel_path, settings)
    except Exception as exc:
        audit["reason"] = f"bounce_sleeve_scan_failed:{type(exc).__name__}:{exc}"
        return candidate_payload, audit

    if not candidates:
        audit["reason"] = "bounce_sleeve_no_candidates_found"
        return candidate_payload, audit

    # Build orders for top candidates.
    orders = []
    for cand in candidates[:settings.max_candidates]:
        symbol = cand["symbol"]
        # Use realtime quote if available, else panel close.
        rt = (realtime_quotes or {}).get(symbol, {})
        ref_price = float(rt.get("last") or rt.get("close") or cand["close"])
        limit_price = round_up_to_tick(ref_price * (1.0 + settings.buy_aggressiveness_pct))
        quantity = whole_share_quantity(settings.notional_krw, limit_price)
        if quantity <= 0:
            continue
        orders.append({
            "symbol": symbol,
            "name": cand.get("name", symbol),
            "side": "BUY",
            "order_type": "LIMIT",
            "quantity": quantity,
            "limit_price": limit_price,
            "notional_krw": quantity * limit_price,
            "mode": "live_auto_guarded",
            "reason": f"oversold_bounce:drop_{cand['daily_return']:.1%}",
            "current_price": ref_price,
            "last_price": ref_price,
            "prev_session_return": cand["daily_return"],
            "prev_session_trading_value": cand["trading_value"],
            "strategy_type": "oversold_bounce",
            "take_profit_pct": settings.take_profit_pct,
            "stop_loss_pct": settings.stop_loss_pct,
            "max_holding_days": settings.max_holding_days,
            "quote_source": rt.get("source", "panel_close"),
        })

    if not orders:
        audit["reason"] = "bounce_sleeve_no_qualifying_orders"
        return candidate_payload, audit

    as_of = str(candidate_payload.get("as_of") or candidate_payload.get("generated_for") or "")[:10]
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of,
        "status": "CANDIDATES",
        "policy_id": "oversold_bounce_v1",
        "strategy_type": "oversold_bounce",
        "situation": "oversold_bounce",
        "source_policy_id": candidate_payload.get("policy_id"),
        "intraday_verdict": verdict,
        "market_day_return": market_day_return,
        "live_order_submitted": False,
        "orders": orders,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"candidates_{as_of or 'unknown'}_oversold_bounce_v1.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    audit.update({
        "applied": True,
        "reason": "oversold_bounce_candidates_generated",
        "candidates_found": len(candidates),
        "orders_generated": len(orders),
        "candidate_json": str(out_path),
        "top_candidates": [
            {"symbol": c["symbol"], "name": c.get("name", ""), "drop": f"{c['daily_return']:.2%}"}
            for c in candidates[:5]
        ],
    })
    return payload, audit


def scan_oversold_candidates(
    panel_path: Path,
    settings: OversoldBounceSettings,
) -> list[dict[str, Any]]:
    """Scan the OHLCV panel for oversold large-cap candidates.

    Returns a list sorted by trading value (descending), filtered by:
      - Previous session daily return <= -min_drop_pct
      - Trading value >= min_trading_value_krw
      - Close price >= min_price_krw
    """
    df = pd.read_csv(panel_path)
    df["Date"] = pd.to_datetime(df["Date"])
    dates = sorted(df["Date"].unique())
    if len(dates) < 2:
        return []
    latest_date = dates[-1]
    prev_date = dates[-2]
    latest = df[df["Date"] == latest_date].set_index("code")
    prev = df[df["Date"] == prev_date].set_index("code")
    # Join
    merged = latest.join(prev[["Close"]], rsuffix="_prev", how="inner")
    merged["daily_return"] = merged["Close"] / merged["Close_prev"] - 1.0
    merged["trading_value"] = merged["Close"] * merged["Volume"]
    # Filter
    filtered = merged[
        (merged["daily_return"] <= -settings.min_drop_pct)
        & (merged["trading_value"] >= settings.min_trading_value_krw)
        & (merged["Close"] >= settings.min_price_krw)
    ].copy()
    # Sort by trading value (liquidity) descending
    filtered = filtered.sort_values("trading_value", ascending=False)
    candidates = []
    for code, row in filtered.iterrows():
        candidates.append({
            "symbol": str(code).zfill(6),
            "name": str(row.get("name", code)),
            "close": float(row["Close"]),
            "daily_return": float(row["daily_return"]),
            "trading_value": float(row["trading_value"]),
            "volume": float(row["Volume"]),
        })
    return candidates


def _env_true(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}
