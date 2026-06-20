from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from toss_alpha.storage.google_sheets import GoogleSheetsClient


@dataclass(frozen=True)
class DailyPipelineSheetsLayout:
    summary_range: str = "summary!A:B"
    artifacts_range: str = "artifacts!A:C"
    history_range: str = "history!A:L"


@dataclass
class DailyPipelineSheetsStore:
    spreadsheet_id: str
    client: GoogleSheetsClient
    layout: DailyPipelineSheetsLayout = DailyPipelineSheetsLayout()

    @classmethod
    def bootstrap_new_sheet(
        cls,
        *,
        client: GoogleSheetsClient,
        title: str = "TOSS Daily Strategy Pipeline",
        layout: DailyPipelineSheetsLayout = DailyPipelineSheetsLayout(),
    ) -> dict[str, str]:
        created = client.create_spreadsheet(
            title=title,
            sheet_names=["summary", "artifacts", "history"],
        )
        spreadsheet_id = str(created["spreadsheetId"])
        client.update_values(spreadsheet_id, layout.summary_range, [["field", "value"]])
        client.update_values(spreadsheet_id, layout.artifacts_range, [["category", "name", "value"]])
        client.update_values(
            spreadsheet_id,
            layout.history_range,
            [[
                "generated_at_utc",
                "period",
                "symbol_count",
                "best_variant",
                "approved_situations",
                "combined_test_total_return_pct",
                "combined_test_max_drawdown_pct",
                "combined_test_sharpe",
                "combined_test_total_trades",
                "panel_csv",
                "policy_json",
                "manifest_json",
            ]],
        )
        return {
            "spreadsheet_id": spreadsheet_id,
            "spreadsheet_url": str(created.get("spreadsheetUrl", "")),
            "title": str(created.get("title", title)),
        }

    def write_manifest(self, manifest: dict[str, Any], *, manifest_json_path: str) -> None:
        summary_rows = [["field", "value"]] + [[key, value] for key, value in _summary_pairs(manifest)]
        self.client.update_values(self.spreadsheet_id, self.layout.summary_range, summary_rows)

        artifact_rows = [["category", "name", "value"]] + _artifact_rows(manifest, manifest_json_path=manifest_json_path)
        self.client.update_values(self.spreadsheet_id, self.layout.artifacts_range, artifact_rows)

        self.client.append_values(
            self.spreadsheet_id,
            self.layout.history_range,
            [[
                manifest.get("generated_at_utc", ""),
                _stringify(manifest.get("data_storage", {}).get("period", "")),
                _stringify(manifest.get("data_storage", {}).get("symbol_count", "")),
                _stringify(manifest.get("daily_sweep", {}).get("best_variant", "")),
                ", ".join(manifest.get("contextual_strategy", {}).get("approved_situations", [])),
                _stringify(manifest.get("contextual_strategy", {}).get("combined_test", {}).get("total_return_pct", "")),
                _stringify(manifest.get("contextual_strategy", {}).get("combined_test", {}).get("max_drawdown_pct", "")),
                _stringify(manifest.get("contextual_strategy", {}).get("combined_test", {}).get("sharpe", "")),
                _stringify(manifest.get("contextual_strategy", {}).get("combined_test", {}).get("total_trades", "")),
                _stringify(manifest.get("data_storage", {}).get("panel_csv", "")),
                _stringify(manifest.get("contextual_strategy", {}).get("policy_json", "")),
                manifest_json_path,
            ]],
        )


def _summary_pairs(manifest: dict[str, Any]) -> list[tuple[str, str]]:
    combined_test = manifest.get("contextual_strategy", {}).get("combined_test", {})
    return [
        ("generated_at_utc", _stringify(manifest.get("generated_at_utc", ""))),
        ("purpose", _stringify(manifest.get("purpose", ""))),
        ("period", _stringify(manifest.get("data_storage", {}).get("period", ""))),
        ("symbol_count", _stringify(manifest.get("data_storage", {}).get("symbol_count", ""))),
        ("best_variant", _stringify(manifest.get("daily_sweep", {}).get("best_variant", ""))),
        ("approved_situations", ", ".join(manifest.get("contextual_strategy", {}).get("approved_situations", []))),
        ("combined_test_total_return_pct", _stringify(combined_test.get("total_return_pct", ""))),
        ("combined_test_max_drawdown_pct", _stringify(combined_test.get("max_drawdown_pct", ""))),
        ("combined_test_sharpe", _stringify(combined_test.get("sharpe", ""))),
        ("combined_test_total_trades", _stringify(combined_test.get("total_trades", ""))),
        ("disclaimer", _stringify(manifest.get("disclaimer", ""))),
    ]


def _artifact_rows(manifest: dict[str, Any], *, manifest_json_path: str) -> list[list[str]]:
    outputs = []
    outputs.append(["data_storage", "panel_csv", _stringify(manifest.get("data_storage", {}).get("panel_csv", ""))])
    outputs.append(["daily_sweep", "summary_csv", _stringify(manifest.get("daily_sweep", {}).get("summary_csv", ""))])
    outputs.append(["daily_sweep", "best_picks_csv", _stringify(manifest.get("daily_sweep", {}).get("best_picks_csv", ""))])
    outputs.append(["daily_sweep", "report_json", _stringify(manifest.get("daily_sweep", {}).get("report_json", ""))])
    outputs.append(["contextual_strategy", "policy_json", _stringify(manifest.get("contextual_strategy", {}).get("policy_json", ""))])
    outputs.append(["contextual_strategy", "report_json", _stringify(manifest.get("contextual_strategy", {}).get("report_json", ""))])
    outputs.append(["pipeline", "manifest_json", manifest_json_path])
    for name, value in (manifest.get("contextual_strategy", {}).get("outputs") or {}).items():
        outputs.append(["contextual_strategy.outputs", _stringify(name), _stringify(value)])
    return outputs


def _stringify(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(_stringify(v) for v in value)
    if value is None:
        return ""
    return str(value)
