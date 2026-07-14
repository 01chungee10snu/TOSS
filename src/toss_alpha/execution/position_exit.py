"""Build guarded SELL candidates from real broker position snapshots.

This module is intentionally order-construction only. It never submits orders;
all output must still pass ``run_live_submit_phase`` and its broker/risk/time
safety gates.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from toss_alpha.connectors.kis_readonly import KisReadOnlyClient
from toss_alpha.data.schema import AccountSnapshot, PositionSnapshot, Quote
from toss_alpha.execution.inverse_sleeve import DEFAULT_ETF_CODE, LEVERAGED_INVERSE_ETF_CODES
from toss_alpha.execution.krx_calendar import is_krx_trading_day
from toss_alpha.execution.live_ready import LiveExecutionConfig


KST = ZoneInfo("Asia/Seoul")


def _env_true(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(env: Mapping[str, str], key: str, default: float) -> float:
    try:
        return float(env.get(key, default))
    except (TypeError, ValueError):
        return default


def _env_int(env: Mapping[str, str], key: str, default: int) -> int:
    try:
        return int(float(env.get(key, default)))
    except (TypeError, ValueError):
        return default


def _round_krw_price(value: float) -> int:
    return max(1, int(round(float(value))))


def _current_price(position: PositionSnapshot) -> float | None:
    if position.quantity and position.market_value is not None and float(position.quantity) > 0:
        return float(position.market_value) / float(position.quantity)
    return None


def _position_snapshot_dict(position: PositionSnapshot) -> dict[str, Any]:
    data = asdict(position)
    as_of = data.get("as_of")
    if isinstance(as_of, datetime):
        data["as_of"] = as_of.isoformat()
    return data


def _account_snapshot_dict(account: AccountSnapshot | None) -> dict[str, Any] | None:
    if account is None:
        return None
    data = asdict(account)
    as_of = data.get("as_of")
    if isinstance(as_of, datetime):
        data["as_of"] = as_of.isoformat()
    return data


def _portfolio_equity(account: AccountSnapshot | None, positions: list[PositionSnapshot]) -> float | None:
    if account and account.total_equity is not None:
        return float(account.total_equity)
    position_value = sum(float(p.market_value or 0.0) for p in positions)
    cash = float(account.cash) if account and account.cash is not None else 0.0
    equity = cash + position_value
    return equity if equity > 0 else None


def _load_guard_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"_state_corrupt": True}
    except Exception:
        return {"_state_corrupt": True}


# ── Position-level tracker: peak price & first-seen date per holding ───────

def _load_position_tracker(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_position_tracker(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temp_path.replace(path)


def _trading_days_between(start: date, end: date, *, env: Mapping[str, str] | None = None) -> int:
    """Count KRX trading days in (start, end], exclusive of start."""
    if end <= start:
        return 0
    count = 0
    cur = start
    from datetime import timedelta
    while cur < end:
        cur += timedelta(days=1)
        if is_krx_trading_day(cur, env=env):
            count += 1
    return count


def _env_cooldown_seconds(source: Mapping[str, str]) -> tuple[int, str]:
    """Resolve cooldown duration in seconds from env.

    Backtest uses ``risk_cooldown_steps`` where 1 step == 1 trading day. In live
    mode the loop may run multiple times per day, so a step counter would shrink
    the intended cooldown by the number of intraday ticks. We therefore express
    cooldown as an absolute wall-clock duration.

    Priority: ``TOSS_EQUITY_GUARD_COOLDOWN_SECONDS`` >
    ``TOSS_EQUITY_GUARD_COOLDOWN_HOURS`` >
    ``TOSS_EQUITY_GUARD_COOLDOWN_DAYS`` (default 8, matching 8 trading days).
    """
    if source.get("TOSS_EQUITY_GUARD_COOLDOWN_SECONDS"):
        return int(_env_float(source, "TOSS_EQUITY_GUARD_COOLDOWN_SECONDS", 0)), "seconds"
    if source.get("TOSS_EQUITY_GUARD_COOLDOWN_HOURS"):
        return int(_env_float(source, "TOSS_EQUITY_GUARD_COOLDOWN_HOURS", 0) * 3600), "hours"
    days = int(_env_float(source, "TOSS_EQUITY_GUARD_COOLDOWN_DAYS", 8))
    return days * 86400, "days"


def evaluate_account_equity_guard(
    account: AccountSnapshot | None,
    positions: list[PositionSnapshot],
    *,
    report_dir: str | Path,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Evaluate live account-level drawdown guard and persist peak/cooldown state.

    This is the live counterpart of replay's ``max_equity_drawdown_stop_pct``:
    when account equity falls from the recorded peak by the configured threshold,
    new BUY orders are blocked and held positions are marked for liquidation.

    Cooldown is wall-clock based (absolute ``cooldown_until_ts`` epoch) so that
    intraday loop frequency does not change the intended cooldown duration.
    """
    source = env or {}
    enabled = _env_true(source.get("TOSS_EQUITY_GUARD_ENABLED"), default=True)
    threshold = _env_float(source, "TOSS_EQUITY_DRAWDOWN_STOP_PCT", 0.06)
    cooldown_seconds, cooldown_unit = _env_cooldown_seconds(source)
    state_path = Path(report_dir) / "live_equity_guard_state.json"
    now_epoch = datetime.now(timezone.utc).timestamp()
    audit: dict[str, Any] = {
        "enabled": enabled,
        "threshold_pct": threshold,
        "cooldown_seconds": cooldown_seconds,
        "cooldown_unit": cooldown_unit,
        "state_path": str(state_path),
        "block_new_buys": False,
        "liquidation_required": False,
    }
    if not enabled:
        audit["status"] = "DISABLED"
        return audit
    equity = _portfolio_equity(account, positions)
    audit["account"] = _account_snapshot_dict(account)
    audit["current_equity"] = equity
    if equity is None or not math.isfinite(equity) or equity <= 0:
        audit["status"] = "BLOCKED_MISSING_EQUITY"
        audit["block_new_buys"] = True
        return audit
    state = _load_guard_state(state_path)
    if state.get("_state_corrupt"):
        audit["status"] = "BLOCKED_CORRUPT_GUARD_STATE"
        audit["block_new_buys"] = True
        return audit
    previous_peak = float(state.get("peak_equity") or equity)
    if not math.isfinite(previous_peak) or previous_peak <= 0:
        audit["status"] = "BLOCKED_CORRUPT_GUARD_STATE"
        audit["block_new_buys"] = True
        return audit
    peak = max(previous_peak, equity)
    drawdown = equity / peak - 1.0 if peak > 0 else 0.0
    triggered = drawdown <= -threshold
    # Wall-clock cooldown: absolute epoch stored on trigger.
    prev_cooldown_until = float(state.get("cooldown_until_ts") or 0.0)
    cooldown_active = (not triggered) and prev_cooldown_until > now_epoch
    next_cooldown_until = (now_epoch + cooldown_seconds) if triggered else prev_cooldown_until
    # Effective remaining covers both trigger and ongoing cooldown.
    effective_remaining = max(0, int(next_cooldown_until - now_epoch)) if (triggered or cooldown_active) else 0
    block_new_buys = triggered or cooldown_active
    liquidation_required = block_new_buys and any(float(p.sellable_quantity or p.quantity or 0.0) > 0 for p in positions)
    status = "TRIGGERED" if triggered else ("COOLDOWN" if cooldown_active else "READY")
    next_state = {
        "peak_equity": peak if not triggered else equity,
        "last_equity": equity,
        "last_drawdown": drawdown,
        "cooldown_until_ts": next_cooldown_until,
        "cooldown_remaining_seconds": effective_remaining,
        "status": status,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(next_state, ensure_ascii=False, indent=2), encoding="utf-8")
    audit.update(
        {
            "status": status,
            "peak_equity": peak,
            "drawdown_pct": drawdown,
            "cooldown_active": cooldown_active,
            "cooldown_remaining_seconds": effective_remaining,
            "cooldown_until_ts": next_cooldown_until,
            "block_new_buys": block_new_buys,
            "liquidation_required": liquidation_required,
        }
    )
    return audit


