from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from toss_alpha.execution.daily_paper import DailyPaperExecutionResult, DailyPaperPlan, DailyPaperOrder, HoldingSeed
from toss_alpha.data.schema import OrderIntent

_DEFAULT_HERMES_HOME = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes/profiles/work")))


def _candidate_google_api_scripts() -> list[Path]:
    candidates: list[Path] = []
    if os.environ.get("TOSS_ALPHA_GOOGLE_API_SCRIPT"):
        candidates.append(Path(os.path.expanduser(os.environ["TOSS_ALPHA_GOOGLE_API_SCRIPT"])))
    candidates.extend(
        [
            _DEFAULT_HERMES_HOME / "skills" / "productivity" / "google-workspace" / "scripts" / "google_api.py",
            Path.home() / ".hermes" / "profiles" / "work" / "skills" / "productivity" / "google-workspace" / "scripts" / "google_api.py",
            Path.home() / ".hermes" / "skills" / "productivity" / "google-workspace" / "scripts" / "google_api.py",
            Path.home() / ".hermes" / "hermes-agent" / "skills" / "productivity" / "google-workspace" / "scripts" / "google_api.py",
        ]
    )
    return candidates


def _resolve_google_api_script(script_path: str | None = None) -> str:
    if script_path:
        return os.path.expanduser(script_path)
    for candidate in _candidate_google_api_scripts():
        if candidate.exists():
            return str(candidate)
    checked = "\n".join(str(candidate) for candidate in _candidate_google_api_scripts())
    raise FileNotFoundError(f"google_api.py not found. Checked:\n{checked}")


def _python_can_import_googleapiclient(python_path: str | Path) -> bool:
    result = subprocess.run(
        [str(python_path), "-c", "import googleapiclient"],
        check=False,
        text=True,
        capture_output=True,
    )
    return result.returncode == 0


def _resolve_google_api_python(python_executable: str | None = None) -> str:
    if python_executable:
        return python_executable
    env_python = os.environ.get("TOSS_ALPHA_GOOGLE_API_PYTHON")
    if env_python:
        return env_python
    candidates: list[str | Path | None] = [
        Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "python",
        shutil.which("python"),
        sys.executable,
        shutil.which("python3"),
    ]
    for candidate in candidates:
        if candidate and _python_can_import_googleapiclient(candidate):
            return str(candidate)
    return sys.executable


@dataclass(frozen=True)
class GoogleSheetsLayout:
    settings_range: str = "settings!A:B"
    holdings_range: str = "holdings!A:C"
    orders_range: str = "orders!A:G"
    runs_range: str = "runs!A:G"
    fills_range: str = "fills!A:H"
    positions_range: str = "positions!A:F"


class GoogleSheetsClient:
    def __init__(self, script_path: str | None = None, python_executable: str | None = None) -> None:
        self.script_path = _resolve_google_api_script(script_path)
        self.python_executable = _resolve_google_api_python(python_executable)

    def create_spreadsheet(self, title: str, sheet_names: list[str] | None = None) -> dict[str, object]:
        names = sheet_names or ["settings"]
        args = ["sheets", "create", "--title", title]
        for sheet_name in names:
            args.extend(["--sheet-name", sheet_name])
        return self._run_json(args)

    def get_values(self, spreadsheet_id: str, range_name: str) -> list[list[object]]:
        return self._run_json(["sheets", "get", spreadsheet_id, range_name])

    def update_values(self, spreadsheet_id: str, range_name: str, values: list[list[object]]) -> dict[str, object]:
        return self._run_json(["sheets", "update", spreadsheet_id, range_name, "--values", json.dumps(values, ensure_ascii=False)])

    def append_values(self, spreadsheet_id: str, range_name: str, values: list[list[object]]) -> dict[str, object]:
        return self._run_json(["sheets", "append", spreadsheet_id, range_name, "--values", json.dumps(values, ensure_ascii=False)])

    def _run_json(self, args: list[str]):
        result = subprocess.run(
            [self.python_executable, self.script_path, *args],
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"google sheets command failed: {args}")
        payload = result.stdout.strip()
        if not payload:
            raise RuntimeError(f"empty google sheets response for args={args}")
        return json.loads(payload)


