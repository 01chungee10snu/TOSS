from __future__ import annotations

import itertools
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "reports" / "backtests"
POLICY_DIR = ROOT / "config" / "generated_policies"
OUT_DIR.mkdir(parents=True, exist_ok=True)
POLICY_DIR.mkdir(parents=True, exist_ok=True)

START = "2022-01-01"
END = "2025-12-31"
RANDOM_SEED = 20260607
PANEL_CSV = OUT_DIR / f"random500_seed{RANDOM_SEED}_{START}_{END}_ohlcv_panel.csv"
STARTING_CASH = 1_000_000.0
FEE_BPS = 1.5
SLIPPAGE_BPS = 5.0
SELL_TAX_BPS = 18.0
ROUND_TRIP_COST = (FEE_BPS + SLIPPAGE_BPS + FEE_BPS + SLIPPAGE_BPS + SELL_TAX_BPS) / 10_000.0
MIN_TRADES_TRAIN = 60
TRAIN_END = pd.Timestamp("2024-12-31")
TEST_START = pd.Timestamp("2025-01-01")


def max_dd(series: pd.Series) -> float:
    peak = series.cummax()
    return float((series / peak - 1).min()) if len(series) else 0.0


def sharpe(ret: pd.Series) -> float:
    sd = float(ret.std())
    if sd == 0 or math.isnan(sd):
        return 0.0
    return float(ret.mean() / sd * math.sqrt(252))


def perf(daily: pd.DataFrame, mask: pd.Series | None = None) -> dict[str, Any]:
    d = daily.copy() if mask is None else daily.loc[mask].copy()
    if d.empty:
        return {"days": 0, "active_days": 0, "total_trades": 0, "total_return_pct": 0.0, "cagr_pct": 0.0, "max_drawdown_pct": 0.0, "sharpe": 0.0, "win_rate_pct": 0.0, "profit_factor": None}
    equity = STARTING_CASH * (1 + d["daily_return"].fillna(0)).cumprod()
    years = max((d["Date"].iloc[-1] - d["Date"].iloc[0]).days / 365.25, 1e-9)
    final = float(equity.iloc[-1])
    ret = d["daily_return"].fillna(0)
    wins = int((ret > 0).sum())
    losses = int((ret < 0).sum())
    gross_profit = float(ret[ret > 0].sum())
    gross_loss = abs(float(ret[ret < 0].sum()))
    return {
        "days": int(len(d)),
        "active_days": int((d["picks"] > 0).sum()),
        "total_trades": int(d["picks"].sum()),
        "final_value_krw": round(final, 2),
        "total_return_pct": round((final / STARTING_CASH - 1) * 100, 2),
        "cagr_pct": round(((final / STARTING_CASH) ** (1 / years) - 1) * 100, 2),
        "max_drawdown_pct": round(max_dd(equity) * 100, 2),
        "sharpe": round(sharpe(ret), 3),
        "win_rate_pct": round(wins / max(wins + losses, 1) * 100, 2),
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else None,
    }


def prepare(panel: pd.DataFrame) -> pd.DataFrame:
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
    data["dollar_volume"] = g.apply(lambda x: (x["Close"] * x["Volume"]).shift(1)).reset_index(level=0, drop=True)
    data["oc_ret"] = data["Close"] / data["Open"] - 1
    data["oo_ret"] = g["Open"].shift(-1) / data["Open"] - 1
    data["cc_next_ret"] = g["Close"].shift(-1) / data["Close"] - 1

    # Equal-weight market proxy from the sample, based only on previous closes for regime labelling.
    market = data.pivot_table(index="Date", columns="code", values="Close").sort_index()
    market_eq = market.pct_change().mean(axis=1, skipna=True).fillna(0)
    market_close = (1 + market_eq).cumprod()
    regime = pd.DataFrame({"Date": market_close.index, "market_ret": market_eq.values, "market_close": market_close.values})
    regime["market_mom_20d"] = regime["market_close"].shift(1) / regime["market_close"].shift(21) - 1
    regime["market_vol_20d"] = regime["market_ret"].shift(1).rolling(20).std()
    vol_median = regime.loc[regime["Date"] <= TRAIN_END, "market_vol_20d"].median()
    regime["market_regime"] = "flat"
    regime.loc[regime["market_mom_20d"] > 0.02, "market_regime"] = "up"
    regime.loc[regime["market_mom_20d"] < -0.02, "market_regime"] = "down"
    regime["vol_regime"] = "low_vol"
    regime.loc[regime["market_vol_20d"] > vol_median, "vol_regime"] = "high_vol"
    regime["situation"] = regime["market_regime"] + "_" + regime["vol_regime"]
    return data.merge(regime[["Date", "situation", "market_mom_20d", "market_vol_20d"]], on="Date", how="left")


