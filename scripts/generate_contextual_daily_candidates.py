from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "config" / "generated_policies" / "contextual_daily_policy_seed20260607.json"
OUT_DIR = ROOT / "reports" / "trade_candidates"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def load_policy(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def prepare_features(panel: pd.DataFrame) -> pd.DataFrame:
    data = panel.copy()
    data["Date"] = pd.to_datetime(data["Date"])
    data = data.sort_values(["code", "Date"]).reset_index(drop=True)
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        data[col] = pd.to_numeric(data[col], errors="coerce")
    g = data.groupby("code", group_keys=False)
    data["ret_cc"] = g["Close"].pct_change(fill_method=None)
    data["mom_1d"] = g["Close"].shift(1) / g["Close"].shift(2) - 1
    data["mom_3d"] = g["Close"].shift(1) / g["Close"].shift(4) - 1
    data["mom_5d"] = g["Close"].shift(1) / g["Close"].shift(6) - 1
    data["mom_10d"] = g["Close"].shift(1) / g["Close"].shift(11) - 1
    data["mom_20d"] = g["Close"].shift(1) / g["Close"].shift(21) - 1
    data["vol_10d"] = g["ret_cc"].transform(lambda s: s.shift(1).rolling(10).std())
    data["vol_20d"] = g["ret_cc"].transform(lambda s: s.shift(1).rolling(20).std())
    data["raw_dollar_volume"] = data["Close"] * data["Volume"]
    data["dollar_volume"] = data.groupby("code")["raw_dollar_volume"].shift(1)

    market = data.pivot_table(index="Date", columns="code", values="Close").sort_index()
    market_eq = market.pct_change(fill_method=None).mean(axis=1, skipna=True).fillna(0)
    market_close = (1 + market_eq).cumprod()
    regime = pd.DataFrame({"Date": market_close.index, "market_ret": market_eq.values, "market_close": market_close.values})
    regime["market_mom_20d"] = regime["market_close"].shift(1) / regime["market_close"].shift(21) - 1
    regime["market_vol_20d"] = regime["market_ret"].shift(1).rolling(20).std()
    vol_median = regime["market_vol_20d"].median()
    regime["market_regime"] = "flat"
    regime.loc[regime["market_mom_20d"] > 0.02, "market_regime"] = "up"
    regime.loc[regime["market_mom_20d"] < -0.02, "market_regime"] = "down"
    regime["vol_regime"] = "low_vol"
    regime.loc[regime["market_vol_20d"] > vol_median, "vol_regime"] = "high_vol"
    regime["situation"] = regime["market_regime"] + "_" + regime["vol_regime"]
    return data.merge(regime[["Date", "situation", "market_mom_20d", "market_vol_20d"]], on="Date", how="left")


def score(frame: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    base = frame[params["momentum_col"]] / frame[params["vol_col"]].replace(0, pd.NA)
    return -base if params["mode"] == "reversal" else base


def krx_tick_size(price: float) -> int:
    """Return a conservative KRX tick size for a share price."""
    if price < 2_000:
        return 1
    if price < 5_000:
        return 5
    if price < 20_000:
        return 10
    if price < 50_000:
        return 50
    if price < 200_000:
        return 100
    if price < 500_000:
        return 500
    return 1_000


def round_up_to_tick(price: float) -> int:
    tick = krx_tick_size(price)
    return int(((float(price) + tick - 1) // tick) * tick)


def buy_limit_price(reference_close: float, *, aggressiveness_pct: float = 0.005) -> int:
    return round_up_to_tick(float(reference_close) * (1.0 + aggressiveness_pct))


def whole_share_quantity(budget_krw: float, limit_price: float) -> int:
    if budget_krw <= 0 or limit_price <= 0:
        return 0
    return int(float(budget_krw) // float(limit_price))


def generate(policy: dict[str, Any], panel: pd.DataFrame, as_of: str | None = None) -> dict[str, Any]:
    data = prepare_features(panel)
    date = pd.Timestamp(as_of) if as_of else data["Date"].max()
    todays = data[data["Date"] == date].copy()
    if todays.empty:
        raise ValueError(f"no panel rows for as_of={date.date()}")
    situation = str(todays["situation"].dropna().iloc[0]) if todays["situation"].notna().any() else "unknown"
    approved = policy.get("situations", {})
    risk = policy.get("risk_gates", {})
    if situation not in approved:
        return {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "as_of": str(date.date()),
            "status": "NO_TRADE",
            "reason": f"situation_not_approved:{situation}",
            "situation": situation,
            "live_order_submitted": False,
            "orders": [],
        }
    params = approved[situation]
    todays["score"] = score(todays, params)
    eligible = todays[
        todays["score"].notna()
        & todays["dollar_volume"].ge(params["min_dollar_volume"])
        & todays["Open"].gt(0)
        & todays["Close"].gt(0)
    ].copy()
    if params["mode"] == "momentum":
        eligible = eligible[eligible[params["momentum_col"]] >= params["min_abs_momentum"]].copy()
    else:
        eligible = eligible[eligible[params["momentum_col"]] <= -params["min_abs_momentum"]].copy()
    picks = eligible.sort_values("score", ascending=False).head(int(params["top_n"]))
    max_total = float(risk.get("max_total_notional_krw", 1_000_000))
    max_per = float(risk.get("max_notional_krw_per_position", 100_000))
    cash_fraction = float(risk.get("cash_fraction_per_entry", 0.0) or 0.0)
    portfolio_value = float(
        risk.get("portfolio_value_krw")
        or risk.get("assumed_initial_cash_krw")
        or max_total
    )
    cash_fraction_budget = portfolio_value * cash_fraction if cash_fraction > 0 else max_per
    per_position_budget = min(max_per, cash_fraction_budget, max_total / max(len(picks), 1))
    limit_aggressiveness_pct = float(risk.get("buy_limit_aggressiveness_pct", 0.005))
    orders = []
    skipped_orders = []
    planned_total = 0.0
    for _, row in picks.iterrows():
        reference_close = float(row["Close"])
        limit_price = buy_limit_price(reference_close, aggressiveness_pct=limit_aggressiveness_pct)
        remaining_total_budget = max_total - planned_total
        budget_krw = min(per_position_budget, max_per, cash_fraction_budget, remaining_total_budget)
        quantity = whole_share_quantity(budget_krw, limit_price)
        symbol = str(row["code"]).zfill(6)
        name = row.get("name", "")
        if quantity < 1:
            skipped_orders.append({
                "symbol": symbol,
                "name": name,
                "side": "BUY",
                "skip_reason": "cannot_buy_one_whole_share_with_budget",
                "budget_krw": round(budget_krw, 0),
                "reference_close": reference_close,
                "limit_price": limit_price,
                "minimum_required_krw": limit_price,
                "score": float(row["score"]),
            })
            continue
        estimated_notional = float(quantity * limit_price)
        planned_total += estimated_notional
        orders.append({
            "symbol": symbol,
            "name": name,
            "side": "BUY",
            "mode": "manual_draft_only",
            "not_live_order": True,
            "order_type": "LIMIT",
            "budget_krw": round(budget_krw, 0),
            "notional_krw": round(estimated_notional, 0),
            "quantity": quantity,
            "limit_price": limit_price,
            "reference_close": reference_close,
            "score": float(row["score"]),
            "reason": f"approved_situation={situation}; whole_share_qty={quantity}; limit_price={limit_price}; budget_krw={round(budget_krw, 0)}; {params['mode']} {params['momentum_col']}/{params['vol_col']}; exit={params['return_col']}",
        })
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "as_of": str(date.date()),
        "status": "CANDIDATES" if orders else "NO_TRADE",
        "situation": situation,
        "policy_id": policy.get("policy_id"),
        "execution_stage": "paper_or_manual_draft_only",
        "live_order_submitted": False,
        "requires_manual_confirmation": True,
        "risk_gates": risk,
        "orders": orders,
        "skipped_orders": skipped_orders,
        "planned_total_notional_krw": round(planned_total, 0),
        "sizing_model": "whole_share_limit_order_budget_to_quantity_with_cash_fraction_cap",
        "sizing_inputs": {
            "max_total_notional_krw": max_total,
            "max_notional_krw_per_position": max_per,
            "cash_fraction_per_entry": cash_fraction,
            "portfolio_value_krw": portfolio_value,
            "cash_fraction_budget_krw": cash_fraction_budget,
            "per_position_budget_krw": per_position_budget,
        },
        "disclaimer": "Research-only candidate draft. Not investment advice. Do not submit real orders without separate approval and broker readiness checks.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate paper/manual daily candidates from the contextual policy. No live orders.")
    parser.add_argument("--policy", default=str(DEFAULT_POLICY))
    parser.add_argument("--panel", default=None, help="OHLCV panel CSV. Defaults to policy universe_source.")
    parser.add_argument("--as-of", default=None, help="YYYY-MM-DD; defaults to latest panel date")
    args = parser.parse_args()
    policy = load_policy(Path(args.policy))
    panel_path = Path(args.panel) if args.panel else Path(policy["universe_source"])
    panel = pd.read_csv(panel_path, dtype={"code": str}, parse_dates=["Date"])
    result = generate(policy, panel, as_of=args.as_of)
    out = OUT_DIR / f"candidates_{result['as_of']}_{policy.get('policy_id', 'policy')}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print(f"CANDIDATES_JSON={out}")


if __name__ == "__main__":
    main()
