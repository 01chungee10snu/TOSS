from toss_alpha.execution.qual_gate import evaluate_disclosure_gate, evaluate_multi_source_qual_gate, evaluate_news_event_gate


def test_evaluate_disclosure_gate_skips_when_no_symbols():
    result = evaluate_disclosure_gate(symbols=[], api_key_present=False)

    assert result["status"] == "SKIPPED_NO_CANDIDATES"
    assert result["reasons"] == ["no_candidate_symbols"]
    assert result["checked_symbols"] == []


def test_evaluate_disclosure_gate_skips_missing_opendart_by_default():
    result = evaluate_disclosure_gate(symbols=["005930", "000660"], api_key_present=False)

    assert result["status"] == "SKIPPED_SOURCE_UNAVAILABLE"
    assert result["reasons"] == ["missing_opendart_api_key"]
    assert result["pending_symbols"] == ["005930", "000660"]


def test_evaluate_disclosure_gate_can_require_opendart_when_policy_demands_it():
    result = evaluate_disclosure_gate(symbols=["005930"], api_key_present=False, require_opendart=True)

    assert result["status"] == "BLOCKED_QUAL_DATA"
    assert result["reasons"] == ["missing_opendart_api_key"]


def test_evaluate_disclosure_gate_marks_review_required_symbols():
    fixtures = {
        "005930": [{"title": "사업보고서"}],
        "000660": [],
    }

    def fake_fetch(symbol: str):
        return fixtures[symbol]

    result = evaluate_disclosure_gate(
        symbols=["005930", "000660"],
        api_key_present=True,
        fetch_recent_filings=fake_fetch,
    )

    assert result["status"] == "READY"
    assert result["checked_symbols"] == ["005930", "000660"]
    assert result["event_counts"] == {"005930": 1, "000660": 0}
    assert result["review_required_symbols"] == ["005930"]


def test_evaluate_disclosure_gate_skips_fetch_error_unless_required():
    def fake_fetch(symbol: str):
        raise RuntimeError(f"boom:{symbol}")

    result = evaluate_disclosure_gate(
        symbols=["005930"],
        api_key_present=True,
        fetch_recent_filings=fake_fetch,
    )

    assert result["status"] == "SKIPPED_SOURCE_ERROR"
    assert result["reasons"] == ["disclosure_fetch_failed"]
    assert "005930" in result["fetch_errors"]

    required = evaluate_disclosure_gate(
        symbols=["005930"],
        api_key_present=True,
        fetch_recent_filings=fake_fetch,
        require_opendart=True,
    )
    assert required["status"] == "BLOCKED_QUAL_DATA"


def test_evaluate_news_event_gate_blocks_bad_realtime_keywords():
    result = evaluate_news_event_gate(
        symbols=["005930"],
        events=[{"symbol": "005930", "title": "삼성전자 횡령 의혹 보도", "source": "naver_news"}],
    )

    assert result["status"] == "BLOCKED_NEWS_EVENT"
    assert result["blocked_symbols"] == ["005930"]
    assert result["events"][0]["matched_keywords"] == ["횡령"]


def test_multi_source_gate_allows_missing_opendart_when_news_is_clear():
    result = evaluate_multi_source_qual_gate(
        symbols=["319400", "306040"],
        opendart_api_key_present=False,
        news_events=[],
    )

    assert result["status"] == "READY"
    assert result["sources"]["opendart"]["status"] == "SKIPPED_SOURCE_UNAVAILABLE"
    assert result["reasons"] == []


def test_multi_source_gate_blocks_news_even_when_opendart_missing():
    result = evaluate_multi_source_qual_gate(
        symbols=["319400"],
        opendart_api_key_present=False,
        news_events=[{"symbol": "319400", "title": "현대무벡스 거래정지 가능성 제기"}],
    )

    assert result["status"] == "BLOCKED_QUAL_EVENT"
    assert result["blocked_symbols"] == ["319400"]
    assert "blocking_news_keywords" in result["reasons"]


def test_multi_source_gate_can_keep_strict_opendart_policy():
    result = evaluate_multi_source_qual_gate(
        symbols=["319400"],
        opendart_api_key_present=False,
        news_events=[],
        require_opendart=True,
    )

    assert result["status"] == "BLOCKED_QUAL_DATA"
    assert "missing_opendart_api_key" in result["reasons"]