def build_score(data: pd.DataFrame, momentum_col: str, vol_col: str, mode: str) -> pd.Series:
    if mode == "momentum":
        return data[momentum_col] / data[vol_col].replace(0, pd.NA)
    if mode == "reversal":
        return -data[momentum_col] / data[vol_col].replace(0, pd.NA)
    raise ValueError(mode)


def simulate(data: pd.DataFrame, params: dict[str, Any], situation: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    d = data.copy()
    if situation is not None:
        d = d[d["situation"] == situation].copy()
    d["score"] = build_score(d, params["momentum_col"], params["vol_col"], params["mode"])
    eligible = d[
        d["score"].notna()
        & d[params["return_col"]].notna()
        & d["dollar_volume"].ge(params["min_dollar_volume"])
        & d["Open"].gt(0)
        & d["Close"].gt(0)
    ].copy()
    if params["mode"] == "momentum":
        eligible = eligible[eligible[params["momentum_col"]] >= params["min_abs_momentum"]].copy()
    else:
        eligible = eligible[eligible[params["momentum_col"]] <= -params["min_abs_momentum"]].copy()
    eligible["rank"] = eligible.groupby("Date")["score"].rank(method="first", ascending=False)
    picks = eligible[eligible["rank"] <= params["top_n"]].copy()
    picks["trade_return"] = picks[params["return_col"]] - ROUND_TRIP_COST
    daily = picks.groupby("Date").agg(picks=("code", "count"), daily_return=("trade_return", "mean")).reset_index()
    all_dates = pd.DataFrame({"Date": sorted(d["Date"].dropna().unique())})
    daily = all_dates.merge(daily, on="Date", how="left")
    daily["picks"] = daily["picks"].fillna(0).astype(int)
    daily["daily_return"] = daily["daily_return"].fillna(0.0)
    return daily, picks


def main() -> None:
    if not PANEL_CSV.exists():
        raise FileNotFoundError(f"missing cached panel: {PANEL_CSV}; run daily strategy script first")
    panel = pd.read_csv(PANEL_CSV, dtype={"code": str}, parse_dates=["Date"])
    data = prepare(panel)
    grid = []
    # Keep this first-pass optimizer deliberately compact. The previous daily tests showed
    # high-turnover strategies are fragile, so we search broad families rather than thousands
    # of micro-parameters that would overfit and run slowly.
    for momentum_col, vol_col, mode, return_col, top_n, min_dv, min_abs_mom in itertools.product(
        ["mom_1d", "mom_5d", "mom_20d"],
        ["vol_20d"],
        ["momentum", "reversal"],
        ["oc_ret", "oo_ret", "cc_next_ret"],
        [3, 10],
        [100_000_000, 1_000_000_000],
        [0.0, 0.03],
    ):
        grid.append({
            "momentum_col": momentum_col,
            "vol_col": vol_col,
            "mode": mode,
            "return_col": return_col,
            "top_n": top_n,
            "min_dollar_volume": min_dv,
            "min_abs_momentum": min_abs_mom,
        })

    situations = sorted(s for s in data["situation"].dropna().unique() if isinstance(s, str))
    rows = []
    selected_by_situation = {}
    for situation in situations:
        best = None
        for params in grid:
            daily, _ = simulate(data, params, situation=situation)
            train_mask = daily["Date"] <= TRAIN_END
            test_mask = daily["Date"] >= TEST_START
            train = perf(daily, train_mask)
            if train["total_trades"] < MIN_TRADES_TRAIN:
                continue
            test = perf(daily, test_mask)
            objective = train["sharpe"] + train["cagr_pct"] / 100 + max(train["max_drawdown_pct"], -80) / 100
            row = {"situation": situation, **params, "objective": round(objective, 4), **{f"train_{k}": v for k, v in train.items()}, **{f"test_{k}": v for k, v in test.items()}}
            rows.append(row)
            if best is None or objective > best["objective"]:
                best = row
        if best:
            selected_by_situation[situation] = best

    all_rows = pd.DataFrame(rows).sort_values(["situation", "objective"], ascending=[True, False])
    selected_df = pd.DataFrame(selected_by_situation.values()).sort_values("objective", ascending=False)
    approved_by_situation = {
        situation: row
        for situation, row in selected_by_situation.items()
        if row["train_total_return_pct"] > 0
        and row["test_total_return_pct"] > 0
        and row["test_sharpe"] > 0
        and row["test_max_drawdown_pct"] > -20
    }
    approved_df = pd.DataFrame(approved_by_situation.values()).sort_values("objective", ascending=False) if approved_by_situation else pd.DataFrame()

    # Simulate combined contextual policy: trade only situations that pass both train and test gates.
    # Other regimes are cash. This intentionally favors "do nothing" over overfit negative regimes.
    all_dates = pd.DataFrame({"Date": sorted(data["Date"].dropna().unique())})
    combined_daily_parts = []
    combined_picks_parts = []
    for situation, row in approved_by_situation.items():
        params = {k: row[k] for k in ["momentum_col", "vol_col", "mode", "return_col", "top_n", "min_dollar_volume", "min_abs_momentum"]}
        daily, picks = simulate(data, params, situation=situation)
        daily["situation"] = situation
        picks["situation"] = situation
        combined_daily_parts.append(daily)
        combined_picks_parts.append(picks)
    if combined_daily_parts:
        combined_daily = pd.concat(combined_daily_parts, ignore_index=True).sort_values("Date")
        # There is exactly one situation per date, but aggregate defensively.
        combined_daily = combined_daily.groupby("Date", as_index=False).agg(picks=("picks", "sum"), daily_return=("daily_return", "mean"))
        combined_daily = all_dates.merge(combined_daily, on="Date", how="left")
        combined_daily["picks"] = combined_daily["picks"].fillna(0).astype(int)
        combined_daily["daily_return"] = combined_daily["daily_return"].fillna(0.0)
    else:
        combined_daily = all_dates.copy()
        combined_daily["picks"] = 0
        combined_daily["daily_return"] = 0.0
    combined_daily["equity"] = STARTING_CASH * (1 + combined_daily["daily_return"]).cumprod()
    combined_picks = pd.concat(combined_picks_parts, ignore_index=True) if combined_picks_parts else pd.DataFrame()
    combined_train = perf(combined_daily, combined_daily["Date"] <= TRAIN_END)
    combined_test = perf(combined_daily, combined_daily["Date"] >= TEST_START)
    combined_all = perf(combined_daily)

    stem = f"random500_seed{RANDOM_SEED}_contextual_optimizer_{START}_{END}"
    all_csv = OUT_DIR / f"{stem}_all_trials.csv"
    selected_csv = OUT_DIR / f"{stem}_selected_by_situation.csv"
    daily_csv = OUT_DIR / f"{stem}_combined_daily_curve.csv"
    picks_csv = OUT_DIR / f"{stem}_combined_picks.csv"
    json_path = OUT_DIR / f"{stem}.json"
    md_path = OUT_DIR / f"{stem}.md"
    policy_path = POLICY_DIR / f"contextual_daily_policy_seed{RANDOM_SEED}.json"
    all_rows.to_csv(all_csv, index=False)
    selected_df.to_csv(selected_csv, index=False)
    combined_daily.to_csv(daily_csv, index=False)
    combined_picks.to_csv(picks_csv, index=False)

    policy = {
        "policy_id": f"contextual_daily_policy_seed{RANDOM_SEED}",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "paper_or_manual_draft_only",
        "live_trading_enabled": False,
        "universe_source": str(PANEL_CSV),
        "selection": "per market situation, use previous-close features only; produce same-day or next-day manual candidates depending on return_col",
        "cost_assumption_round_trip_bps": ROUND_TRIP_COST * 10_000,
        "risk_gates": {
            "max_positions": 10,
            "max_notional_krw_per_position": 100_000,
            "max_total_notional_krw": 1_000_000,
            "require_manual_confirmation": True,
            "block_if_live_trading_enabled": True,
        },
        "situations": approved_by_situation,
        "rejected_situations_best_trials": selected_by_situation,
        "validation": {"train_end": str(TRAIN_END.date()), "test_start": str(TEST_START.date()), "combined_train": combined_train, "combined_test": combined_test, "combined_all": combined_all},
        "disclaimer": "Research-only optimized policy. Not investment advice. Must paper trade before any real order.",
    }
    policy_path.write_text(json.dumps(policy, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), "grid_size": len(grid), "situations": situations, "approved_situations": approved_by_situation, "selected_by_situation_before_validation_gate": selected_by_situation, "combined_train": combined_train, "combined_test": combined_test, "combined_all": combined_all, "policy_path": str(policy_path), "outputs": {"all_trials": str(all_csv), "selected": str(selected_csv), "daily": str(daily_csv), "picks": str(picks_csv)}, "disclaimer": "Research-only contextual optimization; not investment advice; live orders not submitted."}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    lines = [
        f"# Contextual daily strategy optimizer — random 500 seed {RANDOM_SEED}",
        "",
        "Research-only. 실주문 없음. 투자 조언 아님.",
        "",
        "## Method",
        f"- Panel: {PANEL_CSV}",
        f"- Train: <= {TRAIN_END.date()}, test: >= {TEST_START.date()}",
        f"- Grid size: {len(grid)} parameter combinations per situation",
        "- Situations: sample-market 20D momentum up/flat/down × high/low volatility",
        "- Objective: train Sharpe + CAGR penalty/bonus + drawdown penalty, with minimum train trades",
        "- Live execution: disabled; generated policy is paper/manual-draft only",
        "",
        "## Combined approved contextual policy performance",
        f"- Train: {combined_train}",
        f"- Test: {combined_test}",
        f"- All: {combined_all}",
        "",
        "## Approved policy by situation",
    ]
    report_df = approved_df if not approved_df.empty else selected_df.head(0)
    for row in report_df.to_dict(orient="records"):
        lines.append(f"- {row['situation']}: {row['mode']} {row['momentum_col']}/{row['vol_col']} -> {row['return_col']}, top_n={row['top_n']}, min_dv={row['min_dollar_volume']}, min_abs_mom={row['min_abs_momentum']}; train return {row['train_total_return_pct']}%, test return {row['test_total_return_pct']}%, test MDD {row['test_max_drawdown_pct']}%, test Sharpe {row['test_sharpe']}")
    if approved_df.empty:
        lines.append("- none: no situation passed the train/test approval gates")
    lines.extend(["", "## Rejected best-by-train candidates",])
    for row in selected_df.to_dict(orient="records"):
        lines.append(f"- {row['situation']}: train {row['train_total_return_pct']}%, test {row['test_total_return_pct']}%, test MDD {row['test_max_drawdown_pct']}%, test Sharpe {row['test_sharpe']}")
    lines.extend(["", "## Outputs", f"- policy: {policy_path}", f"- all_trials: {all_csv}", f"- selected_by_situation: {selected_csv}", f"- combined_daily_curve: {daily_csv}", f"- combined_picks: {picks_csv}", f"- json: {json_path}"])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    print(f"REPORT_MD={md_path}")
    print(f"POLICY={policy_path}")


if __name__ == "__main__":
    main()
