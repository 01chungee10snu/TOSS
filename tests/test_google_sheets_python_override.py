import os
from pathlib import Path

from toss_alpha.storage.google_sheets import GoogleSheetsClient


def test_google_sheets_client_prefers_env_python_override(monkeypatch):
    monkeypatch.setenv("TOSS_ALPHA_GOOGLE_API_PYTHON", "/usr/local/bin/pythonX")

    client = GoogleSheetsClient(script_path="/tmp/fake_google_api.py")

    assert client.python_executable == "/usr/local/bin/pythonX"


def test_google_sheets_client_resolves_existing_env_script(monkeypatch, tmp_path):
    script = tmp_path / "google_api.py"
    script.write_text("# fake\n", encoding="utf-8")
    monkeypatch.setenv("TOSS_ALPHA_GOOGLE_API_SCRIPT", str(script))
    monkeypatch.setenv("TOSS_ALPHA_GOOGLE_API_PYTHON", "/usr/local/bin/pythonX")

    client = GoogleSheetsClient()

    assert client.script_path == str(script)


def test_google_sheets_client_falls_back_to_existing_hermes_skill_script(monkeypatch):
    expected_candidates = [
        Path.home() / ".hermes" / "profiles" / "work" / "skills" / "productivity" / "google-workspace" / "scripts" / "google_api.py",
        Path.home() / ".hermes" / "skills" / "productivity" / "google-workspace" / "scripts" / "google_api.py",
        Path.home() / ".hermes" / "hermes-agent" / "skills" / "productivity" / "google-workspace" / "scripts" / "google_api.py",
    ]
    expected = next((path for path in expected_candidates if path.exists()), None)
    if expected is None:
        return
    monkeypatch.delenv("TOSS_ALPHA_GOOGLE_API_SCRIPT", raising=False)
    monkeypatch.setenv("TOSS_ALPHA_GOOGLE_API_PYTHON", "/usr/local/bin/pythonX")

    client = GoogleSheetsClient()

    assert client.script_path == str(expected)
