#!/usr/bin/env python3
"""Current-issue market risk report for the TOSS live gate.

Collects lightweight Google News RSS headlines and classifies market-wide current
issues into LOW/MEDIUM/HIGH/CRITICAL. The output JSON is intentionally simple so
`live_submit` and strategy cron scripts can fail-closed on market-wide risk.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = ROOT / "reports" / "harness" / "current_issues"

QUERIES = [
    # Broad market mood: do not anchor only on one headline theme.
    "코스피 코스닥 장전 분위기 미국 선물 환율 유가 금리",
    "한국 증시 장전 리스크 외국인 수급 환율 유가 반도체",
    "뉴욕증시 나스닥 선물 반도체 유가 환율 금리 속보",
    "원달러 환율 급등 유가 급등 코스피 코스닥 영향",
    "VIX WTI USDKRW Nasdaq futures Korea stocks risk off",
    # Geopolitical bucket remains one component, not the entire gate.
    "지정학 리스크 중동 호르무즈 이란 유가 증시",
    "Iran Middle East Hormuz oil markets Korea stocks",
]

CRITICAL_PATTERNS = [
    # Market-wide crash / systemic stress
    r"서킷브레이커", r"사이드카", r"패닉", r"폭락", r"crash", r"plunge", r"sell[- ]off",
    r"circuit breaker", r"limit down",
    # Geopolitical/systemic shock
    r"공습", r"미사일", r"전쟁", r"확전", r"호르무즈.*(봉쇄|공격|피격)",
    r"strike", r"attack", r"missile", r"war", r"Hormuz",
    # Macro shock
    r"환율.*(급등|폭등)", r"유가.*(급등|폭등)", r"금리.*급등",
    r"oil.*surge", r"dollar.*surge", r"yields.*surge",
]
HIGH_PATTERNS = [
    r"risk[- ]off", r"safe haven", r"WTI.*\$?7[0-9]", r"VIX.*(급등|spike|surge)",
    r"중동.*위기", r"지정학.*리스크", r"외국인.*매도", r"반도체.*매도",
    r"나스닥.*하락", r"미국.*선물.*하락", r"원화.*약세",
]
MEDIUM_PATTERNS = [
    r"긴장", r"우려", r"불안", r"변동성", r"경계", r"혼조", r"약세",
    r"volatility", r"concern", r"tension", r"mixed", r"caution",
]
RELIEF_PATTERNS = [
    r"휴전", r"합의", r"진정", r"완화", r"반등", r"상승", r"낙폭.*축소",
    r"ceasefire", r"truce", r"de-escalat", r"talks resume", r"rebound", r"recover",
]


def fetch_google_news(query: str, limit: int = 8) -> list[dict]:
    url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(query) + "&hl=ko&gl=KR&ceid=KR:ko"
    data = urllib.request.urlopen(url, timeout=15).read()
    root = ET.fromstring(data)
    items = []
    for item in root.findall(".//item")[:limit]:
        items.append({
            "query": query,
            "title": item.findtext("title") or "",
            "published": item.findtext("pubDate") or "",
            "link": item.findtext("link") or "",
        })
    return items


def count_matches(text: str, patterns: list[str]) -> int:
    return sum(1 for pattern in patterns if re.search(pattern, text, flags=re.IGNORECASE))


def classify(headlines: list[dict], *, now: datetime | None = None, lookback_hours: int = 36) -> dict:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=lookback_hours)
    counts = Counter()
    matched = []
    categories = Counter()
    considered = []
    stale = []
    for row in headlines:
        published_dt = parse_pubdate(row.get("published") or "")
        if published_dt is not None and published_dt < cutoff:
            stale.append(row)
            continue
        considered.append(row)
        # Important: classify only the article title. Do not include the search
        # query, otherwise a query like "이란 공습" creates false critical hits
        # even for relief/irrelevant headlines.
        text = str(row.get("title", ""))
        c = count_matches(text, CRITICAL_PATTERNS)
        h = count_matches(text, HIGH_PATTERNS)
        m = count_matches(text, MEDIUM_PATTERNS)
        r = count_matches(text, RELIEF_PATTERNS)
        if c:
            counts["critical"] += c
        if h:
            counts["high"] += h
        if m:
            counts["medium"] += m
        if r:
            counts["relief"] += r
        bucket = categorize_title(text)
        if bucket:
            categories[bucket] += 1
        if c or h or m or r:
            matched.append({**row, "category": bucket or "uncategorized", "critical_hits": c, "high_hits": h, "medium_hits": m, "relief_hits": r})
    risk_score = counts["critical"] * 4 + counts["high"] * 2 + counts["medium"] - counts["relief"] * 2
    if counts["critical"] >= 2 or risk_score >= 8:
        severity = "critical"
    elif counts["critical"] >= 1 or counts["high"] >= 2 or risk_score >= 5:
        severity = "high"
    elif counts["high"] >= 1 or counts["medium"] >= 2 or risk_score >= 2:
        severity = "medium"
    else:
        severity = "low"
    buy_gate = "block_new_buy" if severity in {"critical", "high"} else "allow_with_caution" if severity == "medium" else "allow"
    return {"severity": severity, "risk_score": risk_score, "counts": dict(counts), "category_counts": dict(categories), "buy_gate": buy_gate, "matched_headlines": matched[:20], "considered_headline_count": len(considered), "stale_headline_count": len(stale), "lookback_hours": lookback_hours}


def categorize_title(text: str) -> str | None:
    checks = [
        ("geopolitical", [r"이란", r"중동", r"호르무즈", r"전쟁", r"공습", r"Iran", r"Hormuz", r"war", r"attack"]),
        ("oil_fx_rates", [r"유가", r"WTI", r"환율", r"원달러", r"원화", r"금리", r"yield", r"dollar", r"oil"]),
        ("us_global_equity", [r"뉴욕증시", r"나스닥", r"S&P", r"미국.*선물", r"Nasdaq", r"futures"]),
        ("korea_equity", [r"코스피", r"코스닥", r"국장", r"외국인", r"KOSPI", r"KOSDAQ"]),
        ("semiconductor_growth", [r"반도체", r"AI", r"엔비디아", r"삼성전자", r"SK하이닉스", r"Nvidia", r"semiconductor"]),
    ]
    for category, patterns in checks:
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns):
            return category
    return None


def parse_pubdate(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path)
    parser.add_argument("--emit", action="store_true")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    today = now.astimezone(KST).strftime("%Y%m%d")
    headlines = []
    errors = []
    for query in QUERIES:
        try:
            headlines.extend(fetch_google_news(query))
        except Exception as exc:
            errors.append({"query": query, "error": repr(exc)})
    assessment = classify(headlines)
    payload = {
        "generated_at_utc": now.isoformat(),
        "generated_at_kst": now.astimezone(KST).isoformat(),
        "as_of": now.astimezone(KST).date().isoformat(),
        "source": "google_news_rss_keyword_gate",
        "queries": QUERIES,
        "headline_count": len(headlines),
        "errors": errors,
        **assessment,
        "policy": {
            "critical_or_high": "block all new BUY live-submit unless explicitly overridden by TOSS_ALLOW_CURRENT_ISSUE_BUY=true",
            "medium": "allow but strategy should reduce size and require quote confirmation",
            "low": "no current-issue block",
        },
    }
    out = args.out or DEFAULT_OUT_DIR / f"current_issue_risk_report_{today}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.emit:
        print(f"CURRENT_ISSUE_SEVERITY={payload['severity']}")
        print(f"BUY_GATE={payload['buy_gate']}")
        print(f"RISK_SCORE={payload['risk_score']}")
        print(f"WROTE={out}")
        print_strategy_brief(payload, out)
    else:
        print_strategy_brief(payload, out)
    return 0


def print_strategy_brief(payload: dict, out: Path) -> None:
    severity = str(payload.get("severity") or "unknown")
    buy_gate = str(payload.get("buy_gate") or "unknown")
    score = payload.get("risk_score")
    categories = payload.get("category_counts") or {}
    matched = payload.get("matched_headlines") or []
    if buy_gate == "block_new_buy":
        strategy = "일반 주식 신규 BUY 차단, 인버스/현금 방어 우선"
        rebound = "장초 반등주는 current issue 완화 전까지 자동 차단"
        inverse = "인버스는 실시간 시장 약세가 독립 확인된 통합판정(INVERSE_BUY)에서만 진입"
    elif buy_gate == "allow_with_caution":
        strategy = "일반 주식 신규 BUY 축소 허용, 장초 확인 후 제한가만 사용"
        rebound = "09:03~09:25 저점 대비 +1% 반등 확인 시 일부 진입"
        inverse = "인버스는 보조 관찰, 신규 진입은 보류 우선"
    else:
        strategy = "일반 주식 신규 BUY 허용, 기존 정책/fast veto/qual gate 우선"
        rebound = "장초 반등 detector 정상 가동"
        inverse = "인버스 branch 비활성 또는 대기"
    severity_kr = {"critical": "매우 높음", "high": "높음", "medium": "주의", "low": "낮음"}.get(severity, severity)
    gate_kr = {"block_new_buy": "신규 일반매수 차단", "allow_with_caution": "축소 매수", "allow": "매수 허용"}.get(buy_gate, buy_gate)
    category_kr = {
        "geopolitical": "지정학",
        "oil_fx_rates": "유가·환율·금리",
        "us_global_equity": "미국증시·선물",
        "korea_equity": "국내증시·수급",
        "semiconductor_growth": "반도체·성장주",
    }

    print("📌 오늘 예상전략")
    print("")
    print("[시장 분위기]")
    print(f"- 위험도: {severity_kr} ({severity})")
    print(f"- 게이트: {gate_kr} ({buy_gate})")
    print(f"- 점수: {score}")
    if categories:
        readable = ", ".join(f"{category_kr.get(k, k)} {v}" for k, v in sorted(categories.items()))
        print(f"- 위험 축: {readable}")
    print("")
    print("[오늘 운용 방침]")
    print(f"- 기본전략: {strategy}")
    print(f"- 반등주: {rebound}")
    print(f"- 인버스: {inverse}")
    print("- 손절익절: 보유 발생 시 watchdog 유지, SELL은 current issue로 차단하지 않음")
    print("")
    if matched:
        print("[주요 근거]")
        for idx, row in enumerate(matched[:5], start=1):
            category = category_kr.get(str(row.get("category") or ""), str(row.get("category") or "기타"))
            print(f"{idx}. ({category}) {row.get('title') or ''}")
        print("")
    print(f"리포트: {out}")


if __name__ == "__main__":
    raise SystemExit(main())