def _without_buy_orders(candidate_payload: dict[str, Any], reason: str) -> dict[str, Any]:
    blocked = dict(candidate_payload or {})
    orders = list(blocked.get("orders") or [])
    kept = [o for o in orders if str(o.get("side", "BUY")).upper() != "BUY"]
    blocked["orders"] = kept
    blocked["buy_gate_blocked"] = True
    blocked["buy_gate_reason"] = reason
    blocked["equity_guard_buy_blocked"] = True
    blocked["equity_guard_reason"] = reason
    if not kept:
        blocked["status"] = "NO_TRADE"
        blocked["reason"] = reason
    return blocked


def position_quote_invalid_reason(quote: Quote) -> str | None:
    """Return a fail-closed reason when a held-symbol quote cannot price an exit."""
    try:
        last = float(quote.last)
    except (TypeError, ValueError):
        return "invalid_position_quote:last_missing"
    if not math.isfinite(last) or last <= 0:
        return "invalid_position_quote:last_nonpositive"
    try:
        bid = float(quote.bid) if quote.bid is not None else None
    except (TypeError, ValueError):
        return "invalid_position_quote:bid_invalid"
    if bid is None:
        return "invalid_position_quote:bid_missing"
    if not math.isfinite(bid) or bid <= 0:
        return "invalid_position_quote:bid_nonpositive"
    if str(quote.source or "").lower() != "kis":
        return "invalid_position_quote:source_not_kis"
    return None


