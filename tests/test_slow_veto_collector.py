from __future__ import annotations

import json
from pathlib import Path

from toss_alpha.daily.slow_veto import classify_title, collect_slow_veto_events


def test_classify_title_maps_block_keywords_to_block():
    assert classify_title("거래정지 위험 안내") == "block"
    assert classify_title("상장폐지 결정") == "block"
    assert classify_title("회생절차 개시 신청") == "block"
    assert classify_title("자본잠식 공시") == "block"
    assert classify_title("분식회계 의혹") == "block"


def test_classify_title_maps_review_keywords_to_review():
    assert classify_title("전환사채 발행 결정") == "review"
    assert classify_title("유상증자 발행") == "review"
    assert classify_title("최대주주 변경") == "review"
    assert classify_title("제3자배정 유상증자") == "review"


def test_classify_title_returns_info_for_neutral_titles():
    assert classify_title("사업보고서") == "info"
    assert classify_title("정기주주총회 소집공고") == "info"


def test_collect_slow_veto_clear_when_no_api_key_and_no_manual():
    result = collect_slow_veto_events(symbols=["005930"], dart_api_key=None)

    assert result["status"] == "CLEAR"
    assert result["checked_symbols"] == ["005930"]
    assert result["events"] == []
    assert "no_api_key" in result["reasons"]


def test_collect_slow_veto_merges_and_classifies_manual_events(tmp_path: Path):
    manual = tmp_path / "manual.json"
    manual.write_text(
        json.dumps(
            {
                "events": [
                    {"symbol": "005930", "title": "전환사채 발행 결정", "source": "manual"},
                    {"symbol": "000660", "title": "사업보고서 제출"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = collect_slow_veto_events(
        symbols=["005930", "000660"],
        dart_api_key=None,
        manual_events_path=manual,
    )

    by_symbol = {ev["symbol"]: ev for ev in result["events"]}
    assert by_symbol["005930"]["severity"] == "review"
    assert by_symbol["005930"]["source"] == "manual"
    # neutral title stays info and is dropped from veto events
    assert "000660" not in by_symbol
    assert result["status"] == "REVIEW_REQUIRED"


def test_collect_slow_veto_uses_injected_dart_fetcher():
    def fake_fetch(symbol: str):
        return [
            {"report_nm": "전환사채발행결정", "rcept_dt": "20240315"},
            {"report_nm": "감사보고서제출", "rcept_dt": "20240310"},
        ]

    result = collect_slow_veto_events(
        symbols=["005930"],
        dart_api_key="fake-key",
        fetch_disclosures=fake_fetch,
    )

    events = {ev["symbol"]: ev for ev in result["events"]}
    assert "005930" in events
    assert events["005930"]["severity"] == "review"
    assert events["005930"]["source"] == "opendart"
    assert result["status"] == "REVIEW_REQUIRED"
    assert "opendart" in result["sources"]


def test_collect_slow_veto_writes_output_consumable_by_daily_decision(tmp_path: Path):
    manual = tmp_path / "manual.json"
    out = tmp_path / "slow_events.json"
    manual.write_text(
        json.dumps(
            {"events": [{"symbol": "005930", "title": "거래정지 위험", "source": "manual"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = collect_slow_veto_events(
        symbols=["005930"],
        dart_api_key=None,
        manual_events_path=manual,
        output_path=out,
    )

    assert result["status"] == "BLOCK"
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["events"][0]["symbol"] == "005930"
    assert written["events"][0]["severity"] == "block"

    # daily decision's loader must consume this file
    from toss_alpha.daily.decision import _load_slow_veto

    loaded = _load_slow_veto(out, ["005930"])
    assert loaded["status"] == "BLOCK"
    assert "005930" in loaded["events_by_symbol"]
