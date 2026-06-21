"""Tests for the practical universe builder."""
from __future__ import annotations

import pandas as pd
import pytest

from toss_alpha.daily.universe import (
    UniverseConfig,
    build_practical_universe,
    compute_dollar_volume_rank,
    is_tradeable,
)


def _make_listing_row(**kwargs) -> pd.Series:
    defaults = {
        "Code": "005930",
        "Name": "삼성전자",
        "Market": "KOSPI",
        "Dept": "",
        "Sector": "전기전자",
        "Marcap": 400_000_000_000_000,
    }
    defaults.update(kwargs)
    return pd.Series(defaults)


def test_is_tradeable_accepts_normal_kospi_common_stock():
    row = _make_listing_row()
    assert is_tradeable(row)


def test_is_tradeable_rejects_spac():
    row = _make_listing_row(Code="267260", Name="하이골드스팩12호")
    assert not is_tradeable(row)


def test_is_tradeable_rejects_preferred_shares():
    for name in ["삼성전자우", "LG화학우", "현대차2우B", "기아우"]:
        row = _make_listing_row(Name=name)
        assert not is_tradeable(row), f"should reject preferred: {name}"


def test_is_tradeable_rejects_managed_stocks():
    row = _make_listing_row(Dept="관리")
    assert not is_tradeable(row)
    row2 = _make_listing_row(Dept="거래정지")
    assert not is_tradeable(row2)


def test_is_tradeable_rejects_konex():
    row = _make_listing_row(Market="KONEX")
    assert not is_tradeable(row)


def test_is_tradeable_accepts_kosdaq():
    row = _make_listing_row(Market="KOSDAQ")
    assert is_tradeable(row)


def test_build_practical_universe_includes_large_caps():
    """Samsung Electronics and SK Hynix must be included."""
    listing = pd.DataFrame([
        _make_listing_row(Code="005930", Name="삼성전자", Marcap=400_000_000_000_000),
        _make_listing_row(Code="000660", Name="SK하이닉스", Marcap=200_000_000_000_000),
        _make_listing_row(Code="035420", Name="NAVER", Marcap=50_000_000_000_000),
        _make_listing_row(Code="999999", Name="스팩테스트", Marcap=1_000_000_000),
    ])
    universe = build_practical_universe(listing, config=UniverseConfig(size=3, force_include_top_n=2))
    assert "005930" in universe
    assert "000660" in universe
    assert "999999" not in universe  # SPAC excluded


def test_build_practical_universe_respects_sector_cap():
    """No single sector should exceed the cap when enough sectors exist."""
    sectors = ["반도체", "은행", "자동차", "화학"]
    rows = []
    for si, sector in enumerate(sectors):
        for i in range(50):
            rows.append(_make_listing_row(
                Code=f"{si}{i:05d}",
                Name=f"{sector}{i}호",
                Sector=sector,
                Marcap=100_000_000_000 - si * 10_000_000_000 - i * 100_000,
            ))
    listing = pd.DataFrame(rows)
    universe = build_practical_universe(
        listing,
        config=UniverseConfig(size=20, sector_cap_pct=0.25, force_include_top_n=0),
    )
    assert len(universe) == 20
    sector_counts: dict[str, int] = {}
    for code in universe:
        si = int(code[0])
        s = sectors[si]
        sector_counts[s] = sector_counts.get(s, 0) + 1
    # With 4 sectors and 25% cap (5 each), no sector exceeds 5 in pass 1.
    for s, count in sector_counts.items():
        assert count <= 5, f"sector {s} has {count}, exceeds cap of 5"


def test_compute_dollar_volume_rank_orders_correctly():
    panel = pd.DataFrame({
        "code": ["000001", "000002", "000003"] * 40,
        "Date": pd.date_range("2026-01-01", periods=120, freq="B"),
        "Close": [100, 200, 300] * 40,
        "Volume": [1_000_000, 500_000, 100_000] * 40,
    })
    rank = compute_dollar_volume_rank(panel, lookback_days=40)
    assert rank.iloc[0]["code"] == "000001"
    assert rank.iloc[-1]["code"] == "000003"
    assert "avg_dollar_vol" in rank.columns
    assert "liquidity_rank" in rank.columns
