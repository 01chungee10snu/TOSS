"""Symbol-specific issue authorization and market sizing overlay.

Ordinary equity BUYs require fresh company-specific positive evidence. Broad
market evidence controls size and emergency blocks; inverse ETFs remain governed
by the unified intraday market decision.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote
from urllib.request import Request, urlopen
from xml.etree import ElementTree

INVERSE_SYMBOLS = {"114800", "251340", "252670"}
POSITIVE_KEYWORDS: tuple[str, ...] = (
    "수주", "공급계약", "계약 체결", "흑자전환", "실적 개선", "영업이익 증가",
    "영업이익 급증", "자사주 매입", "자사주 소각", "배당 확대", "승인", "허가",
    "특허", "증설", "신제품", "목표가 상향", "수출 확대", "매출 증가",
)
BLOCK_KEYWORDS: tuple[str, ...] = (
    "거래정지", "상장폐지", "파산", "회생", "부도", "영업정지", "부적정",
    "자본잠식", "분식", "횡령", "배임", "관리종목",
)
REVIEW_KEYWORDS: tuple[str, ...] = (
    "전환사채", "교환사채", "신주인수권부사채", "유상증자", "제3자배정", "감자",
    "최대주주 변경", "주요주주 변경", "영업양수", "영업양도", "합병", "분할",
    "투자주의", "투자경고", "환기", "소송", "압수수색",
)


def collect_google_news_events(
    orders: Sequence[Mapping[str, Any]],
    *,
    timeout_seconds: float = 10.0,
    articles_per_symbol: int = 10,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Collect candidate-only Google News RSS evidence."""
    events: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    seen: set[tuple[str, str]] = set()
    for order in orders:
        if str(order.get("side", "BUY")).upper() != "BUY":
            continue
        symbol = str(order.get("symbol") or "").zfill(6)
        if symbol in INVERSE_SYMBOLS:
            continue
        name = str(order.get("name") or "").strip()
        if not symbol or not name:
            errors[symbol or "unknown"] = "symbol_or_name_missing"
            continue
        url = f"https://news.google.com/rss/search?q={quote(name + ' 주가 when:1d')}&hl=ko&gl=KR&ceid=KR:ko"
        try:
            request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - fixed Google News endpoint
                root = ElementTree.fromstring(response.read())
            for item in root.findall(".//item")[:articles_per_symbol]:
                title = str(item.findtext("title") or "").strip()
                published = str(item.findtext("pubDate") or "").strip()
                key = (symbol, title)
                if not title or key in seen:
                    continue
                seen.add(key)
                events.append({
                    "symbol": symbol,
                    "name": name,
                    "title": title,
                    "reported_at": published,
                    "source": "google_news_rss_symbol",
                    "link": str(item.findtext("link") or "").strip(),
                })
        except Exception as exc:
            errors[symbol] = f"{type(exc).__name__}:{exc}"
    return events, errors


