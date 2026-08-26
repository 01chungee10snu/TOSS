"""Collect executable ETF forward-paper evidence using read-only KIS data.

This script never calls an order endpoint. It:
- reads account equity and candidate holdings,
- collects KIS quote/order-book/recent daily price evidence,
- evaluates spread, 20-session traded value, and top-of-book depth,
- records side-aware paper slippage versus the midpoint,
- maintains an independent virtual forward-paper portfolio,
- permits at most one simulated rebalance per calendar month,
- persists forward evidence without enabling live promotion automatically.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from toss_alpha.connectors.kis_readonly import KisReadOnlyClient
from toss_alpha.execution.live_ready import LiveExecutionConfig

BASE = Path(__file__).resolve().parents[1]
VALIDATOR = BASE / "scripts" / "validate_executable_etf_portfolio.py"
OUT = BASE / "reports" / "harness" / "executable_etf_paper_latest.json"
FORWARD_STATE = BASE / "reports" / "harness" / "executable_etf_forward_state.json"
FORWARD_HISTORY = BASE / "reports" / "harness" / "executable_etf_forward_history.jsonl"

STRATEGY_ID = "KODEX_KOSPI_50_KODEX_SHORT_BOND_50_TARGET"
CANDIDATE = {"226490": 0.50, "153130": 0.50}
EXPECTED = {"226490": "KODEX 코스피", "153130": "KODEX 단기채권"}
KST = timezone(timedelta(hours=9))

MAX_SPREAD_BPS = 30.0
MAX_ORDER_ADV_SHARE = 0.001  # 0.10% of trailing 20-session traded value
MAX_TOP_DEPTH_SHARE = 0.20   # at most 20% of best-level displayed notional
STRESS_COST_BPS_PER_SIDE = 75.0
REQUIRED_MONTHLY_REBALANCES = 3


def load_validator():
    spec = importlib.util.spec_from_file_location("executable_etf_validator", VALIDATOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def first_summary(payload: dict) -> dict:
    rows = payload.get("output2") or []
    if isinstance(rows, list):
        return rows[0] if rows else {}
    return rows if isinstance(rows, dict) else {}


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_dict(payload: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, dict):
            return value
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value[0]
    return {}


def _daily_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("output", "output1", "result"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


def market_evidence_from_payloads(
    *,
    code: str,
    name: str,
    quote_payload: dict[str, Any],
    orderbook_payload: dict[str, Any],
    daily_payload: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Normalize KIS market data into fail-closed execution evidence."""
    now = now or datetime.now(timezone.utc)
    quote = _first_dict(quote_payload, "output", "output2", "result")
    orderbook = _first_dict(orderbook_payload, "output1", "output", "result")

    last = _float(quote.get("stck_prpr"))
    bid = _float(orderbook.get("bidp1"))
    ask = _float(orderbook.get("askp1"))
    bid_qty = _float(orderbook.get("bidp_rsqn1"))
    ask_qty = _float(orderbook.get("askp_rsqn1"))
    volume = _float(quote.get("acml_vol"))

    midpoint = (bid + ask) / 2.0 if bid and ask and bid > 0 and ask > 0 else None
    spread_bps = ((ask - bid) / midpoint * 10_000.0) if midpoint and ask >= bid else None

    today_kst = now.astimezone(KST).strftime("%Y%m%d")
    hist: list[dict[str, Any]] = []
    current_session_row_present = False
    for row in _daily_rows(daily_payload):
        date = str(row.get("stck_bsop_date") or "")
        close = _float(row.get("stck_clpr"))
        hist_volume = _float(row.get("acml_vol"))
        if date == today_kst:
            current_session_row_present = True
            continue
        if not date or not close or close <= 0 or hist_volume is None or hist_volume < 0:
            continue
        hist.append({"date": date, "close": close, "volume": hist_volume, "traded_value": close * hist_volume})
    hist.sort(key=lambda row: row["date"], reverse=True)
    trailing = hist[:20]
    avg_dtv_20 = (
        sum(float(row["traded_value"]) for row in trailing) / len(trailing)
        if len(trailing) >= 20
        else None
    )
    latest_history_date = trailing[0]["date"] if trailing else None

    return {
        "code": str(code).zfill(6),
        "name": name,
        "observed_at_utc": now.astimezone(timezone.utc).isoformat(),
        "last": last,
        "bid": bid,
        "ask": ask,
        "midpoint": midpoint,
        "volume": volume,
        "spread_bps": spread_bps,
        "best_bid_quantity": bid_qty,
        "best_ask_quantity": ask_qty,
        "best_bid_notional_krw": bid * bid_qty if bid and bid_qty is not None else None,
        "best_ask_notional_krw": ask * ask_qty if ask and ask_qty is not None else None,
        "history_sessions_used": len(trailing),
        "history_latest_date": latest_history_date,
        "current_session_row_present": current_session_row_present,
        "avg_traded_value_20d_krw": avg_dtv_20,
        "market_data_complete": bool(
            last
            and last > 0
            and bid
            and bid > 0
            and ask
            and ask > 0
            and bid_qty is not None
            and bid_qty > 0
            and ask_qty is not None
            and ask_qty > 0
            and avg_dtv_20 is not None
            and avg_dtv_20 > 0
            and current_session_row_present
        ),
    }


