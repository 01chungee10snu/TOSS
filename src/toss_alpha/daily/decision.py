"""Daily personal investment decision engine wired to safe TOSS primitives.

This module is intentionally fail-closed: it can read panel/account data,
score candidates, review holdings, and create manual drafts, but it never
submits live orders.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Protocol

import pandas as pd

from toss_alpha.agents.execution_draft import build_manual_draft
from toss_alpha.data.schema import AccountSnapshot, OrderIntent, PositionSnapshot, RiskDecision
from toss_alpha.risk import RiskPolicy, validate_order_intent

DEFAULT_DAILY_REPORT_DIR = Path("reports/daily")


class AccountSource(Protocol):
    def account_snapshot(self) -> AccountSnapshot: ...

    def position_snapshots(self) -> list[PositionSnapshot]: ...


def run_daily_decision(
    *,
    panel_csv: str | Path,
    symbols: list[str],
    holdings_path: str | Path | None = None,
    account_source: AccountSource | None = None,
    slow_veto_events_path: str | Path | None = None,
    out_dir: str | Path | None = None,
    as_of: str | None = None,
    max_notional_krw: float = 100_000,
    portfolio_value_krw: float | None = None,
) -> dict[str, Any]:
    """Create a daily decision packet from local market data and optional TOSS account state."""
    if holdings_path and account_source is not None:
        raise ValueError("choose either holdings_path or account_source, not both")
    symbol_list = [str(symbol).zfill(6) for symbol in symbols]
    report_dir = Path(out_dir) if out_dir else DEFAULT_DAILY_REPORT_DIR
    report_dir.mkdir(parents=True, exist_ok=True)

    panel = _load_panel(panel_csv, symbol_list, as_of=as_of)
    latest_date = _latest_date(panel)
    regime = _classify_regime(panel)
    slow_veto = _load_slow_veto(slow_veto_events_path, symbol_list)
    candidates = _apply_slow_veto(_score_candidates(panel, regime=regime), slow_veto)
    account = _load_account(holdings_path=holdings_path, account_source=account_source)
    effective_portfolio_value = float(
        portfolio_value_krw
        or account.get("total_equity_krw")
        or account.get("cash_krw")
        or max_notional_krw * 10
    )
    holdings_review = _review_holdings(account["positions"], candidates, portfolio_value_krw=effective_portfolio_value)
    manual_drafts = _build_candidate_drafts(
        candidates,
        max_notional_krw=max_notional_krw,
        portfolio_value_krw=effective_portfolio_value,
    )

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    stem = f"daily_decision_{latest_date}_{timestamp}"
    report_path = report_dir / f"{stem}.md"
    json_path = report_dir / f"{stem}.json"
    result = {
        "mode": "manual_draft_only",
        "live_order_submitted": False,
        "panel_csv": str(panel_csv),
        "as_of": latest_date,
        "regime": regime,
        "slow_veto": slow_veto,
        "account": account,
        "candidates": candidates,
        "holdings_review": holdings_review,
        "manual_drafts": manual_drafts,
        "report_path": str(report_path),
        "json_path": str(json_path),
        "disclaimer": "Research/manual-draft only. Not investment advice. No live orders submitted.",
    }
    report_path.write_text(_render_markdown(result), encoding="utf-8")
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return result


def _load_panel(panel_csv: str | Path, symbols: list[str], *, as_of: str | None) -> pd.DataFrame:
    panel = pd.read_csv(panel_csv, dtype={"code": str}, parse_dates=["Date"])
    required = {"Date", "code", "Close", "Open", "High", "Low", "Volume"}
    missing = required - set(panel.columns)
    if missing:
        raise ValueError(f"panel missing columns: {sorted(missing)}")
    filtered = panel[panel["code"].astype(str).str.zfill(6).isin(symbols)].copy()
    filtered["code"] = filtered["code"].astype(str).str.zfill(6)
    if as_of:
        filtered = filtered[filtered["Date"] <= pd.Timestamp(as_of)]
    if filtered.empty:
        raise ValueError("panel contains no rows for requested symbols/date")
    return filtered.sort_values(["code", "Date"])


def _latest_date(panel: pd.DataFrame) -> str:
    return pd.Timestamp(panel["Date"].max()).date().isoformat()


def _classify_regime(panel: pd.DataFrame) -> dict[str, Any]:
    per_symbol: list[dict[str, float]] = []
    for symbol, group in panel.groupby("code"):
        closes = [float(value) for value in group.sort_values("Date")["Close"].tolist()]
        if len(closes) < 21:
            continue
        ret20 = closes[-1] / closes[-21] - 1.0
        per_symbol.append({"symbol": str(symbol), "ret20": ret20})
    breadth = mean(1.0 if row["ret20"] > 0 else 0.0 for row in per_symbol) if per_symbol else 0.0
    avg_ret20 = mean(row["ret20"] for row in per_symbol) if per_symbol else 0.0
    if breadth >= 0.6 and avg_ret20 > 0.02:
        status = "risk_on"
    elif breadth <= 0.4 and avg_ret20 < -0.02:
        status = "risk_off"
    else:
        status = "neutral"
    return {
        "status": status,
        "breadth_positive_20d": round(breadth, 4),
        "average_20d_return": round(avg_ret20, 6),
        "rationale": f"20d breadth={breadth:.2%}, avg_ret20={avg_ret20:.2%}",
    }


def _load_slow_veto(path: str | Path | None, symbols: list[str]) -> dict[str, Any]:
    normalized = [str(symbol).zfill(6) for symbol in symbols]
    if path is None:
        return {"status": "CLEAR", "events_by_symbol": {}, "reasons": []}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    raw_events = payload.get("events", payload if isinstance(payload, list) else [])
    events_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for item in raw_events:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or item.get("code") or "").zfill(6)
        if symbol not in normalized:
            continue
        event = dict(item)
        event["symbol"] = symbol
        severity = str(event.get("severity") or "review").lower()
        if severity not in {"info", "review", "block"}:
            severity = "review"
        event["severity"] = severity
        if severity in {"review", "block"}:
            events_by_symbol.setdefault(symbol, []).append(event)
    severities = [event["severity"] for events in events_by_symbol.values() for event in events]
    if "block" in severities:
        status = "BLOCK"
    elif severities:
        status = "REVIEW_REQUIRED"
    else:
        status = "CLEAR"
    return {
        "status": status,
        "events_by_symbol": events_by_symbol,
        "reasons": ["slow_veto_events_present"] if status != "CLEAR" else [],
    }


def _apply_slow_veto(candidates: list[dict[str, Any]], slow_veto: dict[str, Any]) -> list[dict[str, Any]]:
    events_by_symbol = slow_veto.get("events_by_symbol", {})
    result: list[dict[str, Any]] = []
    for candidate in candidates:
        row = dict(candidate)
        events = list(events_by_symbol.get(candidate["symbol"], []))
        severities = [event.get("severity") for event in events]
        if "block" in severities:
            status = "BLOCK"
            row["opinion"] = "정성 차단"
        elif events:
            status = "REVIEW_REQUIRED"
            row["opinion"] = "정성 검토 필요"
        else:
            status = "CLEAR"
        row["slow_veto"] = {"status": status, "events": events}
        result.append(row)
    return result


def _score_candidates(panel: pd.DataFrame, *, regime: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for symbol, group in panel.groupby("code"):
        ordered = group.sort_values("Date")
        closes = [float(value) for value in ordered["Close"].tolist()]
        volumes = [float(value) for value in ordered["Volume"].tolist()]
        if len(closes) < 2:
            continue
        latest = closes[-1]
        momentum_20d = _return_over(closes, 20)
        momentum_60d = _return_over(closes, 60)
        vol = _return_volatility(closes, 20)
        volume_surge = _volume_surge(volumes, 20)
        momentum_score = _clip(50.0 + momentum_20d * 250.0 + momentum_60d * 120.0, 0.0, 100.0)
        volume_score = _clip(50.0 + (volume_surge - 1.0) * 25.0, 0.0, 100.0)
        volatility_score = _clip(100.0 - vol * 900.0, 0.0, 100.0)
        regime_score = {"risk_on": 70.0, "neutral": 50.0, "risk_off": 25.0}.get(str(regime["status"]), 50.0)
        overextension_penalty = max(0.0, (momentum_20d - 0.25) * 80.0)
        final = _clip(
            momentum_score * 0.45
            + volume_score * 0.20
            + volatility_score * 0.20
            + regime_score * 0.15
            - overextension_penalty,
            0.0,
            100.0,
        )
        trigger_price = round(latest * 1.01, 2)
        rows.append(
            {
                "symbol": str(symbol),
                "last_close": latest,
                "final_score": round(final, 2),
                "opinion": _candidate_opinion(final, regime),
                "components": {
                    "momentum_score": round(momentum_score, 2),
                    "volume_score": round(volume_score, 2),
                    "volatility_score": round(volatility_score, 2),
                    "regime_score": round(regime_score, 2),
                    "overextension_penalty": round(overextension_penalty, 2),
                    "momentum_20d": round(momentum_20d, 6),
                    "momentum_60d": round(momentum_60d, 6),
                    "volume_surge": round(volume_surge, 4),
                },
                "entry": {
                    "setup": "breakout" if final >= 70 else "wait",
                    "trigger_price": trigger_price,
                    "stop_price": round(latest * 0.95, 2),
                    "take_profit_1": round(latest * 1.08, 2),
                    "invalidation": "close below 20d trend or risk_off regime",
                },
                "rationale": f"score={final:.1f}; 20d={momentum_20d:.2%}; 60d={momentum_60d:.2%}; volume_surge={volume_surge:.2f}; regime={regime['status']}",
            }
        )
    return sorted(rows, key=lambda row: row["final_score"], reverse=True)


def _load_account(*, holdings_path: str | Path | None, account_source: AccountSource | None) -> dict[str, Any]:
    if account_source is not None:
        snapshot = account_source.account_snapshot()
        positions = account_source.position_snapshots()
        return {
            "source": "toss_readonly",
            "account_id": snapshot.account_id,
            "cash_krw": snapshot.cash,
            "buying_power_krw": snapshot.buying_power,
            "total_equity_krw": snapshot.total_equity,
            "positions": [_position_to_dict(position) for position in positions],
        }
    if holdings_path is None:
        return {"source": "none", "cash_krw": None, "buying_power_krw": None, "total_equity_krw": None, "positions": []}
    payload = json.loads(Path(holdings_path).read_text(encoding="utf-8"))
    return {
        "source": "mock_holdings",
        "account_id": payload.get("account_id", "mock"),
        "cash_krw": payload.get("cash_krw"),
        "buying_power_krw": payload.get("buying_power_krw", payload.get("cash_krw")),
        "total_equity_krw": payload.get("total_equity_krw"),
        "positions": [dict(item) for item in payload.get("positions", [])],
    }


def _position_to_dict(position: PositionSnapshot) -> dict[str, Any]:
    data = asdict(position)
    data["symbol"] = str(data["symbol"]).zfill(6)
    return data


def _review_holdings(positions: list[dict[str, Any]], candidates: list[dict[str, Any]], *, portfolio_value_krw: float) -> list[dict[str, Any]]:
    by_symbol = {row["symbol"]: row for row in candidates}
    reviews: list[dict[str, Any]] = []
    for position in positions:
        symbol = str(position.get("symbol") or position.get("code") or "").zfill(6)
        if not symbol or symbol == "000000":
            continue
        quantity = float(position.get("quantity") or 0.0)
        avg_price = _optional_float(position.get("avg_price") or position.get("avgPrice"))
        candidate = by_symbol.get(symbol)
        last = float(candidate["last_close"]) if candidate else avg_price or 0.0
        market_value = _optional_float(position.get("market_value")) or quantity * last
        position_pct = market_value / portfolio_value_krw if portfolio_value_krw > 0 else 0.0
        if avg_price and last <= avg_price * 0.95:
            action = "SELL"
            reason = "stop_breached: latest close is below -5% from avg price"
        elif position_pct > 0.10:
            action = "TRIM"
            reason = "position_concentration_above_10pct"
        elif candidate and candidate["final_score"] >= 65:
            action = "HOLD"
            reason = "candidate_score_supports_holding"
        else:
            action = "WAIT"
            reason = "no strong add/exit signal"
        reviews.append(
            {
                "symbol": symbol,
                "quantity": quantity,
                "avg_price": avg_price,
                "last_close": last,
                "position_pct": round(position_pct, 4),
                "action": action,
                "reason": reason,
            }
        )
    return reviews


def _build_candidate_drafts(candidates: list[dict[str, Any]], *, max_notional_krw: float, portfolio_value_krw: float) -> list[dict[str, Any]]:
    drafts: list[dict[str, Any]] = []
    policy = RiskPolicy(live_trading_enabled=False, max_order_krw=int(max_notional_krw), require_manual_confirmation=True)
    for candidate in candidates[:3]:
        if candidate.get("slow_veto", {}).get("status", "CLEAR") != "CLEAR":
            continue
        if candidate["final_score"] < 70 or candidate["opinion"] == "관망":
            continue
        notional = min(float(max_notional_krw), max(0.0, portfolio_value_krw * 0.05))
        violations = validate_order_intent(
            side="BUY",
            notional_krw=notional,
            portfolio_value_krw=portfolio_value_krw,
            policy=policy,
            manual_confirmation=False,
        )
        risk_decision = RiskDecision.blocked(violations) if violations else RiskDecision.allowed()
        intent = OrderIntent(
            strategy_id="daily-decision-v1",
            symbol=candidate["symbol"],
            side="BUY",
            notional_krw=notional,
            order_type="LIMIT",
            limit_price=float(candidate["entry"]["trigger_price"]),
            reason=candidate["rationale"],
            mode="manual_draft_only",
        )
        drafts.append(
            build_manual_draft(
                intent,
                risk_decision,
                rationale=candidate["rationale"],
                evidence=[
                    f"candidate_score={candidate['final_score']}",
                    f"trigger_price={candidate['entry']['trigger_price']}",
                    "live_order_submitted=False",
                ],
            )
        )
    return drafts


def daily_decision_to_paper_plan(decision: dict[str, Any], *, output_path: str | Path | None = None) -> dict[str, Any]:
    """Convert a daily decision packet into the existing daily-paper JSON plan shape.

    Only CLEAR, high-scoring BUY candidates become paper orders. Slow-vetoed
    candidates stay out of the plan, preserving the review gate before paper/live.
    """
    account = decision.get("account", {})
    initial_cash = float(account.get("cash_krw") or account.get("buying_power_krw") or 0.0)
    holdings = []
    for position in account.get("positions", []):
        symbol = str(position.get("symbol") or position.get("code") or "").zfill(6)
        quantity = _optional_float(position.get("quantity")) or 0.0
        avg_price = _optional_float(position.get("avg_price") or position.get("avgPrice")) or 0.0
        if symbol and quantity > 0:
            holdings.append({"symbol": symbol, "quantity": float(quantity), "avg_price": float(avg_price)})
    orders = []
    for candidate in decision.get("candidates", []):
        if candidate.get("slow_veto", {}).get("status", "CLEAR") != "CLEAR":
            continue
        if float(candidate.get("final_score") or 0.0) < 70:
            continue
        if candidate.get("opinion") in {"관망", "정성 검토 필요", "정성 차단"}:
            continue
        notional = None
        drafts = decision.get("manual_drafts") or []
        for draft in drafts:
            intent = draft.get("intent", {})
            if intent.get("symbol") == candidate.get("symbol"):
                notional = intent.get("notional_krw")
                break
        orders.append(
            {
                "symbol": candidate["symbol"],
                "side": "BUY",
                "notional_krw": float(notional or 0.0),
                "reason": candidate.get("rationale", "daily decision candidate"),
                "market_price": float(candidate["last_close"]),
                "fees_krw": 0.0,
            }
        )
    payload = {"initial_cash_krw": initial_cash, "holdings": holdings, "orders": orders}
    if output_path is not None:
        Path(output_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _render_markdown(result: dict[str, Any]) -> str:
    candidate_lines = []
    for idx, row in enumerate(result["candidates"][:10], start=1):
        entry = row["entry"]
        candidate_lines.append(
            f"{idx}. {row['symbol']} — {row['opinion']} / score {row['final_score']} / close {row['last_close']}\n"
            f"   - trigger: {entry['trigger_price']} / stop: {entry['stop_price']} / tp1: {entry['take_profit_1']}\n"
            f"   - slow_veto: {row.get('slow_veto', {}).get('status', 'CLEAR')}\n"
            f"   - rationale: {row['rationale']}"
        )
    holding_lines = []
    for row in result["holdings_review"]:
        holding_lines.append(
            f"- {row['symbol']}: {row['action']} / qty {row['quantity']} / last {row['last_close']} / reason: {row['reason']}"
        )
    draft_lines = [draft["markdown"] for draft in result["manual_drafts"]] or ["- 주문 초안 없음"]
    slow_veto_lines = []
    for symbol, events in result.get("slow_veto", {}).get("events_by_symbol", {}).items():
        for event in events:
            slow_veto_lines.append(f"- {symbol}: {event.get('severity')} / {event.get('title', '제목 없음')} / {event.get('source', 'unknown')}")
    return (
        "# Daily Toss Decision Report\n\n"
        "안전 문구: 리서치/수동 주문 초안 전용, 실주문 아님, 투자 조언 아님.\n\n"
        "## Market Regime\n"
        f"- status: {result['regime']['status']}\n"
        f"- rationale: {result['regime']['rationale']}\n\n"
        "## Slow Veto\n"
        f"- status: {result.get('slow_veto', {}).get('status', 'CLEAR')}\n"
        + ("\n".join(slow_veto_lines) if slow_veto_lines else "- 이벤트 없음")
        + "\n\n## Top Candidates\n"
        + ("\n".join(candidate_lines) if candidate_lines else "- 후보 없음")
        + "\n\n## Holdings Review\n"
        + ("\n".join(holding_lines) if holding_lines else "- 보유 종목 없음")
        + "\n\n## Manual Drafts\n"
        + "\n".join(draft_lines)
        + "\n\n## Blocked / Guardrails\n"
        "- live_order_submitted: False\n"
        "- execution mode: manual_draft_only\n"
    )


def _return_over(closes: list[float], days: int) -> float:
    if len(closes) <= days or closes[-days - 1] == 0:
        return 0.0
    return closes[-1] / closes[-days - 1] - 1.0


def _return_volatility(closes: list[float], window: int) -> float:
    if len(closes) < window + 1:
        return 0.0
    returns = [closes[i] / closes[i - 1] - 1.0 for i in range(len(closes) - window, len(closes)) if closes[i - 1] != 0]
    return pstdev(returns) if len(returns) > 1 else 0.0


def _volume_surge(volumes: list[float], window: int) -> float:
    if len(volumes) < window + 1:
        return 1.0
    base = mean(volumes[-window - 1 : -1])
    if base <= 0:
        return 1.0
    return volumes[-1] / base


def _candidate_opinion(score: float, regime: dict[str, Any]) -> str:
    if regime["status"] == "risk_off" and score < 85:
        return "관망"
    if score >= 80:
        return "매수 후보"
    if score >= 65:
        return "조건부 관찰"
    return "관망"


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)
