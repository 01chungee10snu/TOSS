from __future__ import annotations

import importlib.util
import json
from pathlib import Path

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