def evaluate_symbol_issues(
    orders: Sequence[Mapping[str, Any]],
    events: Iterable[Mapping[str, Any]],
    *,
    now: datetime | None = None,
    max_age_seconds: int = 12 * 3600,
    max_disclosure_age_seconds: int = 36 * 3600,
) -> dict[str, Any]:
    now = _utc(now or datetime.now(timezone.utc))
    buy_orders = [o for o in orders if str(o.get("side", "BUY")).upper() == "BUY" and str(o.get("symbol") or "").zfill(6) not in INVERSE_SYMBOLS]
    by_symbol: dict[str, dict[str, Any]] = {}
    for order in buy_orders:
        symbol = str(order.get("symbol") or "").zfill(6)
        by_symbol[symbol] = {
            "symbol": symbol,
            "name": str(order.get("name") or "").strip(),
            "verdict": "WATCH",
            "reason": "fresh_positive_symbol_issue_missing",
            "positive_events": [],
            "review_events": [],
            "blocked_events": [],
            "stale_events": [],
        }

    for raw in events:
        symbol = str(raw.get("symbol") or raw.get("code") or "").strip().zfill(6)
        row = by_symbol.get(symbol)
        if row is None:
            continue
        title = str(raw.get("title") or raw.get("report_nm") or "").strip()
        source = str(raw.get("source") or "symbol_news")
        if not title or (source != "opendart" and not _title_matches_company(title, row["name"])):
            continue
        observed = _parse_datetime(raw.get("reported_at") or raw.get("pub_date") or raw.get("date"))
        normalized = {
            "symbol": symbol,
            "title": title,
            "source": source,
            "reported_at": observed.isoformat() if observed else None,
        }
        age_limit = max_disclosure_age_seconds if source == "opendart" else max_age_seconds
        if observed is None or (now - observed).total_seconds() < -30 or (now - observed).total_seconds() > age_limit:
            row["stale_events"].append(normalized)
            continue
        blocked = [k for k in BLOCK_KEYWORDS if k in title]
        review = [k for k in REVIEW_KEYWORDS if k in title]
        positive = [k for k in POSITIVE_KEYWORDS if k in title]
        normalized["matched_keywords"] = blocked or review or positive
        if blocked:
            row["blocked_events"].append(normalized)
        elif review:
            row["review_events"].append(normalized)
        elif positive:
            row["positive_events"].append(normalized)

    verdicts: dict[str, str] = {}
    for symbol, row in by_symbol.items():
        if row["blocked_events"]:
            row.update(verdict="VETO", reason="blocking_symbol_issue")
        elif row["review_events"]:
            row.update(verdict="REVIEW", reason="review_required_symbol_issue")
        elif row["positive_events"]:
            row.update(verdict="BUY", reason="fresh_positive_symbol_issue_confirmed")
        verdicts[symbol] = row["verdict"]
    return {
        "generated_at_utc": now.isoformat(),
        "max_age_seconds": max_age_seconds,
        "max_disclosure_age_seconds": max_disclosure_age_seconds,
        "checked_symbols": sorted(by_symbol),
        "verdicts_by_symbol": verdicts,
        "symbols": by_symbol,
        "buy_count": sum(v == "BUY" for v in verdicts.values()),
        "watch_count": sum(v == "WATCH" for v in verdicts.values()),
        "review_count": sum(v == "REVIEW" for v in verdicts.values()),
        "veto_count": sum(v == "VETO" for v in verdicts.values()),
    }


def apply_symbol_issue_gate(
    candidate_payload: Mapping[str, Any],
    audit: Mapping[str, Any],
    *,
    require_positive: bool = True,
) -> dict[str, Any]:
    result = dict(candidate_payload)
    verdicts = audit.get("verdicts_by_symbol") if isinstance(audit, Mapping) else {}
    verdicts = verdicts if isinstance(verdicts, Mapping) else {}
    kept: list[dict[str, Any]] = []
    for raw in list(result.get("orders") or []):
        order = dict(raw)
        if str(order.get("side", "BUY")).upper() != "BUY":
            kept.append(order)
            continue
        symbol = str(order.get("symbol") or "").zfill(6)
        if symbol in INVERSE_SYMBOLS:
            kept.append(order)
            continue
        verdict = str(verdicts.get(symbol) or "WATCH").upper()
        authorized = verdict == "BUY" or (not require_positive and verdict == "WATCH")
        order["symbol_issue_verdict"] = verdict
        order["symbol_issue_authorized"] = authorized
        if authorized:
            kept.append(order)
    result["orders"] = kept
    result["symbol_issue"] = dict(audit)
    result["symbol_issue_policy"] = {"require_positive": require_positive}
    ordinary_buys = [o for o in kept if str(o.get("side", "BUY")).upper() == "BUY" and str(o.get("symbol") or "").zfill(6) not in INVERSE_SYMBOLS]
    if str(result.get("status") or "") == "CANDIDATES" and not ordinary_buys and not any(str(o.get("side", "")).upper() == "SELL" for o in kept):
        result["status"] = "NO_TRADE"
        result["reason"] = "symbol_issue_gate_no_authorized_buy"
    return result


