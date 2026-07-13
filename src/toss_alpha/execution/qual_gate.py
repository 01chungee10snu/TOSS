"""Multi-source qualitative and real-time gate helpers for the TOSS harness."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

FetchRecentFilings = Callable[[str], Any]

BLOCK_KEYWORDS: tuple[str, ...] = (
    "거래정지",
    "상장폐지",
    "파산",
    "회생",
    "부도",
    "영업정지",
    "부적정",
    "자본잠식",
    "분식",
    "횡령",
    "배임",
    "관리종목",
)

REVIEW_KEYWORDS: tuple[str, ...] = (
    "전환사채",
    "교환사채",
    "신주인수권부사채",
    "유상증자",
    "제3자배정",
    "감자",
    "최대주주 변경",
    "주요주주 변경",
    "영업양수",
    "영업양도",
    "합병",
    "분할",
    "투자주의",
    "투자경고",
    "환기",
    "소송",
    "압수수색",
)


def evaluate_disclosure_gate(
    *,
    symbols: list[str],
    api_key_present: bool,
    fetch_recent_filings: FetchRecentFilings | None = None,
    require_opendart: bool = False,
) -> dict[str, Any]:
    """Evaluate OpenDART as a slow optional veto/review source.

    OpenDART is deliberately not the only live-submit blocker unless
    ``require_opendart`` is true.  Missing OpenDART is reported as ``SKIPPED``
    by default because it is slow disclosure data, not real-time execution data.
    """
    normalized = [str(s).zfill(6) for s in symbols if str(s).strip()]
    if not normalized:
        return {
            "status": "SKIPPED_NO_CANDIDATES",
            "reasons": ["no_candidate_symbols"],
            "checked_symbols": [],
            "pending_symbols": [],
            "event_counts": {},
            "review_required_symbols": [],
            "fetch_errors": {},
            "source": "opendart",
        }
    if not api_key_present:
        return {
            "status": "BLOCKED_QUAL_DATA" if require_opendart else "SKIPPED_SOURCE_UNAVAILABLE",
            "reasons": ["missing_opendart_api_key"],
            "checked_symbols": [],
            "pending_symbols": normalized,
            "event_counts": {},
            "review_required_symbols": [],
            "fetch_errors": {},
            "source": "opendart",
        }
    if fetch_recent_filings is None:
        return {
            "status": "BLOCKED_QUAL_DATA" if require_opendart else "SKIPPED_SOURCE_UNAVAILABLE",
            "reasons": ["missing_disclosure_fetcher"],
            "checked_symbols": [],
            "pending_symbols": normalized,
            "event_counts": {},
            "review_required_symbols": [],
            "fetch_errors": {},
            "source": "opendart",
        }

    checked: list[str] = []
    counts: dict[str, int] = {}
    review_required: list[str] = []
    fetch_errors: dict[str, str] = {}
    for symbol in normalized:
        try:
            payload = fetch_recent_filings(symbol)
            count = _count_rows(payload)
            checked.append(symbol)
            counts[symbol] = count
            if count > 0:
                review_required.append(symbol)
        except Exception as exc:
            fetch_errors[symbol] = repr(exc)
    if fetch_errors:
        return {
            "status": "BLOCKED_QUAL_DATA" if require_opendart else "SKIPPED_SOURCE_ERROR",
            "reasons": ["disclosure_fetch_failed"],
            "checked_symbols": checked,
            "pending_symbols": [s for s in normalized if s not in checked],
            "event_counts": counts,
            "review_required_symbols": review_required,
            "fetch_errors": fetch_errors,
            "source": "opendart",
        }
    return {
        "status": "READY",
        "reasons": [],
        "checked_symbols": checked,
        "pending_symbols": [],
        "event_counts": counts,
        "review_required_symbols": review_required,
        "fetch_errors": {},
        "source": "opendart",
    }


def evaluate_news_event_gate(*, symbols: list[str], events: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Evaluate recent news/manual events with fast Korean keyword vetoes."""
    normalized = [str(s).zfill(6) for s in symbols if str(s).strip()]
    if not normalized:
        return {
            "status": "SKIPPED_NO_CANDIDATES",
            "reasons": ["no_candidate_symbols"],
            "checked_symbols": [],
            "blocked_symbols": [],
            "review_required_symbols": [],
            "events": [],
            "event_counts": {},
            "source": "news_events",
        }
    allowed = set(normalized)
    relevant: list[dict[str, Any]] = []
    blocked: list[str] = []
    review: list[str] = []
    for event in events or []:
        symbol = str(event.get("symbol") or event.get("code") or "").strip().zfill(6)
        if symbol not in allowed:
            continue
        title = str(event.get("title") or event.get("report_nm") or "").strip()
        explicit = str(event.get("severity") or "").strip().lower()
        severity, matched = classify_event_title(title, explicit_severity=explicit)
        normalized_event = {
            "symbol": symbol,
            "title": title,
            "severity": severity,
            "source": str(event.get("source") or "news"),
            "reported_at": str(event.get("reported_at") or event.get("date") or ""),
            "matched_keywords": matched,
        }
        relevant.append(normalized_event)
        if severity == "block":
            blocked.append(symbol)
        elif severity == "review":
            review.append(symbol)
    if blocked:
        status = "BLOCKED_NEWS_EVENT"
        reasons = ["blocking_news_keywords"]
    elif review:
        status = "REVIEW_REQUIRED_NEWS_EVENT"
        reasons = ["review_news_keywords"]
    else:
        status = "READY"
        reasons = []
    return {
        "status": status,
        "reasons": reasons,
        "checked_symbols": normalized,
        "blocked_symbols": sorted(set(blocked)),
        "review_required_symbols": sorted(set(review)),
        "events": relevant,
        "event_counts": _event_counts(relevant),
        "source": "news_events",
    }


