from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from forward_tracking_daily import OPTIMAL
from toss_alpha.daily.features import compute_features
from toss_alpha.research.profit_loop import _prepare_panel, _reasons_for_trade

PANEL_PATH = ROOT / "reports/backtests/practical_universe_panel.parquet"
SOURCE_DIR = ROOT / "reports/harness/paper_portfolio/full_sim_2026_from_0101"
REPORT_DIR = ROOT / "reports/harness"

VARIANTS: list[dict[str, Any]] = [
    {"variant_id": "baseline", "thresholds": None},
    {
        "variant_id": "leak_free_current_equivalent",
        "thresholds": {
            "max_gap_pct": 0.08,
            "max_intraday_range_pct": 0.22,
            "min_dollar_volume_krw": 1_000_000_000.0,
            "max_prev_volatility_20d": 0.10,
            "min_tail_risk_flags": 1,
        },
    },
    {
        "variant_id": "two_factor_moderate",
        "thresholds": {
            "max_gap_pct": 0.06,
            "max_intraday_range_pct": 0.08,
            "min_dollar_volume_krw": 1_000_000_000.0,
            "max_prev_volatility_20d": 0.06,
            "max_prev_volume_surge_20d": 3.0,
            "min_tail_risk_flags": 2,
        },
    },
    {
        "variant_id": "two_factor_strict",
        "thresholds": {
            "max_gap_pct": 0.05,
            "max_intraday_range_pct": 0.06,
            "min_dollar_volume_krw": 1_000_000_000.0,
            "max_prev_volatility_20d": 0.05,
            "max_prev_volume_surge_20d": 2.0,
            "min_tail_risk_flags": 2,
        },
    },
]


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


def finite_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        if pd.notna(result):
            return result
    except Exception:
        pass
    return default


def prepare_inputs() -> tuple[pd.DataFrame, pd.DataFrame, dict[tuple[pd.Timestamp, str], dict[str, float]]]:
    panel = pd.read_parquet(PANEL_PATH)
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    panel["Date"] = pd.to_datetime(panel["Date"])
    panel = panel.sort_values(["code", "Date"]).reset_index(drop=True)
    prepared = _prepare_panel(panel)
    features = compute_features(panel)
    features["code"] = features["code"].astype(str).str.zfill(6)
    features["Date"] = pd.to_datetime(features["Date"])
    risk_columns = [
        "Date", "code", "Open", "prev_close", "prev_intraday_range_pct",
        "prev_dollar_volume", "prev_volume_surge_20d", "prev_volatility_20d",
    ]
    risk_lookup = {
        (pd.Timestamp(row["Date"]), str(row["code"]).zfill(6)): {key: row[key] for key in risk_columns if key not in {"Date", "code"}}
        for _, row in prepared[risk_columns].iterrows()
    }
    signals = pd.read_csv(SOURCE_DIR / "signals_daily_top10.csv", dtype={"code": str}, parse_dates=["date"])
    signals["code"] = signals["code"].astype(str).str.zfill(6)
    return features, signals, risk_lookup


