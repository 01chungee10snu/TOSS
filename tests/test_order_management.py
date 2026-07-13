from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from toss_alpha.execution.live_submit import LiveOrderLedger
from toss_alpha.execution.order_management import (
    extract_kis_order_ids,
    manage_submitted_order_ledger,
    parse_status_payload,
)


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