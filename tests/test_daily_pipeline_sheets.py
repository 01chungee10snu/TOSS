from toss_alpha.storage.daily_pipeline_sheets import DailyPipelineSheetsStore


class FakeSheetsClient:
    def __init__(self):
        self.create_calls = []
        self.update_calls = []
        self.append_calls = []

    def create_spreadsheet(self, title: str, sheet_names: list[str] | None = None):
        self.create_calls.append((title, sheet_names or ["summary"]))
        return {
            "status": "created",
            "spreadsheetId": "sheet-pipeline-123",
            "title": title,
            "spreadsheetUrl": "https://docs.google.com/spreadsheets/d/sheet-pipeline-123/edit",
        }

    def update_values(self, spreadsheet_id: str, range_name: str, values):
        assert spreadsheet_id == "sheet-pipeline-123"
        self.update_calls.append((range_name, values))
        return {"status": "ok"}

    def append_values(self, spreadsheet_id: str, range_name: str, values):
        assert spreadsheet_id == "sheet-pipeline-123"
        self.append_calls.append((range_name, values))
        return {"status": "ok"}


def _manifest():
    return {
        "generated_at_utc": "2026-06-19T00:00:00+00:00",
        "purpose": "1-day OHLCV data storage, analysis, and trading strategy generation pipeline",
        "data_storage": {
            "panel_csv": "/tmp/panel.csv",
            "symbol_count": 496,
            "period": "2022-01-01..2025-12-31",
        },
        "daily_sweep": {
            "best_variant": "open_to_next_open_bottom_5d_reversal",
            "summary_csv": "/tmp/summary.csv",
            "best_picks_csv": "/tmp/best.csv",
            "report_json": "/tmp/daily.json",
        },
        "contextual_strategy": {
            "approved_situations": ["down_low_vol", "flat_high_vol"],
            "combined_test": {
                "total_return_pct": 25.77,
                "max_drawdown_pct": -9.71,
                "sharpe": 1.379,
                "total_trades": 105,
            },
            "policy_json": "/tmp/policy.json",
            "outputs": {
                "all_trials_csv": "/tmp/all_trials.csv",
                "selected_csv": "/tmp/selected.csv",
            },
            "report_json": "/tmp/contextual.json",
        },
        "disclaimer": "Research-only artifacts. Not investment advice. No live orders submitted.",
    }


def test_bootstrap_daily_pipeline_sheet_creates_expected_tabs_and_headers():
    client = FakeSheetsClient()

    created = DailyPipelineSheetsStore.bootstrap_new_sheet(client=client)

    assert created["spreadsheet_id"] == "sheet-pipeline-123"
    assert client.create_calls == [(
        "TOSS Daily Strategy Pipeline",
        ["summary", "artifacts", "history"],
    )]
    updated_ranges = [name for name, _ in client.update_calls]
    assert updated_ranges == ["summary!A:B", "artifacts!A:C", "history!A:L"]
    assert client.update_calls[0][1][0] == ["field", "value"]


def test_write_manifest_updates_summary_and_artifacts_and_appends_history():
    client = FakeSheetsClient()
    store = DailyPipelineSheetsStore(spreadsheet_id="sheet-pipeline-123", client=client)

    store.write_manifest(_manifest(), manifest_json_path="/tmp/manifest.json")

    updated_ranges = [name for name, _ in client.update_calls]
    appended_ranges = [name for name, _ in client.append_calls]
    assert updated_ranges == ["summary!A:B", "artifacts!A:C"]
    assert appended_ranges == ["history!A:L"]
    summary_rows = client.update_calls[0][1]
    artifact_rows = client.update_calls[1][1]
    history_rows = client.append_calls[0][1]
    assert ["field", "value"] == summary_rows[0]
    assert any(row == ["approved_situations", "down_low_vol, flat_high_vol"] for row in summary_rows)
    assert any(row == ["pipeline", "manifest_json", "/tmp/manifest.json"] for row in artifact_rows)
    assert history_rows[0][0] == "2026-06-19T00:00:00+00:00"
    assert history_rows[0][5] == "25.77"
