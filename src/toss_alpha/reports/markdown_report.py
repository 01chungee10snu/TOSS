"""Safe Markdown report renderer."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from toss_alpha.data.schema import BacktestResult, ResearchGoal, RiskDecision, SignalResult


def render_research_report(
    *,
    goal: ResearchGoal,
    signals: list[SignalResult],
    backtest: BacktestResult,
    risk_decision: RiskDecision,
    manual_draft: dict[str, Any] | None = None,
    data_as_of: str = "unknown",
) -> str:
    signal_lines = "\n".join(f"- {s.name}: score={s.score:.4f} / {s.rationale}" for s in signals) or "- 없음"
    draft_text = (manual_draft or {}).get("markdown", "수동 검토 초안 없음")
    report = f"""# TOSS Alpha Research Report

## 연구 목표
- goal_id: {goal.goal_id}
- mode: {goal.mode}
- symbols: {', '.join(goal.symbols)}
- period: {goal.start} ~ {goal.end}
- strategy: {goal.strategy_name}

## 데이터 기준 시점
- data_as_of: {data_as_of}

## 신호/이벤트 근거
{signal_lines}

## 백테스트 요약
- status: {backtest.status}
- total_return: {backtest.total_return:.6f}
- max_drawdown: {backtest.max_drawdown:.6f}
- trades: {backtest.trades}
- fees_krw: {backtest.fees_krw:.2f}
- slippage_krw: {backtest.slippage_krw:.2f}

## 리스크 게이트
- status: {risk_decision.status}
- allow: {risk_decision.allow}
- violations: {', '.join(risk_decision.violations) or '없음'}

## 수동 검토 초안
{draft_text}

## 주의 문구
- 투자 조언 아님: 이 보고서는 연구/백테스트 보조 자료입니다.
- 손실 가능: 모든 투자는 원금 손실 가능성이 있습니다.
- 실주문 아님: 실행 전 사용자의 별도 수동 확인이 필요합니다.
"""
    return _strip_direct_imperatives(report)


def _strip_direct_imperatives(text: str) -> str:
    return text.replace("매수하세요", "매수 후보로 검토").replace("매도하세요", "매도 후보로 검토")