def order_execution_evidence(
    order: dict[str, Any],
    market: dict[str, Any],
    *,
    max_order_krw: float,
    max_spread_bps: float = MAX_SPREAD_BPS,
    max_order_adv_share: float = MAX_ORDER_ADV_SHARE,
    max_top_depth_share: float = MAX_TOP_DEPTH_SHARE,
) -> dict[str, Any]:
    """Evaluate one hypothetical order against current executable liquidity."""
    side = str(order.get("side") or "").upper()
    qty = int(order.get("quantity") or 0)
    if side == "BUY":
        execution_price = _float(market.get("ask"))
        top_qty = _float(market.get("best_ask_quantity"))
    elif side == "SELL":
        execution_price = _float(market.get("bid"))
        top_qty = _float(market.get("best_bid_quantity"))
    else:
        execution_price = None
        top_qty = None

    midpoint = _float(market.get("midpoint"))
    avg_dtv = _float(market.get("avg_traded_value_20d_krw"))
    spread_bps = _float(market.get("spread_bps"))
    notional = execution_price * qty if execution_price and qty > 0 else None
    top_depth_notional = execution_price * top_qty if execution_price and top_qty is not None else None
    adv_share = notional / avg_dtv if notional is not None and avg_dtv and avg_dtv > 0 else None
    top_depth_share = (
        notional / top_depth_notional
        if notional is not None and top_depth_notional and top_depth_notional > 0
        else None
    )
    slippage_bps = None
    if execution_price is not None and midpoint and midpoint > 0:
        if side == "BUY":
            slippage_bps = (execution_price / midpoint - 1.0) * 10_000.0
        elif side == "SELL":
            slippage_bps = (midpoint / execution_price - 1.0) * 10_000.0

    evidence_complete = all(
        value is not None
        for value in (execution_price, midpoint, avg_dtv, spread_bps, top_qty, adv_share, top_depth_share, slippage_bps)
    )
    checks = {
        "spread_passed": spread_bps is not None and spread_bps <= max_spread_bps,
        "order_vs_20d_average_traded_value_passed": adv_share is not None and adv_share <= max_order_adv_share,
        "order_vs_best_level_depth_passed": top_depth_share is not None and top_depth_share <= max_top_depth_share,
        "max_order_krw_passed": notional is not None and notional <= max_order_krw,
    }
    return {
        "code": str(order.get("code") or "").zfill(6),
        "side": side,
        "quantity": qty,
        "execution_price": execution_price,
        "midpoint": midpoint,
        "notional_krw": notional,
        "spread_bps": spread_bps,
        "paper_slippage_bps_vs_mid": slippage_bps,
        "avg_traded_value_20d_krw": avg_dtv,
        "order_adv_share": adv_share,
        "best_level_quantity": top_qty,
        "best_level_notional_krw": top_depth_notional,
        "order_best_level_depth_share": top_depth_share,
        "evidence_complete": evidence_complete,
        **checks,
        "all_checks_passed": evidence_complete and all(checks.values()),
    }


