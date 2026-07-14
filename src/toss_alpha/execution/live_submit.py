"""Guarded live-submit phase for the TOSS ttak loop.

The module is fail-closed by design.  It can always build/dry-run broker
payloads for audit, but real broker submission requires all of the following:

1. live readiness is already true;
2. broker/risk opt-ins are already reflected in that readiness;
3. submit opt-in ``TOSS_LIVE_SUBMIT_ENABLED=true`` is present;
4. dry-run is explicitly disabled;
5. the exact real-order confirmation phrase matches;
6. qual gate is not blocked unless explicitly overridden;
7. duplicate ledger key is absent.
"""
from __future__ import annotations

import csv
import fcntl
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, time, timezone, date
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from toss_alpha.data.schema import OrderIntent, RiskDecision
from toss_alpha.connectors.kis_readonly import KisReadOnlyClient
from toss_alpha.execution.krx_calendar import is_krx_trading_day
from toss_alpha.execution.live_ready import GuardedLiveExecutor, LiveExecutionConfig, REAL_ORDER_CONFIRMATION_PHRASE
from toss_alpha.execution.order_management import manage_submitted_order_ledger
from toss_alpha.risk import RiskPolicy

KST = ZoneInfo("Asia/Seoul")
KOREA_REGULAR_MARKET_OPEN = time(9, 0)
KOREA_REGULAR_MARKET_LAST_BUY = time(15, 20)


@dataclass(frozen=True)
class LiveSubmitSettings:
    submit_enabled: bool = False
    dry_run: bool = True
    allow_qual_blocked: bool = False
    confirmation_phrase: str = ""
    ledger_path: Path = Path("reports/harness/live_order_ledger.jsonl")
    divergence_ledger_path: Path = Path("reports/harness/live_paper_divergence_ledger.jsonl")
    strategy_id: str = "ttak_absolute_return_loop"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, *, root: Path | None = None) -> "LiveSubmitSettings":
        source = os.environ if env is None else env
        base = Path.cwd() if root is None else root
        ledger = _blank_to_none(source.get("TOSS_LIVE_ORDER_LEDGER"))
        divergence_ledger = _blank_to_none(source.get("TOSS_LIVE_PAPER_DIVERGENCE_LEDGER"))
        return cls(
            submit_enabled=_env_true(source.get("TOSS_LIVE_SUBMIT_ENABLED")),
            dry_run=not _env_false(source.get("TOSS_LIVE_SUBMIT_DRY_RUN")),
            allow_qual_blocked=_env_true(source.get("TOSS_ALLOW_QUAL_DATA_BLOCKED")),
            confirmation_phrase=source.get("TOSS_LIVE_SUBMIT_CONFIRMATION", ""),
            ledger_path=Path(ledger).expanduser() if ledger else base / "reports" / "harness" / "live_order_ledger.jsonl",
            divergence_ledger_path=Path(divergence_ledger).expanduser() if divergence_ledger else base / "reports" / "harness" / "live_paper_divergence_ledger.jsonl",
            strategy_id=source.get("TOSS_LIVE_STRATEGY_ID", "ttak_absolute_return_loop"),
        )


