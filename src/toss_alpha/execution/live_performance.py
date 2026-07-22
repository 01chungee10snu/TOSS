"""Evaluate live FIFO performance without changing or submitting a strategy.

The output is an operational BUY gate. Risk-reducing SELL orders remain outside
this gate. Historical/backtest metrics are deliberately not accepted as live P&L.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class PerformanceThresholds:
    max_cumulative_loss_pct: float = 0.03
    max_consecutive_losing_fill_days: int = 3
    probation_settlement_days: int = 20
    probation_fills: int = 30


def _number(value: object) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def load_settlements(directory: str | Path, *, deployed_since: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(Path(directory).glob("market_close_settlement_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        stamp = str(payload.get("date") or "")
        if stamp >= deployed_since:
            rows.append(payload)
    rows.sort(key=lambda row: str(row.get("date") or ""))
    return rows


def evaluate_live_performance(
    settlements: Iterable[Mapping[str, Any]],
    *,
    policy_id: str,
    deployed_since: str,
    thresholds: PerformanceThresholds | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Return a deterministic performance state and BUY-only gate decision."""
    cfg = thresholds or PerformanceThresholds()
    rows = sorted(
        [dict(row) for row in settlements if str(row.get("date") or "") >= deployed_since],
        key=lambda row: str(row.get("date") or ""),
    )
    now = generated_at or datetime.now(timezone.utc)
    baseline_equity = next(
        (_number((row.get("account") or {}).get("total_equity")) for row in rows if _number((row.get("account") or {}).get("total_equity")) > 0),
        0.0,
    )
    latest_equity = next(
        (_number((row.get("account") or {}).get("total_equity")) for row in reversed(rows) if _number((row.get("account") or {}).get("total_equity")) > 0),
        0.0,
    )
    cumulative_realized = sum(_number((row.get("daily") or {}).get("realized_matched_fifo")) for row in rows)
    fill_count = sum(int(_number((row.get("daily") or {}).get("fill_count"))) for row in rows)
    # The deployment-day ledger can legitimately contain a SELL for a position
    # opened by the previous policy. Keep it visible as inherited inventory, but
    # do not let that one-time migration artifact permanently freeze the new
    # policy. Any unmatched SELL after deployment day still fails closed.
    inherited_unmatched_sell_qty = sum(
        _number((row.get("daily") or {}).get("unmatched_sell_qty"))
        for row in rows
        if str(row.get("date") or "") == deployed_since
    )
    unmatched_sell_qty = sum(
        _number((row.get("daily") or {}).get("unmatched_sell_qty"))
        for row in rows
        if str(row.get("date") or "") > deployed_since
    )

    consecutive_losing_fill_days = 0
    for row in reversed(rows):
        daily = row.get("daily") or {}
        fills = int(_number(daily.get("fill_count")))
        if fills <= 0:
            continue
        if _number(daily.get("realized_matched_fifo")) < 0:
            consecutive_losing_fill_days += 1
            continue
        break

    loss_budget_krw = baseline_equity * cfg.max_cumulative_loss_pct if baseline_equity > 0 else 0.0
    reasons: list[str] = []
    if unmatched_sell_qty > 0:
        reasons.append("unmatched_fifo_sell_cost_basis")
    if loss_budget_krw > 0 and cumulative_realized <= -loss_budget_krw:
        reasons.append("cumulative_realized_loss_limit")
    if consecutive_losing_fill_days >= cfg.max_consecutive_losing_fill_days:
        reasons.append("consecutive_losing_fill_days_limit")

    block_new_buys = bool(reasons)
    enough_sample = len(rows) >= cfg.probation_settlement_days and fill_count >= cfg.probation_fills
    if block_new_buys:
        status = "BLOCK_NEW_BUYS"
    elif enough_sample:
        status = "CONTINUE_LIVE_OBSERVATION"
    else:
        status = "PROBATION_CONTINUE"

    research_reasons: list[str] = []
    if cumulative_realized < 0:
        research_reasons.append("live_realized_pnl_negative")
    if consecutive_losing_fill_days > 0:
        research_reasons.append("latest_fill_day_lost")
    if block_new_buys:
        research_reasons.append("risk_gate_triggered")
    if rows and len(rows) % 5 == 0:
        research_reasons.append("scheduled_five_settlement_review")

    return {
        "schema_version": 1,
        "generated_at_utc": now.astimezone(timezone.utc).isoformat(),
        "policy_id": policy_id,
        "deployed_since": deployed_since,
        "status": status,
        "block_new_buys": block_new_buys,
        "preserve_sell_exits": True,
        "reasons": reasons,
        "sample": {
            "settlement_days": len(rows),
            "fill_count": fill_count,
            "consecutive_losing_fill_days": consecutive_losing_fill_days,
            "latest_settlement_date": str(rows[-1].get("date") or "") if rows else None,
        },
        "live_performance": {
            "baseline_equity_krw": baseline_equity,
            "latest_equity_krw": latest_equity,
            "cumulative_realized_matched_fifo_krw": cumulative_realized,
            "unmatched_sell_qty": unmatched_sell_qty,
            "inherited_deployment_day_unmatched_sell_qty": inherited_unmatched_sell_qty,
            "loss_budget_krw": loss_budget_krw,
        },
        "thresholds": {
            "max_cumulative_loss_pct": cfg.max_cumulative_loss_pct,
            "max_consecutive_losing_fill_days": cfg.max_consecutive_losing_fill_days,
            "probation_settlement_days": cfg.probation_settlement_days,
            "probation_fills": cfg.probation_fills,
        },
        "research": {
            "review_required": bool(research_reasons),
            "reasons": research_reasons,
            "promotion_mode": "CANDIDATE_ONLY_AFTER_PIT_WALKFORWARD_YEAR_COST_GATES",
            "auto_replace_live_policy": False,
        },
    }


