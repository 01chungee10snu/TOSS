from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "format_ttak_loop_korean_summary.py"


def load_module():
    spec = importlib.util.spec_from_file_location("format_ttak_loop_korean_summary_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_summary_surfaces_position_exit_buy_block_reason(tmp_path, monkeypatch, capsys):
    report = tmp_path / "loop.json"
    report.write_text(
        json.dumps(
            {
                "overall_status": "NO_TRADE",
                "live_submit": {"status": "LIVE_SUBMIT_NO_ORDERS", "order_count": 0},
                "position_exit": {
                    "block_new_buys": True,
                    "buy_block_reasons": ["research_validation_hold"],
                    "sell_order_count": 0,
                    "equity_guard": {
                        "status": "READY",
                        "current_equity": 391722,
                        "drawdown_pct": 0.0,
                        "block_new_buys": False,
                    },
                },
                "live": {"status": "LIVE_READY"},
                "qual": {"status": "CLEAR"},
            }
        ),
        encoding="utf-8",
    )
    m = load_module()
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), str(report)])

    assert m.main() == 0
    output = capsys.readouterr().out
    assert "신규매수 차단 여부: True" in output
    assert "신규매수 차단 사유: research_validation_hold" in output
