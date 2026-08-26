"""Fail-closed adaptive market-regime router for shadow research.

This module never creates executable orders. It combines already-produced,
auditable evidence into a shadow candidate plan so regime-specific strategies can
be forward-tested before any live promotion.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

INVERSE_1X = "114800"
LONG_REGIMES = {"up_low_vol", "flat_low_vol", "up_high_vol", "flat_high_vol"}


def build_shadow_plan(
    *,
    now: datetime,
    intraday_decision: Mapping[str, Any],
    forward_report: Mapping[str, Any],
    sector_screen: Mapping[str, Any],
    equity_guard: Mapping[str, Any],
    performance_gate: Mapping[str, Any],
    max_notional_krw: float = 100_000,
    max_intraday_age_seconds: int = 300,
) -> dict[str, Any]:
    """Build a non-executable regime plan from fresh upstream evidence."""
    now_utc = _aware(now).astimezone(timezone.utc)
    reasons: list[str] = []
    base = {
        "generated_at_utc": now_utc.isoformat(),
        "execution_stage": "shadow_only",
        "live_order_submitted": False,
        "status": "NO_TRADE",
        "strategy": "cash",
        "orders": [],
        "reasons": reasons,
        "evidence": {
            "intraday_decision_id": intraday_decision.get("decision_id"),
            "daily_regime": intraday_decision.get("daily_regime"),
            "intraday_verdict": intraday_decision.get("verdict"),
            "forward_predict_date": forward_report.get("predict_date"),
            "macro_regime": (forward_report.get("macro_regime") or {}).get("status"),
        },
    }

    if bool(equity_guard.get("block_new_buys")):
        reasons.append("equity_guard_block")
    if bool(performance_gate.get("block_new_buys")):
        reasons.append("performance_gate_block")
    if reasons:
        return base

    if str(intraday_decision.get("evidence_status") or "").upper() != "FRESH":
        reasons.append("intraday_evidence_not_fresh")
        return base
    if str(intraday_decision.get("news_evidence_status") or "").upper() != "FRESH":
        reasons.append("news_evidence_not_fresh")
        return base
    observed = _parse_datetime(intraday_decision.get("generated_at_utc"))
    if observed is None:
        reasons.append("missing_intraday_timestamp")
        return base
    age = (now_utc - observed.astimezone(timezone.utc)).total_seconds()
    if age < -30 or age > max_intraday_age_seconds:
        reasons.append("stale_intraday_decision")
        return base

    verdict = str(intraday_decision.get("verdict") or "NO_TRADE").upper()
    regime = str(intraday_decision.get("daily_regime") or "unknown")
    notional = max(0.0, min(float(max_notional_krw), 250_000.0))

    if verdict == "INVERSE_BUY":
        # Direct ETF backtests fail every promotion gate.  Keep the market
        # evidence for audit, but cash is the only authorised research action
        # until a PIT, instrument-matched validation explicitly promotes it.
        reasons.append("inverse_strategy_not_validated")
        return base

    if verdict != "LONG_BUY" or regime not in LONG_REGIMES:
        reasons.append(f"intraday_verdict:{verdict}")
        return base

    forward = _quality_forward_candidates(forward_report)
    if regime == "up_high_vol":
        leaders = _confirmed_sector_leaders(sector_screen)
        selected = [item for item in forward if item["symbol"] in leaders]
        if selected:
            base.update(
                status="SHADOW_CANDIDATES",
                strategy="sector_momentum",
                orders=[
                    _shadow_order(
                        item["symbol"],
                        item["name"],
                        notional,
                        "forward_rank_and_sector_breadth_confirmation",
                        reference_price=item["price"],
                    )
                    for item in selected[:2]
                ],
            )
            return base
        reasons.append("no_confirmed_sector_leaders")
        return base

    if forward:
        base.update(
            status="SHADOW_CANDIDATES",
            strategy="fusion_long",
            orders=[
                _shadow_order(
                    item["symbol"],
                    item["name"],
                    notional,
                    "fusion_forward_quality_candidate",
                    reference_price=item["price"],
                )
                for item in forward[:2]
            ],
        )
        return base

    reasons.append("no_quality_long_candidates")
    return base


def _quality_forward_candidates(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in report.get("top10") or []:
        try:
            symbol = str(raw.get("code") or "").zfill(6)
            price = float(raw.get("close") or 0)
            volume = float(raw.get("volume") or 0)
            score = float(raw.get("ml_score") or 0)
        except (TypeError, ValueError):
            continue
        if len(symbol) != 6 or price <= 0 or volume <= 0 or score <= 0:
            continue
        rows.append({"symbol": symbol, "name": str(raw.get("name") or symbol), "price": price, "score": score})
    rows.sort(key=lambda item: item["score"], reverse=True)
    return rows


def _confirmed_sector_leaders(screen: Mapping[str, Any]) -> set[str]:
    confirmed: set[str] = set()
    for group in screen.get("drill_down") or []:
        try:
            sector_return = float(group.get("sector_return") or 0)
        except (TypeError, ValueError):
            continue
        if sector_return < 0.01:
            continue
        positive = []
        for stock in group.get("stocks") or []:
            try:
                symbol = str(stock.get("code") or "").zfill(6)
                day_return = float(stock.get("day_return") or 0)
                last = float(stock.get("last") or 0)
                volume = float(stock.get("volume") or 0)
            except (TypeError, ValueError):
                continue
            if len(symbol) == 6 and day_return >= 0.005 and last > 0 and volume > 0:
                positive.append(symbol)
        if len(positive) >= 2:
            confirmed.update(positive)
    return confirmed


def _shadow_order(
    symbol: str,
    name: str,
    notional: float,
    reason: str,
    *,
    reference_price: float | None = None,
) -> dict[str, Any]:
    return {
        "symbol": str(symbol).zfill(6),
        "name": name,
        "side": "BUY",
        "quantity": 0,
        "shadow_notional_krw": round(float(notional), 2),
        "reference_price": reference_price,
        "reason": reason,
        "submit_allowed": False,
    }


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _aware(value)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware(parsed)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
