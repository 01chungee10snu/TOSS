def test_evaluate_disclosure_gate_skips_when_no_symbols():
    from toss_alpha.execution.qual_gate import evaluate_disclosure_gate

    result = evaluate_disclosure_gate(symbols=[], api_key_present=False)

    assert result["status"] == "SKIPPED_NO_CANDIDATES"
    assert result["reasons"] == ["no_candidate_symbols"]
    assert result["checked_symbols"] == []


def test_evaluate_disclosure_gate_blocks_when_symbols_but_no_api_key():
    from toss_alpha.execution.qual_gate import evaluate_disclosure_gate

    result = evaluate_disclosure_gate(symbols=["005930", "000660"], api_key_present=False)

    assert result["status"] == "BLOCKED_QUAL_DATA"
    assert result["reasons"] == ["missing_opendart_api_key"]
    assert result["pending_symbols"] == ["005930", "000660"]


def test_evaluate_disclosure_gate_marks_review_required_symbols():
    from toss_alpha.execution.qual_gate import evaluate_disclosure_gate

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


def test_evaluate_disclosure_gate_blocks_on_fetch_error():
    from toss_alpha.execution.qual_gate import evaluate_disclosure_gate

    def fake_fetch(symbol: str):
        raise RuntimeError(f"boom:{symbol}")

    result = evaluate_disclosure_gate(
        symbols=["005930"],
        api_key_present=True,
        fetch_recent_filings=fake_fetch,
    )

    assert result["status"] == "BLOCKED_QUAL_DATA"
    assert result["reasons"] == ["disclosure_fetch_failed"]
    assert "005930" in result["fetch_errors"]
