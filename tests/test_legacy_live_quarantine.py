from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY_SCRIPTS = [
    ROOT / "scripts" / "rebound_open_detector_20260708.py",
    ROOT / "scripts" / "risk_off_inverse_entry_20260708.py",
    ROOT / "scripts" / "rebound_exit_watchdog_20260708.py",
]
SHELL_SCRIPT = ROOT / "scripts" / "rebound_exit_watchdog_20260708.sh"

UNSAFE_MARKERS = [
    '"TOSS_RISK_LIVE_TRADING_ENABLED": "true"',
    '"KIS_LIVE_TRADING_ENABLED": "true"',
    '"TOSS_LIVE_SUBMIT_ENABLED": "true"',
    '"TOSS_LIVE_SUBMIT_DRY_RUN": "false"',
    "I UNDERSTAND THIS IS A REAL ORDER",
]


def load_script(path: Path):
    name = f"legacy_quarantine_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_legacy_python_entrypoints_are_permanently_quarantined(capsys):
    for path in PY_SCRIPTS:
        module = load_script(path)
        assert module.main() == 0
        output = capsys.readouterr().out
        assert "LEGACY_LIVE_QUARANTINED" in output


def test_legacy_python_sources_cannot_self_enable_live_submission():
    for path in PY_SCRIPTS:
        text = path.read_text(encoding="utf-8")
        assert "run_live_submit_phase" not in text
        assert "live_readiness" not in text
        for marker in UNSAFE_MARKERS:
            assert marker not in text


def test_legacy_shell_launcher_forces_fail_closed_environment():
    text = SHELL_SCRIPT.read_text(encoding="utf-8")
    assert "TOSS_RISK_LIVE_TRADING_ENABLED=false" in text
    assert "KIS_LIVE_TRADING_ENABLED=false" in text
    assert "TOSS_LIVE_SUBMIT_ENABLED=false" in text
    assert "TOSS_LIVE_SUBMIT_DRY_RUN=true" in text
    assert "unset TOSS_LIVE_SUBMIT_CONFIRMATION" in text
    assert "I UNDERSTAND THIS IS A REAL ORDER" not in text
