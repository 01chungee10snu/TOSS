from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from toss_alpha.data.schema import Quote
from toss_alpha.execution.inverse_sleeve import InverseSleeveSettings, build_inverse_order, maybe_apply_inverse_sleeve
from toss_alpha.execution.live_ready import LiveExecutionConfig
from toss_alpha.execution.live_submit import adapt_buy_order_to_live_quote


def test_build_inverse_order_uses_configured_etf_and_budget():
    settings = InverseSleeveSettings(
        enabled=True,
        etf_code="252670",
        etf_name="KODEX 200선물인버스2X",
        yf_ticker="252670.KS",
        notional_krw=50_000,
        buy_aggressiveness_pct=0.005,
        spread_pct_proxy=0.001,
        trigger_situations=frozenset({"down_high_vol"}),
    )

    order = build_inverse_order(settings, {"close": 84, "volume": 10_000_000, "price_date": "2026-07-02"})

    assert order["symbol"] == "252670"
    assert order["side"] == "BUY"
    assert order["order_type"] == "LIMIT"
    assert order["limit_price"] == 85
    assert order["quantity"] == 588
    assert order["notional_krw"] == 49_980
    assert order["current_price"] == 84
    assert order["dollar_volume"] == 840_000_000
    assert order["spread_pct"] == 0.001


def test_maybe_apply_inverse_sleeve_replaces_bad_regime_payload(tmp_path: Path):
    payload = {
        "status": "CANDIDATES",
        "as_of": "2026-07-03",
        "policy_id": "contextual_mon_fri_policy_seed20260607_aggressive_small_account",
        "situation": "down_high_vol",
        "intraday_decision": {"verdict": "INVERSE_BUY", "evidence_status": "FRESH"},
        "orders": [{"symbol": "307930", "side": "BUY"}],
    }

    transformed, audit = maybe_apply_inverse_sleeve(
        payload,
        out_dir=tmp_path,
        env={"TOSS_INVERSE_SLEEVE_ENABLED": "true", "TOSS_INVERSE_SLEEVE_NOTIONAL_KRW": "50000", "TOSS_LEVERAGED_ETP_EDUCATION_APPROVED": "true"},
        price_provider=lambda ticker, as_of: {"ticker": ticker, "price_date": "2026-07-02", "close": 84, "volume": 10_000_000},
        original_candidate_json="original.json",
    )

    assert audit["applied"] is True
    assert transformed["strategy_type"] == "inverse_sleeve"
    assert transformed["policy_id"] == "inverse_sleeve_risk_off_v1"
    assert transformed["situation"] == "inverse_sleeve_risk_off"
    assert transformed["source_situation"] == "down_high_vol"
    assert transformed["orders"][0]["symbol"] == "114800"
    assert Path(audit["candidate_json"]).exists()


def test_maybe_apply_inverse_sleeve_prefers_same_tick_kis_quote(tmp_path: Path):
    payload = {
        "status": "CANDIDATES",
        "as_of": "2026-07-10",
        "policy_id": "contextual_mon_fri_policy_seed20260607_aggressive_small_account",
        "situation": "down_high_vol",
        "intraday_decision": {"verdict": "INVERSE_BUY", "evidence_status": "FRESH"},
        "orders": [],
    }

    transformed, audit = maybe_apply_inverse_sleeve(
        payload,
        out_dir=tmp_path,
        env={"TOSS_INVERSE_SLEEVE_ENABLED": "true", "TOSS_INVERSE_SLEEVE_NOTIONAL_KRW": "50000"},
        realtime_quote={
            "close": 1117,
            "volume": 12_000_000,
            "price_date": "2026-07-13",
            "observed_at": "2026-07-13T06:48:30+00:00",
            "source": "kis_realtime_quote",
        },
        price_provider=lambda *_: (_ for _ in ()).throw(AssertionError("stale daily quote must not run")),
    )

    order = transformed["orders"][0]
    assert order["current_price"] == 1117
    assert order["limit_price"] == 1123
    assert order["quantity"] == 44
    assert order["quote_source"] == "kis_realtime_quote"
    assert order["quote_price_date"] == "2026-07-13"
    assert order["quote_observed_at"] == "2026-07-13T06:48:30+00:00"
    assert audit["quote_source"] == "kis_realtime_quote"


