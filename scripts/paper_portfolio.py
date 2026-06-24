"""Paper portfolio engine — virtual buy/sell with P&L tracking.

Consumes daily forward_tracking Top-10 candidates and executes virtual trades:
  - Monday open buy (or daily entry depending on mode)
  - Risk exits: stop-loss, take-profit, trailing-stop, max-holding
  - Persistent state in JSON
  - Daily P&L report

Research/paper only. No live orders.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "reports" / "harness" / "paper_portfolio"
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_PATH = STATE_DIR / "paper_portfolio_state.json"
HISTORY_PATH = STATE_DIR / "paper_portfolio_history.json"
DAILY_REPORT_DIR = STATE_DIR / "daily_reports"
DAILY_REPORT_DIR.mkdir(parents=True, exist_ok=True)

FORWARD_DIR = ROOT / "reports" / "harness" / "forward_tracking"
PANEL_PATH = ROOT / "reports" / "backtests" / "practical_universe_panel.parquet"
PANEL_CSV_2026 = ROOT / "reports" / "backtests" / "random500_seed20260607_2022-01-01_2026-latest_ohlcv_panel.csv"

# Strategy parameters (from forward_tracking_daily.py OPTIMAL)
PARAMS = {
    "starting_cash": 1_000_000,
    "cash_fraction_per_entry": 0.40,
    "max_notional": 300_000,
    "stop_loss_pct": 0.06,
    "take_profit_pct": 0.25,
    "trailing_stop_pct": 0.06,
    "max_holding_steps": 20,
    "max_positions": 8,
    "transaction_cost_bps": 30.0,
    "entry_top_n": 3,       # buy top-N from forward tracking each day
}


def _now_kst() -> datetime:
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9)))


def _today_str() -> str:
    return _now_kst().strftime("%Y-%m-%d")


def load_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {
        "created_at": _now_kst().isoformat(),
        "cash": PARAMS["starting_cash"],
        "positions": [],          # [{code, name, entry_date, entry_price, shares, cost_basis, high_water_mark, cost_incl_fee}]
        "trades": [],             # closed trade history (inline copy)
        "last_run_date": None,
        "daily_equity": [],       # [{date, cash, positions_value, total_equity}]
    }


def save_state(state: dict[str, Any]) -> None:
    state["updated_at"] = _now_kst().isoformat()
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    # Also save history snapshot
    history = []
    if HISTORY_PATH.exists():
        history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    history.append({
        "date": _today_str(),
        "timestamp": _now_kst().isoformat(),
        "cash": state["cash"],
        "positions_count": len(state["positions"]),
        "total_equity": state.get("last_total_equity", state["cash"]),
        "closed_trades_today": state.get("closed_trades_today", 0),
    })
    # Keep last 365 entries
    history = history[-365:]
    HISTORY_PATH.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def code_to_yf_ticker(code: str) -> str:
    """Convert 6-digit code to yfinance ticker. Needs market suffix.

    Tries both .KS and .KQ — but we don't know market from code alone.
    Best approach: use the practical universe panel's code→name mapping.
    """
    code = code.zfill(6)
    # Heuristic: KOSPI codes typically start with 0, KOSDAQ with 1/2/3
    # But this is unreliable. Better to look up from panel metadata.
    return code  # caller adds suffix


# Load code→ticker mapping from KRX listing (via FinanceDataReader)
_TICKER_CACHE: dict[str, str] | None = None


def load_ticker_map() -> dict[str, str]:
    """Load code→yfinance ticker mapping from KRX listing via FinanceDataReader.

    Maps MarketId STK→.KS, KSQ→.KQ.
    """
    global _TICKER_CACHE
    if _TICKER_CACHE is not None:
        return _TICKER_CACHE

    import FinanceDataReader as fdr

    _TICKER_CACHE = {}
    listing = fdr.StockListing("KRX")
    listing["Code"] = listing["Code"].astype(str).str.zfill(6)

    def _suffix(row) -> str:
        mid = str(row.get("MarketId", ""))
        market = str(row.get("Market", ""))
        if mid == "KSQ" or market == "KOSDAQ":
            return ".KQ"
        return ".KS"

    for _, row in listing.iterrows():
        code = str(row["Code"]).zfill(6)
        _TICKER_CACHE[code] = code + _suffix(row)
    print(f"  [ticker_map] Loaded {len(_TICKER_CACHE)} codes from KRX")
    return _TICKER_CACHE


def fetch_live_prices(codes: list[str], lookback_days: int = 10) -> dict[str, dict]:
    """Fetch latest OHLCV from yfinance for given codes.

    Returns {code: {date, open, high, low, close, volume}}.
    """
    import yfinance as yf
    from datetime import date, timedelta

    ticker_map = load_ticker_map()
    end = (date.today() + timedelta(days=1)).isoformat()
    start = (date.today() - timedelta(days=lookback_days)).isoformat()

    result = {}
    for code in codes:
        code = str(code).zfill(6)
        ticker = ticker_map.get(code)
        if not ticker:
            print(f"  [live] {code}: no ticker mapping, skipping")
            continue
        try:
            df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True, threads=False)
            if df is None or df.empty:
                print(f"  [live] {code} ({ticker}): no data")
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df.reset_index()
            latest = df.iloc[-1]
            result[code] = {
                "date": str(latest["Date"].date()),
                "open": float(latest["Open"]),
                "high": float(latest["High"]),
                "low": float(latest["Low"]),
                "close": float(latest["Close"]),
                "volume": int(latest.get("Volume", 0)),
            }
        except Exception as exc:
            print(f"  [live] {code} ({ticker}): ERROR {exc}")
    return result


def load_panel_prices() -> pd.DataFrame:
    """Load price data from practical universe panel (parquet, up to 2026-06-19)."""
    panel = pd.read_parquet(PANEL_PATH)
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    panel["Date"] = pd.to_datetime(panel["Date"])
    return panel.sort_values(["code", "Date"]).reset_index(drop=True)


def load_panel_2026_prices() -> pd.DataFrame | None:
    """Load extended panel with 2026 data if available."""
    if not PANEL_CSV_2026.exists():
        return None
    df = pd.read_csv(PANEL_CSV_2026, dtype={"code": str}, parse_dates=["Date"])
    df["code"] = df["code"].astype(str).str.zfill(6)
    return df.sort_values(["code", "Date"]).reset_index(drop=True)


def get_latest_prices(panel: pd.DataFrame, as_of: pd.Timestamp | None = None) -> dict[str, dict]:
    """Get latest available price for each code. Returns {code: {date, open, high, low, close}}."""
    if as_of:
        mask = panel["Date"] <= as_of
        panel = panel[mask]
    latest = panel.groupby("code").tail(1)
    result = {}
    for _, row in latest.iterrows():
        result[str(row["code"]).zfill(6)] = {
            "date": str(row["Date"].date()),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
        }
    return result


def get_price_for_date(panel: pd.DataFrame, code: str, date_str: str) -> dict | None:
    """Get OHLC for a specific code and date."""
    code = code.zfill(6)
    target = pd.Timestamp(date_str)
    rows = panel[(panel["code"] == code) & (panel["Date"] == target)]
    if rows.empty:
        # Find nearest date before target
        rows = panel[(panel["code"] == code) & (panel["Date"] <= target)].tail(1)
    if rows.empty:
        return None
    row = rows.iloc[0]
    return {
        "date": str(row["Date"].date()),
        "open": float(row["Open"]),
        "high": float(row["High"]),
        "low": float(row["Low"]),
        "close": float(row["Close"]),
    }


def find_forward_report(target_date: str | None = None) -> Path | None:
    """Find the most recent forward tracking report."""
    if target_date:
        p = FORWARD_DIR / f"forward_{target_date}.json"
        if p.exists():
            return p
    # Find most recent
    files = sorted(FORWARD_DIR.glob("forward_*.json"), reverse=True)
    return files[0] if files else None


def load_forward_candidates(report_path: Path) -> list[dict]:
    """Load Top-10 candidates from forward tracking report."""
    data = json.loads(report_path.read_text(encoding="utf-8"))
    return data.get("top10", [])


def execute_entries(
    state: dict[str, Any],
    candidates: list[dict],
    prices: dict[str, dict],
    entry_date: str,
) -> list[dict]:
    """Execute virtual buys for top-N candidates."""
    new_positions = []
    cost_bps = PARAMS["transaction_cost_bps"]
    fee_rate = cost_bps / 2 / 10_000  # entry fee only (half of round-trip)

    available_slots = PARAMS["max_positions"] - len(state["positions"])
    if available_slots <= 0:
        print(f"  [entries] Max positions ({PARAMS['max_positions']}) reached, skipping")
        return []

    top_n = min(PARAMS["entry_top_n"], available_slots)
    target_codes = [c for c in [p["code"] for p in state["positions"]]]
    skip_if_held = True

    for cand in candidates[:top_n * 2]:  # scan more to find buyable ones
        if len(new_positions) >= top_n:
            break
        code = str(cand["code"]).zfill(6)
        if skip_if_held and code in target_codes:
            continue
        if code not in prices:
            print(f"  [entries] {code} {cand.get('name','')} — no price data, skipping")
            continue

        price = prices[code]["close"]
        if price <= 0:
            continue

        # Position sizing: fraction of current cash, capped at max_notional
        allocation = state["cash"] * PARAMS["cash_fraction_per_entry"]
        allocation = min(allocation, PARAMS["max_notional"])
        if allocation < price:  # can't even buy 1 share
            print(f"  [entries] {code} — allocation ₩{allocation:,.0f} < 1 share price ₩{price:,.0f}")
            continue

        shares = int(allocation // price)
        if shares <= 0:
            continue

        cost = shares * price
        fee = cost * fee_rate
        total_cost = cost + fee

        if total_cost > state["cash"]:
            shares = int((state["cash"] - fee) // price)
            if shares <= 0:
                continue
            cost = shares * price
            fee = cost * fee_rate
            total_cost = cost + fee

        state["cash"] -= total_cost

        pos = {
            "code": code,
            "name": cand.get("name", "—"),
            "entry_date": entry_date,
            "entry_price": price,
            "shares": shares,
            "cost_basis": cost,
            "fee_paid": fee,
            "high_water_mark": price,
            "ml_score": cand.get("ml_score", 0),
            "days_held": 0,
        }
        new_positions.append(pos)
        state["positions"].append(pos)
        target_codes.append(code)
        print(f"  [BUY] {code} {pos['name']:<14s} | {shares:>4d} shares @ ₩{price:,.0f} | cost ₩{cost:,.0f} + fee ₩{fee:,.0f}")

    return new_positions


def execute_exits(
    state: dict[str, Any],
    prices: dict[str, dict],
    current_date: str,
) -> list[dict]:
    """Check exit conditions and execute virtual sells."""
    closed = []
    cost_bps = PARAMS["transaction_cost_bps"]
    fee_rate = cost_bps / 2 / 10_000  # exit fee only (half of round-trip)
    tax_rate = 0.0  # no capital gains tax on paper for now

    remaining = []
    for pos in state["positions"]:
        code = pos["code"]
        if code not in prices:
            remaining.append(pos)
            continue

        price = prices[code]["close"]
        high = prices[code]["high"]
        low = prices[code]["low"]

        # Update high water mark
        if high > pos["high_water_mark"]:
            pos["high_water_mark"] = high

        pos["days_held"] += 1
        days = pos["days_held"]

        # Exit condition checks
        exit_reason = None

        # 1. Stop loss
        if price <= pos["entry_price"] * (1 - PARAMS["stop_loss_pct"]):
            exit_reason = f"stop_loss_{PARAMS['stop_loss_pct']*100:.0f}%"

        # 2. Take profit
        elif price >= pos["entry_price"] * (1 + PARAMS["take_profit_pct"]):
            exit_reason = f"take_profit_{PARAMS['take_profit_pct']*100:.0f}%"

        # 3. Trailing stop
        elif price <= pos["high_water_mark"] * (1 - PARAMS["trailing_stop_pct"]):
            exit_reason = f"trailing_stop_{PARAMS['trailing_stop_pct']*100:.0f}%"

        # 4. Max holding period
        elif days >= PARAMS["max_holding_steps"]:
            exit_reason = f"max_holding_{PARAMS['max_holding_steps']}d"

        if exit_reason:
            proceeds = pos["shares"] * price
            fee = proceeds * fee_rate
            tax = (proceeds - pos["cost_basis"]) * tax_rate if proceeds > pos["cost_basis"] else 0
            net_proceeds = proceeds - fee - tax
            pnl = net_proceeds - pos["cost_basis"] - pos["fee_paid"]
            pnl_pct = pnl / (pos["cost_basis"] + pos["fee_paid"]) * 100

            state["cash"] += net_proceeds

            trade_record = {
                "code": code,
                "name": pos["name"],
                "entry_date": pos["entry_date"],
                "exit_date": current_date,
                "entry_price": pos["entry_price"],
                "exit_price": price,
                "shares": pos["shares"],
                "cost_basis": pos["cost_basis"],
                "fee_total": pos["fee_paid"] + fee,
                "net_proceeds": round(net_proceeds, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "days_held": days,
                "exit_reason": exit_reason,
                "ml_score": pos.get("ml_score", 0),
            }
            closed.append(trade_record)
            state["trades"].append(trade_record)
            print(f"  [SELL] {code} {pos['name']:<14s} | {pos['shares']:>4d} @ ₩{price:,.0f} | P&L: {pnl:+,.0f} ({pnl_pct:+.2f}%) | {exit_reason}")
        else:
            remaining.append(pos)

    state["positions"] = remaining
    return closed


def compute_equity(state: dict[str, Any], prices: dict[str, dict]) -> float:
    """Compute total equity = cash + sum(position market value)."""
    positions_value = 0
    for pos in state["positions"]:
        code = pos["code"]
        if code in prices:
            positions_value += pos["shares"] * prices[code]["close"]
        else:
            positions_value += pos["shares"] * pos["entry_price"]  # fallback
    return state["cash"] + positions_value


def compute_stats(state: dict[str, Any]) -> dict:
    """Compute summary statistics from closed trades."""
    trades = state["trades"]
    if not trades:
        return {
            "total_trades": 0,
            "win_rate_pct": 0,
            "avg_pnl_pct": 0,
            "total_pnl": 0,
            "best_trade_pct": 0,
            "worst_trade_pct": 0,
        }
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    total_pnl = sum(t["pnl"] for t in trades)
    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 1),
        "avg_pnl_pct": round(sum(t["pnl_pct"] for t in trades) / len(trades), 2),
        "total_pnl": round(total_pnl, 2),
        "best_trade_pct": max(t["pnl_pct"] for t in trades),
        "worst_trade_pct": min(t["pnl_pct"] for t in trades),
        "avg_holding_days": round(sum(t["days_held"] for t in trades) / len(trades), 1),
    }


def generate_report(
    state: dict[str, Any],
    prices: dict[str, dict],
    new_entries: list[dict],
    closed_trades: list[dict],
    equity: float,
    forward_report: Path | None,
) -> str:
    """Generate Telegram-friendly daily report."""
    now = _now_kst()
    starting = PARAMS["starting_cash"]
    total_return = (equity / starting - 1) * 100
    stats = compute_stats(state)

    # Open positions detail
    positions_detail = []
    unrealized_pnl = 0
    for pos in state["positions"]:
        code = pos["code"]
        cur_price = prices.get(code, {}).get("close", pos["entry_price"])
        mv = pos["shares"] * cur_price
        cost = pos["cost_basis"] + pos["fee_paid"]
        upnl = mv - cost
        upnl_pct = upnl / cost * 100
        unrealized_pnl += upnl
        positions_detail.append(
            f"  {pos['name']:<14s} {pos['shares']:>4d}주 @ ₩{pos['entry_price']:,.0f}→₩{cur_price:,.0f} "
            f"| {upnl:+,.0f} ({upnl_pct:+.1f}%) | {pos['days_held']}일"
        )

    lines = [
        f"📊 **가상 포트폴리오 일일 리포트**",
        f"📅 {now.strftime('%Y-%m-%d %H:%M KST')}",
        "",
        f"**총 자산**: ₩{equity:,.0f} (시드 ₩{starting:,} → {total_return:+.2f}%)",
        f"**현금**: ₩{state['cash']:,.0f}",
        f"**보유 포지션**: {len(state['positions'])}개 (미실현 P&L: {unrealized_pnl:+,.0f})",
        f"**오늘 매수**: {len(new_entries)}건 | **오늘 매도**: {len(closed_trades)}건",
        "",
    ]

    if new_entries:
        lines.append("**🟢 신규 매수**")
        for pos in new_entries:
            lines.append(f"  {pos['name']} {pos['shares']}주 @ ₩{pos['entry_price']:,.0f}")
        lines.append("")

    if closed_trades:
        lines.append("**🔴 청산**")
        for t in closed_trades:
            lines.append(
                f"  {t['name']:<14s} {t['pnl']:+,.0f} ({t['pnl_pct']:+.1f}%) "
                f"| {t['exit_reason']} | {t['days_held']}일"
            )
        lines.append("")

    if positions_detail:
        lines.append("**📈 보유 중**")
        lines.extend(positions_detail)
        lines.append("")

    lines.extend([
        f"**누적 통계** ({stats['total_trades']}거래)",
        f"  승률: {stats['win_rate_pct']}% | 평균수익률: {stats['avg_pnl_pct']:+.2f}%",
        f"  누적 실현 P&L: {stats['total_pnl']:+,.0f}",
        f"  평균 보유: {stats.get('avg_holding_days', 0)}일",
        f"  최고/최저: {stats.get('best_trade_pct', 0):+.1f}% / {stats.get('worst_trade_pct', 0):+.1f}%",
        "",
        f"⚙️ 진입: 상위{PARAMS['entry_top_n']}종목, 자본의{PARAMS['cash_fraction_per_entry']*100:.0f}%배분, 최대₩{PARAMS['max_notional']:,}",
        f"⚙️ 청산: 손절{PARAMS['stop_loss_pct']*100:.0f}% 익절{PARAMS['take_profit_pct']*100:.0f}% 추적{PARAMS['trailing_stop_pct']*100:.0f}% 최대{PARAMS['max_holding_steps']}일",
    ])

    if forward_report:
        lines.append(f"\n📁 후보출처: {forward_report.name}")
    lines.append("\n_Research/paper only. No live orders._")

    return "\n".join(lines)


def run(target_date: str | None = None, skip_entries: bool = False) -> dict:
    """Main entry point: run one day of paper portfolio."""
    now = _now_kst()
    today = target_date or _today_str()
    print(f"=== Paper Portfolio Run — {now.strftime('%Y-%m-%d %H:%M KST')} (as-of: {today}) ===")

    state = load_state()
    print(f"  Cash: ₩{state['cash']:,.0f} | Positions: {len(state['positions'])} | Trades: {len(state['trades'])}")

    # Determine which codes we need prices for: held positions + forward tracking candidates
    needed_codes = set(str(p["code"]).zfill(6) for p in state["positions"])

    # Load forward tracking candidates to know which codes we might buy
    forward_report = None
    candidates = []
    if not skip_entries:
        forward_report = find_forward_report()
        if forward_report:
            print(f"  Forward report: {forward_report.name}")
            candidates = load_forward_candidates(forward_report)
            for c in candidates:
                needed_codes.add(str(c["code"]).zfill(6))

    print(f"  Codes needing live prices: {len(needed_codes)} ({', '.join(sorted(needed_codes)[:8])}{'...' if len(needed_codes) > 8 else ''})")

    # Fetch LIVE prices from yfinance (replaces stale panel data)
    print("\nFetching live prices from yfinance...")
    prices = fetch_live_prices(list(needed_codes))
    if prices:
        sample_code = list(prices.keys())[0]
        print(f"  Fetched {len(prices)} codes | latest date: {prices[sample_code]['date']}")
    else:
        print("  WARNING: No live prices fetched, falling back to panel")

    if not prices:
        # Fallback: load stale panel
        print("Loading fallback panel data...")
        panel = load_panel_prices()
        as_of_ts = pd.Timestamp(today)
        latest_panel_date = combined[combined["Date"] <= as_of_ts]["Date"].max() if 'combined' in dir() else panel["Date"].max()
        prices = get_latest_prices(panel, as_of=latest_panel_date)

    price_date = list(prices.values())[0]["date"] if prices else today

    # 1. Check exits first (before new entries)
    print(f"\nChecking exits (price as-of {price_date})...")
    closed_trades = execute_exits(state, prices, price_date)
    state["closed_trades_today"] = len(closed_trades)

    # 2. Execute entries
    new_entries = []
    if not skip_entries and candidates:
        print(f"\nExecuting entries ({len(candidates)} candidates)...")
        new_entries = execute_entries(state, candidates, prices, price_date)
    elif skip_entries:
        print("\nSkipping entries (--skip-entries)")

    # 3. Compute equity
    equity = compute_equity(state, prices)
    state["last_total_equity"] = round(equity, 2)

    # 4. Record daily equity
    daily_entry = {
        "date": price_date,
        "cash": round(state["cash"], 2),
        "positions_value": round(equity - state["cash"], 2),
        "total_equity": round(equity, 2),
        "return_pct": round((equity / PARAMS["starting_cash"] - 1) * 100, 2),
    }
    state["daily_equity"].append(daily_entry)
    # Keep last 365
    state["daily_equity"] = state["daily_equity"][-365:]

    state["last_run_date"] = _today_str()

    # 5. Save
    save_state(state)

    # 6. Generate report
    report = generate_report(state, prices, new_entries, closed_trades, equity, forward_report)
    report_path = DAILY_REPORT_DIR / f"paper_portfolio_{_today_str()}.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"\nReport saved: {report_path}")

    # Save JSON summary for cron consumption
    summary = {
        "date": _today_str(),
        "as_of_price_date": price_date,
        "total_equity": round(equity, 2),
        "cash": round(state["cash"], 2),
        "positions_count": len(state["positions"]),
        "new_entries": len(new_entries),
        "closed_trades": len(closed_trades),
        "total_return_pct": round((equity / PARAMS["starting_cash"] - 1) * 100, 2),
        "stats": compute_stats(state),
    }
    summary_path = DAILY_REPORT_DIR / f"paper_portfolio_{_today_str()}.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 60)
    print(report)
    print("=" * 60)

    # Print markers for cron
    print(f"\nPAPER_PORTFOLIO_EQUITY={equity:.0f}")
    print(f"PAPER_PORTFOLIO_RETURN_PCT={summary['total_return_pct']}")
    print(f"PAPER_PORTFOLIO_POSITIONS={len(state['positions'])}")
    print(f"PAPER_PORTFOLIO_NEW_ENTRIES={len(new_entries)}")
    print(f"PAPER_PORTFOLIO_CLOSED={len(closed_trades)}")
    print(f"PAPER_PORTFOLIO_REPORT={report_path}")

    return summary


def rebuild_state():
    """Initialize fresh state."""
    if STATE_PATH.exists():
        backup = STATE_DIR / f"paper_portfolio_state_backup_{_now_kst().strftime('%Y%m%d%H%M%S')}.json"
        STATE_PATH.rename(backup)
        print(f"Backed up old state to {backup}")
    state = {
        "created_at": _now_kst().isoformat(),
        "cash": PARAMS["starting_cash"],
        "positions": [],
        "trades": [],
        "last_run_date": None,
        "daily_equity": [],
    }
    save_state(state)
    print(f"Fresh state created. Cash: ₩{state['cash']:,}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="TOSS Paper Portfolio Engine")
    parser.add_argument("--date", default=None, help="Target date YYYY-MM-DD")
    parser.add_argument("--skip-entries", action="store_true", help="Skip new entries, only update MTM/exits")
    parser.add_argument("--rebuild", action="store_true", help="Reset to fresh state")
    parser.add_argument("--status", action="store_true", help="Show current status only")
    args = parser.parse_args()

    if args.rebuild:
        rebuild_state()
        sys.exit(0)

    if args.status:
        state = load_state()
        stats = compute_stats(state)
        print(json.dumps({
            "cash": state["cash"],
            "positions": len(state["positions"]),
            "total_trades": len(state["trades"]),
            "last_run_date": state.get("last_run_date"),
            "last_equity": state.get("last_total_equity"),
            "stats": stats,
        }, indent=2, ensure_ascii=False))
        sys.exit(0)

    result = run(target_date=args.date, skip_entries=args.skip_entries)
    print(f"\nDone. Total equity: ₩{result['total_equity']:,.0f} ({result['total_return_pct']:+.2f}%)")
