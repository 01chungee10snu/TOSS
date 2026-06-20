from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from toss_alpha.cli import main
from toss_alpha.research.runner import run_goal


PANEL_HEADER = "Date,code,Close,Open,High,Low,Volume\n"


def _write_panel(path: Path) -> None:
    rows = [PANEL_HEADER]
    start = date(2024, 1, 1)
    for i in range(70):
        day = (start + timedelta(days=i)).isoformat()
        a = 100 + i
        b = 200 - i
        rows.append(f"{day},111111,{a},{a},{a},{a},1000000\n")
        rows.append(f"{day},222222,{b},{b},{b},{b},1000000\n")
    path.write_text("".join(rows), encoding="utf-8")


def _write_goal(path: Path) -> None:
    path.write_text(
        """
goal_id: test_goal
mode: backtest_only
universe:
  symbols: ["111111", "222222"]
period:
  start: "2024-01-01"
  end: "2024-12-31"
strategy:
  name: contextual_daily_with_monfri_submode
  params:
    short_window: 20
    long_window: 60
    volatility_window: 20
risk_profile: conservative
""",
        encoding="utf-8",
    )


def test_run_goal_creates_report_and_selects_best_symbol(tmp_path: Path):
    goal = tmp_path / "goal.yaml"
    panel = tmp_path / "panel.csv"
    out_dir = tmp_path / "reports"
    _write_goal(goal)
    _write_panel(panel)

    result = run_goal(goal, panel_csv=panel, out_dir=out_dir)

    assert result["goal_id"] == "test_goal"
    assert result["selected_symbol"] == "111111"
    assert result["backtest"]["status"] == "PASS"
    assert Path(result["report_path"]).exists()
    assert Path(result["json_path"]).exists()
    report_text = Path(result["report_path"]).read_text(encoding="utf-8")
    assert "TOSS Alpha Research Report" in report_text
    assert "111111" in report_text


def test_cli_research_run_executes_goal_runner(tmp_path: Path, capsys):
    goal = tmp_path / "goal.yaml"
    panel = tmp_path / "panel.csv"
    out_dir = tmp_path / "reports"
    _write_goal(goal)
    _write_panel(panel)

    code = main(["research", "run", str(goal), "--panel-csv", str(panel), "--out-dir", str(out_dir)])

    assert code == 0
    out = capsys.readouterr().out
    assert "status: PASS" in out
    assert "selected_symbol: 111111" in out
    assert "report_path:" in out
