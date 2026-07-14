from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from toss_alpha.execution.live_submit import LiveOrderLedger
from toss_alpha.execution.order_management import (
    extract_kis_order_ids,
    manage_submitted_order_ledger,
    parse_status_payload,
)


def test_kis_order_status_client_uses_current_official_daily_fill_contract(monkeypatch):
    from toss_alpha.execution import order_management as om
    from toss_alpha.execution.live_ready import LiveExecutionConfig

    captured = {}

    class FakeResponse:
        ok = True
        status_code = 200
        text = "ok"

        def json(self):
            return {"rt_cd": "0", "output1": []}

    def fake_request(method, url, headers=None, params=None, timeout=None):
        captured.update(method=method, url=url, headers=headers, params=params)
        return FakeResponse()

    monkeypatch.setattr(om, "kis_request", fake_request)
    client = om.KisOrderStatusClient(
        LiveExecutionConfig(
            provider="kis", app_key="app", app_secret="secret", access_token="token",
            cano="12345678", account_product_code="01",
            base_url="https://openapi.koreainvestment.com:9443",
        )
    )

    client.inquire_daily_fills(order_no="0001", order_orgno="03420", day=datetime(2026, 7, 14, tzinfo=timezone.utc))

    assert captured["headers"]["tr_id"] == "TTTC0081R"
    assert captured["params"]["EXCG_ID_DVSN_CD"] == "KRX"
    assert captured["params"]["ODNO"] == "0001"


def test_kis_cancel_uses_current_official_contract(monkeypatch):
    from toss_alpha.execution import order_management as om
    from toss_alpha.execution.live_ready import LiveExecutionConfig

    calls = []

    class FakeResponse:
        ok = True
        status_code = 200
        text = "ok"

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json})
        if url.endswith("/uapi/hashkey"):
            return FakeResponse({"HASH": "hash-1"})
        return FakeResponse({"rt_cd": "0", "output": {}})

    monkeypatch.setattr(om, "kis_post", fake_post)
    client = om.KisOrderStatusClient(
        LiveExecutionConfig(
            provider="kis", app_key="app", app_secret="secret", access_token="token",
            cano="12345678", account_product_code="01",
            base_url="https://openapi.koreainvestment.com:9443",
        )
    )

    client.cancel_order(order_no="0001", order_orgno="03420")

    assert calls[1]["headers"]["tr_id"] == "TTTC0013U"
    assert calls[1]["json"]["EXCG_ID_DVSN_CD"] == "KRX"
    assert calls[1]["json"]["RVSE_CNCL_DVSN_CD"] == "02"


def test_extract_kis_order_ids_from_submit_result():
    result = {
        "json": {
            "rt_cd": "0",
            "output": {"ODNO": "0000385600", "KRX_FWDG_ORD_ORGNO": "03420"},
        }
    }

    assert extract_kis_order_ids(result) == ("0000385600", "03420")


def test_extract_kis_order_ids_from_reconcile_row():
    result = {"order_no": "0000385600", "order_orgno": "03420", "status": "SUBMITTED"}

    assert extract_kis_order_ids(result) == ("0000385600", "03420")


def test_parse_status_payload_detects_filled_order():
    payload = {
        "json": {
            "output1": [
                {"odno": "0001", "ord_qty": "9", "tot_ccld_qty": "9", "rmn_qty": "0"},
            ]
        }
    }

    parsed = parse_status_payload(payload, order_no="0001")

    assert parsed["status"] == "FILLED"
    assert parsed["filled_qty"] == 9.0
    assert parsed["remaining_qty"] == 0.0


def test_parse_status_payload_detects_unfilled_submitted_order():
    payload = {
        "json": {
            "output1": [
                {"odno": "0001", "ord_qty": "9", "tot_ccld_qty": "0", "rmn_qty": "9"},
            ]
        }
    }

    parsed = parse_status_payload(payload, order_no="0001")

    assert parsed["status"] == "SUBMITTED"
    assert parsed["remaining_qty"] == 9.0


def test_parse_status_payload_does_not_fallback_to_wrong_order():
    payload = {
        "json": {
            "output1": [
                {"odno": "9999", "ord_qty": "9", "tot_ccld_qty": "9", "rmn_qty": "0"},
                {"ord_qty": "3", "tot_ccld_qty": "3", "rmn_qty": "0"},  # missing order id must not match
            ]
        }
    }

    parsed = parse_status_payload(payload, order_no="0001")

    assert parsed["status"] == "UNKNOWN"
    assert parsed["reason"] == "order_no_not_found_in_status_payload"
    assert parsed["filled_qty"] is None