def run_live_submit_phase(
    *,
    candidate_payload: dict[str, Any] | None,
    qual: dict[str, Any],
    live: dict[str, Any],
    report_dir: Path,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build/dry-run/optionally submit the current loop orders.

    The default path is audit-only.  A real submission cannot happen unless
    ``TOSS_LIVE_SUBMIT_DRY_RUN=false`` and ``TOSS_LIVE_SUBMIT_ENABLED=true``
    are both set, and all other guards pass.
    """
    now = now or datetime.now(timezone.utc)
    report_dir.mkdir(parents=True, exist_ok=True)
    root = report_dir.parents[1]
    settings = LiveSubmitSettings.from_env(env, root=root)
    orders = list((candidate_payload or {}).get("orders") or [])
    # Exits are always evaluated before entries, regardless of caller ordering.
    orders.sort(key=lambda order: 0 if str(order.get("side", "BUY")).upper() == "SELL" else 1)
    readiness_ready = live.get("ready") is True or str(live.get("ready")) == "True"
    qual_blocked = str(qual.get("status", "")).startswith("BLOCKED")

    artifact_path = report_dir / f"live_submit_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    phase: dict[str, Any] = {
        "status": "LIVE_SUBMIT_DISABLED",
        "dry_run": settings.dry_run,
        "submit_enabled": settings.submit_enabled,
        "allow_qual_blocked": settings.allow_qual_blocked,
        "order_count": len(orders),
        "attempted_count": 0,
        "submitted_count": 0,
        "unknown_count": 0,
        "blocked_count": 0,
        "artifact_path": str(artifact_path),
        "ledger_path": str(settings.ledger_path),
        "divergence_ledger_path": str(settings.divergence_ledger_path),
        "results": [],
        "violations": [],
    }

    # Reconcile existing active orders even when this tick generates no new
    # candidates. Otherwise quiet ticks leave SUBMITTED/UNKNOWN rows stale and
    # duplicate protection never learns the broker terminal state.
    ledger = LiveOrderLedger(settings.ledger_path)
    order_reconcile: dict[str, Any] = {"status": "SKIPPED_DRY_RUN" if settings.dry_run else "NOT_RUN"}
    if not settings.dry_run:
        desired_order_keys: set[str] = set()
        for raw_order in orders:
            try:
                desired_intent = order_to_intent(
                    raw_order,
                    strategy_id=_order_strategy_id(settings.strategy_id, raw_order),
                )
                desired_order_keys.add(ledger_key(desired_intent, candidate_payload or {}))
            except Exception:
                # Invalid current candidates must not preserve an old BUY order.
                continue
        order_reconcile = manage_submitted_order_ledger(
            ledger_path=settings.ledger_path,
            env=env,
            desired_order_keys=desired_order_keys,
            now=now,
        )
        ledger = LiveOrderLedger(settings.ledger_path)
    phase["order_reconcile"] = order_reconcile

    if not orders:
        phase["status"] = "LIVE_SUBMIT_NO_ORDERS"
        _write_json(artifact_path, phase)
        return phase
    if not readiness_ready:
        phase["status"] = "BLOCKED_LIVE_READINESS"
        phase["violations"].append("live_readiness_not_ready")
    if qual_blocked and not settings.allow_qual_blocked:
        phase["status"] = "BLOCKED_QUAL_DATA"
        phase["violations"].append("qual_gate_blocked")
    promoted_violation = promoted_policy_guard_violation(candidate_payload or {}, root=root, env=env)
    if promoted_violation:
        phase["status"] = "BLOCKED_PROMOTED_POLICY"
        phase["violations"].append(promoted_violation)
    if not settings.dry_run and not settings.submit_enabled:
        phase["status"] = "BLOCKED_SUBMIT_DISABLED"
        phase["violations"].append("live_submit_not_enabled")
    if not settings.dry_run and settings.confirmation_phrase != REAL_ORDER_CONFIRMATION_PHRASE:
        phase["status"] = "BLOCKED_CONFIRMATION"
        phase["violations"].append("real_order_confirmation_phrase_mismatch")
    if not settings.dry_run:
        stale_violation = stale_candidate_violation(candidate_payload or {}, now=now)
        if stale_violation:
            phase["status"] = "BLOCKED_STALE_CANDIDATE"
            phase["violations"].append(stale_violation)
        recent_violation = recent_candidate_violation(candidate_payload or {}, now=now, env=env)
        if recent_violation:
            phase["status"] = "BLOCKED_STALE_CANDIDATE"
            phase["violations"].append(recent_violation)
        regime_violation = market_regime_violation(candidate_payload or {}, env=env)
        if regime_violation:
            phase["status"] = "BLOCKED_MARKET_REGIME"
            phase["violations"].append(regime_violation)
        freshness_violations = live_data_freshness_violations(root=root, now=now, env=env)
        if freshness_violations:
            phase["status"] = "BLOCKED_STALE_DATA"
            phase["violations"].extend(freshness_violations)
        market_violation = korea_regular_market_violation(now, env=env)
        if market_violation:
            phase["status"] = "BLOCKED_MARKET_TIME"
            phase["violations"].append(market_violation)

    config = LiveExecutionConfig.from_env(env)
    policy = RiskPolicy.from_env(env)
    executor = GuardedLiveExecutor(config=config, policy=policy)

    # Intraday submitted-order cap: prevents excessive order churn when the loop
    # runs multiple times per day (e.g. every hour during regular market).
    intraday_cap = _env_int(env, "TOSS_INTRADAY_SUBMIT_CAP", 12)
    today_str = now.strftime("%Y-%m-%d")
    ledger_rows = ledger.records()
    intraday_submitted = _intraday_submit_attempt_count(ledger_rows, today_str)
    intraday_remaining = max(0, intraday_cap - intraday_submitted)
    intraday_audit = {
        "intraday_submit_cap": intraday_cap,
        "intraday_submitted_today": intraday_submitted,
        "intraday_remaining": intraday_remaining,
    }
    phase["intraday_cap"] = intraday_audit

    for raw_order in orders:
        raw_order = dict(raw_order)
        order_strategy_id = _order_strategy_id(settings.strategy_id, raw_order)
        pre_reprice_intent = order_to_intent(raw_order, strategy_id=order_strategy_id)
        pre_reprice_key = ledger_key(pre_reprice_intent, candidate_payload or {})
        reprice_remaining = (order_reconcile.get("reprice_remaining_by_key") or {}).get(pre_reprice_key)
        if reprice_remaining is not None:
            capped_qty = min(int(_float_or_none(raw_order.get("quantity")) or 0), int(reprice_remaining))
            raw_order["quantity"] = capped_qty
            old_limit = _float_or_none(raw_order.get("limit_price")) or 0.0
            raw_order["notional_krw"] = capped_qty * old_limit
            raw_order["reprice_remaining_cap"] = int(reprice_remaining)
        adaptive_audit: dict[str, Any] | None = None
        if not settings.dry_run and str(raw_order.get("side", "BUY")).upper() == "BUY":
            raw_order, adaptive_audit = adapt_buy_order_to_live_quote(raw_order, config=config, env=env)
        intent = order_to_intent(raw_order, strategy_id=order_strategy_id)
        order_violations = validate_live_order_intent(intent, raw_order=raw_order, policy=policy)
        if adaptive_audit and adaptive_audit.get("violation"):
            order_violations.append(str(adaptive_audit["violation"]))
        aggregate_sell_violation = aggregate_sell_quantity_violation(raw_order, orders)
        if aggregate_sell_violation:
            order_violations.append(aggregate_sell_violation)
        duplicate_key = ledger_key(intent, candidate_payload or {})
        if ledger.corrupt:
            order_violations.append("live_order_ledger_corrupt")
        elif ledger.has_live_submission(duplicate_key):
            order_violations.append("duplicate_live_order_ledger_key")
        elif intent.side == "SELL" and ledger.has_other_live_sell(symbol=intent.symbol, key=duplicate_key):
            order_violations.append("active_sell_order_for_symbol")
        # Intraday cap throttles entries but must never trap risk-reducing exits.
        if not settings.dry_run and intent.side == "BUY" and intraday_remaining <= 0:
            order_violations.append("intraday_submit_cap_exceeded")
        if not settings.dry_run:
            if intent.side == "BUY":
                order_violations.extend(order_quality_violations(raw_order, candidate_payload or {}, env=env))
                intraday_violation = intraday_decision_buy_violation(raw_order, candidate_payload or {}, now=now, env=env)
                if intraday_violation:
                    order_violations.append(intraday_violation)
            issue_violation = current_issue_buy_violation(raw_order, root=root, now=now, env=env)
            if issue_violation:
                order_violations.append(issue_violation)
            harness_violation = strategic_harness_audit_buy_violation(raw_order, root=root, now=now, env=env)
            if harness_violation:
                order_violations.append(harness_violation)
        phase_violations = phase["violations"]
        if intent.side == "SELL":
            # Exit orders must remain available in bad or unknown regimes.  BUY
            # freshness/regime/promoted-policy blockers protect entries, but
            # applying them to SELLs can trap positions that the watchdog is
            # trying to reduce.
            phase_violations = [
                violation for violation in phase_violations
                if not _buy_only_phase_violation(str(violation))
            ]
        if phase_violations:
            order_violations.extend(phase_violations)
        reserved = False
        if not settings.dry_run and not order_violations:
            reserved = ledger.reserve_if_absent(
                duplicate_key,
                {
                    "ledger_key": duplicate_key,
                    "status": "PENDING_SUBMIT",
                    "timestamp": now.isoformat(),
                    "intent_id": intent.intent_id,
                    "symbol": intent.symbol,
                    "side": intent.side,
                    "quantity": intent.quantity,
                    "limit_price": intent.limit_price,
                },
            )
            if not reserved:
                order_violations.append(
                    "live_order_ledger_corrupt" if ledger.corrupt
                    else "duplicate_live_order_ledger_key_concurrent"
                )
        decision = RiskDecision.allowed() if not order_violations else RiskDecision.blocked(_unique(order_violations))
        try:
            result = executor.submit_manual_draft(
                intent,
                decision,
                confirmation_phrase=settings.confirmation_phrase or REAL_ORDER_CONFIRMATION_PHRASE,
                dry_run=settings.dry_run,
            )
        except Exception as exc:
            # The broker may have accepted the request before the response was
            # lost. Keep the reservation active rather than risk a duplicate.
            result = {
                "status": "UNKNOWN",
                "not_submitted": False,
                "violations": ["broker_submission_outcome_unknown"],
                "exception_type": type(exc).__name__,
                "provider": config.provider,
            }
        result["ledger_key"] = duplicate_key
        result["symbol"] = intent.symbol
        result["side"] = intent.side
        if adaptive_audit is not None:
            result["adaptive_pricing"] = adaptive_audit
        phase["results"].append(result)
        append_divergence_record(
            settings.divergence_ledger_path,
            now=now,
            candidate_payload=candidate_payload or {},
            raw_order=raw_order,
            result=result,
            ledger_key_value=duplicate_key,
        )
        phase["attempted_count"] += 1
        if result.get("status") == "SUBMITTED":
            phase["submitted_count"] += 1
            if intent.side == "BUY":
                intraday_remaining -= 1
            ledger.append({"ledger_key": duplicate_key, "status": "SUBMITTED", "timestamp": now.isoformat(), "result": _redact_result(result)})
        elif result.get("status") == "UNKNOWN":
            phase["unknown_count"] += 1
            phase["blocked_count"] += 1
            if reserved:
                ledger.append({"ledger_key": duplicate_key, "status": "UNKNOWN", "timestamp": now.isoformat(), "result": _redact_result(result)})
        elif result.get("status") in {"BLOCK", "REJECTED"}:
            phase["blocked_count"] += 1
            if reserved:
                ledger.append({"ledger_key": duplicate_key, "status": str(result.get("status")), "timestamp": now.isoformat(), "result": _redact_result(result)})

    if phase["unknown_count"]:
        phase["status"] = "LIVE_SUBMIT_OUTCOME_UNKNOWN"
    elif phase["submitted_count"]:
        phase["status"] = "LIVE_SUBMITTED"
    elif settings.dry_run and phase["blocked_count"]:
        phase["status"] = "LIVE_SUBMIT_DRY_RUN_BLOCKED"
    elif settings.dry_run:
        phase["status"] = "LIVE_SUBMIT_DRY_RUN_READY"
    elif phase["blocked_count"]:
        phase["status"] = "LIVE_SUBMIT_BLOCKED"

    _write_json(artifact_path, phase)
    return phase


def adapt_buy_order_to_live_quote(
    raw_order: Mapping[str, Any],
    *,
    config: LiveExecutionConfig,
    env: Mapping[str, str] | None = None,
    quote_client: Any | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a marketable BUY limit from a fresh KIS quote, fail-closed."""
    order = dict(raw_order)
    symbol = str(order.get("symbol") or "").zfill(6)
    reference = _float_or_none(
        order.get("chase_reference_price")
        or order.get("reference_close")
        or order.get("current_price")
        or order.get("last_price")
    )
    max_chase_pct = float(_env_value(env, "TOSS_ADAPTIVE_LIMIT_MAX_CHASE_PCT", "0.02"))
    enabled = _env_true(_env_value(env, "TOSS_ADAPTIVE_LIMIT_ENABLED", "true"))
    audit: dict[str, Any] = {"enabled": enabled, "symbol": symbol, "reference_price": reference, "max_chase_pct": max_chase_pct}
    if not enabled:
        audit["status"] = "DISABLED"
        return order, audit
    if config.provider != "kis" or not symbol or reference is None or reference <= 0:
        audit.update({"status": "BLOCKED", "violation": "adaptive_quote_prerequisite_missing"})
        return order, audit
    try:
        client = quote_client or KisReadOnlyClient(
            app_key=config.app_key or "", app_secret=config.app_secret or "", cano=config.cano or "",
            account_product_code=config.account_product_code or "01", mock_trading=config.kis_mock_trading,
            base_url=config.base_url, timeout=config.timeout,
        )
        quote = client.quote_snapshot(symbol)
    except Exception as exc:
        audit.update({"status": "BLOCKED", "violation": "adaptive_quote_fetch_failed", "exception_type": type(exc).__name__})
        return order, audit
    ask = _float_or_none(quote.ask)
    bid = _float_or_none(quote.bid)
    if ask is None or ask <= 0 or bid is None or bid <= 0:
        audit.update({"status": "BLOCKED", "violation": "adaptive_quote_orderbook_missing", "live_bid": bid, "live_ask": ask, "live_last": quote.last})
        return order, audit
    live_price = ask
    chase_pct = live_price / reference - 1.0
    spread_pct = None
    if bid is not None and bid > 0:
        mid = (bid + live_price) / 2.0
        spread_pct = (live_price - bid) / mid if mid > 0 else None
    max_spread_pct = float(_env_value(env, "TOSS_MAX_LIVE_SPREAD_PCT", "0.003"))
    audit.update({"live_bid": bid, "live_ask": ask, "live_last": quote.last, "selected_limit_price": live_price, "chase_pct": chase_pct, "spread_pct": spread_pct})
    if chase_pct > max_chase_pct:
        audit.update({"status": "BLOCKED", "violation": "adaptive_limit_chase_cap_exceeded"})
        return order, audit
    if spread_pct is not None and spread_pct > max_spread_pct:
        audit.update({"status": "BLOCKED", "violation": "adaptive_limit_spread_too_wide"})
        return order, audit
    original_notional = _float_or_none(order.get("notional_krw"))
    if original_notional is None:
        quantity0 = _float_or_none(order.get("quantity")) or 0.0
        original_notional = quantity0 * (_float_or_none(order.get("limit_price")) or reference)
    quantity = int(original_notional // live_price)
    if quantity <= 0:
        audit.update({"status": "BLOCKED", "violation": "adaptive_limit_quantity_zero"})
        return order, audit
    order.update({"limit_price": live_price, "quantity": quantity, "notional_krw": quantity * live_price,
                  "current_price": quote.last, "best_bid": bid, "best_ask": ask,
                  "volume": quote.volume, "dollar_volume_krw": (float(quote.volume) * float(quote.last)) if quote.volume is not None and quote.last is not None else None,
                  "spread_pct": spread_pct, "quote_source": "kis_live_adaptive",
                  "quote_observed_at": quote.timestamp.isoformat() if quote.timestamp else None})
    audit.update({"status": "ADAPTED", "quantity": quantity, "notional_krw": quantity * live_price})
    return order, audit


def order_to_intent(order: Mapping[str, Any], *, strategy_id: str) -> OrderIntent:
    return OrderIntent(
        strategy_id=strategy_id,
        symbol=str(order.get("symbol", "")).zfill(6),
        side=str(order.get("side", "BUY")).upper(),
        reason=str(order.get("reason", "ttak live-submit candidate")),
        notional_krw=_float_or_none(order.get("notional_krw")),
        quantity=_float_or_none(order.get("quantity")),
        order_type=str(order.get("order_type", "LIMIT")).upper(),
        limit_price=_float_or_none(order.get("limit_price")),
        time_in_force=str(order.get("time_in_force", "DAY")),
        mode="live_auto_guarded",
        not_live_order=False,
    )


def _buy_only_phase_violation(violation: str) -> bool:
    """Return True for entry gates that must never trap a position exit."""
    if violation == "qual_gate_blocked":
        return True
    return violation.startswith((
        "market_regime_",
        "promoted_policy_",
        "candidate_as_of_",
        "panel_latest_",
        "sentiment_",
    ))


def validate_live_order_intent(intent: OrderIntent, *, raw_order: Mapping[str, Any], policy: RiskPolicy) -> list[str]:
    violations: list[str] = []
    if intent.side not in {"BUY", "SELL"}:
        violations.append("invalid_side")
    if intent.order_type.upper() != "LIMIT":
        violations.append("only_limit_orders_allowed")
    if intent.quantity is None or intent.quantity <= 0 or not float(intent.quantity).is_integer():
        violations.append("quantity_must_be_positive_integer")
    if intent.limit_price is None or intent.limit_price <= 0:
        violations.append("limit_price_required")
    if intent.side == "SELL":
        sellable_violation = sellable_quantity_violation(intent, raw_order=raw_order)
        if sellable_violation:
            violations.append(sellable_violation)
    notional = intent.notional_krw
    if notional is None and intent.quantity is not None and intent.limit_price is not None:
        notional = float(intent.quantity) * float(intent.limit_price)
    if notional is None or notional <= 0:
        violations.append("notional_required")
    elif intent.side == "BUY" and notional > policy.max_order_krw:
        violations.append("max_order_krw_exceeded")
    if raw_order.get("mode") not in {"manual_draft_only", "live_auto_guarded", None}:
        violations.append("unsupported_order_mode")
    return _unique(violations)


def sellable_quantity_violation(intent: OrderIntent, *, raw_order: Mapping[str, Any]) -> str | None:
    """Return a SELL safety violation unless sellable quantity explicitly covers the order.

    SELL orders may come from a broker reconciliation/read-only holdings slice or
    a manual review packet.  In both cases the order must carry an explicit
    sellable quantity field; current position quantity alone is not enough.
    """
    if intent.side != "SELL":
        return None
    if intent.quantity is None or intent.quantity <= 0:
        return "sell_quantity_missing"
    sellable = _sellable_quantity_from_order(raw_order)
    if sellable is None:
        return "sellable_quantity_missing"
    if float(intent.quantity) > sellable:
        return "sellable_quantity_shortfall"
    return None


def _sellable_quantity_from_order(raw_order: Mapping[str, Any]) -> float | None:
    for key in ("sellable_quantity", "sellable_qty", "ord_psbl_qty", "sellableQuantity"):
        value = raw_order.get(key)
        if value in (None, ""):
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed) and parsed >= 0:
            return parsed
    return None


def aggregate_sell_quantity_violation(raw_order: Mapping[str, Any], orders: list[Mapping[str, Any]]) -> str | None:
    if str(raw_order.get("side", "BUY")).upper() != "SELL":
        return None
    symbol = str(raw_order.get("symbol", "")).zfill(6)
    total = 0.0
    sellable_values: list[float] = []
    for order in orders:
        if str(order.get("side", "BUY")).upper() != "SELL":
            continue
        if str(order.get("symbol", "")).zfill(6) != symbol:
            continue
        quantity = _positive_float_or_none(order.get("quantity"))
        if quantity is not None:
            total += quantity
        sellable = _sellable_quantity_from_order(order)
        if sellable is not None:
            sellable_values.append(sellable)
    if not sellable_values:
        return None
    if total > min(sellable_values):
        return "aggregate_sell_quantity_shortfall"
    return None


def _positive_float_or_none(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed


def korea_regular_market_violation(now: datetime, env: Mapping[str, str] | None = None) -> str | None:
    """Return fail-closed violation if real KIS submit is outside KR regular session.

    The cron may run pre-open for research/readiness, but live BUY submission is
    only allowed after the 09:00 KST open. The 15:20 cutoff avoids relying on
    closing-auction/session-specific order semantics.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    kst_now = now.astimezone(KST)
    if kst_now.weekday() >= 5:
        return "outside_korea_regular_market_weekday"
    if not is_krx_trading_day(kst_now.date(), env=env):
        return "outside_krx_trading_day"
    if kst_now.time() < KOREA_REGULAR_MARKET_OPEN:
        return "before_korea_regular_market_open_0900_kst"
    if kst_now.time() > KOREA_REGULAR_MARKET_LAST_BUY:
        return "after_korea_regular_market_last_buy_1520_kst"
    return None


def promoted_policy_guard_violation(candidate_payload: Mapping[str, Any], *, root: Path, env: Mapping[str, str] | None = None) -> str | None:
    """Block aggressive live candidates when the promoted policy says NO_TRADE.

    Aggressive small-account candidates are allowed to exist for paper/manual
    review, but they must not become broker-submitted live orders when the
    promoted walk-forward policy is not actionable for the same ``as_of`` date.
    """
    policy_id = str(candidate_payload.get("policy_id") or "")
    if "aggressive" not in policy_id:
        return None
    as_of = candidate_payload.get("as_of") or candidate_payload.get("generated_for")
    if not as_of:
        return "promoted_policy_status_missing_for_aggressive_live"
    promoted_payload, load_error = _load_promoted_candidate_payload(str(as_of)[:10], root=root, env=env)
    if load_error:
        return load_error
    promoted_status = str(promoted_payload.get("status") or "UNKNOWN")
    if promoted_status == "NO_TRADE":
        return "promoted_policy_no_trade_blocks_aggressive_live"
    if promoted_status != "CANDIDATES":
        return "promoted_policy_not_actionable_blocks_aggressive_live"
    return None


def _load_promoted_candidate_payload(as_of: str, *, root: Path, env: Mapping[str, str] | None = None) -> tuple[dict[str, Any], str | None]:
    source = os.environ if env is None else env
    explicit = _blank_to_none(source.get("TOSS_PROMOTED_CANDIDATE_JSON"))
    path = Path(explicit).expanduser() if explicit else root / "reports" / "trade_candidates" / f"candidates_{as_of}_contextual_mon_fri_policy_seed20260607_walkforward_promoted.json"
    if not path.exists():
        return {}, "promoted_policy_status_missing_for_aggressive_live"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}, "promoted_policy_status_invalid_for_aggressive_live"
    if not isinstance(payload, dict):
        return {}, "promoted_policy_status_invalid_for_aggressive_live"
    return payload, None


def stale_candidate_violation(candidate_payload: Mapping[str, Any], *, now: datetime, max_calendar_days: int = 7) -> str | None:
    """Fail closed when live-submit candidates are not based on recent market data."""
    as_of_text = candidate_payload.get("as_of") or candidate_payload.get("generated_for")
    if not as_of_text:
        return "candidate_as_of_missing"
    try:
        candidate_date = date.fromisoformat(str(as_of_text)[:10])
    except ValueError:
        return "candidate_as_of_invalid"
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    today = now.astimezone(KST).date()
    if candidate_date > today:
        return "candidate_as_of_in_future"
    if (today - candidate_date).days > max_calendar_days:
        return "candidate_as_of_stale"
    return None


def recent_candidate_violation(candidate_payload: Mapping[str, Any], *, now: datetime, env: Mapping[str, str] | None = None) -> str | None:
    as_of_text = candidate_payload.get("as_of") or candidate_payload.get("generated_for")
    if not as_of_text:
        return "candidate_as_of_missing"
    try:
        candidate_date = date.fromisoformat(str(as_of_text)[:10])
    except ValueError:
        return "candidate_as_of_invalid"
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    today = now.astimezone(KST).date()
    max_lag = int(_env_value(env, "TOSS_MAX_CANDIDATE_TRADING_DAY_LAG", "1"))
    lag = _trading_day_lag(candidate_date, today, env=env)
    if lag is None:
        return "candidate_as_of_invalid_trading_day"
    if lag > max_lag:
        return "candidate_as_of_not_recent_krx_trading_day"
    return None


def market_regime_violation(candidate_payload: Mapping[str, Any], *, env: Mapping[str, str] | None = None) -> str | None:
    situation = str(candidate_payload.get("situation") or "").strip()
    if not situation:
        return "market_regime_missing"
    blocked = {item.strip() for item in _env_value(env, "TOSS_BLOCK_LIVE_REGIMES", "down_high_vol,flat_high_vol").split(",") if item.strip()}
    if situation in blocked:
        return f"market_regime_blocked:{situation}"
    return None


def live_data_freshness_violations(*, root: Path, now: datetime, env: Mapping[str, str] | None = None) -> list[str]:
    violations: list[str] = []
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    today = now.astimezone(KST).date()
    panel_path = Path(_env_value(env, "TOSS_PANEL_CSV", str(root / "reports" / "backtests" / "random500_seed20260607_2022-01-01_2026-latest_ohlcv_panel.csv"))).expanduser()
    panel_date = _latest_panel_date(panel_path)
    if panel_date is None:
        violations.append("panel_latest_date_missing")
    else:
        max_panel_lag = int(_env_value(env, "TOSS_MAX_PANEL_TRADING_DAY_LAG", "1"))
        lag = _trading_day_lag(panel_date, today, env=env)
        if lag is None or lag > max_panel_lag:
            violations.append("panel_latest_date_stale")
    sentiment_path = Path(_env_value(env, "TOSS_SENTIMENT_REPORT_JSON", str(root / "reports" / "harness" / "sentiment_forward" / f"sentiment_forward_report_{today.strftime('%Y%m%d')}.json"))).expanduser()
    if sentiment_path.exists():
        sentiment_date = _json_date_field(sentiment_path, "latest_panel_date")
        if sentiment_date is None:
            violations.append("sentiment_latest_panel_date_missing")
        else:
            max_sentiment_lag = int(_env_value(env, "TOSS_MAX_SENTIMENT_TRADING_DAY_LAG", "3"))
            lag = _trading_day_lag(sentiment_date, today, env=env)
            if lag is None or lag > max_sentiment_lag:
                violations.append("sentiment_latest_panel_date_stale")
    elif _env_true(_env_value(env, "TOSS_REQUIRE_SENTIMENT_REPORT", "false")):
        violations.append("sentiment_report_missing")
    return violations


def order_quality_violations(raw_order: Mapping[str, Any], candidate_payload: Mapping[str, Any], *, env: Mapping[str, str] | None = None) -> list[str]:
    violations: list[str] = []
    violations.extend(bad_event_violations(raw_order, candidate_payload))
    violations.extend(liquidity_quality_violations(raw_order, env=env))
    fill_violation = fill_probability_violation(raw_order, env=env)
    if fill_violation:
        violations.append(fill_violation)
    return _unique(violations)


def intraday_decision_buy_violation(
    raw_order: Mapping[str, Any],
    candidate_payload: Mapping[str, Any],
    *,
    now: datetime,
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Require a fresh, matching unified intraday verdict for every live BUY."""
    if str(raw_order.get("side", "BUY")).upper() != "BUY":
        return None
    decision = candidate_payload.get("intraday_decision")
    if not isinstance(decision, Mapping):
        return "intraday_decision_missing"
    if not str(decision.get("decision_id") or "").startswith("intraday-"):
        return "intraday_decision_id_missing"
    if str(decision.get("evidence_status") or "").upper() != "FRESH":
        return "intraday_evidence_not_fresh"
    if str(decision.get("news_evidence_status") or "").upper() != "FRESH":
        return "intraday_news_evidence_not_fresh"
    if bool(decision.get("signal_conflict")):
        return "intraday_signal_conflict"
    generated = decision.get("generated_at_utc")
    if not generated:
        return "intraday_decision_timestamp_missing"
    try:
        generated_at = datetime.fromisoformat(str(generated).replace("Z", "+00:00"))
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=timezone.utc)
    except ValueError:
        return "intraday_decision_timestamp_invalid"
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    max_age = int(_env_value(env, "TOSS_INTRADAY_MAX_QUOTE_AGE_SECONDS", "300"))
    age = (now.astimezone(timezone.utc) - generated_at.astimezone(timezone.utc)).total_seconds()
    if age < -30 or age > max_age:
        return "intraday_decision_stale"
    verdict = str(decision.get("verdict") or "").upper()
    symbol = str(raw_order.get("symbol") or "").zfill(6)
    inverse_symbols = {
        "114800", "251340", "252670",
        str(_env_value(env, "TOSS_INVERSE_ETF_CODE", "114800")).zfill(6),
    }
    expected = "INVERSE_BUY" if symbol in inverse_symbols else "LONG_BUY"
    if verdict != expected:
        return f"intraday_verdict_mismatch:{verdict or 'missing'}:{expected}"
    return None


def current_issue_buy_violation(raw_order: Mapping[str, Any], *, root: Path, now: datetime, env: Mapping[str, str] | None = None) -> str | None:
    """Block new BUY submissions when the daily current-issue gate is high risk.

    SELL orders are intentionally exempt so stop-loss/take-profit exits keep
    working during risk-off headlines. The report is generated by
    scripts/current_issue_risk_report.py and can be overridden only with the
    explicit env ``TOSS_ALLOW_CURRENT_ISSUE_BUY=true``.
    """
    if str(raw_order.get("side", "BUY")).upper() != "BUY":
        return None
    if _env_true(_env_value(env, "TOSS_ALLOW_CURRENT_ISSUE_BUY", "false")):
        return None
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    today = now.astimezone(KST).date()
    default_path = root / "reports" / "harness" / "current_issues" / f"current_issue_risk_report_{today.strftime('%Y%m%d')}.json"
    path = Path(_env_value(env, "TOSS_CURRENT_ISSUE_RISK_JSON", str(default_path))).expanduser()
    if not path.exists():
        if _env_true(_env_value(env, "TOSS_REQUIRE_CURRENT_ISSUE_REPORT", "true")):
            return "current_issue_report_missing"
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return "current_issue_report_invalid"
    if not isinstance(payload, Mapping):
        return "current_issue_report_invalid"
    report_date_text = payload.get("as_of") or payload.get("generated_at_kst") or payload.get("generated_at_utc")
    if not report_date_text:
        return "current_issue_report_date_missing"
    try:
        report_date = date.fromisoformat(str(report_date_text)[:10])
    except ValueError:
        return "current_issue_report_date_invalid"
    if report_date != today:
        return "current_issue_report_stale"
    severity = str(payload.get("severity") or "").lower()
    buy_gate = str(payload.get("buy_gate") or "").lower()
    if severity in {"critical", "high"} or buy_gate == "block_new_buy":
        return f"current_issue_buy_block:{severity or buy_gate}"
    return None


def strategic_harness_audit_buy_violation(raw_order: Mapping[str, Any], *, root: Path, now: datetime, env: Mapping[str, str] | None = None) -> str | None:
    """Require a fresh PASS from the strategic live-decision audit for BUYs."""
    if str(raw_order.get("side", "BUY")).upper() != "BUY":
        return None
    if not _env_true(_env_value(env, "TOSS_REQUIRE_STRATEGIC_HARNESS_AUDIT", "true")):
        return None
    path = Path(_env_value(env, "TOSS_STRATEGIC_HARNESS_AUDIT_JSON", str(root / "reports" / "harness" / "strategic_live_decision_harness_audit.json"))).expanduser()
    if not path.exists():
        return "strategic_harness_audit_missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return "strategic_harness_audit_invalid"
    if not isinstance(payload, Mapping) or str(payload.get("status") or "").upper() != "PASS":
        return "strategic_harness_audit_not_pass"
    generated = payload.get("generated_at_utc")
    if not generated:
        return "strategic_harness_audit_timestamp_missing"
    try:
        generated_at = datetime.fromisoformat(str(generated).replace("Z", "+00:00"))
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=timezone.utc)
    except ValueError:
        return "strategic_harness_audit_timestamp_invalid"
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    max_age = int(_env_value(env, "TOSS_STRATEGIC_HARNESS_AUDIT_MAX_AGE_SECONDS", "43200"))
    age = (now.astimezone(timezone.utc) - generated_at.astimezone(timezone.utc)).total_seconds()
    if age < -30 or age > max_age:
        return "strategic_harness_audit_stale"
    return None


