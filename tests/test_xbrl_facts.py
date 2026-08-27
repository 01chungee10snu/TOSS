from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from toss_alpha.research.xbrl_facts import parse_xbrl_archive, read_numeric_facts_from_archive


NS = (
    'xmlns:xbrli="http://www.xbrl.org/2003/instance" '
    'xmlns:xbrldi="http://xbrl.org/2006/xbrldi" '
    'xmlns:ifrs-full="http://xbrl.ifrs.org/taxonomy/2025-03-27/ifrs-full" '
    'xmlns:dart="http://dart.fss.or.kr/xbrl/taxonomy/2025-03-31/dart" '
    'xmlns:iso4217="http://www.xbrl.org/2003/iso4217"'
)


def _instance(*, equity=1_000_000, parent_equity=None, revenue=300_000, cumulative_revenue=None, shares=100_000, issued_only=False, forecast_revenue=None):
    parent = "" if parent_equity is None else f'<ifrs-full:EquityAttributableToOwnersOfParent contextRef="i" unitRef="krw">{parent_equity}</ifrs-full:EquityAttributableToOwnersOfParent>'
    share_fact = (
        f'<dart:NumberOfIssuedShares contextRef="i" unitRef="shares">{shares}</dart:NumberOfIssuedShares>'
        if issued_only
        else f'<dart:NumberOfSharesOutstanding contextRef="i" unitRef="shares">{shares}</dart:NumberOfSharesOutstanding>'
    )
    cumulative = "" if cumulative_revenue is None else f'<ifrs-full:Revenue contextRef="cum" unitRef="krw">{cumulative_revenue}</ifrs-full:Revenue>'
    forecast = "" if forecast_revenue is None else f'<ifrs-full:Revenue contextRef="forecast" unitRef="krw">{forecast_revenue}</ifrs-full:Revenue>'
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl {NS}>
  <xbrli:unit id="krw"><xbrli:measure>iso4217:KRW</xbrli:measure></xbrli:unit>
  <xbrli:unit id="shares"><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unit>
  <xbrli:context id="i"><xbrli:entity><xbrli:identifier scheme="dart">001</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2025-09-30</xbrli:instant></xbrli:period></xbrli:context>
  <xbrli:context id="q"><xbrli:entity><xbrli:identifier scheme="dart">001</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2025-07-01</xbrli:startDate><xbrli:endDate>2025-09-30</xbrli:endDate></xbrli:period></xbrli:context>
  <xbrli:context id="cum"><xbrli:entity><xbrli:identifier scheme="dart">001</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-09-30</xbrli:endDate></xbrli:period></xbrli:context>
  <xbrli:context id="forecast"><xbrli:entity><xbrli:identifier scheme="dart">001</xbrli:identifier><xbrli:segment><xbrldi:explicitMember dimension="dart:StatementScenarioAxis">dart:ScenarioForecastMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:startDate>2025-07-01</xbrli:startDate><xbrli:endDate>2025-09-30</xbrli:endDate></xbrli:period></xbrli:context>
  <ifrs-full:Assets contextRef="i" unitRef="krw">2000000</ifrs-full:Assets>
  <ifrs-full:Equity contextRef="i" unitRef="krw">{equity}</ifrs-full:Equity>
  {parent}
  <ifrs-full:Revenue contextRef="q" unitRef="krw">{revenue}</ifrs-full:Revenue>
  {cumulative}
  {forecast}
  {share_fact}
</xbrli:xbrl>""".encode()


def _zip(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    return path


def test_parser_prefers_parent_equity_and_standalone_quarter(tmp_path):
    archive = _zip(
        tmp_path / "x.zip",
        {"consolidated.xbrl": _instance(parent_equity=900_000, cumulative_revenue=850_000)},
    )
    parsed = parse_xbrl_archive(archive, period_end="2025-09-30", reprt_code="11014")

    assert parsed.assets.value == 2_000_000
    assert parsed.book_equity.value == 900_000
    assert parsed.book_equity.concept == "EquityAttributableToOwnersOfParent"
    assert parsed.revenue.value == 300_000
    assert parsed.revenue.duration_days == 92
    assert parsed.revenue_basis == "quarter"
    assert parsed.shares_outstanding.value == 100_000
    assert parsed.bps == 9.0
    assert parsed.ready_for_hml_cma is True


def test_parser_prefers_consolidated_instance_over_separate_when_other_scores_match(tmp_path):
    archive = _zip(
        tmp_path / "x.zip",
        {
            "company_separate.xbrl": _instance(equity=800_000),
            "company_consolidated.xbrl": _instance(equity=1_000_000),
        },
    )
    parsed = parse_xbrl_archive(archive, period_end="2025-09-30", reprt_code="11014")
    assert parsed.book_equity.value == 1_000_000
    assert parsed.book_equity.instance_path == "company_consolidated.xbrl"


def test_forecast_dimension_is_excluded(tmp_path):
    archive = _zip(
        tmp_path / "x.zip",
        {"consolidated.xbrl": _instance(revenue=300_000, forecast_revenue=999_000)},
    )
    parsed = parse_xbrl_archive(archive, period_end="2025-09-30", reprt_code="11014")
    assert parsed.revenue.value == 300_000


def test_conflicting_equal_priority_facts_fail_closed_as_ambiguous(tmp_path):
    payload = _instance().replace(
        b'<ifrs-full:Equity contextRef="i" unitRef="krw">1000000</ifrs-full:Equity>',
        b'<ifrs-full:Equity contextRef="i" unitRef="krw">1000000</ifrs-full:Equity><ifrs-full:Equity contextRef="i" unitRef="krw">1100000</ifrs-full:Equity>',
    )
    archive = _zip(tmp_path / "x.zip", {"consolidated.xbrl": payload})
    parsed = parse_xbrl_archive(archive, period_end="2025-09-30", reprt_code="11014")
    assert parsed.book_equity.status == "AMBIGUOUS"
    assert parsed.book_equity.value is None
    assert parsed.bps is None
    assert parsed.ready_for_hml_cma is False


def test_issued_shares_are_not_used_as_outstanding_share_fallback(tmp_path):
    archive = _zip(tmp_path / "x.zip", {"consolidated.xbrl": _instance(issued_only=True)})
    parsed = parse_xbrl_archive(archive, period_end="2025-09-30", reprt_code="11014")
    assert parsed.shares_outstanding.status == "MISSING"
    assert parsed.bps is None
    assert parsed.ready_for_hml_cma is False


def test_non_xbrl_xml_is_ignored_and_scale_is_applied(tmp_path):
    instance = _instance().replace(b">2000000</ifrs-full:Assets>", b' scale="3">2000</ifrs-full:Assets>')
    archive = _zip(tmp_path / "x.zip", {"schema.xml": b"<schema/>", "data.xbrl": instance})
    facts, count = read_numeric_facts_from_archive(archive)
    assets = [fact for fact in facts if fact.concept == "Assets"]
    assert count == 1
    assert assets[0].value == 2_000_000


def test_invalid_or_missing_archive_fails_explicitly(tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_xbrl_archive(tmp_path / "missing.zip", period_end="2025-09-30", reprt_code="11014")
    bad = tmp_path / "bad.zip"
    bad.write_text("not a zip", encoding="utf-8")
    with pytest.raises(ValueError, match="not an XBRL zip"):
        parse_xbrl_archive(bad, period_end="2025-09-30", reprt_code="11014")
