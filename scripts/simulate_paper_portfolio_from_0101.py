"""Replay forward-tracking paper portfolio from a chosen start date.

Generates the counterfactual data requested by the paper/research workflow:
"if we had started on 1/1, what signals, orders, positions, and equity would
we have accumulated?"

Research/paper only. No live orders.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from toss_alpha.daily.features import FEATURE_COLUMNS, compute_features
from toss_alpha.daily.macro_signals import CACHE_PATH as MACRO_CACHE, get_macro_regime
from fusion_3layer_backtest import (
    SENT_CSV,
    build_sentiment_map,
    compute_macro_adjusted_scores,
    predict_ml_scores,
    train_ml_model,
)
from forward_tracking_daily import OPTIMAL, load_name_map

PANEL_PATH = ROOT / "reports" / "backtests" / "practical_universe_panel.parquet"
DEFAULT_OUT_ROOT = ROOT / "reports" / "harness" / "paper_portfolio" / "full_sim_2026_from_0101"


@dataclass
class Position:
    code: str
    name: str
    entry_date: str
    entry_price: float
    shares: int
    cost_basis: float
    entry_fee: float
    high_water_mark: float
    ml_score: float
    rank: int
    days_held: int = 0


def finite_float(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        if math.isfinite(x):
            return x
    except Exception:
        pass
    return default


def load_panel() -> pd.DataFrame:
    panel = pd.read_parquet(PANEL_PATH)
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    panel["Date"] = pd.to_datetime(panel["Date"])
    return panel.sort_values(["code", "Date"]).reset_index(drop=True)


def load_macro() -> pd.DataFrame | None:
    if MACRO_CACHE.exists():
        return pd.read_parquet(MACRO_CACHE)
    return None


def build_fusion_scores(features: pd.DataFrame, trade_year: int) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    train_end_year = trade_year - 1
    model = train_ml_model(features, train_end_year)
    ml_map = predict_ml_scores(model, features, trade_year)

    macro_df = load_macro()
    sent_map = None
    if SENT_CSV.exists():
        sent_df = pd.read_csv(SENT_CSV, parse_dates=["date"])
        raw = build_sentiment_map(sent_df)
        sent_map = {d: scores for d, scores in raw.items() if pd.Timestamp(d).year == trade_year}

    fusion_map = compute_macro_adjusted_scores(ml_map, macro_df, sent_map)
    info = {
        "train_end_year": train_end_year,
        "trade_year": trade_year,
        "ml_prediction_dates": len(ml_map),
        "fusion_prediction_dates": len(fusion_map),
        "sentiment_dates": len(sent_map or {}),
        "macro_cache": str(MACRO_CACHE) if MACRO_CACHE.exists() else None,
    }
    return fusion_map, info


def build_fusion_scores_for_years(
    features: pd.DataFrame,
    trade_years: list[int],
) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    """Build expanding-walk-forward fusion scores for every replay year."""
    combined: dict[str, dict[str, float]] = {}
    yearly: list[dict[str, Any]] = []
    for trade_year in sorted(set(trade_years)):
        year_map, info = build_fusion_scores(features, trade_year)
        combined.update(year_map)
        yearly.append(info)
    return combined, {
        "mode": "expanding_walk_forward_by_calendar_year",
        "years": yearly,
        "total_fusion_prediction_dates": len(combined),
    }


def daily_candidates(
    features: pd.DataFrame,
    fusion_map: dict[str, dict[str, float]],
    date: pd.Timestamp,
    name_map: dict[str, str],
    top_n: int = 10,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    date_str = date.date().isoformat()
    rows = features[features["Date"] == date].copy()
    if rows.empty:
        return [], {"status": "no_rows"}
    scores = fusion_map.get(date_str, {})
    rows["ml_score"] = rows["code"].map(lambda c: finite_float(scores.get(str(c).zfill(6), 0.0)))
    rows = rows.sort_values("ml_score", ascending=False).reset_index(drop=True)
    rows["rank"] = range(1, len(rows) + 1)

    macro = get_macro_regime(date, load_macro())
    before = len(rows)
    if macro.get("status") == "risk_off" and "vol_60d" in rows.columns:
        vol_median = rows["vol_60d"].median()
        rows = rows[rows["vol_60d"] <= vol_median].copy()
        rows = rows.sort_values("ml_score", ascending=False).reset_index(drop=True)
    else:
        vol_median = None

    out: list[dict[str, Any]] = []
    for _, r in rows.head(top_n).iterrows():
        code = str(r["code"]).zfill(6)
        out.append(
            {
                "date": date_str,
                "rank": int(r["rank"]),
                "code": code,
                "name": str(name_map.get(code, "—")),
                "ml_score": round(finite_float(r.get("ml_score")), 8),
                "close": finite_float(r.get("Close")),
                "open": finite_float(r.get("Open")),
                "high": finite_float(r.get("High")),
                "low": finite_float(r.get("Low")),
                "volume": int(finite_float(r.get("Volume"))),
                "vol_60d": round(finite_float(r.get("vol_60d")), 8),
                "rsi_14": round(finite_float(r.get("rsi_14"), 50.0), 4),
                "ret_60d": round(finite_float(r.get("ret_60d")), 8),
                "ret_120d": round(finite_float(r.get("ret_120d")), 8),
            }
        )
    return out, {
        "status": "ok",
        "macro_regime": macro,
        "candidates_before_macro_gate": before,
        "candidates_after_macro_gate": len(rows),
        "risk_off_vol_median": None if vol_median is None else finite_float(vol_median),
    }


def price_lookup(features: pd.DataFrame, date: pd.Timestamp) -> dict[str, dict[str, float]]:
    rows = features[features["Date"] == date]
    return {
        str(r["code"]).zfill(6): {
            "open": finite_float(r.get("Open")),
            "high": finite_float(r.get("High")),
            "low": finite_float(r.get("Low")),
            "close": finite_float(r.get("Close")),
        }
        for _, r in rows.iterrows()
    }


def compute_equity(cash: float, positions: list[Position], prices: dict[str, dict[str, float]]) -> tuple[float, float]:
    pos_value = 0.0
    for p in positions:
        px = prices.get(p.code, {}).get("close", p.entry_price)
        pos_value += p.shares * px
    return cash + pos_value, pos_value


def simulate(features: pd.DataFrame, start: str, end: str, out_root: Path) -> dict[str, Any]:
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    name_map = load_name_map()

    dates = [d for d in sorted(features["Date"].drop_duplicates()) if start_ts <= d <= end_ts]
    if not dates:
        raise RuntimeError(f"no trading dates for {start} ~ {end}")
    trade_years = sorted({int(d.year) for d in dates})
    fusion_map, model_info = build_fusion_scores_for_years(features, trade_years)

    params = dict(OPTIMAL)
    starting_cash = 1_000_000.0
    cash = starting_cash
    positions: list[Position] = []
    trades: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    daily: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []

    fee_rate = finite_float(params["transaction_cost_bps"]) / 2.0 / 10_000.0
    entry_top_n = 3

    for step_idx, date in enumerate(dates, start=1):
        date_str = date.date().isoformat()
        prices = price_lookup(features, date)
        top10, day_info = daily_candidates(features, fusion_map, date, name_map, top_n=10)
        for s in top10:
            s.update({"macro_status": day_info.get("macro_regime", {}).get("status")})
            signals.append(s)

        # Exits first, then entries: same as a practical end-of-day paper loop.
        remaining: list[Position] = []
        closed_today = 0
        for pos in positions:
            px = prices.get(pos.code)
            if not px:
                remaining.append(pos)
                continue
            close = px["close"]
            high = px["high"]
            if high > pos.high_water_mark:
                pos.high_water_mark = high
            pos.days_held += 1

            exit_reason = None
            if close <= pos.entry_price * (1.0 - params["stop_loss_pct"]):
                exit_reason = f"stop_loss_{params['stop_loss_pct']*100:.0f}%"
            elif close >= pos.entry_price * (1.0 + params["take_profit_pct"]):
                exit_reason = f"take_profit_{params['take_profit_pct']*100:.0f}%"
            elif close <= pos.high_water_mark * (1.0 - params["trailing_stop_pct"]):
                exit_reason = f"trailing_stop_{params['trailing_stop_pct']*100:.0f}%"
            elif pos.days_held >= params["max_holding_steps"]:
                exit_reason = f"max_holding_{params['max_holding_steps']}d"

            if exit_reason:
                proceeds = pos.shares * close
                exit_fee = proceeds * fee_rate
                net = proceeds - exit_fee
                pnl = net - pos.cost_basis - pos.entry_fee
                pnl_pct = pnl / (pos.cost_basis + pos.entry_fee) * 100.0
                cash += net
                rec = {
                    "date": date_str,
                    "side": "SELL",
                    "code": pos.code,
                    "name": pos.name,
                    "price": round(close, 4),
                    "shares": pos.shares,
                    "gross_value": round(proceeds, 2),
                    "fee": round(exit_fee, 2),
                    "net_cash_flow": round(net, 2),
                    "reason": exit_reason,
                    "entry_date": pos.entry_date,
                    "entry_price": round(pos.entry_price, 4),
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl_pct, 4),
                    "days_held": pos.days_held,
                    "ml_score": pos.ml_score,
                    "entry_rank": pos.rank,
                }
                orders.append(rec)
                trades.append(rec)
                closed_today += 1
            else:
                remaining.append(pos)
        positions = remaining

        held = {p.code for p in positions}
        opened_today = 0
        slots = max(0, int(params["max_positions"]) - len(positions))
        for cand in top10:
            if opened_today >= min(entry_top_n, slots):
                break
            code = cand["code"]
            if code in held:
                continue
            px = prices.get(code)
            if not px:
                continue
            close = px["close"]
            allocation = min(cash * finite_float(params["cash_fraction_per_entry"]), finite_float(params["max_notional"]))
            if allocation < close:
                continue
            shares = int(allocation // close)
            if shares <= 0:
                continue
            cost = shares * close
            fee = cost * fee_rate
            total = cost + fee
            if total > cash:
                shares = int(cash // (close * (1.0 + fee_rate)))
                cost = shares * close
                fee = cost * fee_rate
                total = cost + fee
            if shares <= 0 or total > cash:
                continue
            cash -= total
            pos = Position(
                code=code,
                name=cand["name"],
                entry_date=date_str,
                entry_price=close,
                shares=shares,
                cost_basis=cost,
                entry_fee=fee,
                high_water_mark=px["high"],
                ml_score=finite_float(cand["ml_score"]),
                rank=int(cand["rank"]),
            )
            positions.append(pos)
            held.add(code)
            opened_today += 1
            orders.append(
                {
                    "date": date_str,
                    "side": "BUY",
                    "code": code,
                    "name": cand["name"],
                    "price": round(close, 4),
                    "shares": shares,
                    "gross_value": round(cost, 2),
                    "fee": round(fee, 2),
                    "net_cash_flow": round(-total, 2),
                    "reason": "daily_top3_entry",
                    "entry_rank": int(cand["rank"]),
                    "ml_score": finite_float(cand["ml_score"]),
                }
            )

        equity, pos_value = compute_equity(cash, positions, prices)
        daily.append(
            {
                "date": date_str,
                "cash": round(cash, 2),
                "positions_value": round(pos_value, 2),
                "total_equity": round(equity, 2),
                "return_pct": round((equity / starting_cash - 1.0) * 100.0, 6),
                "positions_count": len(positions),
                "signals_count": len(top10),
                "buy_orders": opened_today,
                "sell_orders": closed_today,
                "macro_status": day_info.get("macro_regime", {}).get("status"),
            }
        )
        for pos in positions:
            cur = prices.get(pos.code, {}).get("close", pos.entry_price)
            snapshots.append(
                {
                    "date": date_str,
                    **asdict(pos),
                    "current_price": round(cur, 4),
                    "market_value": round(pos.shares * cur, 2),
                    "unrealized_pnl": round(pos.shares * cur - pos.cost_basis - pos.entry_fee, 2),
                }
            )

    out_root.mkdir(parents=True, exist_ok=True)
    signals_csv = out_root / "signals_daily_top10.csv"
    orders_csv = out_root / "orders.csv"
    trades_csv = out_root / "closed_trades.csv"
    equity_csv = out_root / "daily_equity.csv"
    positions_csv = out_root / "positions_daily.csv"
    summary_json = out_root / "summary.json"
    report_md = out_root / "README.md"

    pd.DataFrame(signals).to_csv(signals_csv, index=False)
    pd.DataFrame(orders).to_csv(orders_csv, index=False)
    pd.DataFrame(trades).to_csv(trades_csv, index=False)
    pd.DataFrame(daily).to_csv(equity_csv, index=False)
    pd.DataFrame(snapshots).to_csv(positions_csv, index=False)

    equities = [r["total_equity"] for r in daily]
    step_rets = [equities[i] / equities[i - 1] - 1.0 for i in range(1, len(equities)) if equities[i - 1] > 0]
    peak = starting_cash
    max_dd = 0.0
    for eq in equities:
        peak = max(peak, eq)
        max_dd = min(max_dd, (eq - peak) / peak * 100.0)
    wins = [t for t in trades if t.get("pnl", 0) > 0]
    losses = [t for t in trades if t.get("pnl", 0) <= 0]
    sharpe = 0.0
    if len(step_rets) > 1 and pstdev(step_rets) > 0:
        sharpe = mean(step_rets) / pstdev(step_rets) * (252 ** 0.5)

    summary = {
        "paper_only": True,
        "live_order_submitted": False,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "panel_path": str(PANEL_PATH),
        "panel_date_min": str(features["Date"].min().date()),
        "panel_date_max": str(features["Date"].max().date()),
        "start_date_requested": start,
        "first_trading_date": daily[0]["date"],
        "last_trading_date": daily[-1]["date"],
        "trading_days": len(daily),
        "params": params | {"starting_cash": starting_cash, "entry_top_n": entry_top_n},
        "model_info": model_info,
        "final_equity_krw": round(equities[-1], 2),
        "total_return_pct": round((equities[-1] / starting_cash - 1.0) * 100.0, 4),
        "max_drawdown_pct": round(max_dd, 4),
        "sharpe_ratio": round(sharpe, 4),
        "orders": len(orders),
        "closed_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(trades) * 100.0, 4) if trades else 0.0,
        "open_positions": len(positions),
        "cash_krw": round(cash, 2),
        "files": {
            "signals_daily_top10": str(signals_csv),
            "orders": str(orders_csv),
            "closed_trades": str(trades_csv),
            "daily_equity": str(equity_csv),
            "positions_daily": str(positions_csv),
            "summary": str(summary_json),
            "report": str(report_md),
        },
    }
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    report_md.write_text(
        "\n".join(
            [
                f"# {start} start counterfactual paper portfolio simulation",
                "",
                "Paper/research only. live_order_submitted: False.",
                "",
                "## Summary",
                f"- Period: {summary['first_trading_date']} ~ {summary['last_trading_date']} ({summary['trading_days']} trading days)",
                f"- Initial cash: ₩{starting_cash:,.0f}",
                f"- Final equity: ₩{summary['final_equity_krw']:,.0f}",
                f"- Total return: {summary['total_return_pct']:.2f}%",
                f"- Sharpe: {summary['sharpe_ratio']:.2f}",
                f"- Max drawdown: {summary['max_drawdown_pct']:.2f}%",
                f"- Closed trades: {summary['closed_trades']} / win rate {summary['win_rate_pct']:.1f}%",
                f"- Open positions: {summary['open_positions']}",
                "",
                "## Files",
                *[f"- {k}: `{v}`" for k, v in summary["files"].items()],
                "",
            ]
        ),
        encoding="utf-8",
    )
    return summary


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default=None, help="default: latest panel date")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_ROOT))
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    panel = load_panel()
    features = compute_features(panel).sort_values(["code", "Date"]).reset_index(drop=True)
    # compute_features already creates the 5-day forward label used for training.
    start_year = pd.Timestamp(args.start).year
    end = args.end
    if end is None:
        end = str(features["Date"].max().date())
    summary = simulate(features, args.start, end, Path(args.out_dir))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