def build_liquidity_gate(
    markets: dict[str, dict[str, Any]],
    order_evidence: list[dict[str, Any]],
    *,
    max_spread_bps: float = MAX_SPREAD_BPS,
    max_order_adv_share: float = MAX_ORDER_ADV_SHARE,
    max_top_depth_share: float = MAX_TOP_DEPTH_SHARE,
) -> dict[str, Any]:
    market_complete = all(bool(market.get("market_data_complete")) for market in markets.values())
    spread_passed = all(
        _float(market.get("spread_bps")) is not None
        and float(market["spread_bps"]) <= max_spread_bps
        for market in markets.values()
    )
    order_evidence_complete = all(bool(row.get("evidence_complete")) for row in order_evidence)
    orders_passed = all(bool(row.get("all_checks_passed")) for row in order_evidence)
    return {
        "max_spread_bps": max_spread_bps,
        "max_order_adv_share": max_order_adv_share,
        "max_top_depth_share": max_top_depth_share,
        "market_data_complete": market_complete,
        "spread_passed": spread_passed,
        "order_vs_20d_average_traded_value_passed": all(
            bool(row.get("order_vs_20d_average_traded_value_passed")) for row in order_evidence
        ),
        "order_vs_best_ask_or_bid_depth_passed": all(
            bool(row.get("order_vs_best_level_depth_passed")) for row in order_evidence
        ),
        "order_evidence_complete": order_evidence_complete,
        "all_order_checks_passed": orders_passed,
        "live_liquidity_evidence_complete": market_complete and order_evidence_complete,
        "liquidity_gate_passed": market_complete and spread_passed and order_evidence_complete and orders_passed,
    }


def regular_market_session(now: datetime) -> bool:
    local = now.astimezone(KST)
    if local.weekday() >= 5:
        return False
    minutes = local.hour * 60 + local.minute
    return 9 * 60 <= minutes < 15 * 60 + 30


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_or_initialize_forward_state(*, initial_equity: float, now: datetime) -> dict[str, Any]:
    if FORWARD_STATE.exists():
        try:
            payload = json.loads(FORWARD_STATE.read_text(encoding="utf-8"))
            if payload.get("strategy") == STRATEGY_ID:
                return payload
        except Exception:
            pass
    return {
        "schema_version": 1,
        "strategy": STRATEGY_ID,
        "created_at_utc": now.astimezone(timezone.utc).isoformat(),
        "initial_equity_krw": float(initial_equity),
        "cash_krw": float(initial_equity),
        "positions": {code: 0 for code in CANDIDATE},
        "last_rebalance_month": None,
        "completed_monthly_rebalances": 0,
        "rebalance_events": [],
        "equity_history": [],
    }


def mark_to_market(state: dict[str, Any], markets: dict[str, dict[str, Any]]) -> float:
    equity = float(state.get("cash_krw") or 0.0)
    for code in CANDIDATE:
        qty = int((state.get("positions") or {}).get(code) or 0)
        market = markets[code]
        price = _float(market.get("midpoint")) or _float(market.get("last")) or 0.0
        equity += qty * price
    return float(equity)


