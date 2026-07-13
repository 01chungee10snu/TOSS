from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "run_ttak_autotrading_loop.py"


def _load_loop_module():
    spec = importlib.util.spec_from_file_location("run_ttak_autotrading_loop", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_resolve_policy_json_prefers_walkforward_promoted(tmp_path):
    module = _load_loop_module()
    promoted = tmp_path / "promoted.json"
    fallback = tmp_path / "fallback.json"
    promoted.write_text("{}", encoding="utf-8")
    fallback.write_text("{}", encoding="utf-8")

    chosen = module.resolve_policy_json([promoted, fallback])

    assert chosen == promoted


def test_load_fast_veto_thresholds_reads_overlay(tmp_path):
    module = _load_loop_module()
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps(
            {
                "policy_id": "demo",
                "fast_veto_overlay": {
                    "variant_id": "veto_higher_liquidity",
                    "thresholds": {
                        "max_gap_pct": 0.1,
                        "max_intraday_range_pct": 0.2,
                        "min_dollar_volume_krw": 1_000_000_000.0,
                        "max_prev_volatility_20d": 0.12,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    thresholds = module.load_fast_veto_thresholds(policy)

    assert thresholds == {
        "max_gap_pct": 0.1,
        "max_intraday_range_pct": 0.2,
        "min_dollar_volume_krw": 1_000_000_000.0,
        "max_prev_volatility_20d": 0.12,
    }


def test_fast_phase_applies_policy_overlay_thresholds(tmp_path):
    module = _load_loop_module()
    panel = pd.DataFrame(
        [
            {"Date": "2025-12-29", "code": "111111", "Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 20_000_000},
            {"Date": "2025-12-30", "code": "111111", "Open": 109, "High": 110, "Low": 108, "Close": 109, "Volume": 20_000_000},
        ]
    )
    panel_path = tmp_path / "panel.csv"
    panel.to_csv(panel_path, index=False)
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "policy_id": "demo",
                "fast_veto_overlay": {
                    "thresholds": {
                        "max_gap_pct": 0.1,
                        "max_intraday_range_pct": 0.2,
                        "min_dollar_volume_krw": 1_000_000_000.0,
                        "max_prev_volatility_20d": 0.12,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    module.PANEL_CSV = panel_path
    module.POLICY_JSON = policy_path
    result = module.fast_phase(
        {
            "status": "CANDIDATES",
            "as_of": "2025-12-30",
            "orders": [{"symbol": "111111", "side": "BUY"}],
        }
    )

    assert result["status"] == "READY"
    assert result["allowed_count"] == 1
    assert result["thresholds"]["max_gap_pct"] == 0.1


def test_fast_phase_bypasses_universe_panel_for_inverse_sleeve(tmp_path):
    module = _load_loop_module()
    module.PANEL_CSV = tmp_path / "missing_panel.csv"
    module.POLICY_JSON = tmp_path / "missing_policy.json"

    result = module.fast_phase(
        {
            "status": "CANDIDATES",
            "as_of": "2026-07-03",
            "strategy_type": "inverse_sleeve",
            "intraday_decision": {"evidence_status": "FRESH", "news_evidence_status": "FRESH", "verdict": "INVERSE_BUY", "signal_conflict": False},
            "orders": [{"symbol": "252670", "side": "BUY", "quote_source": "kis_realtime_quote", "current_price": 1117, "dollar_volume_krw": 13_404_000_000}],
        }
    )

    assert result["status"] == "READY"
    assert result["allowed_count"] == 1
    assert result["allowed_orders"][0]["symbol"] == "252670"
    assert result["bypass_reason"] == "inverse_sleeve_uses_realtime_etf_gate_not_universe_panel"


def test_qual_phase_bypasses_single_stock_dart_for_inverse_sleeve():
    module = _load_loop_module()

    result = module.qual_phase(
        {
            "status": "CANDIDATES",
            "as_of": "2026-07-03",
            "strategy_type": "inverse_sleeve",
            "intraday_decision": {"evidence_status": "FRESH", "news_evidence_status": "FRESH", "verdict": "INVERSE_BUY", "signal_conflict": False},
            "orders": [{"symbol": "252670", "side": "BUY", "quote_source": "kis_realtime_quote", "current_price": 1117, "dollar_volume_krw": 13_404_000_000}],
        }
    )

    assert result["status"] == "READY"
    assert result["checked_symbols"] == ["252670"]
    assert result["sources"]["inverse_sleeve"]["reason"] == "etf_sleeve_bypasses_single_stock_dart_only_after_fresh_intraday_news_gate"


def test_inverse_fast_and_qual_fail_closed_without_realtime_evidence(tmp_path):
    module = _load_loop_module()
    module.PANEL_CSV = tmp_path / "missing_panel.csv"
    payload = {
        "status": "CANDIDATES", "strategy_type": "inverse_sleeve",
        "orders": [{"symbol": "252670", "side": "BUY"}],
    }

    fast = module.fast_phase(payload)
    qual = module.qual_phase(payload)

    assert fast["status"] == "BLOCKED_FAST_VETO_DATA"
    assert "inverse_quote_source_not_kis_realtime" in fast["reasons"]
    assert qual["status"] == "BLOCKED_QUAL_DATA"
    assert qual["blocked_symbols"] == ["252670"]


def test_intraday_phase_kis_collection_success_and_failure_fail_closed(tmp_path, monkeypatch):
    module = _load_loop_module()
    from toss_alpha.execution import live_ready
    from toss_alpha.connectors import kis_readonly

    now = datetime.now(timezone.utc)
    monkeypatch.setattr(module, "REPORT_DIR", tmp_path)
    issue = tmp_path / "current_issues" / f"current_issue_risk_report_{now.astimezone(module.ZoneInfo('Asia/Seoul')).strftime('%Y%m%d')}.json"
    issue.parent.mkdir(parents=True)
    issue.write_text(json.dumps({"severity": "high", "generated_at_utc": now.isoformat()}), encoding="utf-8")
    config = SimpleNamespace(
        provider="kis", app_key="app", app_secret="secret", cano="12345678",
        account_product_code="01", kis_mock_trading=False, base_url="https://example.invalid", timeout=1,
    )
    monkeypatch.setattr(live_ready.LiveExecutionConfig, "from_env", staticmethod(lambda _env: config))

    class FakeClient:
        fail = False

        def __init__(self, **_kwargs):
            pass

        def position_snapshots(self):
            return []

        def quote(self, symbol):
            if self.fail:
                raise RuntimeError("quote unavailable")
            values = {
                "069500": {"stck_prpr": "9850", "stck_oprc": "9950", "stck_sdpr": "10000", "acml_vol": "1000000"},
                "114800": {"stck_prpr": "10120", "stck_oprc": "10050", "stck_sdpr": "10000", "acml_vol": "1200000"},
            }
            return {"json": {"output": values[str(symbol).zfill(6)]}}

    monkeypatch.setattr(kis_readonly, "KisReadOnlyClient", FakeClient)
    monkeypatch.setenv("TOSS_INVERSE_SLEEVE_ENABLED", "true")
    monkeypatch.setenv("TOSS_INVERSE_ETF_CODE", "114800")
    candidate = {"status": "CANDIDATES", "situation": "down_high_vol", "source_situation": "down_high_vol", "orders": [{"symbol": "005930", "side": "BUY", "quantity": 1}]}

    filtered, audit = module.intraday_phase(candidate, now=now)
    decision = audit["decision"]
    assert decision["verdict"] == "INVERSE_BUY"
    assert filtered["orders"], audit["inverse_sleeve"]
    assert filtered["orders"][0]["quote_source"] == "kis_realtime_quote"
    assert filtered["orders"][0]["current_price"] == 10120.0

    FakeClient.fail = True
    filtered, audit = module.intraday_phase(candidate, now=now)
    decision = audit["decision"]
    assert decision["verdict"] == "NO_TRADE"
    assert decision["reason"] == "intraday_collection_failed"
    assert filtered["orders"] == []


def test_choose_overall_prioritizes_submit_block_over_actionable_candidates():
    module = _load_loop_module()
    result = module.choose_overall(
        {"status": "ACTIONABLE_CANDIDATES"},
        {"status": "READY"},
        {"status": "READY"},
        {"status": "LIVE_READY"},
        {"status": "LIVE_SUBMIT_BLOCKED"},
        {"sell_order_count": 1},
    )
    assert result == "LIVE_SUBMIT_BLOCKED"


def test_choose_overall_prioritizes_unknown_submit_outcome():
    module = _load_loop_module()
    result = module.choose_overall(
        {"status": "ACTIONABLE_CANDIDATES"},
        {"status": "READY"},
        {"status": "READY"},
        {"status": "LIVE_READY"},
        {"status": "LIVE_SUBMIT_UNKNOWN"},
    )
    assert result == "LIVE_SUBMIT_UNKNOWN"


def test_summary_includes_full_position_exit_parity_fields():
    module = _load_loop_module()
    payload = {
        "generated_at_utc": "2026-07-08T00:00:00+00:00",
        "overall_status": "NO_TRADE",
        "quant": {"status": "NO_TRADE", "panel_exists": True, "policy_exists": True, "policy_json": "p", "candidate_json": "c"},
        "fast": {"status": "SKIPPED_NO_CANDIDATES", "policy_json": "p", "thresholds": {}, "reasons": [], "checked_symbols": [], "vetoed_symbols": [], "allowed_count": 0, "original_order_count": 0, "reasons_by_symbol": {}},
        "position_exit": {
            "enabled": True,
            "sell_order_count": 0,
            "stop_loss_pct": 0.10,
            "take_profit_pct": 0.08,
            "trailing_stop_pct": 0.05,
            "max_holding_trading_days": 5,
            "max_positions_limit": 3,
            "equity_guard": {"status": "READY", "threshold_pct": 0.08, "cooldown_seconds": 1036800, "cooldown_unit": "days", "block_new_buys": False, "liquidation_required": False},
        },
        "qual": {"status": "SKIPPED_NO_CANDIDATES", "connector_exists": True, "opendart_api_key_present": False, "require_opendart": False, "news_events_path": "n", "news_events_count": 0, "news_events_error": None, "reasons": [], "checked_symbols": [], "pending_symbols": [], "blocked_symbols": [], "review_required_symbols": [], "event_counts": {}, "sources": {}},
        "live": {"status": "LIVE_BLOCKED", "ready": False, "default_mode": "BLOCK", "dry_run_available": True, "missing": []},
        "live_submit": {"status": "LIVE_SUBMIT_NO_ORDERS", "dry_run": True, "submit_enabled": False, "order_count": 0, "attempted_count": 0, "submitted_count": 0, "blocked_count": 0, "violations": [], "artifact_path": "a", "ledger_path": "l"},
    }

    md = module.summarize(payload)

    assert "- trailing_stop_pct: 0.05" in md
    assert "- max_holding_trading_days: 5" in md
    assert "- max_positions_limit: 3" in md
    assert "- equity_guard_threshold_pct: 0.08" in md
    assert "- equity_guard_cooldown_seconds: 1036800" in md
