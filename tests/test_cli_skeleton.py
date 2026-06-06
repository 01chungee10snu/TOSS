import subprocess
import sys


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "toss_alpha.cli", *args],
        check=False,
        text=True,
        capture_output=True,
        env={"PYTHONPATH": "src"},
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
    assert run_cli("research", "run", "--help").returncode == 0
    assert run_cli("backtest", "run", "--help").returncode == 0
    assert run_cli("draft-order", "--help").returncode == 0


def test_forbidden_commands_do_not_exist():
    help_text = run_cli("--help").stdout
    for forbidden in ["live", "place-order", "buy", "sell"]:
        assert forbidden not in help_text
        assert run_cli(forbidden).returncode != 0