def apply_symbol_market_overlay(
    candidate_payload: Mapping[str, Any],
    *,
    decision: Mapping[str, Any],
    env: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source = env or {}
    result = dict(candidate_payload)
    metrics = decision.get("metrics") if isinstance(decision, Mapping) else {}
    metrics = metrics if isinstance(metrics, Mapping) else {}
    market_day = _float_or_none(metrics.get("market_day_return"))
    evidence_fresh = str(decision.get("evidence_status") or "").upper() == "FRESH"
    news_fresh = str(decision.get("news_evidence_status") or "").upper() == "FRESH"
    severity = str(decision.get("news_severity") or "").lower()
    emergency_threshold = float(source.get("TOSS_SYMBOL_EMERGENCY_MARKET_DROP_PCT", "-0.03"))
    emergency = not evidence_fresh or not news_fresh or market_day is None or market_day <= emergency_threshold or severity == "critical"
    regime = str(decision.get("market_regime") or "").lower()
    if emergency:
        multiplier = 0.0
        reason = "symbol_market_emergency_block"
    elif regime == "risk_on":
        multiplier = float(source.get("TOSS_SYMBOL_RISK_ON_SIZE_MULTIPLIER", "1.0"))
        reason = "risk_on_full_size"
    elif regime == "risk_off" or bool(metrics.get("risk_context")):
        multiplier = float(source.get("TOSS_SYMBOL_RISK_OFF_SIZE_MULTIPLIER", "0.35"))
        reason = "risk_off_reduced_size"
    else:
        multiplier = float(source.get("TOSS_SYMBOL_NEUTRAL_SIZE_MULTIPLIER", "0.65"))
        reason = "neutral_reduced_size"
    multiplier = min(1.0, max(0.0, multiplier))

    kept: list[dict[str, Any]] = []
    for raw in list(result.get("orders") or []):
        order = dict(raw)
        side = str(order.get("side", "BUY")).upper()
        symbol = str(order.get("symbol") or "").zfill(6)
        if side != "BUY" or symbol in INVERSE_SYMBOLS:
            kept.append(order)
            continue
        if not bool(order.get("symbol_issue_authorized")) or emergency:
            continue
        original_quantity = int(float(order.get("quantity") or 0))
        original_notional = _float_or_none(order.get("notional_krw"))
        quantity = int(original_quantity * multiplier)
        price = _float_or_none(order.get("limit_price") or order.get("current_price") or order.get("reference_close"))
        if quantity <= 0 or price is None or price <= 0:
            continue
        order["quantity"] = quantity
        order["notional_krw"] = quantity * price
        order["market_sizing_applied"] = True
        order["market_original_quantity"] = original_quantity
        order["market_original_notional_krw"] = original_notional
        order["market_size_multiplier"] = multiplier
        order["market_overlay_reason"] = reason
        kept.append(order)
    ordinary = [o for o in kept if str(o.get("side", "BUY")).upper() == "BUY" and str(o.get("symbol") or "").zfill(6) not in INVERSE_SYMBOLS]
    audit = {
        "ordinary_buy_authorized": bool(ordinary),
        "authorized_symbols": [str(o.get("symbol") or "").zfill(6) for o in ordinary],
        "size_multiplier": multiplier,
        "emergency_block": emergency,
        "emergency_threshold": emergency_threshold,
        "market_day_return": market_day,
        "market_regime": regime,
        "news_severity": severity,
        "reason": reason,
    }
    result["orders"] = kept
    result["market_overlay"] = audit
    result["intraday_decision"] = dict(decision)
    if str(result.get("status") or "") == "CANDIDATES" and not ordinary and not any(str(o.get("side", "")).upper() == "SELL" for o in kept):
        result["status"] = "NO_TRADE"
        result["reason"] = reason
    return result, audit


def _title_matches_company(title: str, name: str) -> bool:
    normalized_name = _normalize_name(name)
    return bool(normalized_name) and normalized_name in _normalize_name(title)


def _normalize_name(value: str) -> str:
    text = re.sub(r"주식회사|㈜|\(주\)|[^0-9A-Za-z가-힣]", "", str(value)).lower()
    return text


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _utc(value)
    if not value:
        return None
    text = str(value).strip()
    try:
        return _utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        try:
            return _utc(parsedate_to_datetime(text))
        except (TypeError, ValueError):
            return None


def _utc(value: datetime) -> datetime:
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _float_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
