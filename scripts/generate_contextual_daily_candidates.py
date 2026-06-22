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
    data["ret_cc"] = g["Close"].pct_change()
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
    market_eq = market.pct_change().mean(axis=1, skipna=True).fillna(0)
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
    per_position = min(max_per, max_total / max(len(picks), 1))
    orders = []
    for _, row in picks.iterrows():
        orders.append({
            "symbol": str(row["code"]).zfill(6),
            "name": row.get("name", ""),
            "side": "BUY",
            "mode": "manual_draft_only",
            "not_live_order": True,
            "notional_krw": round(per_position, 0),
            "reference_close": float(row["Close"]),
            "score": float(row["score"]),
            "reason": f"approved_situation={situation}; {params['mode']} {params['momentum_col']}/{params['vol_col']}; exit={params['return_col']}",
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
