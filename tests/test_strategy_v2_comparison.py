from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "strategy_v2_comparison.py"


def load_module():
    spec = importlib.util.spec_from_file_location("strategy_v2_comparison_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _synthetic_panel(days: int = 26, symbols: int = 20) -> pd.DataFrame:
    dates = pd.date_range("2026-01-02", periods=days, freq="B")
    rows = []
    for day_idx, dt in enumerate(dates):
        for symbol_idx in range(symbols):
            # Earlier dates are much more negative than the final date. A
            # whole-sample 5% quantile therefore sits near -50%, which would
            # incorrectly reject the final day's genuine -10% cross-sectional
            # oversold name. A per-date quantile must still select it.
            if day_idx < days - 1:
                mom = -0.50 if symbol_idx == 0 else -0.40 + symbol_idx * 0.001
            else:
                mom = -0.10 if symbol_idx == 0 else 0.01 + symbol_idx * 0.001
            rows.append(
                {
                    "Date": dt,
                    "code": f"{symbol_idx + 1:06d}",
                    "Close": 100.0,
                    "mom_5d": mom,
                    "dollar_volume": 1_000_000_000.0 + symbol_idx,
                    "rsi_14": 20.0,
                    "vol_20d": 0.02,
                    "fwd_close_1d": 101.0,
                    "fwd_high_1d": 102.0,
                    "fwd_low_1d": 99.0,
                    "mkt_ret": -0.01,
                    "mkt_vol": 0.02,
                    "mkt_mom_5d": -0.03,
                    "bb_lower": 95.0,
                }
            )
    return pd.DataFrame(rows)


def test_oversold_quantile_is_computed_cross_sectionally_per_date():
    m = load_module()
    metrics, trades = m.run_strategy(
        _synthetic_panel(),
        rsi_max=100,
        use_bb=False,
        regime_filter="none",
        hold=1,
        topn=1,
        sl_pct=0,
        label="synthetic",
    )

    assert metrics is not None
    assert len(trades) == 1
    assert trades.iloc[0]["symbol"] == "000001"
    assert trades.iloc[0]["date"] == "2026-02-06"


def test_short_history_returns_no_trade_instead_of_index_error():
    m = load_module()
    metrics, trades = m.run_strategy(
        _synthetic_panel(days=10),
        rsi_max=100,
        use_bb=False,
        regime_filter="none",
        hold=1,
        topn=1,
        sl_pct=0,
        label="short",
    )
    assert metrics is None
    assert trades == []
