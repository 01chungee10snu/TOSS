from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from toss_alpha.storage.google_sheets import GoogleSheetsClient

# Ordered metric fields surfaced in the summary tab and the history time-series.
# This is the single source of truth for the 17-metric dashboard schema.
SUMMARY_FIELDS: list[str] = [
    "generated_at_utc",
    "robust_winner",
    "alert_level",
    "worst_case_scenario_id",
    "worst_case_return_pct",
    "worst_case_mdd_pct",
    "worst_case_sharpe_proxy",
    "stress_pass_count",
    "all_scenarios_approved",
    "base_return_pct",
    "base_mdd_pct",
    "base_sharpe_proxy",
    "base_trades",
    "consistency_ratio",
    "negative_test_years",
    "runner_up_variant",
    "runner_up_worst_case_return_pct",
    "ttak_status",
    "candidate_count",
    "disclaimer",
]

# Subset written as one row per run into the history tab (time-series trail).
HISTORY_FIELDS: list[str] = [
    "generated_at_utc",
    "robust_winner",
    "alert_level",
    "worst_case_scenario_id",
    "worst_case_return_pct",
    "worst_case_mdd_pct",
    "worst_case_sharpe_proxy",
    "stress_pass_count",
    "all_scenarios_approved",
    "base_return_pct",
    "base_mdd_pct",
    "base_sharpe_proxy",
    "base_trades",
    "consistency_ratio",
    "negative_test_years",
    "runner_up_variant",
    "runner_up_worst_case_return_pct",
    "ttak_status",
    "candidate_count",
]

# Artifact references grouped by category for the artifacts tab.
ARTIFACT_DEFS: list[tuple[str, str]] = [
    ("reports", "stress_report_md"),
    ("reports", "stress_report_json"),
    ("policies", "promoted_policy_json"),
    ("policies", "base_policy_json"),
    ("state", "cron_state_json"),
]


def _column_letter(index_one_based: int) -> str:
    """Convert a 1-based column index to a spreadsheet column letter (1 -> A)."""
    result = ""
    n = index_one_based
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


@dataclass(frozen=True)
class ProfitLoopSheetsLayout:
    summary_range: str = "summary!A:B"
    artifacts_range: str = "artifacts!A:C"
    history_range: str = f"history!A:{_column_letter(len(HISTORY_FIELDS))}"


@dataclass
class ProfitLoopSheetsStore:
    spreadsheet_id: str
    client: GoogleSheetsClient
    layout: ProfitLoopSheetsLayout = ProfitLoopSheetsLayout()

    @classmethod
    def bootstrap_new_sheet(
        cls,
        *,
        client: GoogleSheetsClient,
        title: str = "TOSS Profit Loop DB",
        layout: ProfitLoopSheetsLayout = ProfitLoopSheetsLayout(),
    ) -> dict[str, str]:
        created = client.create_spreadsheet(title=title, sheet_names=["summary", "artifacts", "history"])
        spreadsheet_id = str(created["spreadsheetId"])
        client.update_values(spreadsheet_id, layout.summary_range, [["field", "value"]])
        client.update_values(spreadsheet_id, layout.artifacts_range, [["category", "name", "value"]])
        client.update_values(spreadsheet_id, layout.history_range, [list(HISTORY_FIELDS)])
        return {
            "spreadsheet_id": spreadsheet_id,
            "spreadsheet_url": str(created.get("spreadsheetUrl", "")),
            "title": str(created.get("title", title)),
        }

    def write_snapshot(self, snapshot: dict[str, Any]) -> None:
        summary_rows = [["field", "value"]] + [[k, _stringify(snapshot.get(k, ""))] for k in SUMMARY_FIELDS]
        self.client.update_values(self.spreadsheet_id, self.layout.summary_range, summary_rows)
        artifact_rows = [["category", "name", "value"]] + _artifact_rows(snapshot)
        self.client.update_values(self.spreadsheet_id, self.layout.artifacts_range, artifact_rows)
        self.client.append_values(
            self.spreadsheet_id,
            self.layout.history_range,
            [[_stringify(snapshot.get(k, "")) for k in HISTORY_FIELDS]],
        )


def _artifact_rows(snapshot: dict[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    for category, field in ARTIFACT_DEFS:
        rows.append([category, field, _stringify(snapshot.get(field, ""))])
    return rows


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    return str(value)