def evaluate_multi_source_qual_gate(
    *,
    symbols: list[str],
    opendart_api_key_present: bool,
    fetch_recent_filings: FetchRecentFilings | None = None,
    news_events: Iterable[dict[str, Any]] | None = None,
    require_opendart: bool = False,
) -> dict[str, Any]:
    """Combine fast-ish news/manual vetoes with optional slow OpenDART checks."""
    normalized = [str(s).zfill(6) for s in symbols if str(s).strip()]
    if not normalized:
        return {
            "status": "SKIPPED_NO_CANDIDATES",
            "reasons": ["no_candidate_symbols"],
            "checked_symbols": [],
            "pending_symbols": [],
            "review_required_symbols": [],
            "blocked_symbols": [],
            "event_counts": {},
            "sources": {},
        }
    news = evaluate_news_event_gate(symbols=normalized, events=news_events)
    opendart = evaluate_disclosure_gate(
        symbols=normalized,
        api_key_present=opendart_api_key_present,
        fetch_recent_filings=fetch_recent_filings,
        require_opendart=require_opendart,
    )
    reasons: list[str] = []
    blocked_symbols: list[str] = []
    review_symbols: list[str] = []
    if news["status"].startswith("BLOCKED"):
        reasons.extend(news["reasons"])
        blocked_symbols.extend(news.get("blocked_symbols", []))
    if news["status"].startswith("REVIEW"):
        reasons.extend(news["reasons"])
        review_symbols.extend(news.get("review_required_symbols", []))
    if opendart["status"] == "BLOCKED_QUAL_DATA":
        reasons.extend(opendart["reasons"])
    review_symbols.extend(opendart.get("review_required_symbols", []))

    if blocked_symbols:
        status = "BLOCKED_QUAL_EVENT"
    elif require_opendart and opendart["status"] == "BLOCKED_QUAL_DATA":
        status = "BLOCKED_QUAL_DATA"
    elif review_symbols:
        status = "REVIEW_REQUIRED_QUAL_EVENT"
    else:
        status = "READY"

    return {
        "status": status,
        "reasons": list(dict.fromkeys(reasons)),
        "checked_symbols": normalized,
        "pending_symbols": opendart.get("pending_symbols", []),
        "review_required_symbols": sorted(set(review_symbols)),
        "blocked_symbols": sorted(set(blocked_symbols)),
        "event_counts": {
            "news_events": news.get("event_counts", {}),
            "opendart": opendart.get("event_counts", {}),
        },
        "sources": {
            "news_events": news,
            "opendart": opendart,
        },
    }


def classify_event_title(title: str, *, explicit_severity: str = "") -> tuple[str, list[str]]:
    if explicit_severity in {"block", "review", "info"}:
        return explicit_severity, []
    matched: list[str] = []
    for keyword in BLOCK_KEYWORDS:
        if keyword in title:
            matched.append(keyword)
    if matched:
        return "block", matched
    for keyword in REVIEW_KEYWORDS:
        if keyword in title:
            matched.append(keyword)
    if matched:
        return "review", matched
    return "info", []


def _event_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"block": 0, "review": 0, "info": 0}
    for event in events:
        severity = event.get("severity")
        if severity in counts:
            counts[severity] += 1
    return counts


def _count_rows(payload: Any) -> int:
    if payload is None:
        return 0
    if hasattr(payload, "shape") and getattr(payload, "shape", None):
        return int(payload.shape[0])
    if isinstance(payload, dict):
        return len(payload)
    if isinstance(payload, str):
        return 1 if payload else 0
    try:
        return len(payload)
    except Exception:
        return 1
