from datetime import datetime, timezone

from scripts.current_issue_risk_report import classify


NOW = datetime(2026, 7, 15, 2, 0, tzinfo=timezone.utc)


def headline(title: str, published: str) -> dict:
    return {"query": "test", "title": title, "published": published, "link": "https://example.test"}


def test_default_window_excludes_yesterdays_old_geopolitical_story():
    result = classify([
        headline("호르무즈 전쟁 우려로 뉴욕증시 급락 - 매체", "Mon, 13 Jul 2026 22:34:33 GMT")
    ], now=NOW)

    assert result["lookback_hours"] == 12
    assert result["stale_headline_count"] == 1
    assert result["considered_headline_count"] == 0
    assert result["severity"] == "low"
    assert result["buy_gate"] == "allow"


def test_duplicate_headlines_are_scored_once():
    rows = [
        headline("나스닥 하락, 변동성 확대 - A뉴스", "Wed, 15 Jul 2026 00:30:00 GMT"),
        headline("나스닥 하락, 변동성 확대 - B뉴스", "Wed, 15 Jul 2026 00:29:00 GMT"),
    ]
    result = classify(rows, now=NOW)

    assert result["duplicate_headline_count"] == 1
    assert result["considered_headline_count"] == 1
    assert result["risk_score"] == 3.0
    assert result["severity"] == "medium"


def test_older_in_window_headline_receives_half_weight():
    result = classify([
        headline("중동 전쟁 우려", "Tue, 14 Jul 2026 16:00:00 GMT")
    ], now=NOW)

    assert result["matched_headlines"][0]["age_weight"] == 0.5
    assert result["risk_score"] == 2.5
    assert result["severity"] == "medium"
