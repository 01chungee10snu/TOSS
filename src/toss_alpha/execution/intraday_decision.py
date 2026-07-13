"""Pure, fail-closed intraday decision engine for the guarded live loop."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from uuid import uuid4

INVERSE_SYMBOLS = {"114800", "251340", "252670"}
RISK_OFF_DAILY = {"down_high_vol", "flat_high_vol", "risk_off"}
HIGH_NEWS = {"high", "critical"}


def evaluate_intraday_decision(
    *,
    daily_regime: str | None,
    news_severity: str | None,
    market_quotes: Mapping[str, Mapping[str, Any]],
    positions: Sequence[Mapping[str, Any]] = (),
    now: datetime | None = None,
    max_quote_age_seconds: int = 300,
    news_observed_at: Any = None,
    max_news_age_seconds: int = 1200,
    require_fresh_news: bool = False,
    market_symbol: str = "069500",
    inverse_symbol: str = "114800",
) -> dict[str, Any]:
    """Return HOLD/LONG_BUY/INVERSE_BUY/SELL/NO_TRADE with audit evidence.

    KODEX 200 and KODEX Inverse are independent exchange-traded market proxies.
    Every directional transition requires both quotes to be fresh and internally
    consistent. Missing or contradictory data never authorises a live order.
    """
    now = now or datetime.now(timezone.utc)
    daily = str(daily_regime or "unknown").strip()
    news = str(news_severity or "unknown").strip().lower()
    base = {
        "decision_id": f"intraday-{uuid4().hex}",
        "generated_at_utc": now.astimezone(timezone.utc).isoformat(),
        "daily_regime": daily,
        "news_severity": news,
        "evidence_status": "MISSING",
        "signal_conflict": False,
        "regime_liquidation_allowed": False,
        "market_regime": None,
        "sell_symbols": [],
        "market_symbol": market_symbol,
        "inverse_symbol": inverse_symbol,
    }

    news_observed = _parse_datetime(news_observed_at)
    if require_fresh_news:
        if news_observed is None:
            return {**base, "verdict": "NO_TRADE", "reason": "news_evidence_unavailable", "news_evidence_status": "MISSING"}
        news_age = (now.astimezone(timezone.utc) - news_observed.astimezone(timezone.utc)).total_seconds()
        if news_age < -30 or news_age > max_news_age_seconds:
            return {
                **base,
                "verdict": "NO_TRADE",
                "reason": "news_evidence_unavailable",
                "news_evidence_status": "STALE",
                "news_age_seconds": news_age,
            }
        base["news_evidence_status"] = "FRESH"
        base["news_age_seconds"] = news_age

    parsed: dict[str, dict[str, float]] = {}
    errors: list[str] = []
    for symbol in (market_symbol, inverse_symbol):
        quote = market_quotes.get(symbol)
        values, error = _validate_quote(quote, now=now, max_age_seconds=max_quote_age_seconds)
        if error:
            errors.append(f"{symbol}:{error}")
        else:
            parsed[symbol] = values
    if errors:
        return {**base, "verdict": "NO_TRADE", "reason": "market_evidence_unavailable", "evidence_errors": errors}

    market = parsed[market_symbol]
    inverse = parsed[inverse_symbol]
    market_day = market["last"] / market["prev_close"] - 1.0
    market_open = market["last"] / market["open"] - 1.0
    inverse_day = inverse["last"] / inverse["prev_close"] - 1.0
    max_abs_day_return = 0.08
    if abs(market_day) > max_abs_day_return or abs(inverse_day) > max_abs_day_return:
        return {
            **base,
            "verdict": "NO_TRADE",
            "reason": "quote_basis_inconsistent",
            "evidence_status": "INVALID",
            "metrics": {
                "market_day_return": market_day,
                "market_open_return": market_open,
                "inverse_day_return": inverse_day,
                "max_abs_day_return": max_abs_day_return,
            },
            "raw_quotes": {market_symbol: dict(market_quotes[market_symbol]), inverse_symbol: dict(market_quotes[inverse_symbol])},
        }
    bullish = (market_day >= 0.005 or market_open >= 0.003) and inverse_day <= 0.001
    bearish = market_day <= -0.005 and market_open <= -0.003 and inverse_day >= 0.003
    risk_context = daily in RISK_OFF_DAILY or news in HIGH_NEWS

    ordinary = [p for p in positions if str(p.get("symbol") or "").zfill(6) not in INVERSE_SYMBOLS]
    inverse_positions = [p for p in positions if str(p.get("symbol") or "").zfill(6) in INVERSE_SYMBOLS]
    weak_symbols = _weak_position_symbols(ordinary)
    metrics = {
        "market_day_return": market_day,
        "market_open_return": market_open,
        "inverse_day_return": inverse_day,
        "bullish_confirmed": bullish,
        "bearish_confirmed": bearish,
        "risk_context": risk_context,
    }
    ready = {
        **base,
        "evidence_status": "FRESH",
        "metrics": metrics,
        "raw_quotes": {market_symbol: dict(market_quotes[market_symbol]), inverse_symbol: dict(market_quotes[inverse_symbol])},
    }

    if risk_context and bullish:
        verdict = "HOLD" if positions else "NO_TRADE"
        return {**ready, "verdict": verdict, "reason": "risk_context_conflicts_with_intraday_strength", "signal_conflict": True}
    if risk_context and bearish:
        if weak_symbols:
            return {**ready, "verdict": "SELL", "reason": "risk_off_confirmed_and_positions_weak", "market_regime": "risk_off", "sell_symbols": weak_symbols}
        if ordinary:
            return {**ready, "verdict": "HOLD", "reason": "risk_off_confirmed_but_holdings_show_relative_strength", "market_regime": "risk_off"}
        if inverse_positions:
            return {**ready, "verdict": "HOLD", "reason": "risk_off_confirmed_inverse_already_held", "market_regime": "risk_off"}
        return {**ready, "verdict": "INVERSE_BUY", "reason": "risk_off_confirmed_by_intraday_market", "market_regime": "risk_off"}
    if inverse_positions and bullish:
        return {
            **ready,
            "verdict": "SELL",
            "reason": "intraday_recovery_unwind_inverse",
            "market_regime": "risk_on",
            "sell_symbols": sorted({str(p.get("symbol")).zfill(6) for p in inverse_positions}),
        }
    if not risk_context and bullish:
        return {**ready, "verdict": "HOLD" if positions else "LONG_BUY", "reason": "intraday_strength_confirmed"}
    if bearish:
        return {**ready, "verdict": "HOLD" if positions else "NO_TRADE", "reason": "intraday_weakness_without_independent_risk_context"}
    return {**ready, "verdict": "HOLD" if positions else "NO_TRADE", "reason": "intraday_direction_unconfirmed"}


def apply_intraday_decision(candidate_payload: Mapping[str, Any], decision: Mapping[str, Any]) -> dict[str, Any]:
    """Attach the decision and retain only BUYs authorised by its verdict."""
    result = dict(candidate_payload)
    result["intraday_decision"] = dict(decision)
    verdict = str(decision.get("verdict") or "NO_TRADE")
    orders = list(result.get("orders") or [])
    if verdict == "LONG_BUY":
        orders = [o for o in orders if str(o.get("side", "BUY")).upper() != "BUY" or str(o.get("symbol") or "").zfill(6) not in INVERSE_SYMBOLS]
    elif verdict == "INVERSE_BUY":
        orders = [o for o in orders if str(o.get("side", "BUY")).upper() != "BUY" or str(o.get("symbol") or "").zfill(6) in INVERSE_SYMBOLS]
    else:
        orders = [o for o in orders if str(o.get("side", "BUY")).upper() != "BUY"]
    result["orders"] = orders
    if not orders and result.get("status") == "CANDIDATES":
        result["status"] = "NO_TRADE"
        result["reason"] = f"intraday_decision:{verdict}:{decision.get('reason', 'unspecified')}"
    return result


def _validate_quote(
    quote: Mapping[str, Any] | None,
    *,
    now: datetime,
    max_age_seconds: int,
) -> tuple[dict[str, float], str | None]:
    if not isinstance(quote, Mapping):
        return {}, "missing"
    try:
        last = float(quote.get("last") or 0)
        open_price = float(quote.get("open") or 0)
        prev_close = float(quote.get("prev_close") or 0)
    except (TypeError, ValueError):
        return {}, "invalid_price"
    if min(last, open_price, prev_close) <= 0:
        return {}, "non_positive_price"
    observed = _parse_datetime(quote.get("observed_at") or quote.get("timestamp"))
    if observed is None:
        return {}, "timestamp_missing"
    age = (now.astimezone(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()
    if age < -30 or age > max_age_seconds:
        return {}, "stale"
    return {"last": last, "open": open_price, "prev_close": prev_close, "age_seconds": age}, None


def _weak_position_symbols(positions: Sequence[Mapping[str, Any]]) -> list[str]:
    weak: list[str] = []
    for position in positions:
        try:
            last = float(position.get("last") or 0)
            open_price = float(position.get("open") or 0)
            prev_close = float(position.get("prev_close") or 0)
            avg_price = float(position.get("avg_price") or 0)
        except (TypeError, ValueError):
            continue
        if min(last, open_price, prev_close, avg_price) <= 0:
            continue
        day_return = last / prev_close - 1.0
        open_return = last / open_price - 1.0
        pnl_return = last / avg_price - 1.0
        if day_return <= -0.01 and open_return <= -0.005 and pnl_return <= 0:
            weak.append(str(position.get("symbol") or "").zfill(6))
    return sorted(set(weak))


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
