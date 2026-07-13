"""Conservative KRX trading-day calendar helpers.

The live-order path must fail closed on weekends and known KRX holidays.  This
module intentionally keeps the dependency surface small: it ships a curated
holiday set for near-term operation and can be extended by setting
``TOSS_KRX_HOLIDAYS`` to a comma/newline separated list of YYYY-MM-DD dates or
``TOSS_KRX_HOLIDAYS_FILE`` to a text/CSV file whose first column is YYYY-MM-DD.
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Mapping

# KRX full-market holidays / substitute holidays / election day for 2025-2027.
# Keep this list conservative: a false holiday blocks live orders, which is safer
# than submitting during a closed/special market.  Temporary closures can be added
# through TOSS_KRX_HOLIDAYS(_FILE) without code changes.
BUILTIN_KRX_HOLIDAYS: frozenset[date] = frozenset(
    date.fromisoformat(day)
    for day in {
        # 2025
        "2025-01-01",
        "2025-01-28",
        "2025-01-29",
        "2025-01-30",
        "2025-03-03",
        "2025-05-01",
        "2025-05-05",
        "2025-05-06",
        "2025-06-03",
        "2025-06-06",
        "2025-08-15",
        "2025-10-03",
        "2025-10-06",
        "2025-10-07",
        "2025-10-08",
        "2025-10-09",
        "2025-12-25",
        "2025-12-31",
        # 2026
        "2026-01-01",
        "2026-02-16",
        "2026-02-17",
        "2026-02-18",
        "2026-03-02",
        "2026-05-01",
        "2026-05-05",
        "2026-05-25",
        "2026-06-03",
        "2026-08-17",
        "2026-09-24",
        "2026-09-25",
        "2026-10-05",
        "2026-10-09",
        "2026-12-25",
        "2026-12-31",
        # 2027
        "2027-01-01",
        "2027-02-08",
        "2027-02-09",
        "2027-02-10",
        "2027-03-01",
        "2027-05-05",
        "2027-05-13",
        "2027-09-14",
        "2027-09-15",
        "2027-09-16",
        "2027-10-04",
        "2027-10-11",
        "2027-12-27",
        "2027-12-31",
    }
)


def krx_holidays(env: Mapping[str, str] | None = None) -> set[date]:
    source = os.environ if env is None else env
    holidays = set(BUILTIN_KRX_HOLIDAYS)
    holidays.update(_parse_dates(source.get("TOSS_KRX_HOLIDAYS", "").replace(",", "\n").splitlines()))
    file_text = source.get("TOSS_KRX_HOLIDAYS_FILE")
    if file_text:
        path = Path(file_text).expanduser()
        if path.exists():
            holidays.update(_parse_dates(_first_column_lines(path.read_text(encoding="utf-8").splitlines())))
    return holidays


def is_krx_trading_day(day: date, env: Mapping[str, str] | None = None) -> bool:
    if day.weekday() >= 5:
        return False
    return day not in krx_holidays(env)


def next_krx_trading_day(day: date, env: Mapping[str, str] | None = None) -> date:
    cur = day + timedelta(days=1)
    for _ in range(14):
        if is_krx_trading_day(cur, env):
            return cur
        cur += timedelta(days=1)
    raise RuntimeError("next_krx_trading_day search exceeded 14 calendar days")


def previous_krx_trading_day(day: date, env: Mapping[str, str] | None = None) -> date:
    cur = day - timedelta(days=1)
    for _ in range(14):
        if is_krx_trading_day(cur, env):
            return cur
        cur -= timedelta(days=1)
    raise RuntimeError("previous_krx_trading_day search exceeded 14 calendar days")


def week_first_krx_trading_day(day: date, env: Mapping[str, str] | None = None) -> date | None:
    monday = day - timedelta(days=day.weekday())
    for offset in range(5):
        cur = monday + timedelta(days=offset)
        if is_krx_trading_day(cur, env):
            return cur
    return None


def week_last_krx_trading_day(day: date, env: Mapping[str, str] | None = None) -> date | None:
    monday = day - timedelta(days=day.weekday())
    for offset in range(4, -1, -1):
        cur = monday + timedelta(days=offset)
        if is_krx_trading_day(cur, env):
            return cur
    return None


def _parse_dates(values: Iterable[str]) -> set[date]:
    parsed: set[date] = set()
    for raw in values:
        token = str(raw).strip()
        if not token or token.startswith("#") or token.lower() in {"date", "holiday"}:
            continue
        try:
            parsed.add(date.fromisoformat(token[:10]))
        except ValueError:
            continue
    return parsed


def _first_column_lines(lines: Iterable[str]) -> list[str]:
    return [line.split(",", 1)[0].strip() for line in lines]