def test_parse_status_payload_detects_partial_fill_then_canceled_terminal():
    payload = {
        "json": {
            "output1": [
                {"odno": "0001", "ord_qty": "9", "tot_ccld_qty": "4", "rmn_qty": "0"},
            ]
        }
    }

    parsed = parse_status_payload(payload, order_no="0001")

    assert parsed["status"] == "PARTIALLY_FILLED_CANCELED"


def test_live_order_ledger_releases_key_after_filled_status(tmp_path):
    ledger_path = tmp_path / "ledger.jsonl"
    ledger = LiveOrderLedger(ledger_path)
    key = "2026-07-07:ttak_absolute_return_loop:307930:BUY"
    ledger.append({"ledger_key": key, "status": "SUBMITTED", "timestamp": "2026-07-07T01:00:00+00:00"})
    assert ledger.has_live_submission(key) is True

    ledger.append({"ledger_key": key, "status": "FILLED", "timestamp": "2026-07-07T01:05:00+00:00"})

    assert ledger.has_live_submission(key) is False


def test_manage_submitted_order_ledger_appends_filled_status(tmp_path, monkeypatch):
    from toss_alpha.execution import order_management as om

    ledger_path = tmp_path / "ledger.jsonl"
    key = "2026-07-07:ttak_absolute_return_loop:307930:BUY"
    LiveOrderLedger(ledger_path).append(
        {
            "ledger_key": key,
            "status": "SUBMITTED",
            "timestamp": "2026-07-07T01:00:00+00:00",
            "result": {"json": {"output": {"ODNO": "0001", "KRX_FWDG_ORD_ORGNO": "03420"}}},
        }
    )

    class FakeClient:
        def __init__(self, config):
            self.config = config

        def inquire_daily_fills(self, *, order_no, order_orgno, day):
            return {"json": {"output1": [{"odno": order_no, "ord_qty": "9", "tot_ccld_qty": "9", "rmn_qty": "0"}]}}

    monkeypatch.setattr(om, "KisOrderStatusClient", FakeClient)

    audit = manage_submitted_order_ledger(
        ledger_path=ledger_path,
        env={
            "BROKER_PROVIDER": "kis",
            "KIS_APP_KEY": "app",
            "KIS_APP_SECRET": "sec",
            "KIS_CANO": "12345678",
            "KIS_ACNT_PRDT_CD": "01",
        },
        now=datetime(2026, 7, 7, 1, 5, tzinfo=timezone.utc),
    )

    assert audit["status"] == "OK"
    assert audit["updated_count"] == 1
    assert LiveOrderLedger(ledger_path).has_live_submission(key) is False


def test_manage_submitted_order_ledger_cancels_stale_unfilled_when_enabled(tmp_path, monkeypatch):
    from toss_alpha.execution import order_management as om

    ledger_path = tmp_path / "ledger.jsonl"
    key = "2026-07-07:ttak_absolute_return_loop:307930:BUY"
    submitted_at = datetime(2026, 7, 7, 1, 0, tzinfo=timezone.utc)
    LiveOrderLedger(ledger_path).append(
        {
            "ledger_key": key,
            "status": "SUBMITTED",
            "timestamp": submitted_at.isoformat(),
            "result": {"json": {"output": {"ODNO": "0001", "KRX_FWDG_ORD_ORGNO": "03420"}}},
        }
    )

    inquiries = []

    class FakeClient:
        def __init__(self, config):
            self.config = config

        def inquire_daily_fills(self, *, order_no, order_orgno, day):
            inquiries.append(day)
            remaining = "9" if len(inquiries) == 1 else "0"
            filled = "0" if len(inquiries) == 1 else "4"
            return {"json": {"output1": [{"odno": order_no, "ord_qty": "9", "tot_ccld_qty": filled, "rmn_qty": remaining}]}}

        def cancel_order(self, *, order_no, order_orgno, quantity="0"):
            return {"ok": True, "json": {"rt_cd": "0", "msg1": "취소 주문 완료"}, "payload": {"CANO": "12345678", "ACNT_PRDT_CD": "01"}}

    monkeypatch.setattr(om, "KisOrderStatusClient", FakeClient)

    audit = manage_submitted_order_ledger(
        ledger_path=ledger_path,
        env={
            "BROKER_PROVIDER": "kis",
            "KIS_APP_KEY": "app",
            "KIS_APP_SECRET": "sec",
            "KIS_CANO": "12345678",
            "KIS_ACNT_PRDT_CD": "01",
            "TOSS_CANCEL_STALE_UNFILLED_ENABLED": "true",
            "TOSS_UNFILLED_CANCEL_AFTER_MINUTES": "30",
        },
        now=submitted_at + timedelta(minutes=45),
    )

    assert audit["cancel_attempted_count"] == 1
    assert audit["reprice_remaining_by_key"] == {}
    assert LiveOrderLedger(ledger_path).has_live_submission(key) is True

    confirmed = manage_submitted_order_ledger(
        ledger_path=ledger_path,
        env={
            "BROKER_PROVIDER": "kis",
            "KIS_APP_KEY": "app",
            "KIS_APP_SECRET": "sec",
            "KIS_CANO": "12345678",
            "KIS_ACNT_PRDT_CD": "01",
            "TOSS_CANCEL_STALE_UNFILLED_ENABLED": "true",
            "TOSS_UNFILLED_CANCEL_AFTER_MINUTES": "30",
        },
        now=submitted_at + timedelta(minutes=46),
    )

    assert confirmed["cancel_attempted_count"] == 0
    assert confirmed["reprice_remaining_by_key"][key] == 5
    assert LiveOrderLedger(ledger_path).has_live_submission(key) is False
    assert inquiries == [submitted_at, submitted_at]


