import json
import os
import subprocess
import sys


def run_cli(*args, env=None):
    full_env = os.environ.copy()
    full_env["PYTHONPATH"] = "src"
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "toss_alpha.cli", *args],
        check=False,
        text=True,
        capture_output=True,
        env=full_env,
    )


def test_cli_help_exits_zero():
    result = run_cli("--help")
    assert result.returncode == 0
    assert "research" in result.stdout


def test_safe_commands_exist():
    help_text = run_cli("--help").stdout
    assert "research" in help_text
    assert "backtest" in help_text
    assert "draft-order" in help_text
    assert "live-readiness" in help_text
    assert "paper-order" in help_text
    assert "daily-paper" in help_text
    assert run_cli("research", "run", "--help").returncode == 0
    assert run_cli("backtest", "run", "--help").returncode == 0
    assert run_cli("draft-order", "--help").returncode == 0
    assert run_cli("live-readiness", "--help").returncode == 0
    assert run_cli("paper-order", "--help").returncode == 0
    assert run_cli("daily-paper", "--help").returncode == 0
    assert run_cli("init-daily-paper-sheet", "--help").returncode == 0


def test_live_readiness_reports_not_ready_by_default():
    result = run_cli("live-readiness")
    assert result.returncode == 0
    assert "ready: False" in result.stdout
    assert "BLOCK_UNLESS_DOUBLE_OPT_IN" in result.stdout


def test_paper_order_runs_safe_simulation_only():
    result = run_cli(
        "paper-order",
        "--symbol",
        "005930",
        "--side",
        "BUY",
        "--quantity",
        "5",
        "--price",
        "10000",
    )
    assert result.returncode == 0
    assert "status: FILLED" in result.stdout
    assert "mode: paper_auto" in result.stdout
    assert "position_qty: 5.0" in result.stdout


def test_daily_paper_runs_batch_simulation_from_plan_file(tmp_path):
    plan_path = tmp_path / "daily-paper-plan.json"
    plan_path.write_text(
        '{\n'
        '  "initial_cash_krw": 700000,\n'
        '  "holdings": [{"symbol": "005930", "quantity": 5, "avg_price": 10000}],\n'
        '  "orders": [\n'
        '    {"symbol": "005930", "side": "SELL", "quantity": 2, "reason": "trim", "market_price": 12000, "fees_krw": 100},\n'
        '    {"symbol": "000660", "side": "BUY", "quantity": 3, "reason": "entry", "market_price": 50000, "fees_krw": 200}\n'
        '  ]\n'
        '}\n',
        encoding="utf-8",
    )

    result = run_cli("daily-paper", "--plan", str(plan_path))

    assert result.returncode == 0
    assert "status: OK" in result.stdout
    assert "mode: paper_auto" in result.stdout
    assert "total_orders: 2" in result.stdout
    assert "filled_orders: 2" in result.stdout
    assert "blocked_orders: 0" in result.stdout
    assert "position: 005930 qty=3.0 avg=10000.0 state=LONG" in result.stdout
    assert "position: 000660 qty=3.0 avg=50000.0 state=LONG" in result.stdout


def test_daily_paper_can_load_from_google_sheet_via_fake_script(tmp_path):
    fake_script = tmp_path / "fake_google_api.py"
    append_log = tmp_path / "append_log.jsonl"
    fake_script.write_text(
        """
import json
import os
import sys

cmd = sys.argv[1:]
if cmd[:2] != ['sheets', 'get'] and cmd[:2] != ['sheets', 'append']:
    raise SystemExit(9)
if cmd[:2] == ['sheets', 'get']:
    range_name = cmd[3]
    payloads = {
        'settings!A:B': [['key', 'value'], ['initial_cash_krw', '700000']],
        'holdings!A:C': [['symbol', 'quantity', 'avg_price'], ['005930', '5', '10000']],
        'orders!A:G': [['symbol', 'side', 'quantity', 'notional_krw', 'reason', 'market_price', 'fees_krw'], ['005930', 'SELL', '2', '', 'trim', '12000', '100'], ['000660', 'BUY', '3', '', 'entry', '50000', '200']],
    }
    print(json.dumps(payloads[range_name]))
else:
    range_name = cmd[3]
    values = json.loads(cmd[5])
    with open(os.environ['TOSS_ALPHA_APPEND_LOG'], 'a', encoding='utf-8') as fh:
        fh.write(json.dumps({'range': range_name, 'values': values}, ensure_ascii=False) + '\\n')
    print(json.dumps({'status': 'ok'}))
""".strip(),
        encoding="utf-8",
    )

    result = run_cli(
        "daily-paper",
        "--sheet-id",
        "sheet123",
        env={
            "TOSS_ALPHA_GOOGLE_API_SCRIPT": str(fake_script),
            "TOSS_ALPHA_APPEND_LOG": str(append_log),
        },
    )

    assert result.returncode == 0
    assert "status: OK" in result.stdout
    assert "filled_orders: 2" in result.stdout
    lines = append_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert json.loads(lines[0])["range"] == "runs!A:G"


def test_init_daily_paper_sheet_creates_template_via_fake_script(tmp_path):
    fake_script = tmp_path / "fake_google_api.py"
    create_log = tmp_path / "create_log.jsonl"
    fake_script.write_text(
        """
import json
import os
import sys
cmd = sys.argv[1:]
if cmd[:2] == ['sheets', 'create']:
    print(json.dumps({'status': 'created', 'spreadsheetId': 'sheet999', 'title': cmd[2], 'spreadsheetUrl': 'https://docs.google.com/spreadsheets/d/sheet999/edit'}))
elif cmd[:2] == ['sheets', 'update']:
    with open(os.environ['TOSS_ALPHA_CREATE_LOG'], 'a', encoding='utf-8') as fh:
        fh.write(json.dumps({'range': cmd[3], 'values': json.loads(cmd[5])}, ensure_ascii=False) + '\\n')
    print(json.dumps({'status': 'ok'}))
else:
    raise SystemExit(9)
""".strip(),
        encoding='utf-8',
    )

    result = run_cli(
        'init-daily-paper-sheet',
        '--title',
        'TOSS Daily Paper',
        env={
            'TOSS_ALPHA_GOOGLE_API_SCRIPT': str(fake_script),
            'TOSS_ALPHA_CREATE_LOG': str(create_log),
        },
    )

    assert result.returncode == 0
    assert 'spreadsheet_id: sheet999' in result.stdout
    assert 'spreadsheet_url: https://docs.google.com/spreadsheets/d/sheet999/edit' in result.stdout
    lines = create_log.read_text(encoding='utf-8').strip().splitlines()
    assert len(lines) == 6
    assert json.loads(lines[0])['range'] == 'settings!A:B'



def test_forbidden_shortcut_commands_do_not_exist():
    help_text = run_cli("--help").stdout
    for forbidden in ["place-order", "buy", "sell", "auto-trade"]:
        assert forbidden not in help_text
        assert run_cli(forbidden).returncode != 0
