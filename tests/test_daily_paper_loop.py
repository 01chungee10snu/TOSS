from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from toss_alpha.cli import main
from toss_alpha.daily.paper_loop import run_daily_paper_loop


PANEL_HEADER = "Date,code,Close,Open,High,Low,Volume\n"


class FakeSheetStore:
    spreadsheet_id = "sheet123"

    def __init__(self) -> None:
        self.write_calls = []

    def write_result(self, result, *, as_of: str | None = None) -> None:
        self.write_calls.append((result, as_of))


def _write_panel(path: Path) -> None:
    rows = [PANEL_HEADER]
    start = date(2024, 1, 1)
    for i in range(80):
        day = (start + timedelta(days=i)).isoformat()
        strong = 100 + i * 2
        weak = 200 - i
        rows.append(f"{day},111111,{strong},{strong-1},{strong+2},{strong-2},{1000000 + i * 20000}\n")
        rows.append(f"{day},222222,{weak},{weak+1},{weak+2},{weak-2},{1000000 - i * 5000}\n")
    path.write_text("".join(rows), encoding="utf-8")


def test_daily_paper_loop_runs_decision_plan_and_paper_execution(tmp_path: Path):
    panel = tmp_path / "panel.csv"
    holdings = tmp_path / "holdings.json"
    out_dir = tmp_path / "loop"
    _write_panel(panel)
    holdings.write_text(
        json.dumps({"cash_krw": 500000, "positions": [{"symbol": "222222", "quantity": 3, "avg_price": 190}]}),
        encoding="utf-8",
    )

    result = run_daily_paper_loop(
        panel_csv=panel,
        symbols=["111111", "222222"],
        holdings_path=holdings,
        out_dir=out_dir,
        as_of="2024-03-20",
    )

    assert result["mode"] == "paper_auto"
    assert result["live_order_submitted"] is False
    assert result["decision"]["mode"] == "manual_draft_only"
    assert result["paper_plan"]["orders"][0]["symbol"] == "111111"
    assert result["paper_execution"]["status"] == "OK"
    assert result["paper_execution"]["total_orders"] == 1
    assert result["paper_execution"]["filled_orders"] == 1
    assert result["paper_execution"]["blocked_orders"] == 0
    for key in ["decision_json_path", "paper_plan_path", "paper_json_path", "paper_report_path"]:
        assert Path(result["artifacts"][key]).exists()
    report_text = Path(result["artifacts"]["paper_report_path"]).read_text(encoding="utf-8")
    assert "Daily Paper Loop Report" in report_text
    assert "실주문 아님" in report_text
    assert "filled_orders: 1" in report_text


def test_daily_paper_loop_respects_slow_veto_and_executes_zero_orders(tmp_path: Path):
    panel = tmp_path / "panel.csv"
    holdings = tmp_path / "holdings.json"
    events = tmp_path / "slow_events.json"
    out_dir = tmp_path / "loop"
    _write_panel(panel)
    holdings.write_text(json.dumps({"cash_krw": 500000, "positions": []}), encoding="utf-8")
    events.write_text(
        json.dumps({"events": [{"symbol": "111111", "severity": "block", "title": "거래정지 위험"}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = run_daily_paper_loop(
        panel_csv=panel,
        symbols=["111111", "222222"],
        holdings_path=holdings,
        slow_veto_events_path=events,
        out_dir=out_dir,
        as_of="2024-03-20",
    )

    assert result["decision"]["slow_veto"]["status"] == "BLOCK"
    assert result["paper_plan"]["orders"] == []
    assert result["paper_execution"]["total_orders"] == 0
    assert result["paper_execution"]["filled_orders"] == 0


def test_daily_paper_loop_appends_result_to_google_sheet_store(tmp_path: Path):
    panel = tmp_path / "panel.csv"
    holdings = tmp_path / "holdings.json"
    out_dir = tmp_path / "loop"
    sheet_store = FakeSheetStore()
    _write_panel(panel)
    holdings.write_text(json.dumps({"cash_krw": 500000, "positions": []}), encoding="utf-8")

    result = run_daily_paper_loop(
        panel_csv=panel,
        symbols=["111111", "222222"],
        holdings_path=holdings,
        out_dir=out_dir,
        as_of="2024-03-20",
        sheet_store=sheet_store,
    )

    assert result["sheet_writeback"] == {"enabled": True, "spreadsheet_id": "sheet123"}
    assert len(sheet_store.write_calls) == 1
    paper_result, as_of = sheet_store.write_calls[0]
    assert as_of == "2024-03-20"
    assert paper_result.status == "OK"
    assert paper_result.total_orders == 1
    assert paper_result.filled_orders == 1


def test_cli_daily_paper_loop_writes_artifacts(tmp_path: Path, capsys):
    panel = tmp_path / "panel.csv"
    holdings = tmp_path / "holdings.json"
    out_dir = tmp_path / "loop"
    _write_panel(panel)
    holdings.write_text(json.dumps({"cash_krw": 500000, "positions": []}), encoding="utf-8")

    code = main(
        [
            "daily",
            "paper-loop",
            "--panel-csv",
            str(panel),
            "--symbols",
            "111111,222222",
            "--mock-holdings",
            str(holdings),
            "--out-dir",
            str(out_dir),
            "--date",
            "2024-03-20",
        ]
    )

    assert code == 0
    stdout = capsys.readouterr().out
    assert "mode: paper_auto" in stdout
    assert "live_order_submitted: False" in stdout
    assert "paper_status: OK" in stdout
    assert "paper_plan_path:" in stdout
    assert "paper_report_path:" in stdout
