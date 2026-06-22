from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from toss_alpha.daily.features import compute_features


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_SOURCE = ROOT / "scripts" / "generate_contextual_daily_candidates.py"


def _load_candidate_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("generate_contextual_daily_candidates", SCRIPT_SOURCE)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module
FEATURES_SOURCE = ROOT / "src" / "toss_alpha" / "daily" / "features.py"


def test_log_return_uses_transform_not_groupby_apply_future_warning_path():
    source = FEATURES_SOURCE.read_text(encoding="utf-8")

    assert 'grouped["Close"].apply' not in source
    assert 'grouped["Close"].transform' in source


def test_log_return_matches_per_symbol_previous_close():
    panel = pd.DataFrame(
        [
            {"Date": "2026-01-01", "code": "1", "Open": 10, "High": 11, "Low": 9, "Close": 10.0, "Volume": 100},
            {"Date": "2026-01-02", "code": "1", "Open": 12, "High": 13, "Low": 11, "Close": 12.0, "Volume": 100},
            {"Date": "2026-01-01", "code": "2", "Open": 20, "High": 21, "Low": 19, "Close": 20.0, "Volume": 100},
            {"Date": "2026-01-02", "code": "2", "Open": 18, "High": 19, "Low": 17, "Close": 18.0, "Volume": 100},
        ]
    )

    features = compute_features(panel)
    by_code = features.set_index(["code", "Date"])

    assert np.isnan(by_code.loc[("000001", pd.Timestamp("2026-01-01")), "log_ret_1d"])
    assert by_code.loc[("000001", pd.Timestamp("2026-01-02")), "log_ret_1d"] == np.log(12.0 / 10.0)
    assert np.isnan(by_code.loc[("000002", pd.Timestamp("2026-01-01")), "log_ret_1d"])
    assert by_code.loc[("000002", pd.Timestamp("2026-01-02")), "log_ret_1d"] == np.log(18.0 / 20.0)


def test_candidate_dollar_volume_uses_transform_not_groupby_apply_future_warning_path():
    source = SCRIPT_SOURCE.read_text(encoding="utf-8")

    assert "g.apply(lambda x: (x[\"Close\"] * x[\"Volume\"]).shift(1))" not in source
    assert "data.groupby(\"code\")[\"raw_dollar_volume\"].shift(1)" in source


def test_candidate_dollar_volume_matches_previous_symbol_row():
    module = _load_candidate_module()
    rows = []
    for day in range(1, 26):
        date = f"2026-01-{day:02d}"
        rows.append({"Date": date, "code": "1", "Open": 10 + day, "High": 11 + day, "Low": 9 + day, "Close": 10.0 + day, "Volume": 100 + day})
        rows.append({"Date": date, "code": "2", "Open": 20 + day, "High": 21 + day, "Low": 19 + day, "Close": 20.0 + day, "Volume": 300 + day})
    panel = pd.DataFrame(rows)

    features = module.prepare_features(panel)
    by_code = features.set_index(["code", "Date"])

    assert np.isnan(by_code.loc[("1", pd.Timestamp("2026-01-01")), "dollar_volume"])
    assert by_code.loc[("1", pd.Timestamp("2026-01-02")), "dollar_volume"] == 11.0 * 101
    assert np.isnan(by_code.loc[("2", pd.Timestamp("2026-01-01")), "dollar_volume"])
    assert by_code.loc[("2", pd.Timestamp("2026-01-02")), "dollar_volume"] == 21.0 * 301