def block_buys_for_position_quote_errors(candidate_payload: dict[str, Any], quote_errors: Mapping[str, str]) -> dict[str, Any]:
    """Preserve SELLs but block every new BUY when any held-symbol quote failed."""
    if not quote_errors:
        return candidate_payload
    return _without_buy_orders(candidate_payload, "position_exit_quote_unavailable")


def build_position_exit_orders(
    positions: list[PositionSnapshot],
    *,
    env: Mapping[str, str] | None = None,
    as_of: str | None = None,
    report_dir: str | Path | None = None,
    market_regime: str | None = None,
    realtime_quotes: Mapping[str, Quote] | None = None,
    require_realtime_quotes: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return SELL orders for held positions that hit configured exit rules.

    Supported rules (mirrors backtest replay exit logic):
    - stop loss: current price <= avg_price * (1 - stop_loss_pct)
    - take profit: current price >= avg_price * (1 + take_profit_pct)
    - trailing stop: current price <= peak_price * (1 - trailing_stop_pct)
    - time exit: trading days held >= max_holding_trading_days
    - regime risk_off: market regime is risk_off → liquidate all
    - optional explicit force-exit symbols via ``TOSS_FORCE_EXIT_SYMBOLS``
    """
    source = env or {}
    stop_loss_pct = _env_float(source, "TOSS_POSITION_STOP_LOSS_PCT", 0.06)
    take_profit_pct = _env_float(source, "TOSS_POSITION_TAKE_PROFIT_PCT", 0.25)
    trailing_stop_pct = _env_float(source, "TOSS_POSITION_TRAILING_STOP_PCT", 0.0)
    inverse_stop_loss_pct = _env_float(
        source,
        "TOSS_INVERSE_STOP_LOSS_PCT",
        _env_float(source, "TOSS_POSITION_STOP_LOSS_PCT", 0.025) if "TOSS_POSITION_STOP_LOSS_PCT" in source else 0.025,
    )
    inverse_profit_lock_activation_pct = _env_float(source, "TOSS_INVERSE_PROFIT_LOCK_ACTIVATION_PCT", 0.015)
    inverse_profit_floor_pct = _env_float(source, "TOSS_INVERSE_PROFIT_FLOOR_PCT", 0.002)
    inverse_partial_1_pct = _env_float(source, "TOSS_INVERSE_PARTIAL_1_PCT", 0.025)
    inverse_partial_2_pct = _env_float(source, "TOSS_INVERSE_PARTIAL_2_PCT", 0.04)
    inverse_partial_fraction = min(0.49, max(0.01, _env_float(source, "TOSS_INVERSE_PARTIAL_FRACTION", 0.33)))
    inverse_trailing_stop_pct = _env_float(source, "TOSS_INVERSE_TRAILING_STOP_PCT", 0.015)
    max_holding_days = _env_int(source, "TOSS_POSITION_MAX_HOLDING_DAYS", 0)
    risk_off_exit = _env_true(source.get("TOSS_POSITION_RISK_OFF_EXIT"), default=False)
    risk_off_regimes = {
        item.strip()
        for item in str(source.get(
            "TOSS_POSITION_RISK_OFF_REGIMES",
            "risk_off,inverse_sleeve_risk_off,down_high_vol,flat_high_vol",
        )).split(",")
        if item.strip()
    }
    inverse_hedge_symbols = {
        DEFAULT_ETF_CODE,
        *LEVERAGED_INVERSE_ETF_CODES,
        str(source.get("TOSS_INVERSE_ETF_CODE", DEFAULT_ETF_CODE)).strip().zfill(6),
    }
    forced_symbols = {
        str(item).strip().zfill(6)
        for item in str(source.get("TOSS_FORCE_EXIT_SYMBOLS", "")).split(",")
        if str(item).strip()
    }
    force_all = _env_true(source.get("TOSS_FORCE_EXIT_ALL"), default=False)

    # Load position tracker for peak prices and first-seen dates.
    tracker_data: dict[str, Any] = {}
    tracker_path: Path | None = None
    if report_dir is not None:
        tracker_path = Path(report_dir) / "live_position_tracker.json"
        tracker_data = _load_position_tracker(tracker_path)

    today = datetime.now(KST).date()
    orders: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    updated_tracker = dict(tracker_data)

    for position in positions:
        symbol = str(position.symbol).zfill(6)
        quantity = float(position.quantity or 0.0)
        sellable_quantity = position.sellable_quantity
        avg_price = position.avg_price
        quote = (realtime_quotes or {}).get(symbol)
        if quote is not None and quote.last is not None and float(quote.last) > 0:
            current = float(quote.last)
            exit_limit_price = float(quote.bid) if quote.bid is not None and float(quote.bid) > 0 else None
            quote_source = quote.source or "kis"
            quote_observed_at = quote.timestamp.isoformat() if quote.timestamp else None
        elif require_realtime_quotes:
            current = None
            exit_limit_price = None
            quote_source = None
            quote_observed_at = None
        else:
            current = _current_price(position)
            exit_limit_price = current
            quote_source = "position_market_value_derived"
            quote_observed_at = position.as_of.isoformat() if position.as_of else None
        reasons: list[str] = []
        partial_stage: str | None = None
        partial_quantity: int | None = None
        protected_price: float | None = None

        # Update / read tracker state for this symbol. A changed average cost or
        # increased quantity indicates a new/blended lifecycle; stale peaks and
        # holding ages must not carry into the new exposure.
        pos_state = updated_tracker.get(symbol, {})
        stored_avg = pos_state.get("avg_price")
        stored_qty = pos_state.get("quantity")
        lifecycle_changed = (
            stored_avg is not None
            and avg_price is not None
            and not math.isclose(float(stored_avg), float(avg_price), rel_tol=1e-6, abs_tol=1e-6)
        ) or (
            stored_qty is not None
            and quantity > float(stored_qty) + 1e-9
        )
        if lifecycle_changed:
            pos_state = {}
            updated_tracker[symbol] = pos_state
        prev_peak = pos_state.get("peak_price")
        first_seen = pos_state.get("first_seen_date")
        lifecycle_id = str(pos_state.get("lifecycle_id") or "")
        if not lifecycle_id:
            lifecycle_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            updated_tracker.setdefault(symbol, {})["lifecycle_id"] = lifecycle_id
        initial_quantity = float(pos_state.get("initial_quantity") or quantity)
        updated_tracker.setdefault(symbol, {})["initial_quantity"] = initial_quantity

        if current is not None:
            # Persisted tracker state survives process restarts. When it already
            # exists, the official KIS session high repairs peaks missed during
            # watchdog downtime without applying a pre-entry high to a new trade.
            session_high = getattr(quote, "session_high", None) if quote is not None else None
            peak_candidate = float(current)
            if pos_state and prev_peak is not None and session_high is not None and float(session_high) > 0:
                peak_candidate = max(peak_candidate, float(session_high))
            if prev_peak is None or peak_candidate > float(prev_peak):
                updated_tracker[symbol] = dict(updated_tracker.get(symbol, pos_state), peak_price=peak_candidate)
                pos_state = updated_tracker[symbol]
                prev_peak = peak_candidate
            # First-seen date init.
            if first_seen is None:
                updated_tracker[symbol]["first_seen_date"] = today.isoformat()
                pos_state = updated_tracker[symbol]
                first_seen = today.isoformat()
        updated_tracker.setdefault(symbol, {})["quantity"] = quantity
        updated_tracker[symbol]["avg_price"] = avg_price

        # --- exit rule evaluation ---
        if symbol in forced_symbols:
            reasons.append("forced_exit_symbol")
        if force_all:
            reasons.append("equity_drawdown_stop")
        is_inverse_hedge = symbol in inverse_hedge_symbols
        if risk_off_exit and market_regime in risk_off_regimes:
            # The inverse sleeve is the hedge for a risk-off regime. Applying
            # the ordinary-long liquidation rule to it creates a buy-then-sell
            # loop while the same risk signal remains active.
            if not is_inverse_hedge:
                reasons.append("regime_risk_off")
        elif risk_off_exit and market_regime is not None and is_inverse_hedge:
            # Once independently classified market risk clears, unwind the
            # hedge through the same guarded SELL path.
            reasons.append("inverse_regime_recovery")
        if avg_price is not None and current is not None:
            avg = float(avg_price)
            if is_inverse_hedge:
                if current <= avg * (1.0 - inverse_stop_loss_pct):
                    reasons.append(f"inverse_stop_loss_{inverse_stop_loss_pct:.2%}")
                if prev_peak is not None and float(prev_peak) >= avg * (1.0 + inverse_profit_lock_activation_pct):
                    protected_price = max(
                        avg * (1.0 + inverse_profit_floor_pct),
                        float(prev_peak) * (1.0 - inverse_trailing_stop_pct),
                    )
                    if current <= protected_price:
                        reasons.append(f"inverse_profit_lock_{inverse_trailing_stop_pct:.2%}")
                # Stage completion is confirmed only by a broker position-size
                # reduction. Merely constructing/submitting an order never moves
                # the state machine forward.
                stage_qty = int(math.floor(initial_quantity * inverse_partial_fraction))
                sold_qty = max(0.0, initial_quantity - quantity)
                stage1_done = stage_qty > 0 and sold_qty + 1e-9 >= stage_qty
                stage2_done = stage_qty > 0 and sold_qty + 1e-9 >= stage_qty * 2
                gain = current / avg - 1.0
                if not reasons and stage_qty > 0:
                    if gain >= inverse_partial_1_pct and not stage1_done:
                        partial_stage = "inverse_profit_1"
                        partial_quantity = min(stage_qty, int(quantity), int(float(sellable_quantity or 0)))
                    elif gain >= inverse_partial_2_pct and stage1_done and not stage2_done:
                        partial_stage = "inverse_profit_2"
                        partial_quantity = min(stage_qty, int(quantity), int(float(sellable_quantity or 0)))
                if partial_stage and partial_quantity and partial_quantity > 0:
                    reasons.append(f"{partial_stage}_{gain:.2%}")
            else:
                if current <= avg * (1.0 - stop_loss_pct):
                    reasons.append(f"stop_loss_{stop_loss_pct:.2%}")
                if current >= avg * (1.0 + take_profit_pct):
                    reasons.append(f"take_profit_{take_profit_pct:.2%}")
                if (
                    trailing_stop_pct > 0
                    and prev_peak is not None
                    and float(prev_peak) > avg
                    and current <= float(prev_peak) * (1.0 - trailing_stop_pct)
                ):
                    reasons.append(f"trailing_stop_{trailing_stop_pct:.2%}")
        # Time exit: max holding trading days.
        if max_holding_days > 0 and first_seen is not None:
            try:
                start_date = date.fromisoformat(str(first_seen)[:10])
                held_days = _trading_days_between(start_date, today, env=source)
                if held_days >= max_holding_days:
                    reasons.append(f"time_exit_{max_holding_days}d")
            except (ValueError, TypeError):
                pass  # skip if first_seen malformed

        # Build review entry.
        peak_price = pos_state.get("peak_price")
        held_trading_days = None
        if first_seen:
            try:
                held_trading_days = _trading_days_between(date.fromisoformat(str(first_seen)[:10]), today, env=source)
            except (ValueError, TypeError):
                pass
        review = {
            "symbol": symbol,
            "quantity": quantity,
            "sellable_quantity": sellable_quantity,
            "avg_price": avg_price,
            "current_price": current,
            "quote_source": quote_source,
            "quote_observed_at": quote_observed_at,
            "best_bid": exit_limit_price,
            "peak_price": peak_price,
            "protected_price": protected_price,
            "initial_quantity": initial_quantity,
            "partial_stage": partial_stage,
            "partial_quantity": partial_quantity,
            "lifecycle_id": lifecycle_id,
            "first_seen_date": first_seen,
            "held_trading_days": held_trading_days,
            "market_value": position.market_value,
            "unrealized_pnl": position.unrealized_pnl,
            "reasons": reasons,
            "action": "SELL" if reasons else "HOLD",
        }
        reviews.append(review)
        if not reasons:
            continue
        if sellable_quantity is None or float(sellable_quantity) <= 0:
            review["action"] = "BLOCKED"
            review.setdefault("blocked_reasons", []).append("sellable_quantity_missing_or_zero")
            continue
        sell_qty = (
            min(float(partial_quantity), quantity, float(sellable_quantity))
            if partial_stage and partial_quantity
            else min(quantity, float(sellable_quantity))
        )
        if sell_qty <= 0 or current is None:
            review["action"] = "BLOCKED"
            review.setdefault("blocked_reasons", []).append("fresh_exit_quote_missing")
            continue
        if exit_limit_price is None or exit_limit_price <= 0:
            review["action"] = "BLOCKED"
            review.setdefault("blocked_reasons", []).append("fresh_exit_bid_missing")
            continue
        int_qty = int(sell_qty)
        if int_qty <= 0:
            review["action"] = "BLOCKED"
            review.setdefault("blocked_reasons", []).append("quantity_below_one_share")
            continue
        limit_price = _round_krw_price(exit_limit_price)
        orders.append(
            {
                "symbol": symbol,
                "side": "SELL",
                "order_type": "LIMIT",
                "quantity": int_qty,
                "sellable_quantity": float(sellable_quantity),
                "limit_price": limit_price,
                "current_price": current,
                "best_bid": exit_limit_price,
                "quote_source": quote_source,
                "quote_observed_at": quote_observed_at,
                "notional_krw": float(int_qty * limit_price),
                "mode": "live_auto_guarded",
                "reason": ",".join(reasons),
                "idempotency_scope": f"{partial_stage}-{lifecycle_id}" if partial_stage else None,
                "exit_stage": partial_stage or "full_exit",
                "position_snapshot": _position_snapshot_dict(position),
            }
        )

    # Clean tracker: remove symbols no longer held, persist updated state.
    held_symbols = {str(p.symbol).zfill(6) for p in positions}
    cleaned_tracker = {k: v for k, v in updated_tracker.items() if k in held_symbols}
    if tracker_path is not None and cleaned_tracker != tracker_data:
        _save_position_tracker(tracker_path, cleaned_tracker)

    audit = {
        "enabled": True,
        "as_of": as_of,
        "stop_loss_pct": stop_loss_pct,
        "take_profit_pct": take_profit_pct,
        "trailing_stop_pct": trailing_stop_pct,
        "inverse_exit_policy": {
            "stop_loss_pct": inverse_stop_loss_pct,
            "profit_lock_activation_pct": inverse_profit_lock_activation_pct,
            "profit_floor_pct": inverse_profit_floor_pct,
            "partial_1_pct": inverse_partial_1_pct,
            "partial_2_pct": inverse_partial_2_pct,
            "partial_fraction": inverse_partial_fraction,
            "trailing_stop_pct": inverse_trailing_stop_pct,
        },
        "max_holding_trading_days": max_holding_days,
        "risk_off_exit": risk_off_exit,
        "risk_off_regimes": sorted(risk_off_regimes),
        "inverse_hedge_symbols": sorted(inverse_hedge_symbols),
        "market_regime": market_regime,
        "forced_symbols": sorted(forced_symbols),
        "force_all": force_all,
        "positions_checked": len(positions),
        "sell_order_count": len(orders),
        "reviews": reviews,
    }
    return orders, audit


def merge_exit_orders(candidate_payload: dict[str, Any], sell_orders: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge SELL exits with BUY candidates, giving exits priority per symbol."""
    merged = dict(candidate_payload or {})
    existing_orders = list(merged.get("orders") or [])
    sell_symbols = {str(order.get("symbol", "")).zfill(6) for order in sell_orders}
    kept_buys = [
        order for order in existing_orders
        if not (str(order.get("side", "BUY")).upper() == "BUY" and str(order.get("symbol", "")).zfill(6) in sell_symbols)
    ]
    merged_orders = list(sell_orders) + kept_buys
    merged["orders"] = merged_orders
    if merged_orders:
        merged["status"] = "CANDIDATES"
    else:
        merged.setdefault("status", "NO_TRADE")
    if sell_orders:
        merged["position_exit_applied"] = True
        merged["position_exit_order_count"] = len(sell_orders)
    return merged


def append_position_exit_orders(
    candidate_payload: dict[str, Any],
    *,
    report_dir: str | Path,
    env: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fetch KIS holdings and append exit SELL orders when enabled.

    Defaults to enabled so the recurring loop can both buy and sell once the
    outer live-submit gates are configured. Set ``TOSS_POSITION_EXIT_ENABLED=0``
    to disable.
    """
    source = env or {}
    enabled = _env_true(source.get("TOSS_POSITION_EXIT_ENABLED"), default=True)
    audit: dict[str, Any] = {
        "enabled": enabled,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sell_order_count": 0,
    }
    if not enabled:
        audit["reason"] = "position_exit_disabled"
        return candidate_payload, audit
    try:
        config = LiveExecutionConfig.from_env(source)
        if config.provider != "kis":
            audit["reason"] = "provider_not_kis"
            return candidate_payload, audit
        missing = []
        if not config.app_key:
            missing.append("app_key")
        if not config.app_secret:
            missing.append("app_secret")
        if not config.cano:
            missing.append("cano")
        if not config.account_product_code:
            missing.append("account_product_code")
        if missing:
            audit["reason"] = "missing_kis_readonly_config"
            audit["missing"] = missing
            return _without_buy_orders(candidate_payload, "position_exit_missing_kis_readonly_config"), audit
        client = KisReadOnlyClient(
            app_key=config.app_key,
            app_secret=config.app_secret,
            cano=config.cano,
            account_product_code=config.account_product_code or "01",
            mock_trading=config.kis_mock_trading,
            base_url=config.base_url,
            timeout=config.timeout,
        )
        positions = [position for position in client.position_snapshots() if float(position.quantity or 0) > 0]
        account = client.account_snapshot()
        realtime_quotes: dict[str, Quote] = {}
        quote_errors: dict[str, str] = {}
        for position in positions:
            symbol = str(position.symbol).zfill(6)
            try:
                quote = client.quote_snapshot(symbol)
                invalid_reason = position_quote_invalid_reason(quote)
                if invalid_reason:
                    quote_errors[symbol] = invalid_reason
                else:
                    realtime_quotes[symbol] = quote
            except Exception as exc:
                quote_errors[symbol] = f"{type(exc).__name__}:{exc}"
        audit["position_quote_errors"] = quote_errors
        audit["position_quote_count"] = len(realtime_quotes)
        equity_guard = evaluate_account_equity_guard(
            account,
            positions,
            report_dir=report_dir,
            env=source,
        )
        build_env = dict(source)
        if equity_guard.get("liquidation_required"):
            build_env["TOSS_FORCE_EXIT_ALL"] = "1"
        decision_sell_symbols = position_exit_sell_symbols(candidate_payload)
        if decision_sell_symbols:
            configured_forced = str(build_env.get("TOSS_FORCE_EXIT_SYMBOLS", "")).strip()
            combined = [*decision_sell_symbols]
            if configured_forced:
                combined.extend(item.strip() for item in configured_forced.split(",") if item.strip())
            build_env["TOSS_FORCE_EXIT_SYMBOLS"] = ",".join(sorted(set(combined)))
        # A daily candidate label (including the transformed inverse-sleeve
        # situation) is not fresh intraday evidence and must never liquidate a
        # live holding by itself. Regime liquidation is enabled only by the
        # unified intraday decision after freshness and conflict checks.
        market_regime = position_exit_market_regime(candidate_payload)
        sell_orders, build_audit = build_position_exit_orders(
            positions,
            env=build_env,
            as_of=str(candidate_payload.get("as_of") or candidate_payload.get("generated_for") or ""),
            report_dir=report_dir,
            market_regime=market_regime,
            realtime_quotes=realtime_quotes,
            require_realtime_quotes=True,
        )
        audit.update(build_audit)
        audit["equity_guard"] = equity_guard
        base_payload = candidate_payload
        buy_block_reasons: list[str] = []
        if quote_errors:
            base_payload = block_buys_for_position_quote_errors(base_payload, quote_errors)
            buy_block_reasons.append("position_exit_quote_unavailable")
        if equity_guard.get("block_new_buys"):
            base_payload = _without_buy_orders(base_payload, "equity_drawdown_guard_active")
            buy_block_reasons.append("equity_drawdown_guard_active")
        audit["block_new_buys"] = bool(buy_block_reasons)
        audit["buy_block_reasons"] = buy_block_reasons
        merged = merge_exit_orders(base_payload, sell_orders)
        # max_positions live enforcement: trim BUY orders that would exceed the limit.
        max_positions = _env_int(source, "TOSS_MAX_POSITIONS", 0)
        if max_positions > 0:
            merged = _enforce_max_positions(merged, len(positions), max_positions)
        report_path = Path(report_dir) / "latest_position_exit_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        audit["report_path"] = str(report_path)
        return merged, audit
    except Exception as exc:
        audit["reason"] = "position_exit_exception"
        audit["exception_type"] = type(exc).__name__
        audit["exception"] = str(exc)
        # Position/account/equity state is required for safe new entries.
        # Preserve pre-existing SELLs but fail closed on BUYs.
        return _without_buy_orders(candidate_payload, "position_exit_state_unavailable"), audit


def position_exit_sell_symbols(candidate_payload: Mapping[str, Any]) -> list[str]:
    """Return fresh, conflict-free symbol-specific SELL authorisations."""
    decision = candidate_payload.get("intraday_decision")
    if not isinstance(decision, Mapping):
        return []
    if str(decision.get("evidence_status") or "").upper() != "FRESH":
        return []
    if str(decision.get("verdict") or "").upper() != "SELL":
        return []
    if bool(decision.get("signal_conflict")):
        return []
    return sorted({
        str(symbol).strip().zfill(6)
        for symbol in (decision.get("sell_symbols") or [])
        if str(symbol).strip()
    })


def position_exit_market_regime(candidate_payload: Mapping[str, Any]) -> str | None:
    """Return verified intraday evidence eligible for regime liquidation.

    Missing, stale, conflicting, or unauthorised envelopes fail closed. Price
    exits, explicit force exits, time exits, and equity-drawdown exits remain
    independent from this value.
    """
    decision = candidate_payload.get("intraday_decision")
    if not isinstance(decision, Mapping):
        return None
    if str(decision.get("evidence_status") or "").upper() != "FRESH":
        return None
    if not bool(decision.get("regime_liquidation_allowed")):
        return None
    if bool(decision.get("signal_conflict")):
        return None
    regime = str(decision.get("market_regime") or "").strip()
    return regime or None


def _enforce_max_positions(merged_payload: dict[str, Any], held_count: int, max_positions: int) -> dict[str, Any]:
    """Trim BUY orders so held + new BUYs do not exceed ``max_positions``.

    SELL orders are always kept (they reduce positions). Only BUY orders that
    would cause the total to exceed the limit are dropped.
    """
    orders = list(merged_payload.get("orders") or [])
    # A submitted SELL is not a filled SELL. Count every broker holding until
    # reconciliation proves the exit filled; otherwise replacement BUYs can
    # temporarily breach max_positions in the same tick.
    effective_held = max(0, held_count)
    slots_available = max(0, max_positions - effective_held)
    kept_orders: list[dict[str, Any]] = []
    buy_count = 0
    trimmed = 0
    for order in orders:
        if str(order.get("side", "BUY")).upper() == "BUY":
            if buy_count < slots_available:
                kept_orders.append(order)
                buy_count += 1
            else:
                trimmed += 1
        else:
            kept_orders.append(order)
    result = dict(merged_payload)
    result["orders"] = kept_orders
    if trimmed > 0:
        result["max_positions_trimmed_buys"] = trimmed
        result["max_positions_limit"] = max_positions
        result["max_positions_effective_held"] = effective_held
        if not kept_orders:
            result.setdefault("status", "NO_TRADE")
            result.setdefault("reason", "max_positions_reached")
    return result
