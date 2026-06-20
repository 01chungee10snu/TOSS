from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from toss_alpha.cli import main
from toss_alpha.daily.decision import daily_decision_to_paper_plan, run_daily_decision
from toss_alpha.data.schema import AccountSnapshot, PositionSnapshot


PANEL_HEADER = "Date,code,Close,Open,High,Low,Volume\n"


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


def test_daily_decision_scores_candidates_reviews_holdings_and_writes_artifacts(tmp_path: Path):
    panel = tmp_path / "panel.csv"
    holdings = tmp_path / "holdings.json"
    out_dir = tmp_path / "daily"
    _write_panel(panel)
    holdings.write_text(
        json.dumps(
            {
                "cash_krw": 500000,
                "positions": [
                    {"symbol": "111111", "quantity": 2, "avg_price": 180},
                    {"symbol": "222222", "quantity": 3, "avg_price": 190},
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_daily_decision(
        panel_csv=panel,
        symbols=["111111", "222222"],
        holdings_path=holdings,
        out_dir=out_dir,
        as_of="2024-03-20",
    )

    assert result["mode"] == "manual_draft_only"
    assert result["live_order_submitted"] is False
    assert result["regime"]["status"] in {"risk_on", "neutral", "risk_off"}
    assert result["candidates"][0]["symbol"] == "111111"
    assert result["candidates"][0]["final_score"] > result["candidates"][1]["final_score"]
    assert {row["symbol"] for row in result["holdings_review"]} == {"111111", "222222"}
    assert any(row["action"] == "SELL" for row in result["holdings_review"])
    assert result["manual_drafts"]
    assert result["manual_drafts"][0]["not_live_order"] is True
    assert "live_trading_disabled" in result["manual_drafts"][0]["violations"]
    assert Path(result["report_path"]).exists()
    assert Path(result["json_path"]).exists()
    report_text = Path(result["report_path"]).read_text(encoding="utf-8")
    assert "Daily Toss Decision Report" in report_text
    assert "실주문 아님" in report_text
    assert "111111" in report_text


def test_daily_decision_can_use_injected_toss_account_source(tmp_path: Path):
    panel = tmp_path / "panel.csv"
    out_dir = tmp_path / "daily"
    _write_panel(panel)

    class FakeAccountSource:
        def account_snapshot(self):
            return AccountSnapshot(account_id="acc-1", cash=123456, buying_power=120000, total_equity=900000)

        def position_snapshots(self):
            return [PositionSnapshot(symbol="111111", quantity=4, avg_price=150, market_value=700)]

    result = run_daily_decision(
        panel_csv=panel,
        symbols=["111111", "222222"],
        account_source=FakeAccountSource(),
        out_dir=out_dir,
        as_of="2024-03-20",
    )

    assert result["account"]["source"] == "toss_readonly"
    assert result["account"]["cash_krw"] == 123456
    assert result["holdings_review"][0]["symbol"] == "111111"


def test_cli_daily_run_generates_decision_report(tmp_path: Path, capsys):
    panel = tmp_path / "panel.csv"
    holdings = tmp_path / "holdings.json"
    out_dir = tmp_path / "daily"
    _write_panel(panel)
    holdings.write_text(json.dumps({"positions": []}), encoding="utf-8")

    code = main(
        [
            "daily",
            "run",
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
    assert "mode: manual_draft_only" in stdout
    assert "top_candidate: 111111" in stdout
    assert "report_path:" in stdout


def test_daily_decision_slow_veto_blocks_candidate_and_removes_manual_draft(tmp_path: Path):
    panel = tmp_path / "panel.csv"
    holdings = tmp_path / "holdings.json"
    events = tmp_path / "slow_events.json"
    out_dir = tmp_path / "daily"
    _write_panel(panel)
    holdings.write_text(json.dumps({"positions": []}), encoding="utf-8")
    events.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "symbol": "111111",
                        "severity": "review",
                        "title": "전환사채 발행 결정",
                        "source": "mock_dart",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = run_daily_decision(
        panel_csv=panel,
        symbols=["111111", "222222"],
        holdings_path=holdings,
        slow_veto_events_path=events,
        out_dir=out_dir,
        as_of="2024-03-20",
    )

    blocked = next(row for row in result["candidates"] if row["symbol"] == "111111")
    assert blocked["slow_veto"]["status"] == "REVIEW_REQUIRED"
    assert blocked["opinion"] == "정성 검토 필요"
    assert result["manual_drafts"] == []
    assert result["slow_veto"]["status"] == "REVIEW_REQUIRED"
    report_text = Path(result["report_path"]).read_text(encoding="utf-8")
    assert "Slow Veto" in report_text
    assert "전환사채 발행 결정" in report_text


def test_daily_decision_to_paper_plan_exports_safe_orders(tmp_path: Path):
    panel = tmp_path / "panel.csv"
    holdings = tmp_path / "holdings.json"
    out_dir = tmp_path / "daily"
    paper_plan = tmp_path / "paper-plan.json"
    _write_panel(panel)
    holdings.write_text(
        json.dumps(
            {
                "cash_krw": 500000,
                "positions": [{"symbol": "222222", "quantity": 3, "avg_price": 190}],
            }
        ),
        encoding="utf-8",
    )
    decision = run_daily_decision(
        panel_csv=panel,
        symbols=["111111", "222222"],
        holdings_path=holdings,
        out_dir=out_dir,
        as_of="2024-03-20",
    )

    payload = daily_decision_to_paper_plan(decision, output_path=paper_plan)

    assert payload["initial_cash_krw"] == 500000
    assert payload["holdings"] == [{"symbol": "222222", "quantity": 3.0, "avg_price": 190.0}]
    assert payload["orders"][0]["symbol"] == "111111"
    assert payload["orders"][0]["side"] == "BUY"
    assert payload["orders"][0]["market_price"] == decision["candidates"][0]["last_close"]
    assert "notional_krw" in payload["orders"][0]
    assert json.loads(paper_plan.read_text(encoding="utf-8"))["orders"][0]["symbol"] == "111111"


def test_cli_daily_run_can_write_paper_plan_and_apply_slow_veto(tmp_path: Path, capsys):
    panel = tmp_path / "panel.csv"
    holdings = tmp_path / "holdings.json"
    events = tmp_path / "slow_events.json"
    out_dir = tmp_path / "daily"
    paper_plan = tmp_path / "paper-plan.json"
    _write_panel(panel)
    holdings.write_text(json.dumps({"cash_krw": 500000, "positions": []}), encoding="utf-8")
    events.write_text(json.dumps({"events": [{"symbol": "111111", "severity": "block", "title": "거래정지 위험"}]}, ensure_ascii=False), encoding="utf-8")

    code = main(
        [
            "daily",
            "run",
            "--panel-csv",
            str(panel),
            "--symbols",
            "111111,222222",
            "--mock-holdings",
            str(holdings),
            "--slow-veto-events",
            str(events),
            "--paper-plan-out",
            str(paper_plan),
            "--out-dir",
            str(out_dir),
            "--date",
            "2024-03-20",
        ]
    )

    assert code == 0
    stdout = capsys.readouterr().out
    assert "slow_veto: BLOCK" in stdout
    assert "paper_plan_path:" in stdout
    assert paper_plan.exists()
    assert json.loads(paper_plan.read_text(encoding="utf-8"))["orders"] == []