def bad_event_violations(raw_order: Mapping[str, Any], candidate_payload: Mapping[str, Any]) -> list[str]:
    symbol = str(raw_order.get("symbol", "")).zfill(6)
    severe = {"trading_halt", "delisting_risk", "audit_opinion_adverse", "capital_raise", "cb_bw_issuance", "embezzlement", "lawsuit", "sanction", "earnings_shock", "pledged_share_liquidation"}
    events = []
    for source in (raw_order.get("event_tags"), raw_order.get("risk_tags"), candidate_payload.get("event_tags"), candidate_payload.get("risk_tags"), candidate_payload.get("news_events")):
        if isinstance(source, list):
            events.extend(source)
    for event in events:
        if isinstance(event, str) and event in severe:
            return [f"bad_event_veto:{event}"]
        if isinstance(event, Mapping):
            event_symbol = str(event.get("symbol") or event.get("ticker") or symbol).zfill(6)
            if event_symbol != symbol:
                continue
            event_type = str(event.get("event_type") or event.get("type") or "")
            severity = str(event.get("severity") or "").lower()
            direction = str(event.get("direction") or "").lower()
            if event_type in severe or severity in {"high", "critical"} or direction == "negative":
                return [f"bad_event_veto:{event_type or severity or direction}"]
    return []


