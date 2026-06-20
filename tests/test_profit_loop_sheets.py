from toss_alpha.research.profit_loop import build_profit_snapshot, derive_alert_level
from toss_alpha.storage.profit_loop_sheets import (
    ARTIFACT_DEFS,
    HISTORY_FIELDS,
    SUMMARY_FIELDS,
    ProfitLoopSheetsStore,
)


class FakeSheetsClient:
    def __init__(self):
        self.create_calls = []
        self.update_calls = []
        self.append_calls = []

    def create_spreadsheet(self, title: str, sheet_names: list[str] | None = None):
        self.create_calls.append((title, sheet_names or ["summary"]))
        return {
            "status": "created",
            "spreadsheetId": "profit-sheet-123",
            "title": title,
            "spreadsheetUrl": "https://docs.google.com/spreadsheets/d/profit-sheet-123/edit",
        }

    def update_values(self, spreadsheet_id: str, range_name: str, values):
        assert spreadsheet_id == "profit-sheet-123"
        self.update_calls.append((range_name, values))
        return {"status": "ok"}

    def append_values(self, spreadsheet_id: str, range_name: str, values):
        assert spreadsheet_id == "profit-sheet-123"
        self.append_calls.append((range_name, values))
        return {"status": "ok"}


def _winner_bundle(variant_id: str, *, worst_return: float, all_approved: bool = True):
    return {
        "variant_id": variant_id,
        "all_scenarios_approved": all_approved,
        "stress_pass_count": 4 if all_approved else 2,
        "worst_case_return_pct": worst_return,
        "worst_case_mdd_pct": -24.74,
        "worst_case_scenario_id": "stress_plus_30bps",
        "worst_case_sharpe_proxy": 1.234,
        "base_scenario": {
            "variant_id": variant_id,
            "aggregate_oos": {
                "total_return_pct": 118.22,
                "max_drawdown_pct": -21.21,
                "sharpe_proxy": 1.512,
                "total_trades": 76,
            },
            "consistency_ratio": 1.0,
            "negative_test_years": 0,
        },
        "scenarios": [
            {"scenario_id": "base", "aggregate_oos": {"total_return_pct": 118.22, "max_drawdown_pct": -21.21, "sharpe_proxy": 1.512}},
            {"scenario_id": "stress_plus_30bps", "aggregate_oos": {"total_return_pct": worst_return, "max_drawdown_pct": -24.74, "sharpe_proxy": 1.234}},
        ],
    }


def _snapshot():
    return build_profit_snapshot(
        robust_winner=_winner_bundle("veto_higher_liquidity_looser_range", worst_return=96.83),
        runner_up=_winner_bundle("veto_higher_liquidity", worst_return=95.06),
        generated_at_utc="2026-06-19T03:10:00+00:00",
        stress_report_md="/tmp/stress.md",
        stress_report_json="/tmp/stress.json",
        promoted_policy_json="/tmp/promoted.json",
        cron_state_json="/tmp/state.json",
    )


def test_bootstrap_profit_loop_sheet_creates_expected_tabs_and_headers():
    client = FakeSheetsClient()

    created = ProfitLoopSheetsStore.bootstrap_new_sheet(client=client)

    assert created["spreadsheet_id"] == "profit-sheet-123"
    assert client.create_calls == [(
        "TOSS Profit Loop DB",
        ["summary", "artifacts", "history"],
    )]
    updated_ranges = [name for name, _ in client.update_calls]
    assert updated_ranges == ["summary!A:B", "artifacts!A:C", "history!A:S"]
    history_header = client.update_calls[2][1][0]
    assert history_header == HISTORY_FIELDS


def test_write_snapshot_writes_all_seventeen_metrics_and_expanded_artifacts():
    client = FakeSheetsClient()
    store = ProfitLoopSheetsStore(spreadsheet_id="profit-sheet-123", client=client)

    store.write_snapshot(_snapshot())

    assert [name for name, _ in client.update_calls] == ["summary!A:B", "artifacts!A:C"]
    assert [name for name, _ in client.append_calls] == ["history!A:S"]
    summary_rows = client.update_calls[0][1]
    artifact_rows = client.update_calls[1][1]
    history_row = client.append_calls[0][1][0]

    # summary contains all 17 metrics + generated_at + disclaimer
    summary_fields = [row[0] for row in summary_rows[1:]]
    for field in SUMMARY_FIELDS:
        assert field in summary_fields, f"missing summary field: {field}"
    assert any(row == ["robust_winner", "veto_higher_liquidity_looser_range"] for row in summary_rows)
    assert any(row == ["alert_level", "GREEN"] for row in summary_rows)
    assert any(row == ["worst_case_sharpe_proxy", "1.234"] for row in summary_rows)
    assert any(row == ["runner_up_variant", "veto_higher_liquidity"] for row in summary_rows)

    # artifacts expanded to include base_policy_json
    assert ["reports", "stress_report_md", "/tmp/stress.md"] in artifact_rows
    assert any(row[1] == "base_policy_json" for row in artifact_rows)
    assert len(artifact_rows) - 1 == len(ARTIFACT_DEFS)

    # history row carries the new columns in declared order
    assert len(history_row) == len(HISTORY_FIELDS)
    assert history_row[1] == "veto_higher_liquidity_looser_range"  # robust_winner
    assert history_row[2] == "GREEN"  # alert_level
    assert history_row[8] is not None  # all_scenarios_approved slot
    assert history_row[17] == "NO_TRADE"  # ttak_status
    assert history_row[18] == "0"  # candidate_count


def test_build_profit_snapshot_degrades_to_red_when_no_winner():
    snapshot = build_profit_snapshot(
        robust_winner=None,
        runner_up=None,
        generated_at_utc="2026-06-19T03:10:00+00:00",
    )
    assert snapshot["alert_level"] == "RED"
    assert snapshot["robust_winner"] == ""
    assert snapshot["stress_pass_count"] == 0


def test_derive_alert_level_classification():
    assert derive_alert_level(robust_winner={"x": 1}, all_scenarios_approved=True, worst_case_return_pct=10.0) == "GREEN"
    assert derive_alert_level(robust_winner={"x": 1}, all_scenarios_approved=False, worst_case_return_pct=10.0) == "AMBER"
    assert derive_alert_level(robust_winner={"x": 1}, all_scenarios_approved=True, worst_case_return_pct=-5.0) == "AMBER"
    assert derive_alert_level(robust_winner=None, all_scenarios_approved=False, worst_case_return_pct=0.0) == "RED"