def simulate_forward_fills(
    *,
    cash: float,
    positions: dict[str, int],
    orders: list[dict[str, Any]],
    evidence_by_key: dict[tuple[str, str], dict[str, Any]],
    cost_bps_per_side: float,
) -> tuple[float, dict[str, int], list[dict[str, Any]], str | None]:
    """Atomically simulate top-of-book fills; return original state on failure."""
    next_cash = float(cash)
    next_positions = {str(k).zfill(6): int(v) for k, v in positions.items()}
    fills: list[dict[str, Any]] = []
    cost_rate = cost_bps_per_side / 10_000.0

    for order in sorted(orders, key=lambda row: 0 if str(row.get("side")).upper() == "SELL" else 1):
        code = str(order.get("code") or "").zfill(6)
        side = str(order.get("side") or "").upper()
        qty = int(order.get("quantity") or 0)
        evidence = evidence_by_key.get((code, side)) or {}
        price = _float(evidence.get("execution_price"))
        if qty <= 0 or price is None or price <= 0 or not evidence.get("all_checks_passed"):
            return cash, positions, [], f"unexecutable_order:{code}:{side}"
        notional = qty * price
        stress_cost = notional * cost_rate
        if side == "SELL":
            held = int(next_positions.get(code) or 0)
            if qty > held:
                return cash, positions, [], f"insufficient_position:{code}"
            next_positions[code] = held - qty
            next_cash += notional - stress_cost
        elif side == "BUY":
            required = notional + stress_cost
            if required > next_cash + 1e-6:
                return cash, positions, [], f"insufficient_virtual_cash:{code}"
            next_cash -= required
            next_positions[code] = int(next_positions.get(code) or 0) + qty
        else:
            return cash, positions, [], f"unsupported_side:{side}"
        fills.append(
            {
                "code": code,
                "side": side,
                "quantity": qty,
                "fill_price": price,
                "notional_krw": notional,
                "stress_cost_krw": stress_cost,
                "paper_slippage_bps_vs_mid": evidence.get("paper_slippage_bps_vs_mid"),
            }
        )
    return next_cash, next_positions, fills, None


def update_equity_history(state: dict[str, Any], *, now: datetime, equity: float) -> None:
    date = now.astimezone(KST).date().isoformat()
    history = list(state.get("equity_history") or [])
    row = {"date": date, "equity_krw": round(float(equity), 2)}
    if history and history[-1].get("date") == date:
        history[-1] = row
    else:
        history.append(row)
    state["equity_history"] = history


def forward_metrics(state: dict[str, Any]) -> dict[str, Any]:
    history = [row for row in state.get("equity_history") or [] if _float(row.get("equity_krw")) is not None]
    initial = float(state.get("initial_equity_krw") or 0.0)
    if not history or initial <= 0:
        return {"observations": len(history), "total_return_pct": None, "max_drawdown_pct": None}
    values = [float(row["equity_krw"]) for row in history]
    peak = values[0]
    max_dd = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            max_dd = min(max_dd, value / peak - 1.0)
    return {
        "observations": len(values),
        "start_date": history[0]["date"],
        "end_date": history[-1]["date"],
        "latest_equity_krw": round(values[-1], 2),
        "total_return_pct": round((values[-1] / initial - 1.0) * 100.0, 4),
        "max_drawdown_pct": round(max_dd * 100.0, 4),
    }


