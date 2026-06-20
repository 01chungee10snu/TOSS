import json
from pathlib import Path

from toss_alpha.execution.daily_paper import DailyPaperPlan, run_daily_paper
from toss_alpha.storage.google_sheets import GoogleSheetsDailyPaperStore, GoogleSheetsLayout


class FakeSheetsClient:
    def __init__(self):
        self.get_payloads = {
            "settings!A:B": [["key", "value"], ["initial_cash_krw", "700000"]],
            "holdings!A:C": [["symbol", "quantity", "avg_price"], ["005930", "5", "10000"]],
            "orders!A:G": [
                ["symbol", "side", "quantity", "notional_krw", "reason", "market_price", "fees_krw"],
                ["005930", "SELL", "2", "", "trim", "12000", "100"],
                ["000660", "BUY", "3", "", "entry", "50000", "200"],
            ],
        }
        self.append_calls = []
        self.update_calls = []
        self.create_calls = []

    def create_spreadsheet(self, title: str, sheet_names: list[str] | None = None):
        self.create_calls.append((title, sheet_names or ["settings"]))
        return {"status": "created", "spreadsheetId": "sheet123", "title": title, "spreadsheetUrl": "https://docs.google.com/spreadsheets/d/sheet123/edit"}

    def get_values(self, spreadsheet_id: str, range_name: str):
        assert spreadsheet_id == "sheet123"
        return self.get_payloads[range_name]

    def append_values(self, spreadsheet_id: str, range_name: str, values: list[list[object]]):
        assert spreadsheet_id == "sheet123"
        self.append_calls.append((range_name, values))
        return {"status": "ok"}

    def update_values(self, spreadsheet_id: str, range_name: str, values: list[list[object]]):
        assert spreadsheet_id == "sheet123"
        self.update_calls.append((range_name, values))
        return {"status": "ok"}



def test_google_sheets_store_loads_daily_paper_plan():
    store = GoogleSheetsDailyPaperStore(client=FakeSheetsClient(), spreadsheet_id="sheet123")

    plan = store.load_plan(strategy_id="sheet-strategy")

    assert isinstance(plan, DailyPaperPlan)
    assert plan.initial_cash_krw == 700_000
    assert plan.holdings[0].symbol == "005930"
    assert plan.orders[0].intent.symbol == "005930"
    assert plan.orders[0].intent.strategy_id == "sheet-strategy"
    assert plan.orders[0].intent.mode == "paper_auto"
    assert plan.orders[1].intent.symbol == "000660"
    assert plan.orders[1].market_price == 50_000



def test_google_sheets_store_appends_run_fill_and_position_rows():
    client = FakeSheetsClient()
    store = GoogleSheetsDailyPaperStore(client=client, spreadsheet_id="sheet123")
    plan = store.load_plan(strategy_id="sheet-strategy")
    result = run_daily_paper(plan)

    store.write_result(result, as_of="2026-06-15")

    appended_ranges = [name for name, _ in client.append_calls]
    assert appended_ranges == ["runs!A:G", "fills!A:H", "positions!A:F"]
    runs_rows = client.append_calls[0][1]
    fills_rows = client.append_calls[1][1]
    positions_rows = client.append_calls[2][1]
    assert runs_rows[0][1] == "OK"
    assert runs_rows[0][2] == 2
    assert runs_rows[0][5] == 573700.0
    assert len(fills_rows) == 2
    assert fills_rows[0][1] == "'005930"
    assert fills_rows[1][1] == "'000660"
    assert positions_rows[-1][1] == "'005930"



def test_google_sheets_store_bootstraps_new_sheet_tabs_and_headers():
    client = FakeSheetsClient()

    created = GoogleSheetsDailyPaperStore.bootstrap_new_sheet(client=client, title="TOSS Daily Paper")

    assert created["spreadsheet_id"] == "sheet123"
    assert client.create_calls == [(
        "TOSS Daily Paper",
        ["settings", "holdings", "orders", "runs", "fills", "positions"],
    )]
    updated_ranges = [name for name, _ in client.update_calls]
    assert updated_ranges == ["settings!A:B", "holdings!A:C", "orders!A:G", "runs!A:G", "fills!A:H", "positions!A:F"]
    assert client.update_calls[0][1][0] == ["key", "value"]
    assert client.update_calls[0][1][1] == ["initial_cash_krw", "1000000"]



def test_google_sheets_layout_accepts_custom_tab_names():
    layout = GoogleSheetsLayout(settings_range="config!A:B", holdings_range="book!A:C", orders_range="queue!A:G")
    client = FakeSheetsClient()
    client.get_payloads = {
        "config!A:B": [["key", "value"], ["initial_cash_krw", "100000"]],
        "book!A:C": [["symbol", "quantity", "avg_price"]],
        "queue!A:G": [["symbol", "side", "quantity", "notional_krw", "reason", "market_price", "fees_krw"]],
    }
    store = GoogleSheetsDailyPaperStore(client=client, spreadsheet_id="sheet123", layout=layout)

    plan = store.load_plan(strategy_id="custom-layout")

    assert plan.initial_cash_krw == 100_000
    assert plan.holdings == []
    assert plan.orders == []
