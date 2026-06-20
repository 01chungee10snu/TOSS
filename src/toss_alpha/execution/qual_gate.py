"""Qualitative disclosure gate helpers for the TOSS harness."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


FetchRecentFilings = Callable[[str], Any]


def evaluate_disclosure_gate(
    *,
    symbols: list[str],
    api_key_present: bool,
    fetch_recent_filings: FetchRecentFilings | None = None,
) -> dict[str, Any]:
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
        }
    if not api_key_present:
        return {
            "status": "BLOCKED_QUAL_DATA",
            "reasons": ["missing_opendart_api_key"],
            "checked_symbols": [],
            "pending_symbols": normalized,
            "event_counts": {},
            "review_required_symbols": [],
            "fetch_errors": {},
        }
    if fetch_recent_filings is None:
        return {
            "status": "BLOCKED_QUAL_DATA",
            "reasons": ["missing_disclosure_fetcher"],
            "checked_symbols": [],
            "pending_symbols": normalized,
            "event_counts": {},
            "review_required_symbols": [],
            "fetch_errors": {},
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
            "status": "BLOCKED_QUAL_DATA",
            "reasons": ["disclosure_fetch_failed"],
            "checked_symbols": checked,
            "pending_symbols": [s for s in normalized if s not in checked],
            "event_counts": counts,
            "review_required_symbols": review_required,
            "fetch_errors": fetch_errors,
        }
    return {
        "status": "READY",
        "reasons": [],
        "checked_symbols": checked,
        "pending_symbols": [],
        "event_counts": counts,
        "review_required_symbols": review_required,
        "fetch_errors": {},
    }


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