def test_manage_submitted_order_ledger_keeps_duplicate_block_when_cancel_rejected(tmp_path, monkeypatch):
    from toss_alpha.execution import order_management as om

    ledger_path = tmp_path / "ledger.jsonl"
    key = "2026-07-07:ttak_absolute_return_loop:307930:BUY"
    submitted_at = datetime(2026, 7, 7, 1, 0, tzinfo=timezone.utc)
    LiveOrderLedger(ledger_path).append(
        {
            "ledger_key": key,
            "status": "SUBMITTED",
            "timestamp": submitted_at.isoformat(),
            "result": {"json": {"output": {"ODNO": "0001", "KRX_FWDG_ORD_ORGNO": "03420"}}},
        }
    )

    class FakeClient:
        def __init__(self, config):
            self.config = config

        def inquire_daily_fills(self, *, order_no, order_orgno, day):
            return {"json": {"output1": [{"odno": order_no, "ord_qty": "9", "tot_ccld_qty": "0", "rmn_qty": "9"}]}}

        def cancel_order(self, *, order_no, order_orgno, quantity="0"):
            return {"ok": True, "json": {"rt_cd": "7", "msg1": "취소 거절"}, "payload": {"CANO": "12345678", "ACNT_PRDT_CD": "01"}}

    monkeypatch.setattr(om, "KisOrderStatusClient", FakeClient)

    audit = manage_submitted_order_ledger(
        ledger_path=ledger_path,
        env={
            "BROKER_PROVIDER": "kis",
            "KIS_APP_KEY": "app",
            "KIS_APP_SECRET": "sec",
            "KIS_CANO": "12345678",
            "KIS_ACNT_PRDT_CD": "01",
            "TOSS_CANCEL_STALE_UNFILLED_ENABLED": "true",
            "TOSS_UNFILLED_CANCEL_AFTER_MINUTES": "30",
        },
        now=submitted_at + timedelta(minutes=45),
    )

    assert audit["cancel_attempted_count"] == 1
    assert LiveOrderLedger(ledger_path).has_live_submission(key) is True


def test_manage_submitted_order_ledger_cancels_buy_removed_from_current_signal(tmp_path, monkeypatch):
    from toss_alpha.execution import order_management as om

    ledger_path = tmp_path / "ledger.jsonl"
    key = "2026-07-07:ttak_absolute_return_loop:307930:BUY"
    submitted_at = datetime(2026, 7, 7, 1, 0, tzinfo=timezone.utc)
    LiveOrderLedger(ledger_path).append({
        "ledger_key": key,
        "status": "SUBMITTED",
        "timestamp": submitted_at.isoformat(),
        "result": {"json": {"output": {"ODNO": "0001", "KRX_FWDG_ORD_ORGNO": "03420"}}},
    })

    class FakeClient:
        def __init__(self, config):
            self.config = config

        def inquire_daily_fills(self, *, order_no, order_orgno, day):
            return {"json": {"output1": [{"odno": order_no, "ord_qty": "9", "tot_ccld_qty": "0", "rmn_qty": "9"}]}}

        def cancel_order(self, *, order_no, order_orgno, quantity="0"):
            return {"ok": True, "json": {"rt_cd": "0"}, "payload": {"CANO": "12345678", "ACNT_PRDT_CD": "01"}}

    monkeypatch.setattr(om, "KisOrderStatusClient", FakeClient)
    audit = manage_submitted_order_ledger(
        ledger_path=ledger_path,
        env={
            "BROKER_PROVIDER": "kis", "KIS_APP_KEY": "app", "KIS_APP_SECRET": "sec",
            "KIS_CANO": "12345678", "KIS_ACNT_PRDT_CD": "01",
            "TOSS_CANCEL_INVALIDATED_ORDERS_ENABLED": "true",
        },
        desired_order_keys=set(),
        now=submitted_at + timedelta(minutes=1),
    )

    latest = LiveOrderLedger(ledger_path).records()[-1]
    assert audit["cancel_attempted_count"] == 1
    assert audit["invalidated_cancel_attempted_count"] == 1
    assert latest["status"] == "CANCEL_REQUESTED"
    assert latest["cancel_reason"] == "current_buy_signal_removed"


