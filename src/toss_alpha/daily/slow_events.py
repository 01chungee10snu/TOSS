"""File-based slow-veto event collector for API-less daily decision runs.

This collector normalizes manual/news/DART-export files into a single
``slow_events.json`` payload. It is intentionally broker-independent and does
not require Toss or OpenDART credentials.
"""
from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
    "합병",
    "분할",
    "영업양수",
    "영업양도",
    "투자주의",
    "경고",
    "환기",
)


def collect_slow_veto_events(
    *,
    symbols: list[str],
    source_paths: list[str | Path] | None = None,
    output_path: str | Path | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Normalize JSON/CSV slow-event sources into a daily-decision payload."""
    normalized_symbols = [str(symbol).strip().zfill(6) for symbol in symbols if str(symbol).strip()]
    allowed = set(normalized_symbols)
    events: list[dict[str, Any]] = []
    for source_path in source_paths or []:
        for raw in _read_source(Path(source_path)):
            symbol = str(raw.get("symbol") or raw.get("code") or "").strip().zfill(6)
            if symbol not in allowed:
                continue
            title = str(raw.get("title") or raw.get("report_nm") or "").strip()
            explicit = str(raw.get("severity") or "").strip().lower()
            severity, matched = _classify_with_keywords(title, explicit_severity=explicit)
            if severity not in {"block", "review", "info"}:
                severity = "info"
            events.append(
                {
                    "symbol": symbol,
                    "severity": severity,
                    "title": title,
                    "source": str(raw.get("source") or Path(source_path).stem),
                    "reported_at": str(raw.get("reported_at") or raw.get("rcept_dt") or raw.get("date") or ""),
                    "matched_keywords": matched,
                }
            )
    severities = [event["severity"] for event in events]
    if "block" in severities:
        status = "BLOCK"
    elif "review" in severities:
        status = "REVIEW_REQUIRED"
    else:
        status = "CLEAR"
    payload: dict[str, Any] = {
        "status": status,
        "as_of": as_of or datetime.now(UTC).date().isoformat(),
        "checked_symbols": normalized_symbols,
        "events": events,
        "event_counts": _event_counts(events),
        "source_paths": [str(path) for path in source_paths or []],
    }
    if output_path is not None:
        Path(output_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        payload["output_path"] = str(output_path)
    return payload


def _read_source(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        raw = payload.get("events", [])
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
    return []


def _classify_with_keywords(title: str, *, explicit_severity: str = "") -> tuple[str, list[str]]:
    if explicit_severity in {"block", "review", "info"}:
        return explicit_severity, []
    block_matches = [keyword for keyword in BLOCK_KEYWORDS if keyword in title]
    if block_matches:
        return "block", block_matches
    review_matches = [keyword for keyword in REVIEW_KEYWORDS if keyword in title]
    if review_matches:
        return "review", review_matches
    return "info", []


def _event_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        counts[event["symbol"]] = counts.get(event["symbol"], 0) + 1
    return counts