def read_live_performance_gate(
    path: str | Path,
    *,
    enabled: bool = True,
    max_artifact_age_hours: float = 96.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read the BUY-only gate; corrupt/stale state fails closed, absence starts probation."""
    if not enabled:
        return {"enabled": False, "status": "DISABLED", "block_new_buys": False, "preserve_sell_exits": True}
    artifact = Path(path)
    base: dict[str, Any] = {"enabled": True, "path": str(artifact), "preserve_sell_exits": True}
    if not artifact.exists():
        return {**base, "status": "PROBATION_NO_ARTIFACT", "block_new_buys": False, "reasons": []}
    try:
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        generated = datetime.fromisoformat(str(payload["generated_at_utc"]).replace("Z", "+00:00"))
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {**base, "status": "BLOCKED_CORRUPT_ARTIFACT", "block_new_buys": True, "reasons": [type(exc).__name__]}
    current = now or datetime.now(timezone.utc)
    age_hours = (current.astimezone(timezone.utc) - generated.astimezone(timezone.utc)).total_seconds() / 3600
    if age_hours < -1 or age_hours > max_artifact_age_hours:
        return {**base, "status": "BLOCKED_STALE_ARTIFACT", "block_new_buys": True, "reasons": ["artifact_stale"], "age_hours": age_hours}
    return {
        **base,
        "status": str(payload.get("status") or "UNKNOWN"),
        "block_new_buys": bool(payload.get("block_new_buys")),
        "reasons": list(payload.get("reasons") or []),
        "age_hours": age_hours,
        "policy_id": payload.get("policy_id"),
        "sample": payload.get("sample") or {},
        "live_performance": payload.get("live_performance") or {},
    }


def render_markdown(payload: Mapping[str, Any]) -> str:
    sample = payload.get("sample") or {}
    perf = payload.get("live_performance") or {}
    research = payload.get("research") or {}
    lines = [
        "# TOSS live performance feedback",
        "",
        f"- status: {payload.get('status')}",
        f"- policy_id: {payload.get('policy_id')}",
        f"- deployed_since: {payload.get('deployed_since')}",
        f"- block_new_buys: {payload.get('block_new_buys')}",
        f"- preserve_sell_exits: {payload.get('preserve_sell_exits')}",
        f"- reasons: {payload.get('reasons') or []}",
        f"- settlements/fills: {sample.get('settlement_days', 0)}/{sample.get('fill_count', 0)}",
        f"- cumulative_realized_fifo_krw: {perf.get('cumulative_realized_matched_fifo_krw', 0)}",
        f"- loss_budget_krw: {perf.get('loss_budget_krw', 0)}",
        f"- unmatched_sell_qty: {perf.get('unmatched_sell_qty', 0)}",
        f"- research_review_required: {research.get('review_required')}",
        f"- research_reasons: {research.get('reasons') or []}",
        f"- promotion_mode: {research.get('promotion_mode')}",
        "",
        "Live FIFO only. Backtest metrics are not included in live P&L.",
    ]
    return "\n".join(lines) + "\n"
