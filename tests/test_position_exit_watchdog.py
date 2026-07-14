from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "toss_position_exit_watchdog.py"


def _module():
    spec = importlib.util.spec_from_file_location("toss_position_exit_watchdog", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_quiet_tick_still_runs_canonical_reconciliation(monkeypatch):
    module = _module()
    calls = []
    monkeypatch.delenv("TOSS_CANCEL_SUPERSEDED_SELL_ENABLED", raising=False)
    monkeypatch.setattr(module, "korea_regular_market_violation", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "load_recent_intraday_decision", lambda _now: None)
    monkeypatch.setattr(module, "append_position_exit_orders", lambda candidate, **_kwargs: (candidate, {"reason": "no_positions"}))
    monkeypatch.setattr(module, "live_readiness", lambda: {"ready": True})

    def fake_submit(**kwargs):
        calls.append(kwargs)
        return {"status": "LIVE_SUBMIT_NO_ORDERS", "order_reconcile": {"status": "NO_ACTIVE_ORDERS"}}

    monkeypatch.setattr(module, "run_live_submit_phase", fake_submit)

    assert module.main() == 0
    assert len(calls) == 1
    assert calls[0]["candidate_payload"]["orders"] == []
    assert calls[0]["env"]["TOSS_CANCEL_SUPERSEDED_SELL_ENABLED"] == "true"


def test_quiet_tick_surfaces_tracker_recovery_block(monkeypatch, capsys):
    module = _module()
    monkeypatch.setattr(module, "korea_regular_market_violation", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "load_recent_intraday_decision", lambda _now: None)
    monkeypatch.setattr(
        module,
        "append_position_exit_orders",
        lambda candidate, **_kwargs: (
            candidate,
            {
                "status": "BLOCKED_CORRUPT_POSITION_TRACKER",
                "block_new_buys": True,
                "buy_block_reasons": ["blocked_corrupt_position_tracker"],
            },
        ),
    )
    monkeypatch.setattr(module, "live_readiness", lambda: {"ready": True})
    monkeypatch.setattr(
        module,
        "run_live_submit_phase",
        lambda **_kwargs: {"status": "LIVE_SUBMIT_NO_ORDERS", "order_reconcile": {"status": "NO_ACTIVE_ORDERS"}},
    )

    assert module.main() == 0
    assert "BLOCKED: position exit recovery required" in capsys.readouterr().out
