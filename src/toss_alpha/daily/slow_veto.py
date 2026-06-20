"""Slow-veto event collector for the daily decision pipeline.

Aggregates qualitative risk signals (DART disclosures, news, manual overrides)
into a single slow_events.json payload that :func:`run_daily_decision` consumes.

Design goals
------------
* Fail-open by default but explicit: with no API key and no manual events the
  result is ``CLEAR`` so the daily decision can still run; the reason list
  records *why* it is CLEAR (``no_api_key``).
* API key + fetcher are optional. The real OpenDART fetcher lives in
  ``toss_alpha.connectors.opendart``; tests inject a stub.
* A keyword classifier maps Korean disclosure/news titles to
  ``block`` / ``review`` / ``info`` severities. Only ``review`` and ``block``
  survive into the veto payload.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Korean disclosure / news title keyword → severity.
# BLOCK = immediate trade halt signal; REVIEW = needs qualitative judgement.
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
    "경영악화",
    "유예",
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
    "적자",
    "영업손실",
    "투자주의",
    "경고",
    "의견개시",
    "환기",
    "신주발행",
)

FetchDisclosures = Callable[[str], list[dict[str, Any]]]


def classify_title(title: str) -> str:
    """Map a Korean disclosure/news title to a veto severity.

    Returns one of ``block``, ``review``, ``info``. ``info`` titles are neutral
    (e.g. regular business reports) and are dropped from the veto payload.
    """
    if not title:
        return "info"
    for keyword in BLOCK_KEYWORDS:
        if keyword in title:
            return "block"
    for keyword in REVIEW_KEYWORDS:
        if keyword in title:
            return "review"
    return "info"


def collect_slow_veto_events(
    *,
    symbols: list[str],
    dart_api_key: str | None = None,
    fetch_disclosures: FetchDisclosures | None = None,
    manual_events_path: str | Path | None = None,
    lookback_days: int = 14,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Aggregate slow-veto events into the slow_events.json payload shape.

    Parameters
    ----------
    symbols
        Candidate symbols (6-digit strings; zero-padded internally).
    dart_api_key
        OpenDART API key. When ``None`` the DART source is skipped and
        ``no_api_key`` is recorded as a reason.
    fetch_disclosures
        Injected OpenDART fetcher ``symbol -> list[filing_dict]``. Required to
        actually pull disclosures even when a key is present.
    manual_events_path
        Optional JSON file with ``{"events": [{symbol, title, source}, ...]}``
        for hand-curated / news-derived overrides.
    output_path
        When provided, writes ``{"events": [...]}`` so ``daily run`` can load it.
    """
    normalized = [str(symbol).zfill(6) for symbol in symbols if str(symbol).strip()]
    reasons: list[str] = []
    sources: list[str] = []
    events: list[dict[str, Any]] = []

    # --- DART / OpenDART source (optional) ---------------------------------
    if dart_api_key and fetch_disclosures is not None:
        sources.append("opendart")
        for symbol in normalized:
            try:
                filings = fetch_disclosures(symbol) or []
            except Exception as exc:  # noqa: BLE001 - record and continue
                reasons.append(f"opendart_fetch_failed:{symbol}")
                _record_error(events, symbol, exc, source="opendart")
                continue
            for filing in filings:
                title = str(filing.get("report_nm") or filing.get("title") or "")
                severity = classify_title(title)
                if severity == "info":
                    continue
                events.append(
                    {
                        "symbol": symbol,
                        "severity": severity,
                        "title": title,
                        "source": "opendart",
                        "reported_at": str(filing.get("rcept_dt") or filing.get("date") or ""),
                    }
                )
    elif dart_api_key and fetch_disclosures is None:
        reasons.append("opendart_key_without_fetcher")
    else:
        reasons.append("no_api_key")

    # --- Manual / news override source -------------------------------------
    if manual_events_path is not None:
        sources.append("manual")
        payload = json.loads(Path(manual_events_path).read_text(encoding="utf-8"))
        raw_events = payload.get("events", payload if isinstance(payload, list) else [])
        for item in raw_events:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or item.get("code") or "").zfill(6)
            if symbol not in normalized:
                continue
            title = str(item.get("title") or "")
            severity = str(item.get("severity") or classify_title(title)).lower()
            if severity not in {"block", "review"}:
                continue
            events.append(
                {
                    "symbol": symbol,
                    "severity": severity,
                    "title": title,
                    "source": str(item.get("source") or "manual"),
                    "reported_at": str(item.get("reported_at") or ""),
                }
            )

    # --- Aggregate status ---------------------------------------------------
    severities = [event["severity"] for event in events]
    if "block" in severities:
        status = "BLOCK"
    elif severities:
        status = "REVIEW_REQUIRED"
    else:
        status = "CLEAR"

    event_counts: dict[str, int] = {}
    for event in events:
        event_counts[event["symbol"]] = event_counts.get(event["symbol"], 0) + 1

    result: dict[str, Any] = {
        "status": status,
        "checked_symbols": normalized,
        "sources": sources,
        "events": events,
        "event_counts": event_counts,
        "reasons": reasons,
        "as_of": datetime.now(UTC).date().isoformat(),
        "lookback_days": lookback_days,
    }

    if output_path is not None:
        Path(output_path).write_text(
            json.dumps({"events": events}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        result["output_path"] = str(output_path)

    return result


def _record_error(events: list[dict[str, Any]], symbol: str, exc: Exception, *, source: str) -> None:
    """Record a fetch error as a review-level event so it surfaces in the report."""
    events.append(
        {
            "symbol": symbol,
            "severity": "review",
            "title": f"공시 수집 실패: {exc}",
            "source": source,
            "reported_at": "",
        }
    )


def default_lookback_window(*, lookback_days: int = 14) -> tuple[str, str]:
    """Return ``(begin_ymd, end_ymd)`` for OpenDART ``bgn_de``/``end_de`` params."""
    end = datetime.now(UTC).date()
    begin = end - timedelta(days=lookback_days)
    return begin.strftime("%Y%m%d"), end.strftime("%Y%m%d")