def replay(
    panel: pd.DataFrame,
    signals: pd.DataFrame,
    risk_lookup: dict[tuple[pd.Timestamp, str], dict[str, float]],
    variant: dict[str, Any],
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    params = dict(OPTIMAL)
    fee_rate = finite_float(params["transaction_cost_bps"]) / 2.0 / 10_000.0
    starting_cash = 1_000_000.0
    cash = starting_cash
    positions: list[Position] = []
    trades: list[dict[str, Any]] = []
    replay_buys: list[dict[str, Any]] = []
    equities: list[float] = []
    buy_orders = 0
    blocked_entries = 0
    blocked_reasons: dict[str, int] = {}
    thresholds = variant.get("thresholds")

    replay_dates = sorted(
        pd.read_csv(SOURCE_DIR / "daily_equity.csv", parse_dates=["date"])["date"].drop_duplicates()
    )
    panel = panel[panel["Date"].isin(replay_dates)].copy()
    price_by_date = {
        date: {
            str(row["code"]).zfill(6): {
                "open": finite_float(row["Open"]),
                "high": finite_float(row["High"]),
                "low": finite_float(row["Low"]),
                "close": finite_float(row["Close"]),
            }
            for _, row in group.iterrows()
        }
        for date, group in panel.groupby("Date")
    }
    signal_by_date = {date: group.sort_values("rank").to_dict(orient="records") for date, group in signals.groupby("date")}

    for date in replay_dates:
        date = pd.Timestamp(date)
        date_str = date.date().isoformat()
        prices = price_by_date.get(date, {})
        remaining: list[Position] = []
        for pos in positions:
            price = prices.get(pos.code)
            if price is None:
                remaining.append(pos)
                continue
            close = price["close"]
            pos.high_water_mark = max(pos.high_water_mark, price["high"])
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
            if exit_reason is None:
                remaining.append(pos)
                continue
            proceeds = pos.shares * close
            exit_fee = proceeds * fee_rate
            net = proceeds - exit_fee
            pnl = net - pos.cost_basis - pos.entry_fee
            cash += net
            trades.append({
                "variant_id": variant["variant_id"], "date": date_str, "code": pos.code,
                "name": pos.name, "reason": exit_reason, "entry_date": pos.entry_date,
                "entry_price": pos.entry_price, "exit_price": close, "shares": pos.shares,
                "pnl": pnl, "pnl_pct": pnl / (pos.cost_basis + pos.entry_fee) * 100.0,
                "days_held": pos.days_held,
            })
        positions = remaining

        held = {position.code for position in positions}
        opened_today = 0
        slots = max(0, int(params["max_positions"]) - len(positions))
        for candidate in signal_by_date.get(date, []):
            if opened_today >= min(3, slots):
                break
            code = str(candidate["code"]).zfill(6)
            if code in held or code not in prices:
                continue
            if thresholds is not None:
                risk_row = dict(risk_lookup.get((date, code), {}))
                reasons = _reasons_for_trade(pd.Series(risk_row), thresholds)
                if reasons:
                    blocked_entries += 1
                    for reason in reasons:
                        blocked_reasons[reason] = blocked_reasons.get(reason, 0) + 1
                    continue
            close = prices[code]["close"]
            allocation = min(cash * finite_float(params["cash_fraction_per_entry"]), finite_float(params["max_notional"]))
            if allocation < close:
                continue
            shares = int(allocation // close)
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
            positions.append(Position(
                code=code, name=str(candidate.get("name", "—")), entry_date=date_str,
                entry_price=close, shares=shares, cost_basis=cost, entry_fee=fee,
                high_water_mark=prices[code]["high"], ml_score=finite_float(candidate.get("ml_score")),
                rank=int(candidate["rank"]),
            ))
            held.add(code)
            opened_today += 1
            buy_orders += 1
            replay_buys.append({
                "variant_id": variant["variant_id"], "date": date_str, "code": code,
                "entry_rank": int(candidate["rank"]), "shares": shares,
                "price": close, "ml_score": finite_float(candidate.get("ml_score")),
            })

        position_value = sum(position.shares * prices.get(position.code, {}).get("close", position.entry_price) for position in positions)
        equities.append(cash + position_value)

    peak = starting_cash
    max_drawdown = 0.0
    for equity in equities:
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, (equity / peak - 1.0) * 100.0)
    step_returns = [equities[index] / equities[index - 1] - 1.0 for index in range(1, len(equities)) if equities[index - 1] > 0]
    closed = pd.DataFrame(trades)
    one_day = closed[closed["days_held"] == 1] if not closed.empty else closed
    stops = closed[closed["reason"].str.startswith("stop_loss")] if not closed.empty else closed
    summary = {
        "variant_id": variant["variant_id"],
        "thresholds": thresholds,
        "final_equity": round(equities[-1], 2),
        "total_return_pct": round((equities[-1] / starting_cash - 1.0) * 100.0, 4),
        "max_drawdown_pct": round(max_drawdown, 4),
        "closed_trades": int(len(closed)),
        "buy_orders": buy_orders,
        "win_rate_pct": round(float((closed["pnl"] > 0).mean() * 100.0), 2) if not closed.empty else 0.0,
        "realized_pnl": round(float(closed["pnl"].sum()), 2) if not closed.empty else 0.0,
        "stop_losses": int(len(stops)),
        "stop_loss_pnl": round(float(stops["pnl"].sum()), 2) if not stops.empty else 0.0,
        "one_day_trades": int(len(one_day)),
        "one_day_pnl": round(float(one_day["pnl"].sum()), 2) if not one_day.empty else 0.0,
        "one_day_loss_trades": int(((one_day["pnl"] < 0).sum())) if not one_day.empty else 0,
        "blocked_entry_attempts": blocked_entries,
        "blocked_reasons": blocked_reasons,
        "sharpe_proxy": round(mean(step_returns) / pstdev(step_returns) * (252 ** 0.5), 4) if len(step_returns) > 1 and pstdev(step_returns) > 0 else 0.0,
    }
    return summary, closed, pd.DataFrame(replay_buys)


def main() -> None:
    panel, signals, risk_lookup = prepare_inputs()
    summaries = []
    trade_frames = []
    buy_frames = []
    for variant in VARIANTS:
        summary, trades, buys = replay(panel, signals, risk_lookup, variant)
        summaries.append(summary)
        trade_frames.append(trades)
        buy_frames.append(buys)

    source_summary = json.loads((SOURCE_DIR / "summary.json").read_text(encoding="utf-8"))
    baseline = summaries[0]
    parity = {
        "return_abs_diff_pct": abs(float(baseline["total_return_pct"]) - float(source_summary["total_return_pct"])),
        "closed_trade_count_diff": int(baseline["closed_trades"]) - int(source_summary["closed_trades"]),
        "passed": abs(float(baseline["total_return_pct"]) - float(source_summary["total_return_pct"])) <= 0.0001
        and int(baseline["closed_trades"]) == int(source_summary["closed_trades"]),
    }
    for summary in summaries:
        summary["return_delta_vs_baseline_pct"] = round(summary["total_return_pct"] - baseline["total_return_pct"], 4)
        summary["mdd_delta_vs_baseline_pct"] = round(summary["max_drawdown_pct"] - baseline["max_drawdown_pct"], 4)
        summary["promotion_eligible"] = bool(
            summary["variant_id"] != "baseline"
            and parity["passed"]
            and summary["total_return_pct"] >= baseline["total_return_pct"]
            and summary["max_drawdown_pct"] >= baseline["max_drawdown_pct"]
            and summary["one_day_pnl"] >= baseline["one_day_pnl"]
            and summary["closed_trades"] >= 60
        )

    eligible = [summary for summary in summaries if summary["promotion_eligible"]]
    verdict = "PROMOTE_FOR_WALKFORWARD" if eligible else "REJECT_NO_POLICY_CHANGE"
    generated = datetime.now(timezone.utc)
    stem = f"daily_entry_veto_replay_{generated.strftime('%Y%m%dT%H%M%SZ')}"
    json_path = REPORT_DIR / f"{stem}.json"
    md_path = REPORT_DIR / f"{stem}.md"
    trades_path = REPORT_DIR / f"{stem}_trades.csv"
    buys_path = REPORT_DIR / f"{stem}_buys.csv"
    payload = {
        "generated_at_utc": generated.isoformat(),
        "research_only": True,
        "live_order_submitted": False,
        "policy_written": False,
        "source_dir": str(SOURCE_DIR),
        "baseline_parity": parity,
        "summaries": summaries,
        "verdict": verdict,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.concat(trade_frames, ignore_index=True).to_csv(trades_path, index=False)
    pd.concat(buy_frames, ignore_index=True).to_csv(buys_path, index=False)
    table = pd.DataFrame(summaries)[[
        "variant_id", "total_return_pct", "max_drawdown_pct", "closed_trades",
        "one_day_pnl", "one_day_loss_trades", "stop_loss_pnl", "blocked_entry_attempts",
        "promotion_eligible",
    ]].to_markdown(index=False)
    md_path.write_text("\n".join([
        "# Daily paper entry-veto replay",
        "",
        "Research only. No policy writes or live orders.",
        "",
        f"- Baseline parity: {parity}",
        f"- Verdict: **{verdict}**",
        "- Features: same-day opening gap; all other veto inputs lagged one trading session.",
        "",
        table,
        "",
        f"- JSON: `{json_path}`",
        f"- Trades: `{trades_path}`",
        f"- Buys: `{buys_path}`",
    ]) + "\n", encoding="utf-8")
    print(json.dumps({"parity": parity, "verdict": verdict, "summaries": summaries, "json": str(json_path), "markdown": str(md_path), "trades": str(trades_path), "buys": str(buys_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
