import os

from toss_alpha.storage.google_sheets import GoogleSheetsClient


def test_google_sheets_client_prefers_env_python_override(monkeypatch):
    monkeypatch.setenv("TOSS_ALPHA_GOOGLE_API_PYTHON", "/usr/local/bin/pythonX")

    client = GoogleSheetsClient(script_path="/tmp/fake_google_api.py")

    assert client.python_executable == "/usr/local/bin/pythonX"