@dataclass
class GoogleSheetsDailyPaperStore:
    spreadsheet_id: str
    client: GoogleSheetsClient
    layout: GoogleSheetsLayout = GoogleSheetsLayout()

    @classmethod
    def bootstrap_new_sheet(
        cls,
        *,
        client: GoogleSheetsClient,
        title: str = "TOSS Daily Paper",
        layout: GoogleSheetsLayout = GoogleSheetsLayout(),
        initial_cash_krw: float = 1_000_000,
    ) -> dict[str, str]:
        created = client.create_spreadsheet(
            title=title,
            sheet_names=["settings", "holdings", "orders", "runs", "fills", "positions"],
        )
        spreadsheet_id = str(created["spreadsheetId"])
        client.update_values(spreadsheet_id, layout.settings_range, [["key", "value"], ["initial_cash_krw", str(int(initial_cash_krw))]])
        client.update_values(spreadsheet_id, layout.holdings_range, [["symbol", "quantity", "avg_price"]])
        client.update_values(spreadsheet_id, layout.orders_range, [["symbol", "side", "quantity", "notional_krw", "reason", "market_price", "fees_krw"]])
        client.update_values(spreadsheet_id, layout.runs_range, [["as_of", "status", "total_orders", "filled_orders", "blocked_orders", "ending_cash_krw", "realized_pnl_krw"]])
        client.update_values(spreadsheet_id, layout.fills_range, [["as_of", "symbol", "side", "fill_price", "fill_quantity", "fees_krw", "realized_pnl_krw", "intent_id"]])
        client.update_values(spreadsheet_id, layout.positions_range, [["as_of", "symbol", "quantity", "avg_price", "state", "cash_krw"]])
        return {
            "spreadsheet_id": spreadsheet_id,
            "spreadsheet_url": str(created.get("spreadsheetUrl", "")),
            "title": str(created.get("title", title)),
        }

    def load_plan(self, *, strategy_id: str = "daily-paper-sheet") -> DailyPaperPlan:
        settings_rows = self.client.get_values(self.spreadsheet_id, self.layout.settings_range)
        holdings_rows = self.client.get_values(self.spreadsheet_id, self.layout.holdings_range)
        orders_rows = self.client.get_values(self.spreadsheet_id, self.layout.orders_range)

        initial_cash_krw = float(_mapping_from_rows(settings_rows).get("initial_cash_krw", 0.0))
        holdings = [_holding_from_row(row) for row in _records_from_rows(holdings_rows)]
        orders = [_order_from_row(row, strategy_id=strategy_id) for row in _records_from_rows(orders_rows)]
        return DailyPaperPlan(initial_cash_krw=initial_cash_krw, holdings=holdings, orders=orders)

    def write_result(self, result: DailyPaperExecutionResult, *, as_of: str | None = None) -> None:
        stamp = as_of or datetime.now(timezone.utc).date().isoformat()
        self.client.append_values(
            self.spreadsheet_id,
            self.layout.runs_range,
            [[stamp, result.status, result.total_orders, result.filled_orders, result.blocked_orders, result.ledger.cash_krw, result.ledger.realized_pnl_krw]],
        )
        fill_rows = []
        for order_result in result.order_results:
            if order_result.fill is None:
                continue
            fill_rows.append([
                stamp,
                _sheet_symbol(order_result.fill.symbol),
                order_result.fill.side,
                order_result.fill.fill_price,
                order_result.fill.fill_quantity,
                order_result.fill.fees_krw,
                order_result.fill.realized_pnl_krw,
                order_result.fill.intent_id,
            ])
        if fill_rows:
            self.client.append_values(self.spreadsheet_id, self.layout.fills_range, fill_rows)
        position_rows = [
            [stamp, _sheet_symbol(symbol), position.quantity, position.avg_price, position.state, result.ledger.cash_krw]
            for symbol, position in sorted(result.ledger.positions.items())
        ]
        if position_rows:
            self.client.append_values(self.spreadsheet_id, self.layout.positions_range, position_rows)


def parse_google_sheet_id(value: str) -> str:
    value = value.strip()
    if "/spreadsheets/d/" in value:
        parts = value.split("/spreadsheets/d/", 1)[1]
        return parts.split("/", 1)[0]
    parsed = urlparse(value)
    if parsed.query:
        query = parse_qs(parsed.query)
        if "id" in query and query["id"]:
            return query["id"][0]
    return value


def _mapping_from_rows(rows: list[list[object]]) -> dict[str, str]:
    if not rows:
        return {}
    records = rows[1:] if rows and len(rows[0]) >= 2 and str(rows[0][0]).lower() == "key" else rows
    mapping: dict[str, str] = {}
    for row in records:
        if not row:
            continue
        key = str(row[0]).strip()
        if not key:
            continue
        value = "" if len(row) < 2 else str(row[1]).strip()
        mapping[key] = value
    return mapping


def _records_from_rows(rows: list[list[object]]) -> list[dict[str, str]]:
    if not rows:
        return []
    header = [str(cell).strip() for cell in rows[0]]
    records: list[dict[str, str]] = []
    for row in rows[1:]:
        if not any(str(cell).strip() for cell in row):
            continue
        record = {header[idx]: str(row[idx]).strip() if idx < len(row) else "" for idx in range(len(header))}
        records.append(record)
    return records


def _holding_from_row(row: dict[str, str]) -> HoldingSeed:
    return HoldingSeed(symbol=_normalize_symbol(row["symbol"]), quantity=float(row["quantity"]), avg_price=float(row["avg_price"]))


def _order_from_row(row: dict[str, str], *, strategy_id: str) -> DailyPaperOrder:
    quantity = float(row["quantity"]) if row.get("quantity") else None
    notional_krw = float(row["notional_krw"]) if row.get("notional_krw") else None
    intent = OrderIntent(
        strategy_id=strategy_id,
        symbol=_normalize_symbol(row["symbol"]),
        side=row["side"],
        quantity=quantity,
        notional_krw=notional_krw,
        reason=row.get("reason") or "sheet order",
        mode="paper_auto",
    )
    return DailyPaperOrder(
        intent=intent,
        market_price=float(row["market_price"]),
        fees_krw=float(row.get("fees_krw") or 0.0),
    )


def _normalize_symbol(value: object) -> str:
    symbol = str(value).strip()
    if symbol.isdigit() and 1 <= len(symbol) < 6:
        return symbol.zfill(6)
    return symbol


def _sheet_symbol(value: object) -> str:
    symbol = _normalize_symbol(value)
    if symbol.isdigit():
        return f"'{symbol}"
    return symbol