def test_same_tick_inverse_candidate_survives_live_reprice_chase_cap():
    settings = InverseSleeveSettings(enabled=True, notional_krw=50_000)
    order = build_inverse_order(
        settings,
        {
            "close": 1_117,
            "volume": 12_000_000,
            "price_date": "2026-07-13",
            "observed_at": "2026-07-13T06:48:30+00:00",
            "source": "kis_realtime_quote",
        },
    )

    class SameTickQuoteClient:
        def quote_snapshot(self, symbol):
            return Quote(
                symbol=symbol,
                timestamp=datetime(2026, 7, 13, 6, 48, 30, tzinfo=timezone.utc),
                last=1_117,
                bid=1_116,
                ask=1_118,
                volume=12_000_000,
                source="kis",
            )

    adapted, adaptive_audit = adapt_buy_order_to_live_quote(
        order,
        config=LiveExecutionConfig.from_env(
            {"BROKER_PROVIDER": "kis", "KIS_APP_KEY": "app", "KIS_APP_SECRET": "sec", "KIS_CANO": "12345678"}
        ),
        env={"TOSS_ADAPTIVE_LIMIT_MAX_CHASE_PCT": "0.02", "TOSS_MAX_LIVE_SPREAD_PCT": "0.003"},
        quote_client=SameTickQuoteClient(),
    )

    assert adaptive_audit["status"] == "ADAPTED"
    assert "violation" not in adaptive_audit
    assert adapted["limit_price"] == 1_118
    assert adapted["quantity"] == 44
    assert adapted["notional_krw"] <= order["notional_krw"]


def test_maybe_apply_inverse_sleeve_requires_fresh_integrated_decision(tmp_path: Path):
    payload = {
        "status": "CANDIDATES",
        "as_of": "2026-07-03",
        "situation": "down_high_vol",
        "orders": [{"symbol": "307930", "side": "BUY"}],
    }

    transformed, audit = maybe_apply_inverse_sleeve(
        payload,
        out_dir=tmp_path,
        env={"TOSS_INVERSE_SLEEVE_ENABLED": "true"},
        price_provider=lambda *_: (_ for _ in ()).throw(AssertionError("quote must not run")),
    )

    assert transformed["status"] == "NO_TRADE"
    assert transformed["orders"] == []
    assert audit["reason"] == "inverse_sleeve_blocked:intraday_decision:missing"


def test_maybe_apply_inverse_sleeve_is_noop_when_disabled(tmp_path: Path):
    payload = {"status": "NO_TRADE", "as_of": "2026-07-03", "reason": "situation_not_approved:down_high_vol", "orders": []}

    transformed, audit = maybe_apply_inverse_sleeve(payload, out_dir=tmp_path, env={})

    assert transformed is payload
    assert audit["applied"] is False
    assert audit["reason"] == "inverse_sleeve_disabled"


def test_maybe_apply_inverse_sleeve_blocks_leveraged_etf_without_education(tmp_path: Path):
    payload = {
        "status": "CANDIDATES",
        "as_of": "2026-07-03",
        "policy_id": "contextual_mon_fri_policy_seed20260607_aggressive_small_account",
        "situation": "down_high_vol",
        "orders": [{"symbol": "307930", "side": "BUY"}],
    }

    transformed, audit = maybe_apply_inverse_sleeve(
        payload,
        out_dir=tmp_path,
        env={"TOSS_INVERSE_SLEEVE_ENABLED": "true", "TOSS_INVERSE_SLEEVE_NOTIONAL_KRW": "50000", "TOSS_INVERSE_ETF_CODE": "252670"},
        price_provider=lambda ticker, as_of: (_ for _ in ()).throw(AssertionError("price lookup should not run")),
        original_candidate_json="original.json",
    )

    assert audit["applied"] is False
    assert audit["reason"] == "inverse_sleeve_blocked:leveraged_etp_education_not_approved:252670"
    assert transformed["status"] == "NO_TRADE"
    assert transformed["orders"] == []
    assert transformed["reason"] == audit["reason"]
