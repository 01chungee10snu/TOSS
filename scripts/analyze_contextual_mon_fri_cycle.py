from __future__ import annotations

import itertools
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from optimize_contextual_daily_strategy import (
    OUT_DIR,
    PANEL_CSV,
    POLICY_DIR,
    RANDOM_SEED,
    START,
    END,
    TRAIN_END,
    TEST_START,
    MIN_TRADES_TRAIN,
    MIN_TRADES_TEST,
    ROUND_TRIP_COST,
    prepare,
    build_score,
    objective_score,
)


ROOT = Path(__file__).resolve().parents[1]
STARTING_CASH = 1_000_000.0


def safe_perf(daily: pd.DataFrame, mask: pd.Series | None = None) -> dict[str, Any]:
    d = daily.copy() if mask is None else daily.loc[mask].copy()
    if d.empty:
        return {"days": 0, "active_days": 0, "total_trades": 0, "final_value_krw": STARTING_CASH, "total_return_pct": 0.0, "cagr_pct": 0.0, "max_drawdown_pct": 0.0, "sharpe": 0.0, "win_rate_pct": 0.0, "profit_factor": None}
    ret = d["daily_return"].fillna(0.0)
    equity = STARTING_CASH * (1 + ret).cumprod()
    final = float(equity.iloc[-1])
    years = max((d["Date"].iloc[-1] - d["Date"].iloc[0]).days / 365.25, 1 / 365.25)
    ratio = max(final / STARTING_CASH, 1e-12)
    cagr = ratio ** (1 / years) - 1
    peak = equity.cummax()
    mdd = float((equity / peak - 1).min()) if len(equity) else 0.0
    sd = float(ret.std())
    sharpe = 0.0 if sd == 0 or pd.isna(sd) else float(ret.mean() / sd * (52 ** 0.5))
    wins = int((ret > 0).sum())
    losses = int((ret < 0).sum())
    gross_profit = float(ret[ret > 0].sum())
    gross_loss = abs(float(ret[ret < 0].sum()))
    return {
        "days": int(len(d)),
        "active_days": int((d["picks"] > 0).sum()),
        "total_trades": int(d["picks"].sum()),
        "final_value_krw": round(final, 2),
        "total_return_pct": round((ratio - 1) * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "max_drawdown_pct": round(mdd * 100, 2),
        "sharpe": round(sharpe, 3),
        "win_rate_pct": round(wins / max(wins + losses, 1) * 100, 2),
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else None,
    }


def add_mon_fri_returns(data: pd.DataFrame) -> pd.DataFrame:
    d = data.copy()
    d["weekday"] = d["Date"].dt.weekday
    d["week_key"] = d["Date"].dt.to_period("W-FRI").astype(str)

    monday = d[d["weekday"] == 0][["code", "week_key", "Open"]].rename(columns={"Open": "monday_open"})
    friday = d[d["weekday"] == 4][["code", "week_key", "Close"]].rename(columns={"Close": "friday_close"})
    weekly = monday.merge(friday, on=["code", "week_key"], how="inner")
    weekly["monfri_open_to_fri_close_ret"] = weekly["friday_close"] / weekly["monday_open"] - 1
    d = d.merge(weekly[["code", "week_key", "monday_open", "friday_close", "monfri_open_to_fri_close_ret"]], on=["code", "week_key"], how="left")
    return d


def simulate_mon_fri(data: pd.DataFrame, params: dict[str, Any], situation: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    d = data.copy()
    if situation is not None:
        d = d[d["situation"] == situation].copy()
    d = d[d["weekday"] == 0].copy()
    d["score"] = build_score(d, params["momentum_col"], params["vol_col"], params["mode"])
    eligible = d[
        d["score"].notna()
        & d["monfri_open_to_fri_close_ret"].notna()
        & d["dollar_volume"].ge(params["min_dollar_volume"])
        & d["Open"].gt(0)
        & d["friday_close"].gt(0)
    ].copy()
    if params["mode"] == "momentum":
        eligible = eligible[eligible[params["momentum_col"]] >= params["min_abs_momentum"]].copy()
    else:
        eligible = eligible[eligible[params["momentum_col"]] <= -params["min_abs_momentum"]].copy()
    eligible["rank"] = eligible.groupby("Date")["score"].rank(method="first", ascending=False)
    picks = eligible[eligible["rank"] <= params["top_n"]].copy()
    picks["trade_return"] = picks["monfri_open_to_fri_close_ret"] - ROUND_TRIP_COST
    daily = picks.groupby("Date").agg(picks=("code", "count"), daily_return=("trade_return", "mean")).reset_index()
    all_dates = pd.DataFrame({"Date": sorted(d["Date"].dropna().unique())})
    daily = all_dates.merge(daily, on="Date", how="left")
    daily["picks"] = daily["picks"].fillna(0).astype(int)
    daily["daily_return"] = daily["daily_return"].fillna(0.0)
    return daily, picks


def main() -> None:
    panel = pd.read_csv(PANEL_CSV, dtype={"code": str}, parse_dates=["Date"])
    data = add_mon_fri_returns(prepare(panel))

    grid = []
    for momentum_col, vol_col, mode, top_n, min_dv, min_abs_mom in itertools.product(
        ["mom_1d", "mom_5d", "mom_20d"],
        ["vol_20d"],
        ["momentum", "reversal"],
        [3, 10],
        [100_000_000, 1_000_000_000],
        [0.0, 0.03],
    ):
        grid.append({
            "momentum_col": momentum_col,
            "vol_col": vol_col,
            "mode": mode,
            "return_col": "monfri_open_to_fri_close_ret",
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
            daily, _ = simulate_mon_fri(data, params, situation=situation)
            train_mask = daily["Date"] <= TRAIN_END
            test_mask = daily["Date"] >= TEST_START
            train = safe_perf(daily, train_mask)
            if train["total_trades"] < MIN_TRADES_TRAIN:
                continue
            test = safe_perf(daily, test_mask)
            objective = objective_score(train, test)
            row = {
                "situation": situation,
                **params,
                "objective": round(objective, 4),
                "train_test_return_gap_pct": round(abs(train["total_return_pct"] - test["total_return_pct"]), 2),
                "train_test_sharpe_gap": round(abs(train["sharpe"] - test["sharpe"]), 3),
                **{f"train_{k}": v for k, v in train.items()},
                **{f"test_{k}": v for k, v in test.items()},
            }
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
        and row["test_total_trades"] >= MIN_TRADES_TEST
        and row["train_test_return_gap_pct"] <= 20
    }
    approved_df = pd.DataFrame(approved_by_situation.values()).sort_values("objective", ascending=False) if approved_by_situation else pd.DataFrame()

    all_dates = pd.DataFrame({"Date": sorted(data.loc[data["weekday"] == 0, "Date"].dropna().unique())})
    combined_daily_parts = []
    combined_picks_parts = []
    for situation, row in approved_by_situation.items():
        params = {k: row[k] for k in ["momentum_col", "vol_col", "mode", "return_col", "top_n", "min_dollar_volume", "min_abs_momentum"]}
        daily, picks = simulate_mon_fri(data, params, situation=situation)
        daily["situation"] = situation
        picks["situation"] = situation
        combined_daily_parts.append(daily)
        combined_picks_parts.append(picks)
    if combined_daily_parts:
        combined_daily = pd.concat(combined_daily_parts, ignore_index=True).sort_values("Date")
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
    combined_train = safe_perf(combined_daily, combined_daily["Date"] <= TRAIN_END)
    combined_test = safe_perf(combined_daily, combined_daily["Date"] >= TEST_START)
    combined_all = safe_perf(combined_daily)

    stem = f"random500_seed{RANDOM_SEED}_contextual_optimizer_mon_fri_cycle_{START}_{END}"
    all_csv = OUT_DIR / f"{stem}_all_trials.csv"
    selected_csv = OUT_DIR / f"{stem}_selected_by_situation.csv"
    daily_csv = OUT_DIR / f"{stem}_combined_daily_curve.csv"
    picks_csv = OUT_DIR / f"{stem}_combined_picks.csv"
    json_path = OUT_DIR / f"{stem}.json"
    md_path = OUT_DIR / f"{stem}.md"
    policy_path = POLICY_DIR / f"contextual_mon_fri_policy_seed{RANDOM_SEED}.json"

    all_rows.to_csv(all_csv, index=False)
    selected_df.to_csv(selected_csv, index=False)
    combined_daily.to_csv(daily_csv, index=False)
    combined_picks.to_csv(picks_csv, index=False)

    policy = {
        "policy_id": f"contextual_mon_fri_policy_seed{RANDOM_SEED}",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "paper_or_manual_draft_only",
        "live_trading_enabled": False,
        "entry_exit_cycle": {"buy": "monday_open", "sell": "friday_close_same_week"},
        "universe_source": str(PANEL_CSV),
        "selection": "per market situation, use previous-close features only; Monday open entry and same-week Friday close exit",
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
        "disclaimer": "Research-only optimized Monday-buy Friday-sell policy. Not investment advice."
    }
    policy_path.write_text(json.dumps(policy, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "cycle": {"buy": "monday_open", "sell": "friday_close_same_week"},
        "grid_size": len(grid),
        "situations": situations,
        "approved_situations": approved_by_situation,
        "selected_by_situation_before_validation_gate": selected_by_situation,
        "combined_train": combined_train,
        "combined_test": combined_test,
        "combined_all": combined_all,
        "policy_path": str(policy_path),
        "outputs": {"all_trials": str(all_csv), "selected": str(selected_csv), "daily": str(daily_csv), "picks": str(picks_csv)},
        "disclaimer": "Research-only Monday-buy Friday-sell contextual optimization; not investment advice; live orders not submitted."
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    lines = [
        f"# Contextual Monday-buy Friday-sell optimizer — random 500 seed {RANDOM_SEED}",
        "",
        "Research-only. 실주문 없음. 투자 조언 아님.",
        "",
        "## Method",
        f"- Panel: {PANEL_CSV}",
        f"- Entry/Exit: Monday open -> same-week Friday close",
        f"- Train: <= {TRAIN_END.date()}, test: >= {TEST_START.date()}",
        f"- Grid size: {len(grid)} parameter combinations per situation",
        "- Objective: train core score + test bonus - train/test gap penalty - weak-test penalty",
        "",
        "## Combined approved contextual policy performance",
        f"- Train: {combined_train}",
        f"- Test: {combined_test}",
        f"- All: {combined_all}",
        "",
        "## Approved policy by situation",
    ]
    report_df = approved_df if not approved_df.empty else selected_df.head(0)
    for row in report_df.to_dict(orient='records'):
        lines.append(f"- {row['situation']}: {row['mode']} {row['momentum_col']}/{row['vol_col']} -> monfri_open_to_fri_close_ret, top_n={row['top_n']}, min_dv={row['min_dollar_volume']}, min_abs_mom={row['min_abs_momentum']}; train return {row['train_total_return_pct']}%, test return {row['test_total_return_pct']}%, return gap {row['train_test_return_gap_pct']}%, test MDD {row['test_max_drawdown_pct']}%, test Sharpe {row['test_sharpe']}")
    if approved_df.empty:
        lines.append("- none: no situation passed the train/test approval gates")
    lines.extend(["", "## Rejected best-by-objective candidates"])
    for row in selected_df.to_dict(orient='records'):
        lines.append(f"- {row['situation']}: train {row['train_total_return_pct']}%, test {row['test_total_return_pct']}%, return gap {row['train_test_return_gap_pct']}%, test MDD {row['test_max_drawdown_pct']}%, test Sharpe {row['test_sharpe']}")
    lines.extend(["", "## Outputs", f"- policy: {policy_path}", f"- all_trials: {all_csv}", f"- selected_by_situation: {selected_csv}", f"- combined_daily_curve: {daily_csv}", f"- combined_picks: {picks_csv}", f"- json: {json_path}"])
    md_path.write_text("\n".join(lines) + "\n", encoding='utf-8')

    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    print(f"REPORT_MD={md_path}")
    print(f"POLICY={policy_path}")


if __name__ == '__main__':
    main()
