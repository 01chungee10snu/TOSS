import pandas as pd


def _panel(rows: list[dict]):
    columns = ["Date", "code", "Open", "High", "Low", "Close", "Volume"]
    frame = pd.DataFrame(rows, columns=columns)
    frame["Date"] = pd.to_datetime(frame["Date"])
    return frame


def test_fast_veto_skips_when_no_orders():
    from toss_alpha.execution.fast_veto import evaluate_fast_veto

    result = evaluate_fast_veto(candidate_payload={"orders": []}, panel=_panel([]), as_of="2025-12-30")

    assert result["status"] == "SKIPPED_NO_CANDIDATES"
    assert result["allowed_orders"] == []
    assert result["vetoed_symbols"] == []


def test_fast_veto_filters_high_gap_symbol_and_keeps_calm_symbol():
    from toss_alpha.execution.fast_veto import evaluate_fast_veto

    panel = _panel([
        {"Date": "2025-12-29", "code": "005930", "Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000000},
        {"Date": "2025-12-30", "code": "005930", "Open": 112, "High": 114, "Low": 110, "Close": 113, "Volume": 1000000},
        {"Date": "2025-12-29", "code": "000660", "Open": 200, "High": 202, "Low": 198, "Close": 200, "Volume": 1000000},
        {"Date": "2025-12-30", "code": "000660", "Open": 202, "High": 206, "Low": 200, "Close": 205, "Volume": 1000000},
    ])
    payload = {
        "orders": [
            {"symbol": "005930", "side": "BUY", "notional_krw": 100000},
            {"symbol": "000660", "side": "BUY", "notional_krw": 100000},
        ]
    }

    result = evaluate_fast_veto(candidate_payload=payload, panel=panel, as_of="2025-12-30", max_gap_pct=0.08, max_intraday_range_pct=0.15)

    assert result["status"] == "READY_WITH_VETO"
    assert [order["symbol"] for order in result["allowed_orders"]] == ["000660"]
    assert result["vetoed_symbols"] == ["005930"]
    assert result["reasons_by_symbol"]["005930"] == ["excessive_gap"]


def test_fast_veto_blocks_when_all_orders_are_vetoed():
    from toss_alpha.execution.fast_veto import evaluate_fast_veto

    panel = _panel([
        {"Date": "2025-12-29", "code": "005930", "Open": 100, "High": 102, "Low": 98, "Close": 100, "Volume": 1000000},
        {"Date": "2025-12-30", "code": "005930", "Open": 100, "High": 125, "Low": 95, "Close": 120, "Volume": 1000000},
    ])
    payload = {
        "orders": [
            {"symbol": "005930", "side": "BUY", "notional_krw": 100000},
        ]
    }

    result = evaluate_fast_veto(candidate_payload=payload, panel=panel, as_of="2025-12-30", max_gap_pct=0.20, max_intraday_range_pct=0.20)

    assert result["status"] == "BLOCKED_FAST_VETO"
    assert result["allowed_orders"] == []
    assert result["vetoed_symbols"] == ["005930"]
    assert result["reasons_by_symbol"]["005930"] == ["excessive_intraday_range"]


def test_fast_veto_filters_low_dollar_volume_symbol():
    from toss_alpha.execution.fast_veto import evaluate_fast_veto

    panel = _panel([
        {"Date": "2025-12-29", "code": "111111", "Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 10000},
        {"Date": "2025-12-30", "code": "111111", "Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 10000},
        {"Date": "2025-12-29", "code": "222222", "Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000000},
        {"Date": "2025-12-30", "code": "222222", "Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 1000000},
    ])
    payload = {
        "orders": [
            {"symbol": "111111", "side": "BUY", "notional_krw": 100000},
            {"symbol": "222222", "side": "BUY", "notional_krw": 100000},
        ]
    }

    result = evaluate_fast_veto(candidate_payload=payload, panel=panel, as_of="2025-12-30", min_dollar_volume_krw=5_000_000)

    assert [order["symbol"] for order in result["allowed_orders"]] == ["222222"]
    assert result["reasons_by_symbol"]["111111"] == ["low_dollar_volume"]


def test_fast_veto_filters_high_prev_volatility_symbol():
    from toss_alpha.execution.fast_veto import evaluate_fast_veto

    stable = []
    volatile = []
    stable_closes = [100] * 22
    volatile_closes = [100, 120, 85, 130, 80, 125, 82, 128, 84, 122, 86, 126, 88, 124, 90, 123, 92, 121, 94, 119, 96, 118]
    dates = pd.date_range("2025-12-09", periods=22, freq="D")
    for d, close in zip(dates, stable_closes):
        stable.append({"Date": str(d.date()), "code": "333333", "Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close, "Volume": 1000000})
    for d, close in zip(dates, volatile_closes):
        volatile.append({"Date": str(d.date()), "code": "444444", "Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close, "Volume": 1000000})
    panel = _panel(stable + volatile)
    payload = {
        "orders": [
            {"symbol": "333333", "side": "BUY", "notional_krw": 100000},
            {"symbol": "444444", "side": "BUY", "notional_krw": 100000},
        ]
    }

    result = evaluate_fast_veto(
        candidate_payload=payload,
        panel=panel,
        as_of="2025-12-30",
        max_gap_pct=1.0,
        max_intraday_range_pct=1.0,
        max_prev_volatility_20d=0.12,
    )

    assert [order["symbol"] for order in result["allowed_orders"]] == ["333333"]
    assert result["reasons_by_symbol"]["444444"] == ["excessive_prev_volatility_20d"]
