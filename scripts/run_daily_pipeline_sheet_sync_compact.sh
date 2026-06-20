#!/usr/bin/env bash
set -euo pipefail

ROOT="/Users/01chungee10/Github/TOSS"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"
LOG_PATH="$LOG_DIR/daily_pipeline_sheet_sync_$(date +%Y%m%d_%H%M%S).log"

cd "$ROOT"

export PYTHONPATH=src
export TOSS_ALPHA_DAILY_PIPELINE_EXPORT_TO_SHEETS=true
export TOSS_ALPHA_DAILY_PIPELINE_SHEET_ID="1rIawUGSPb0140dgBEom6BfIcG8MaxgYeuOuQBDcI4Ns"

.venv/bin/python scripts/run_daily_data_strategy_pipeline.py > "$LOG_PATH"

python3 - <<'PY' "$LOG_PATH"
import json, pathlib, re, sys
log_path = pathlib.Path(sys.argv[1])
root = pathlib.Path('/Users/01chungee10/Github/TOSS')
manifest_path = root / 'reports' / 'backtests' / 'daily_data_strategy_pipeline_manifest.json'
manifest = json.loads(manifest_path.read_text())
text = log_path.read_text()
sheet_id = re.search(r'SHEET_ID=(.+)', text)
sheet_url = re.search(r'SHEET_URL=(.+)', text)
data_storage = manifest.get('data_storage', {})
contextual = manifest.get('contextual_strategy', {})
combined_test = contextual.get('combined_test', {})
out = {
  'status': 'ok',
  'log_path': str(log_path),
  'manifest_json': str(manifest_path),
  'generated_at_utc': manifest.get('generated_at_utc'),
  'symbol_count': data_storage.get('symbol_count'),
  'approved_situations': contextual.get('approved_situations'),
  'combined_test_total_return_pct': combined_test.get('total_return_pct'),
  'combined_test_sharpe': combined_test.get('sharpe'),
  'sheet_id': sheet_id.group(1).strip() if sheet_id else '1rIawUGSPb0140dgBEom6BfIcG8MaxgYeuOuQBDcI4Ns',
  'sheet_url': sheet_url.group(1).strip() if sheet_url else 'https://docs.google.com/spreadsheets/d/1rIawUGSPb0140dgBEom6BfIcG8MaxgYeuOuQBDcI4Ns/edit'
}
print(json.dumps(out, ensure_ascii=False, indent=2))
PY
