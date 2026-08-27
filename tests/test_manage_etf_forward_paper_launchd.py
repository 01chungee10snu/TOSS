from __future__ import annotations

import importlib.util
import plistlib
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "manage_etf_forward_paper_launchd.py"


def load_module():
    spec = importlib.util.spec_from_file_location("manage_etf_forward_paper_launchd_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_plist_is_readonly_wrapper_and_weekday_1005_schedule(tmp_path):
    m = load_module()
    repo = tmp_path / "TOSS"
    payload = m.build_plist(repo=repo)

    assert payload["Label"] == "com.toss.etf-forward-paper"
    assert payload["ProgramArguments"] == ["/bin/bash", str(repo.resolve() / "scripts" / "run_executable_etf_paper.sh")]
    assert payload["WorkingDirectory"] == str(repo.resolve())
    assert payload["RunAtLoad"] is False
    assert payload["KeepAlive"] is False
    assert payload["StartCalendarInterval"] == [
        {"Weekday": weekday, "Hour": 10, "Minute": 5} for weekday in (2, 3, 4, 5, 6)
    ]


def test_plist_round_trip_is_stable(tmp_path):
    m = load_module()
    payload = m.build_plist(repo=tmp_path / "repo", hour=11, minute=7)
    assert plistlib.loads(m.plist_bytes(payload)) == payload


def test_check_installation_reports_match_without_mutating(tmp_path):
    m = load_module()
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    path = m.installed_path(home=home)
    path.parent.mkdir(parents=True)
    path.write_bytes(m.plist_bytes(m.build_plist(repo=repo)))
    before = path.read_bytes()

    result = m.check_installation(repo=repo, home=home)

    assert result["status"] == "MATCH"
    assert result["matches_expected"] is True
    assert path.read_bytes() == before


def test_check_installation_reports_drift_and_missing(tmp_path):
    m = load_module()
    repo = tmp_path / "repo"
    home = tmp_path / "home"

    missing = m.check_installation(repo=repo, home=home)
    assert missing["status"] == "MISSING"

    path = m.installed_path(home=home)
    path.parent.mkdir(parents=True)
    drift = m.build_plist(repo=repo)
    drift["StartCalendarInterval"][0]["Hour"] = 9
    path.write_bytes(m.plist_bytes(drift))

    result = m.check_installation(repo=repo, home=home)
    assert result["status"] == "DRIFT"
    assert result["matches_expected"] is False


def test_invalid_schedule_is_rejected(tmp_path):
    m = load_module()
    for hour, minute in [(-1, 5), (24, 5), (10, -1), (10, 60)]:
        try:
            m.build_plist(repo=tmp_path, hour=hour, minute=minute)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid schedule should fail")