def liquidity_quality_violations(raw_order: Mapping[str, Any], *, env: Mapping[str, str] | None = None) -> list[str]:
    violations: list[str] = []
    min_dollar_volume = float(_env_value(env, "TOSS_MIN_LIVE_DOLLAR_VOLUME_KRW", "1000000000"))
    max_spread_pct = float(_env_value(env, "TOSS_MAX_LIVE_SPREAD_PCT", "0.003"))
    dollar_volume = _float_or_none(raw_order.get("dollar_volume") or raw_order.get("prev_dollar_volume_krw") or raw_order.get("dollar_volume_krw"))
    if dollar_volume is None:
        violations.append("liquidity_quality_missing_dollar_volume")
    elif dollar_volume < min_dollar_volume:
        violations.append("liquidity_quality_low_dollar_volume")
    spread_pct = _spread_pct(raw_order)
    if spread_pct is None:
        violations.append("liquidity_quality_missing_spread")
    elif spread_pct > max_spread_pct:
        violations.append("liquidity_quality_wide_spread")
    return violations


def fill_probability_violation(raw_order: Mapping[str, Any], *, env: Mapping[str, str] | None = None) -> str | None:
    limit_price = _float_or_none(raw_order.get("limit_price"))
    current_price = _float_or_none(raw_order.get("current_price") or raw_order.get("last_price") or raw_order.get("quote_price"))
    if limit_price is None or limit_price <= 0:
        return "fill_probability_missing_limit_price"
    if current_price is None:
        return "fill_probability_missing_current_price"
    max_distance = float(_env_value(env, "TOSS_MAX_BUY_LIMIT_DISTANCE_PCT", "0.003"))
    side = str(raw_order.get("side", "BUY")).upper()
    if side == "BUY":
        distance = current_price / limit_price - 1.0
        if distance > max_distance:
            return "fill_probability_low_limit_too_far_below_current"
    elif side == "SELL":
        distance = limit_price / current_price - 1.0
        if distance > max_distance:
            return "fill_probability_low_limit_too_far_above_current"
    return None


