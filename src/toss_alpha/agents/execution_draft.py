"""Manual order draft guardrails.

This module intentionally creates review text only. It has no execution methods.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from toss_alpha.data.schema import OrderIntent, RiskDecision


def build_manual_draft(
    intent: OrderIntent,
    risk_decision: RiskDecision,
    *,
    rationale: str,
    evidence: list[str],
) -> dict[str, Any]:
    status = "ALLOW" if risk_decision.allow else "BLOCK"
    markdown = _render_markdown(intent, risk_decision, status=status, rationale=rationale, evidence=evidence)
    return {
        "status": status,
        "mode": "manual_draft_only",
        "not_live_order": True,
        "intent": asdict(intent),
        "risk_decision": asdict(risk_decision),
        "violations": list(risk_decision.violations),
        "rationale": rationale,
        "evidence": list(evidence),
        "markdown": markdown,
    }


def _render_markdown(
    intent: OrderIntent,
    risk_decision: RiskDecision,
    *,
    status: str,
    rationale: str,
    evidence: list[str],
) -> str:
    evidence_text = "\n".join(f"- {item}" for item in evidence) or "- 근거 없음"
    return (
        "## 수동 주문 검토 초안\n"
        "- 상태: {status}\n"
        "- 모드: manual_draft_only\n"
        "- 안전 문구: 실주문 아님 / 수동 확인 필요\n"
        "- 종목: {symbol}\n"
        "- 방향 후보: {side}\n"
        "- 금액 후보: {notional}\n"
        "- 사유: {reason}\n"
        "- 리스크 위반: {violations}\n"
        "- 판단 근거: {rationale}\n"
        "### Evidence\n"
        "{evidence_text}\n"
    ).format(
        status=status,
        symbol=intent.symbol,
        side=intent.side,
        notional=intent.notional_krw,
        reason=intent.reason,
        violations=", ".join(risk_decision.violations) or "없음",
        rationale=rationale,
        evidence_text=evidence_text,
    )
