from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from toss_alpha.storage import DailyPipelineSheetsStore, GoogleSheetsClient, parse_google_sheet_id

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "backtests"
POLICY_DIR = ROOT / "config" / "generated_policies"

DAILY_SWEEP_JSON = REPORT_DIR / "random500_seed20260607_daily_strategy_sweep_2022-01-01_2025-12-31.json"
CONTEXTUAL_JSON = REPORT_DIR / "random500_seed20260607_contextual_optimizer_2022-01-01_2025-12-31.json"
CONTEXTUAL_POLICY = POLICY_DIR / "contextual_daily_policy_seed20260607.json"
MANIFEST_PATH = REPORT_DIR / "daily_data_strategy_pipeline_manifest.json"
MARKDOWN_PATH = REPORT_DIR / "daily_data_strategy_pipeline_manifest.md"


def run_step(script_rel: str) -> None:
    cmd = [sys.executable, str(ROOT / script_rel)]
    subprocess.run(cmd, cwd=ROOT, check=True)



def load_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"missing expected artifact: {path}")
    return json.loads(path.read_text(encoding="utf-8"))



def export_manifest_to_google_sheets(manifest: dict) -> dict | None:
    enabled = os.environ.get("TOSS_ALPHA_DAILY_PIPELINE_EXPORT_TO_SHEETS", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return None

    client = GoogleSheetsClient()
    sheet_value = os.environ.get("TOSS_ALPHA_DAILY_PIPELINE_SHEET_ID", "").strip()
    if sheet_value:
        spreadsheet_id = parse_google_sheet_id(sheet_value)
        spreadsheet_url = sheet_value if sheet_value.startswith("http") else f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
    else:
        created = DailyPipelineSheetsStore.bootstrap_new_sheet(
            client=client,
            title=os.environ.get("TOSS_ALPHA_DAILY_PIPELINE_SHEET_TITLE", "TOSS Daily Strategy Pipeline"),
        )
        spreadsheet_id = created["spreadsheet_id"]
        spreadsheet_url = created["spreadsheet_url"]

    store = DailyPipelineSheetsStore(spreadsheet_id=spreadsheet_id, client=client)
    store.write_manifest(manifest, manifest_json_path=str(MANIFEST_PATH))
    return {
        "spreadsheet_id": spreadsheet_id,
        "spreadsheet_url": spreadsheet_url,
    }



def main() -> None:
    run_step("scripts/run_random500_daily_strategy_sweep.py")
    run_step("scripts/optimize_contextual_daily_strategy.py")

    daily = load_json(DAILY_SWEEP_JSON)
    contextual = load_json(CONTEXTUAL_JSON)

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "1-day OHLCV data storage, analysis, and trading strategy generation pipeline",
        "data_storage": {
            "panel_csv": daily.get("panel_csv"),
            "symbol_count": daily.get("data_counts", {}).get("PANEL_CACHE"),
            "period": daily.get("period"),
        },
        "daily_sweep": {
            "best_variant": daily.get("best_variant"),
            "summary_csv": daily.get("outputs", {}).get("summary_csv"),
            "best_picks_csv": daily.get("outputs", {}).get("best_picks_csv"),
            "report_json": str(DAILY_SWEEP_JSON),
        },
        "contextual_strategy": {
            "approved_situations": list((contextual.get("approved_situations") or {}).keys()),
            "combined_train": contextual.get("combined_train"),
            "combined_test": contextual.get("combined_test"),
            "combined_all": contextual.get("combined_all"),
            "policy_json": str(CONTEXTUAL_POLICY),
            "outputs": contextual.get("outputs"),
            "report_json": str(CONTEXTUAL_JSON),
        },
        "disclaimer": "Research-only artifacts. Not investment advice. No live orders submitted.",
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Daily data → analysis → strategy pipeline manifest",
        "",
        "Research-only. 실주문 없음.",
        "",
        "## Data storage",
        f"- panel_csv: {manifest['data_storage']['panel_csv']}",
        f"- symbol_count: {manifest['data_storage']['symbol_count']}",
        f"- period: {manifest['data_storage']['period']}",
        "",
        "## Daily sweep",
        f"- best_variant: {manifest['daily_sweep']['best_variant']}",
        f"- summary_csv: {manifest['daily_sweep']['summary_csv']}",
        f"- best_picks_csv: {manifest['daily_sweep']['best_picks_csv']}",
        "",
        "## Contextual strategy",
        f"- approved_situations: {manifest['contextual_strategy']['approved_situations']}",
        f"- combined_test: {manifest['contextual_strategy']['combined_test']}",
        f"- policy_json: {manifest['contextual_strategy']['policy_json']}",
        f"- outputs: {manifest['contextual_strategy']['outputs']}",
        "",
        "## Outputs",
        f"- manifest_json: {MANIFEST_PATH}",
        f"- manifest_md: {MARKDOWN_PATH}",
    ]
    MARKDOWN_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    sheet_export = export_manifest_to_google_sheets(manifest)

    print(f"MANIFEST_JSON={MANIFEST_PATH}")
    print(f"MANIFEST_MD={MARKDOWN_PATH}")
    print(f"PANEL_CSV={manifest['data_storage']['panel_csv']}")
    print(f"POLICY_JSON={manifest['contextual_strategy']['policy_json']}")
    if sheet_export is not None:
        print(f"SHEET_ID={sheet_export['spreadsheet_id']}")
        print(f"SHEET_URL={sheet_export['spreadsheet_url']}")


if __name__ == "__main__":
    main()