def append_divergence_record(path: Path, *, now: datetime, candidate_payload: Mapping[str, Any], raw_order: Mapping[str, Any], result: Mapping[str, Any], ledger_key_value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "timestamp": now.isoformat(),
        "as_of": candidate_payload.get("as_of") or candidate_payload.get("generated_for"),
        "policy_id": candidate_payload.get("policy_id"),
        "situation": candidate_payload.get("situation"),
        "symbol": str(raw_order.get("symbol", "")).zfill(6),
        "name": raw_order.get("name"),
        "side": raw_order.get("side", "BUY"),
        "quantity": raw_order.get("quantity"),
        "limit_price": raw_order.get("limit_price"),
        "notional_krw": raw_order.get("notional_krw"),
        "result_status": result.get("status"),
        "violations": result.get("violations", []),
        "ledger_key": ledger_key_value,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")


class LiveOrderLedger:
    def __init__(self, path: Path):
        self.path = path
        self.corrupt = False

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                self.corrupt = True
                continue
            if isinstance(row, dict):
                rows.append(row)
            else:
                self.corrupt = True
        return rows

    def latest_status_by_key(self) -> dict[str, str]:
        latest: dict[str, str] = {}
        for row in self.records():
            key = row.get("ledger_key")
            if key:
                latest[str(key)] = str(row.get("status") or "")
        return latest

    def has_live_submission(self, key: str) -> bool:
        # Only the latest ledger row for a key should block duplicates. A later
        # FILLED/CANCELED/REJECTED reconciliation row releases the key.
        return self.latest_status_by_key().get(key) in {"PENDING_SUBMIT", "SUBMITTED", "PARTIALLY_FILLED", "UNKNOWN", "CANCEL_REQUESTED"}

    def has_other_live_sell(self, *, symbol: str, key: str) -> bool:
        suffix = f":{str(symbol).zfill(6)}:SELL"
        active = {"PENDING_SUBMIT", "SUBMITTED", "PARTIALLY_FILLED", "UNKNOWN", "CANCEL_REQUESTED"}
        return any(
            existing_key != key and existing_key.endswith(suffix) and status in active
            for existing_key, status in self.latest_status_by_key().items()
        )

    def reserve_if_absent(self, key: str, row: dict[str, Any]) -> bool:
        """Atomically reserve a key across cron and manual processes."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.seek(0)
                latest: dict[str, str] = {}
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        self.corrupt = True
                        return False
                    if not isinstance(record, dict):
                        self.corrupt = True
                        return False
                    record_key = record.get("ledger_key")
                    if record_key:
                        latest[str(record_key)] = str(record.get("status") or "")
                active = {"PENDING_SUBMIT", "SUBMITTED", "PARTIALLY_FILLED", "UNKNOWN", "CANCEL_REQUESTED"}
                if latest.get(key) in active:
                    return False
                if key.endswith(":SELL"):
                    parts = key.rsplit(":", 2)
                    sell_suffix = f":{parts[-2]}:SELL" if len(parts) == 3 else ""
                    if sell_suffix and any(
                        other_key != key and other_key.endswith(sell_suffix) and status in active
                        for other_key, status in latest.items()
                    ):
                        return False
                handle.seek(0, os.SEEK_END)
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
                return True
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def append(self, row: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def ledger_key(intent: OrderIntent, candidate_payload: Mapping[str, Any]) -> str:
    as_of = candidate_payload.get("as_of") or candidate_payload.get("generated_for") or "unknown-date"
    return f"{as_of}:{intent.strategy_id}:{intent.symbol}:{intent.side}"


def _order_strategy_id(base_strategy_id: str, raw_order: Mapping[str, Any]) -> str:
    """Add an immutable semantic scope for intentional same-day staged exits."""
    scope = str(raw_order.get("idempotency_scope") or "").strip()
    if not scope:
        return base_strategy_id
    safe = "".join(ch for ch in scope if ch.isalnum() or ch in {"_", "-"})[:48]
    return f"{base_strategy_id}@{safe}" if safe else base_strategy_id


def _intraday_submit_attempt_count(rows: list[dict[str, Any]], today: str) -> int:
    """Count broker attempts without double-counting reconcile status echoes."""
    def counts_as_buy_attempt(row: Mapping[str, Any]) -> bool:
        key = str(row.get("ledger_key") or "")
        side = str(row.get("side") or "").upper()
        result = row.get("result") if isinstance(row.get("result"), Mapping) else {}
        side = side or str(result.get("side") or "").upper()
        # Legacy rows may lack side. Count unknown rows conservatively, but
        # explicitly identified SELL exits never consume the BUY churn cap.
        return side != "SELL" and not key.endswith(":SELL")

    pending = [
        row for row in rows
        if row.get("status") == "PENDING_SUBMIT"
        and str(row.get("timestamp", "")).startswith(today)
        and counts_as_buy_attempt(row)
    ]
    pending_keys = {str(row.get("ledger_key") or "") for row in pending}
    legacy_submitted_keys = {
        str(row.get("ledger_key") or "")
        for row in rows
        if row.get("status") == "SUBMITTED"
        and str(row.get("timestamp", "")).startswith(today)
        and counts_as_buy_attempt(row)
        and str(row.get("ledger_key") or "") not in pending_keys
    }
    return len(pending) + len(legacy_submitted_keys)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _redact_result(result: Mapping[str, Any]) -> dict[str, Any]:
    clean = dict(result)
    if isinstance(clean.get("payload"), dict):
        clean["payload"] = dict(clean["payload"])
    return clean


def _trading_day_lag(start: date, end: date, env: Mapping[str, str] | None = None) -> int | None:
    if start > end:
        return None
    if start == end:
        return 0
    current = end
    lag = 0
    while current > start:
        current = current.fromordinal(current.toordinal() - 1)
        if is_krx_trading_day(current, env=env):
            lag += 1
    return lag


def _latest_panel_date(path: Path) -> date | None:
    if not path.exists():
        return None
    latest: date | None = None
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                text = row.get("Date") or row.get("date")
                if not text:
                    continue
                try:
                    value = date.fromisoformat(str(text)[:10])
                except ValueError:
                    continue
                latest = value if latest is None or value > latest else latest
    except Exception:
        return None
    return latest


def _json_date_field(path: Path, field: str) -> date | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    value = payload.get(field) if isinstance(payload, dict) else None
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _spread_pct(raw_order: Mapping[str, Any]) -> float | None:
    spread_pct = _float_or_none(raw_order.get("spread_pct"))
    if spread_pct is not None:
        return spread_pct
    bid = _float_or_none(raw_order.get("best_bid") or raw_order.get("bid_price"))
    ask = _float_or_none(raw_order.get("best_ask") or raw_order.get("ask_price"))
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2.0
    return (ask - bid) / mid if mid > 0 else None


def _env_value(env: Mapping[str, str] | None, key: str, default: str) -> str:
    source = os.environ if env is None else env
    return str(source.get(key, default))


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _env_true(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_false(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"0", "false", "no", "n", "off"}


def _env_int(env: Mapping[str, str] | None, key: str, default: int) -> int:
    try:
        return int(float((env or {}).get(key, default)))
    except (TypeError, ValueError):
        return default


def _blank_to_none(value: str | None) -> str | None:
    if value is None or value.strip() == "":
        return None
    return value.strip()


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
