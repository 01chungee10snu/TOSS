"""API-less daily decision -> paper execution loop.

This module stitches together the existing safe pieces:
1. daily decision packet (manual_draft_only, no live orders),
2. daily-paper JSON plan,
3. deterministic paper execution ledger,
4. markdown/json artifacts.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from toss_alpha.daily.decision import daily_decision_to_paper_plan, run_daily_decision
from toss_alpha.execution.daily_paper import DailyPaperPlan, DailyPaperExecutionResult, run_daily_paper

DEFAULT_LOOP_DIR = Path("reports/daily_paper_loop")


class SheetResultStore(Protocol):
    spreadsheet_id: str

    def write_result(self, result: DailyPaperExecutionResult, *, as_of: str | None = None) -> None: ...


def run_daily_paper_loop(
    *,
    panel_csv: str | Path,
    symbols: list[str],
    holdings_path: str | Path | None = None,
    slow_veto_events_path: str | Path | None = None,
    out_dir: str | Path | None = None,
    as_of: str | None = None,
    max_notional_krw: float = 100_000,
    sheet_store: SheetResultStore | None = None,
) -> dict[str, Any]:
    """Run daily decision, convert to paper plan, execute paper orders, save artifacts."""
    loop_dir = Path(out_dir) if out_dir else DEFAULT_LOOP_DIR
    loop_dir.mkdir(parents=True, exist_ok=True)
    decision_dir = loop_dir / "decision"
    decision = run_daily_decision(
        panel_csv=panel_csv,
        symbols=symbols,
        holdings_path=holdings_path,
        slow_veto_events_path=slow_veto_events_path,
        out_dir=decision_dir,
        as_of=as_of,
        max_notional_krw=max_notional_krw,
    )
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    stem = f"daily_paper_loop_{decision['as_of']}_{timestamp}"
    paper_plan_path = loop_dir / f"{stem}_paper_plan.json"
    paper_json_path = loop_dir / f"{stem}_paper_result.json"
    paper_report_path = loop_dir / f"{stem}.md"

    paper_plan_payload = daily_decision_to_paper_plan(decision, output_path=paper_plan_path)
    paper_plan = DailyPaperPlan.from_dict(paper_plan_payload, strategy_id="daily-paper-loop")
    paper_result = run_daily_paper(paper_plan)
    paper_execution = {
        "status": paper_result.status,
        "total_orders": paper_result.total_orders,
        "filled_orders": paper_result.filled_orders,
        "blocked_orders": paper_result.blocked_orders,
        "ending_cash_krw": paper_result.ledger.cash_krw,
        "realized_pnl_krw": paper_result.ledger.realized_pnl_krw,
        "positions": {
            symbol: {
                "quantity": position.quantity,
                "avg_price": position.avg_price,
                "state": position.state,
            }
            for symbol, position in sorted(paper_result.ledger.positions.items())
        },
        "order_results": [
            {
                "status": item.status,
                "violations": list(item.violations),
                "fill": asdict(item.fill) if item.fill is not None else None,
            }
            for item in paper_result.order_results
        ],
    }
    if sheet_store is not None:
        sheet_store.write_result(paper_result, as_of=decision["as_of"])
    result = {
        "mode": "paper_auto",
        "live_order_submitted": False,
        "decision": decision,
        "paper_plan": paper_plan_payload,
        "paper_execution": paper_execution,
        "sheet_writeback": {
            "enabled": sheet_store is not None,
            "spreadsheet_id": getattr(sheet_store, "spreadsheet_id", None) if sheet_store is not None else None,
        },
        "artifacts": {
            "decision_report_path": decision["report_path"],
            "decision_json_path": decision["json_path"],
            "paper_plan_path": str(paper_plan_path),
            "paper_json_path": str(paper_json_path),
            "paper_report_path": str(paper_report_path),
        },
        "disclaimer": "Paper simulation only. Not investment advice. No live orders submitted.",
    }
    paper_json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    paper_report_path.write_text(_render_paper_loop_report(result), encoding="utf-8")
    return result


def _render_paper_loop_report(result: dict[str, Any]) -> str:
    execution = result["paper_execution"]
    plan = result["paper_plan"]
    decision = result["decision"]
    positions = execution.get("positions", {})
    position_lines = [
        f"- {symbol}: qty={payload['quantity']} avg={payload['avg_price']} state={payload['state']}"
        for symbol, payload in positions.items()
    ] or ["- 포지션 없음"]
    return (
        "# Daily Paper Loop Report\n\n"
        "안전 문구: paper simulation only / 실주문 아님 / 투자 조언 아님.\n\n"
        "## Decision\n"
        f"- as_of: {decision['as_of']}\n"
        f"- decision_mode: {decision['mode']}\n"
        f"- slow_veto: {decision['slow_veto']['status']}\n"
        f"- manual_drafts: {len(decision['manual_drafts'])}\n\n"
        "## Paper Plan\n"
        f"- orders: {len(plan['orders'])}\n"
        f"- holdings: {len(plan['holdings'])}\n"
        f"- initial_cash_krw: {plan['initial_cash_krw']}\n\n"
        "## Paper Execution\n"
        f"- status: {execution['status']}\n"
        f"- total_orders: {execution['total_orders']}\n"
        f"- filled_orders: {execution['filled_orders']}\n"
        f"- blocked_orders: {execution['blocked_orders']}\n"
        f"- ending_cash_krw: {execution['ending_cash_krw']}\n"
        f"- realized_pnl_krw: {execution['realized_pnl_krw']}\n\n"
        "## Positions\n"
        + "\n".join(position_lines)
        + "\n\n## Sheet Writeback\n"
        f"- enabled: {result.get('sheet_writeback', {}).get('enabled', False)}\n"
        f"- spreadsheet_id: {result.get('sheet_writeback', {}).get('spreadsheet_id')}\n"
        + "\n## Artifacts\n"
        f"- decision_json: {result['artifacts']['decision_json_path']}\n"
        f"- paper_plan: {result['artifacts']['paper_plan_path']}\n"
        f"- paper_json: {result['artifacts']['paper_json_path']}\n"
    )