def advance_forward_portfolio(
    *,
    state: dict[str, Any],
    validator: Any,
    markets: dict[str, dict[str, Any]],
    now: datetime,
    max_position_pct: float,
    max_order_krw: float,
    max_spread_bps: float = MAX_SPREAD_BPS,
    max_order_adv_share: float = MAX_ORDER_ADV_SHARE,
    max_top_depth_share: float = MAX_TOP_DEPTH_SHARE,
    cost_bps_per_side: float = STRESS_COST_BPS_PER_SIDE,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Advance virtual forward paper state by at most one rebalance per month."""
    next_state = deepcopy(state)
    month = now.astimezone(KST).strftime("%Y-%m")
    equity_before = mark_to_market(next_state, markets)
    prices = {
        code: (_float(markets[code].get("ask")) or _float(markets[code].get("last")) or 0.0)
        for code in CANDIDATE
    }
    allocation = validator.allocate_integer_shares(
        equity=equity_before,
        prices=prices,
        target_weights=CANDIDATE,
        cost_bps_per_side=cost_bps_per_side,
        max_position_pct=max_position_pct,
    )
    current = {code: int((next_state.get("positions") or {}).get(code) or 0) for code in CANDIDATE}
    orders = validator.build_rebalance_orders(current=current, target=allocation["quantities"])
    evidence = [
        order_execution_evidence(
            order,
            markets[str(order["code"]).zfill(6)],
            max_order_krw=max_order_krw,
            max_spread_bps=max_spread_bps,
            max_order_adv_share=max_order_adv_share,
            max_top_depth_share=max_top_depth_share,
        )
        for order in orders
    ]
    gate = build_liquidity_gate(
        markets,
        evidence,
        max_spread_bps=max_spread_bps,
        max_order_adv_share=max_order_adv_share,
        max_top_depth_share=max_top_depth_share,
    )
    rebalance_due = next_state.get("last_rebalance_month") != month
    market_session_open = regular_market_session(now)
    fills: list[dict[str, Any]] = []
    execution_error = None
    completed_this_run = False

    if rebalance_due and market_session_open and gate["liquidity_gate_passed"]:
        evidence_by_key = {(row["code"], row["side"]): row for row in evidence}
        new_cash, new_positions, fills, execution_error = simulate_forward_fills(
            cash=float(next_state.get("cash_krw") or 0.0),
            positions=current,
            orders=orders,
            evidence_by_key=evidence_by_key,
            cost_bps_per_side=cost_bps_per_side,
        )
        if execution_error is None:
            next_state["cash_krw"] = round(new_cash, 6)
            next_state["positions"] = new_positions
            next_state["last_rebalance_month"] = month
            next_state["completed_monthly_rebalances"] = int(next_state.get("completed_monthly_rebalances") or 0) + 1
            next_state.setdefault("rebalance_events", []).append(
                {
                    "month": month,
                    "completed_at_utc": now.astimezone(timezone.utc).isoformat(),
                    "equity_before_krw": round(equity_before, 2),
                    "target_quantities": allocation["quantities"],
                    "fills": fills,
                    "zero_trade_rebalance": len(orders) == 0,
                }
            )
            completed_this_run = True

    equity_after = mark_to_market(next_state, markets)
    update_equity_history(next_state, now=now, equity=equity_after)
    metrics = forward_metrics(next_state)
    report = {
        "rebalance_month": month,
        "rebalance_due": rebalance_due,
        "market_session_open": market_session_open,
        "completed_this_run": completed_this_run,
        "execution_error": execution_error,
        "equity_before_krw": round(equity_before, 2),
        "equity_after_krw": round(equity_after, 2),
        "virtual_cash_krw": round(float(next_state.get("cash_krw") or 0.0), 2),
        "virtual_positions": next_state.get("positions") or {},
        "target": allocation,
        "paper_orders": orders,
        "order_execution_evidence": evidence,
        "liquidity_gate": gate,
        "completed_monthly_rebalances": int(next_state.get("completed_monthly_rebalances") or 0),
        "required_monthly_rebalances": REQUIRED_MONTHLY_REBALANCES,
        "metrics": metrics,
    }
    return next_state, report


def append_forward_history(payload: dict[str, Any]) -> None:
    FORWARD_HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with FORWARD_HISTORY.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def main() -> None:
    now = datetime.now(timezone.utc)
    validator = load_validator()
    # Manual invocations should share the same durable token cache as scheduled
    # runs. Without this, each read-only KIS request can trigger tokenP again
    # and hit KIS's token issuance frequency limit.
    os.environ.setdefault(
        "KIS_ACCESS_TOKEN_CACHE",
        str(BASE / "reports" / "harness" / "kis_access_token_cache.json"),
    )
    cfg = LiveExecutionConfig.from_env()
    client = KisReadOnlyClient(
        app_key=cfg.app_key or "",
        app_secret=cfg.app_secret or "",
        cano=cfg.cano or "",
        account_product_code=cfg.account_product_code or "01",
    )

    balance = client.balance_all()["json"]
    summary = first_summary(balance)
    equity = float(summary.get("tot_evlu_amt") or summary.get("nass_amt") or 0)
    cash = float(summary.get("dnca_tot_amt") or summary.get("prvs_rcdl_excc_amt") or 0)
    if equity <= 0:
        raise RuntimeError("KIS account equity unavailable; forward paper remains fail-closed")

    # Only candidate ETF holdings belong in this rebalance plan. Other account
    # positions must never become implicit SELL suggestions.
    raw_current = {
        str(row.get("pdno") or "").zfill(6): int(float(row.get("hldg_qty") or 0))
        for row in balance.get("output1") or []
        if float(row.get("hldg_qty") or 0) > 0
    }
    current = {code: int(raw_current.get(code) or 0) for code in CANDIDATE}

    markets: dict[str, dict[str, Any]] = {}
    executable_prices: dict[str, float] = {}
    for code, name in EXPECTED.items():
        quote_payload = client.quote(code)["json"] or {}
        orderbook_payload = client.orderbook(code)["json"] or {}
        daily_payload = client.daily_prices(code, period_div_code="D", adjusted=True)["json"] or {}
        evidence = market_evidence_from_payloads(
            code=code,
            name=name,
            quote_payload=quote_payload,
            orderbook_payload=orderbook_payload,
            daily_payload=daily_payload,
            now=now,
        )
        markets[code] = evidence
        price = _float(evidence.get("ask")) or _float(evidence.get("last"))
        if price is None or price <= 0:
            raise RuntimeError(f"missing executable price for {code}")
        executable_prices[code] = price

    max_position_pct = float(os.getenv("TOSS_MAX_POSITION_PCT", "0.50"))
    max_order_krw = float(os.getenv("TOSS_MAX_ORDER_KRW", "250000"))
    max_spread_bps = float(os.getenv("TOSS_ETF_MAX_SPREAD_BPS", str(MAX_SPREAD_BPS)))
    max_order_adv_share = float(os.getenv("TOSS_ETF_MAX_ORDER_ADV_SHARE", str(MAX_ORDER_ADV_SHARE)))
    max_top_depth_share = float(os.getenv("TOSS_ETF_MAX_TOP_DEPTH_SHARE", str(MAX_TOP_DEPTH_SHARE)))
    stress_cost_bps = float(os.getenv("TOSS_ETF_FORWARD_COST_BPS_PER_SIDE", str(STRESS_COST_BPS_PER_SIDE)))

    allocation = validator.allocate_integer_shares(
        equity=equity,
        prices=executable_prices,
        target_weights=CANDIDATE,
        cost_bps_per_side=stress_cost_bps,
        max_position_pct=max_position_pct,
    )
    orders = validator.build_rebalance_orders(current=current, target=allocation["quantities"])
    order_evidence = [
        order_execution_evidence(
            order,
            markets[str(order["code"]).zfill(6)],
            max_order_krw=max_order_krw,
            max_spread_bps=max_spread_bps,
            max_order_adv_share=max_order_adv_share,
            max_top_depth_share=max_top_depth_share,
        )
        for order in orders
    ]
    evidence_by_key = {(row["code"], row["side"]): row for row in order_evidence}
    decorated_orders = []
    for order in orders:
        row = dict(order)
        ev = evidence_by_key[(str(order["code"]).zfill(6), str(order["side"]).upper())]
        row.update(
            {
                "paper_execution_price": ev["execution_price"],
                "notional_krw": ev["notional_krw"],
                "paper_slippage_bps_vs_mid": ev["paper_slippage_bps_vs_mid"],
                "within_max_order_krw": ev["max_order_krw_passed"],
            }
        )
        decorated_orders.append(row)
    liquidity_gate = build_liquidity_gate(
        markets,
        order_evidence,
        max_spread_bps=max_spread_bps,
        max_order_adv_share=max_order_adv_share,
        max_top_depth_share=max_top_depth_share,
    )

    state = load_or_initialize_forward_state(initial_equity=equity, now=now)
    next_state, forward = advance_forward_portfolio(
        state=state,
        validator=validator,
        markets=markets,
        now=now,
        max_position_pct=max_position_pct,
        max_order_krw=max_order_krw,
        max_spread_bps=max_spread_bps,
        max_order_adv_share=max_order_adv_share,
        max_top_depth_share=max_top_depth_share,
        cost_bps_per_side=stress_cost_bps,
    )
    _atomic_json(FORWARD_STATE, next_state)

    completed = int(forward["completed_monthly_rebalances"])
    forward_gate_passed = completed >= REQUIRED_MONTHLY_REBALANCES
    forward_paper_gate = {
        "required_monthly_rebalances": REQUIRED_MONTHLY_REBALANCES,
        "completed_monthly_rebalances": completed,
        "passed": forward_gate_passed,
        "latest_rebalance_due": forward["rebalance_due"],
        "latest_completed_this_run": forward["completed_this_run"],
        "latest_liquidity_gate_passed": forward["liquidity_gate"]["liquidity_gate_passed"],
        "forward_metrics": forward["metrics"],
    }
    if forward_gate_passed:
        live_promotion = "BLOCKED_PENDING_EXPLICIT_LIVE_PROMOTION_REVIEW"
    elif liquidity_gate["live_liquidity_evidence_complete"]:
        live_promotion = "BLOCKED_FORWARD_PAPER_REBALANCE_COUNT_INCOMPLETE"
    else:
        live_promotion = "BLOCKED_FORWARD_PAPER_AND_LIQUIDITY_EVIDENCE_MISSING"

    payload = {
        "generated_at": now.astimezone(timezone.utc).isoformat(),
        "mode": "paper_read_only",
        "order_submission": False,
        "strategy": STRATEGY_ID,
        "target_weights": CANDIDATE,
        "account": {
            "equity_krw": equity,
            "cash_krw": cash,
            "candidate_current_quantities": current,
        },
        "market_evidence": markets,
        "target": allocation,
        "paper_orders": decorated_orders,
        "order_execution_evidence": order_evidence,
        "liquidity_gate": liquidity_gate,
        "forward_shadow": forward,
        "forward_paper_gate": forward_paper_gate,
        "research_buy_hold": os.getenv("TOSS_RESEARCH_BUY_HOLD", "true").lower() == "true",
        "live_promotion": live_promotion,
    }
    _atomic_json(OUT, payload)
    append_forward_history(
        {
            "generated_at": payload["generated_at"],
            "strategy": STRATEGY_ID,
            "order_submission": False,
            "market_evidence": markets,
            "paper_orders": decorated_orders,
            "liquidity_gate": liquidity_gate,
            "forward_shadow": forward,
            "forward_paper_gate": forward_paper_gate,
            "live_promotion": live_promotion,
        }
    )

    # Keep stdout intentionally free of account balances and raw broker payloads.
    print(f"strategy={STRATEGY_ID}")
    print("order_submission=False")
    print(f"liquidity_gate_passed={liquidity_gate['liquidity_gate_passed']}")
    print(f"forward_completed={completed}/{REQUIRED_MONTHLY_REBALANCES}")
    print(f"forward_return_pct={forward['metrics'].get('total_return_pct')}")
    print(f"live_promotion={live_promotion}")
    print(f"output={OUT}")


if __name__ == "__main__":
    main()