def test_invalidated_buy_cancel_confirmation_never_reprices(tmp_path, monkeypatch):
    from toss_alpha.execution import order_management as om

    ledger_path = tmp_path / "ledger.jsonl"
    key = "2026-07-07:ttak_absolute_return_loop:307930:BUY"
    submitted_at = datetime(2026, 7, 7, 1, 0, tzinfo=timezone.utc)
    LiveOrderLedger(ledger_path).append({
        "ledger_key": key, "status": "CANCEL_REQUESTED",
        "timestamp": (submitted_at + timedelta(minutes=1)).isoformat(),
        "first_submitted_at": submitted_at.isoformat(), "order_no": "0001",
        "order_orgno": "03420", "replacement_qty": 9,
        "cancel_reason": "current_buy_signal_removed",
    })

    class FakeClient:
        def __init__(self, config):
            self.config = config

        def inquire_daily_fills(self, *, order_no, order_orgno, day):
            return {"json": {"output1": [{"odno": order_no, "ord_qty": "9", "tot_ccld_qty": "0", "rmn_qty": "0"}]}}

    monkeypatch.setattr(om, "KisOrderStatusClient", FakeClient)
    audit = manage_submitted_order_ledger(
        ledger_path=ledger_path,
        env={
            "BROKER_PROVIDER": "kis", "KIS_APP_KEY": "app", "KIS_APP_SECRET": "sec",
            "KIS_CANO": "12345678", "KIS_ACNT_PRDT_CD": "01",
            "TOSS_CANCEL_INVALIDATED_ORDERS_ENABLED": "true",
        },
        desired_order_keys=set(),
        now=submitted_at + timedelta(minutes=2),
    )

    assert audit["reprice_remaining_by_key"] == {}
    assert LiveOrderLedger(ledger_path).has_live_submission(key) is False


def test_removed_sell_signal_is_not_auto_canceled(tmp_path, monkeypatch):
    from toss_alpha.execution import order_management as om

    ledger_path = tmp_path / "ledger.jsonl"
    key = "2026-07-07:ttak_absolute_return_loop:307930:SELL"
    submitted_at = datetime(2026, 7, 7, 1, 0, tzinfo=timezone.utc)
    LiveOrderLedger(ledger_path).append({
        "ledger_key": key, "status": "SUBMITTED", "timestamp": submitted_at.isoformat(),
        "result": {"json": {"output": {"ODNO": "0001", "KRX_FWDG_ORD_ORGNO": "03420"}}},
    })

    class FakeClient:
        def __init__(self, config):
            self.config = config

        def inquire_daily_fills(self, *, order_no, order_orgno, day):
            return {"json": {"output1": [{"odno": order_no, "ord_qty": "9", "tot_ccld_qty": "0", "rmn_qty": "9"}]}}

        def cancel_order(self, **kwargs):
            raise AssertionError("risk-reducing SELL must not be canceled because another loop omitted it")

    monkeypatch.setattr(om, "KisOrderStatusClient", FakeClient)
    audit = manage_submitted_order_ledger(
        ledger_path=ledger_path,
        env={
            "BROKER_PROVIDER": "kis", "KIS_APP_KEY": "app", "KIS_APP_SECRET": "sec",
            "KIS_CANO": "12345678", "KIS_ACNT_PRDT_CD": "01",
            "TOSS_CANCEL_INVALIDATED_ORDERS_ENABLED": "true",
        },
        desired_order_keys=set(),
        now=submitted_at + timedelta(minutes=1),
    )

    assert audit["cancel_attempted_count"] == 0
    assert LiveOrderLedger(ledger_path).has_live_submission(key) is True