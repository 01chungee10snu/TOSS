#!/usr/bin/env python3
"""Intraday sector screener + regime change detector for TOSS/KIS.

Two modes:
  --mode regime     : Detect regime/market condition changes, print alert if changed.
  --mode sector     : Screen sectors by intraday momentum, output top stock candidates.
  --mode combined   : Both: regime alert + sector screen in one run.

Design:
- Uses KIS quote API for real-time sector ETF/index proxies (~20 symbols).
- Ranks sectors by intraday return (vs prev_close).
- For top sectors, queries universe stocks in that sector for intraday momentum.
- Outputs JSON candidates to reports/harness/intraday_sector_screen_<date>.json.
- Regime mode reads loop_state.json and compares against last-seen state file.

KIS env vars must be set (KIS_APP_KEY, KIS_APP_SECRET, KIS_CANO, etc.).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

KST = ZoneInfo("Asia/Seoul")
REPORT_DIR = ROOT / "reports" / "harness"
REGIME_STATE_PATH = REPORT_DIR / "regime_alert_state.json"
LOOP_STATE_PATH = REPORT_DIR / "loop_state.json"

# ── Sector representative ETFs/index proxies ──────────────────────────────
# Each maps a sector ETF to its sector label. Intraday return of the ETF
# serves as a proxy for sector strength.
SECTOR_PROXIES = {
    "102110": "반도체",
    "161510": "반도체컴포넌트",
    "114800": "인버스(방어)",
    "252670": "인버스2X(방어)",
    "091170": "2차전지",
    "155660": "K건강관리(바이오)",
    "329200": "K로보틱스(AI로봇)",
    "085620": "미국나스닥100(기술)",
    "143860": "테크",
    "130660": "정보기술",
    "102130": "증권",
    "102120": "철강",
    "102140": "에너지화학",
    "102150": "운송",
    "102160": "금융",
    "102980": "미디어",
    "139220": "텐버거(소비)",
    "117460": "미국테크",
    "360200": "K-컨텐츠(엔터)",
    "422060": "K-조선",
    "453810": "AI반도체",
    "458200": "K-인터넷",
    "466010": "K-수익인플레이션",
    "465510": "K-인프라",
    "465520": "K-2차전지디바이스",
}

# ── Universe sector mapping (code → sector) ───────────────────────────────
# Major stocks grouped by sector for drill-down screening.
SECTOR_STOCK_MAP = {
    "반도체": ["000660", "005930", "042700", "086520", "234180"],
    "반도체컴포넌트": ["009830", "032640", "067310", "214450"],
    "2차전지": ["006400", "373220", "003670", "267260", "086520", "402340"],
    "바이오/헬스케어": ["207940", "068270", "128940", "302440", "086900", "145020"],
    "AI/로보틱스": ["454910", "348370", "340360", "446280", "452860"],
    "정보기술/인터넷": ["035420", "035720", "259960", "377300", "403870"],
    "증권": ["005940", "003540", "016360", "039490"],
    "철강/소재": ["005490", "010120", "054950", "003670"],
    "에너지화학": ["010950", "078930", "010140", "011790", "051910", "090430"],
    "운송/조선": ["003540", "009830", "010140", "329180"],
    "금융": ["086790", "316140", "105560", "055550"],
    "미디어/엔터": ["035720", "041510", "036570", "259960"],
    "소비/소매": ["028260", "245720", "272090", "139480"],
    "인버스(방어)": ["114800", "252670"],
}


def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")
    tmp.replace(path)


def kis_client():
    from toss_alpha.connectors.kis_readonly import KisReadOnlyClient
    from toss_alpha.execution.live_ready import LiveExecutionConfig
    cfg = LiveExecutionConfig.from_env()
    if not (cfg.app_key and cfg.app_secret and cfg.cano):
        raise RuntimeError("KIS 설정이 부족합니다 (KIS_APP_KEY/KIS_APP_SECRET/KIS_CANO)")
    return KisReadOnlyClient(
        app_key=cfg.app_key,
        app_secret=cfg.app_secret,
        cano=cfg.cano,
        account_product_code=cfg.account_product_code,
        mock_trading=cfg.kis_mock_trading,
        timeout=cfg.timeout,
    )


def get_intraday_returns(client, symbols: list[str]) -> list[dict]:
    """Get intraday return % for each symbol via KIS quote API."""
    results = []
    for sym in symbols:
        try:
            payload = client.quote(sym)
            body = payload.get("json") or {}
            record = body.get("output") if isinstance(body.get("output"), dict) else {}
            if not record:
                continue
            last = float(record.get("stck_prpr", 0) or 0)
            prev_close = float(record.get("stck_sdpr", 0) or 0)  # 전일 종가
            day_high = float(record.get("stck_hgpr", 0) or 0)
            day_low = float(record.get("stck_lgpr", 0) or 0)
            volume = float(record.get("acml_vol", 0) or 0)
            day_return = (last / prev_close - 1.0) if prev_close > 0 else 0.0
            intraday_range = ((day_high - day_low) / prev_close) if prev_close > 0 else 0.0
            results.append({
                "symbol": sym.zfill(6),
                "last": last,
                "prev_close": prev_close,
                "day_return": day_return,
                "day_high": day_high,
                "day_low": day_low,
                "intraday_range": intraday_range,
                "volume": volume,
            })
        except Exception as exc:
            results.append({"symbol": sym.zfill(6), "error": str(exc)[:100]})
    return results


# ── Regime change detection ──────────────────────────────────────────────

def detect_regime_change() -> dict | None:
    """Compare current loop_state regime info against last-seen state.

    Returns alert dict if changed, None if no change.
    """
    loop = load_json(LOOP_STATE_PATH, {})
    if not loop:
        return None

    intraday = loop.get("intraday", {})
    decision = intraday.get("decision", {})
    overlay = intraday.get("market_overlay", {})

    current = {
        "daily_regime": intraday.get("daily_regime") or loop.get("quant", {}).get("candidate_situation", "unknown"),
        "market_regime": overlay.get("market_regime", ""),
        "market_day_return": overlay.get("market_day_return", 0),
        "news_severity": overlay.get("news_severity", ""),
        "ordinary_buy_authorized": overlay.get("ordinary_buy_authorized"),
        "size_multiplier": overlay.get("size_multiplier"),
        "emergency_block": overlay.get("emergency_block"),
        "verdict": decision.get("verdict", ""),
        "bearish_confirmed": decision.get("metrics", {}).get("bearish_confirmed"),
        "bullish_confirmed": decision.get("metrics", {}).get("bullish_confirmed"),
        "generated_at_utc": loop.get("generated_at_utc", ""),
    }

    prev = load_json(REGIME_STATE_PATH, {})
    prev_key = prev.get("last_regime", {})
    # Only compare keys that matter for change detection
    watch_keys = [
        "daily_regime", "market_regime", "news_severity",
        "ordinary_buy_authorized", "emergency_block", "verdict",
        "bearish_confirmed", "bullish_confirmed",
    ]
    changes = {}
    for k in watch_keys:
        old_val = prev_key.get(k)
        new_val = current.get(k)
        if old_val != new_val and old_val is not None:
            changes[k] = {"from": old_val, "to": new_val}

    # Also watch large market_day_return moves (> 0.5% shift)
    old_ret = prev_key.get("market_day_return")
    new_ret = current.get("market_day_return", 0)
    if old_ret is not None and abs(new_ret - old_ret) > 0.005:
        changes["market_day_return_shift"] = {
            "from": f"{old_ret:+.2%}",
            "to": f"{new_ret:+.2%}",
        }

    # Save current as last-seen
    save_json(REGIME_STATE_PATH, {"last_regime": current, "updated_at": datetime.now(KST).isoformat()})

    if not changes:
        return None

    return {
        "type": "regime_change",
        "changes": changes,
        "current": current,
        "timestamp": datetime.now(KST).isoformat(),
    }


def format_regime_alert(alert: dict) -> str:
    lines = ["🔄 국면 전환 감지"]
    lines.append(f"⏰ {alert['timestamp']}")
    for k, v in alert["changes"].items():
        if isinstance(v, dict):
            lines.append(f"• {k}: {v['from']} → {v['to']}")
    c = alert["current"]
    lines.append("")
    lines.append(f"📊 현재: regime={c['daily_regime']}, market={c['market_regime']}")
    lines.append(f"📈 시장수익률: {c['market_day_return']:+.2%}, size×{c.get('size_multiplier', '-')}")
    if c.get("bearish_confirmed"):
        lines.append("⚠️ 약세 확정")
    if c.get("bullish_confirmed"):
        lines.append("✅ 강세 확정")
    return "\n".join(lines)


# ── Sector screening ─────────────────────────────────────────────────────

def screen_sectors(client, top_n: int = 3) -> dict:
    """Screen sector ETFs, return ranked sectors with drill-down stocks."""
    proxy_symbols = list(SECTOR_PROXIES.keys())
    quotes = get_intraday_returns(client, proxy_symbols)

    valid = [q for q in quotes if "error" not in q and q.get("day_return") is not None]
    valid.sort(key=lambda x: x["day_return"], reverse=True)

    # Separate offensive vs defensive
    offensive = [q for q in valid if "방어" not in SECTOR_PROXIES.get(q["symbol"], "")]
    defensive = [q for q in valid if "방어" in SECTOR_PROXIES.get(q["symbol"], "")]

    # Top offensive sectors
    top_offensive = offensive[:top_n]
    # Best defensive (inverse) if market is falling
    top_defensive = defensive[:1] if defensive else []

    # Drill down: for each top sector, get intraday returns of member stocks
    drill_down = []
    for sq in top_offensive:
        sector_name = SECTOR_PROXIES.get(sq["symbol"], "Unknown")
        # Clean up sector name for matching
        clean_sector = sector_name.split("(")[0].replace("K-", "").replace("미국", "")
        member_codes = SECTOR_STOCK_MAP.get(sector_name) or SECTOR_STOCK_MAP.get(clean_sector, [])
        if not member_codes:
            # Try fuzzy match
            for key, codes in SECTOR_STOCK_MAP.items():
                if clean_sector in key or key in clean_sector:
                    member_codes = codes
                    break

        if member_codes:
            stock_quotes = get_intraday_returns(client, member_codes)
            stock_valid = [q for q in stock_quotes if "error" not in q]
            stock_valid.sort(key=lambda x: x["day_return"], reverse=True)
            drill_down.append({
                "sector": sector_name,
                "sector_etf": sq["symbol"],
                "sector_return": sq["day_return"],
                "stocks": [
                    {
                        "code": s["symbol"],
                        "last": s["last"],
                        "day_return": s["day_return"],
                        "volume": s["volume"],
                        "intraday_range": s["intraday_range"],
                    }
                    for s in stock_valid[:5]
                ],
            })

    result = {
        "generated_at_kst": datetime.now(KST).isoformat(),
        "market_snapshot": {
            "best_offensive": [
                {"sector": SECTOR_PROXIES.get(q["symbol"], "?"), "return": q["day_return"]}
                for q in top_offensive
            ],
            "best_defensive": [
                {"sector": SECTOR_PROXIES.get(q["symbol"], "?"), "return": q["day_return"]}
                for q in top_defensive
            ],
        },
        "drill_down": drill_down,
        "all_sector_quotes": [
            {"symbol": q["symbol"], "name": SECTOR_PROXIES.get(q["symbol"], "?"), "return": q["day_return"]}
            for q in valid
        ],
    }

    # Save
    date_str = datetime.now(KST).strftime("%Y%m%d")
    out_path = REPORT_DIR / f"intraday_sector_screen_{date_str}.json"
    save_json(out_path, result)

    return result


def format_sector_screen(result: dict) -> str:
    lines = ["📊 섹터 스크리닝"]
    lines.append(f"⏰ {result['generated_at_kst']}")
    lines.append("")

    ms = result.get("market_snapshot", {})
    lines.append("🔥 강세 섹터:")
    for s in ms.get("best_offensive", []):
        lines.append(f"  • {s['sector']}: {s['return']:+.2%}")

    if ms.get("best_defensive"):
        lines.append("\n🛡️ 방어 섹터:")
        for s in ms["best_defensive"]:
            lines.append(f"  • {s['sector']}: {s['return']:+.2%}")

    lines.append("\n📌 섹터별 상위 종목:")
    for dd in result.get("drill_down", []):
        lines.append(f"\n【{dd['sector']}】 ETF수익률 {dd['sector_return']:+.2%}")
        for stk in dd.get("stocks", []):
            lines.append(
                f"  {stk['code']} {stk['last']:,.0f}원 "
                f"{stk['day_return']:+.2%} "
                f"거래량:{stk['volume']/1e6:.1f}M"
            )

    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────

def is_market_hours() -> bool:
    now_kst = datetime.now(KST)
    if now_kst.weekday() >= 5:
        return False
    t = now_kst.time()
    from datetime import time as dt_time
    return dt_time(9, 0) <= t <= dt_time(15, 25)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["regime", "sector", "combined"], default="combined")
    parser.add_argument("--top-n", type=int, default=3, help="Top sectors to drill down")
    args = parser.parse_args()

    if not is_market_hours():
        # Still do regime check (it reads files, doesn't need market data)
        if args.mode in ("regime", "combined"):
            alert = detect_regime_change()
            if alert:
                print(format_regime_alert(alert))
        return 0

    outputs = []

    # Regime change detection
    if args.mode in ("regime", "combined"):
        alert = detect_regime_change()
        if alert:
            outputs.append(format_regime_alert(alert))

    # Sector screening
    if args.mode in ("sector", "combined"):
        try:
            client = kis_client()
            result = screen_sectors(client, top_n=args.top_n)
            outputs.append(format_sector_screen(result))
        except Exception as exc:
            outputs.append(f"❌ 섹터 스크리닝 오류: {exc!r}")

    if outputs:
        print("\n\n".join(outputs))

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"오류: {exc!r}")
        raise
