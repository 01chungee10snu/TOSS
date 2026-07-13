from __future__ import annotations

import json
from collections import Counter
from math import sqrt
from pathlib import Path
from typing import Any

import pandas as pd


def summarize_picks_performance(picks: pd.DataFrame, *, group_col: str) -> dict[str, Any]:
    if picks.empty:
        return {
            "periods": 0,
            "total_trades": 0,
            "total_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "win_rate_pct": 0.0,
            "sharpe_proxy": 0.0,
        }
    grouped = picks.groupby(group_col)["trade_return"].mean().reset_index(name="period_return")
    equity = (1 + grouped["period_return"]).cumprod()
    drawdown = equity / equity.cummax() - 1
    wins = (grouped["period_return"] > 0).sum()
    losses = (grouped["period_return"] < 0).sum()
    sd = float(grouped["period_return"].std())
    sharpe_proxy = float(grouped["period_return"].mean() / sd * sqrt(max(len(grouped), 1))) if sd else 0.0
    return {
        "periods": int(len(grouped)),
        "total_trades": int(len(picks)),
        "total_return_pct": round((float(equity.iloc[-1]) - 1.0) * 100, 2),
        "max_drawdown_pct": round(float(drawdown.min()) * 100, 2),
        "win_rate_pct": round(float(wins / max(wins + losses, 1) * 100), 2),
        "sharpe_proxy": round(sharpe_proxy, 3),
    }


def apply_extra_cost_bps(picks: pd.DataFrame, *, extra_round_trip_bps: float) -> pd.DataFrame:
    stressed = picks.copy()
    if "trade_return" not in stressed.columns:
        raise KeyError("trade_return column is required")
    stressed["trade_return"] = stressed["trade_return"].astype(float) - float(extra_round_trip_bps) / 10_000.0
    return stressed


def _prepare_panel(panel: pd.DataFrame) -> pd.DataFrame:
    data = panel.copy()
    data["Date"] = pd.to_datetime(data["Date"])
    data["code"] = data["code"].astype(str).str.zfill(6)
    data = data.sort_values(["code", "Date"]).reset_index(drop=True)
    data["prev_close"] = data.groupby("code")["Close"].shift(1)
    data["dollar_volume"] = data["Close"] * data["Volume"]
    data["ret_cc"] = data.groupby("code")["Close"].pct_change()
    data["prev_volatility_20d"] = data.groupby("code")["ret_cc"].transform(lambda s: s.shift(1).rolling(20, min_periods=1).std())
    # Monday-open decisions may only use observations known before the open,
    # except for the opening gap itself. Keep session-level risk inputs lagged.
    data["intraday_range_pct"] = (data["High"] - data["Low"]) / data["prev_close"]
    data["volume_median_20d"] = data.groupby("code")["Volume"].transform(
        lambda s: s.shift(1).rolling(20, min_periods=5).median()
    )
    data["volume_surge_20d"] = data["Volume"] / data["volume_median_20d"]
    data["prev_intraday_range_pct"] = data.groupby("code")["intraday_range_pct"].shift(1)
    data["prev_dollar_volume"] = data.groupby("code")["dollar_volume"].shift(1)
    data["prev_volume_surge_20d"] = data.groupby("code")["volume_surge_20d"].shift(1)
    return data


def _reasons_for_trade(row: pd.Series, thresholds: dict[str, float]) -> list[str]:
    reasons: list[str] = []
    prev_close = float(row.get("prev_close") or 0.0)
    open_px = float(row.get("Open") or 0.0)
    if prev_close <= 0 or open_px <= 0:
        reasons.append("invalid_price_inputs")
        return reasons
    gap_pct = abs(open_px / prev_close - 1.0)
    prev_intraday_range = row.get("prev_intraday_range_pct")
    if gap_pct > thresholds["max_gap_pct"]:
        reasons.append("excessive_gap")
    tail_risk_reasons: list[str] = []
    if pd.notna(prev_intraday_range) and float(prev_intraday_range) > thresholds["max_intraday_range_pct"]:
        tail_risk_reasons.append("excessive_prev_intraday_range")
    prev_vol = row.get("prev_volatility_20d")
    if pd.notna(prev_vol) and float(prev_vol) > thresholds["max_prev_volatility_20d"]:
        tail_risk_reasons.append("excessive_prev_volatility_20d")
    max_prev_volume_surge = thresholds.get("max_prev_volume_surge_20d")
    prev_volume_surge = row.get("prev_volume_surge_20d")
    if max_prev_volume_surge is not None and pd.notna(prev_volume_surge) and float(prev_volume_surge) > float(max_prev_volume_surge):
        tail_risk_reasons.append("excessive_prev_volume_surge_20d")
    min_tail_flags = max(1, int(thresholds.get("min_tail_risk_flags", 1)))
    if len(tail_risk_reasons) >= min_tail_flags:
        reasons.extend(tail_risk_reasons)
    if float(row.get("prev_dollar_volume") or 0.0) < thresholds["min_dollar_volume_krw"]:
        reasons.append("low_prev_dollar_volume")
    return reasons


def evaluate_fast_veto_variant(
    *,
    picks: pd.DataFrame,
    panel: pd.DataFrame,
    thresholds: dict[str, float],
    group_col: str,
) -> dict[str, Any]:
    if picks.empty:
        return {
            "thresholds": thresholds,
            "kept_trades": 0,
            "blocked_trades": 0,
            "blocked_counts_by_reason": {},
            "performance": summarize_picks_performance(picks, group_col=group_col),
            "kept_picks": picks.copy(),
        }
    prepared = _prepare_panel(panel)
    frame = picks.copy()
    frame["Date"] = pd.to_datetime(frame["Date"])
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    risk_cols = [
        "Open", "High", "Low", "Close", "Volume", "prev_close", "dollar_volume",
        "prev_intraday_range_pct", "prev_dollar_volume", "prev_volume_surge_20d",
        "prev_volatility_20d",
    ]
    overlap_cols = [col for col in risk_cols if col in frame.columns]
    if overlap_cols:
        frame = frame.drop(columns=overlap_cols)
    merged = frame.merge(
        prepared[["Date", "code", *risk_cols]],
        on=["Date", "code"],
        how="left",
    )
    kept_rows = []
    blocked_counts: Counter[str] = Counter()
    for _, row in merged.iterrows():
        reasons = _reasons_for_trade(row, thresholds)
        if reasons:
            blocked_counts.update(reasons)
        else:
            kept_rows.append(row.to_dict())
    kept = pd.DataFrame(kept_rows)
    if kept.empty:
        kept = merged.head(0).copy()
    performance = summarize_picks_performance(kept, group_col=group_col)
    return {
        "thresholds": dict(thresholds),
        "kept_trades": int(len(kept)),
        "blocked_trades": int(len(merged) - len(kept)),
        "blocked_counts_by_reason": dict(blocked_counts),
        "performance": performance,
        "kept_picks": kept,
    }


def build_fast_veto_grid() -> list[dict[str, Any]]:
    return [
        {
            "variant_id": "veto_base",
            "thresholds": {
                "max_gap_pct": 0.08,
                "max_intraday_range_pct": 0.15,
                "min_dollar_volume_krw": 10_000_000.0,
                "max_prev_volatility_20d": 0.10,
            },
        },
        {
            "variant_id": "veto_looser_range",
            "thresholds": {
                "max_gap_pct": 0.10,
                "max_intraday_range_pct": 0.22,
                "min_dollar_volume_krw": 10_000_000.0,
                "max_prev_volatility_20d": 0.12,
            },
        },
        {
            "variant_id": "veto_higher_liquidity",
            "thresholds": {
                "max_gap_pct": 0.10,
                "max_intraday_range_pct": 0.20,
                "min_dollar_volume_krw": 1_000_000_000.0,
                "max_prev_volatility_20d": 0.12,
            },
        },
        {
            "variant_id": "veto_higher_liquidity_looser_range",
            "thresholds": {
                "max_gap_pct": 0.08,
                "max_intraday_range_pct": 0.22,
                "min_dollar_volume_krw": 1_000_000_000.0,
                "max_prev_volatility_20d": 0.10,
            },
        },
        {
            "variant_id": "veto_looser_all",
            "thresholds": {
                "max_gap_pct": 0.12,
                "max_intraday_range_pct": 0.25,
                "min_dollar_volume_krw": 0.0,
                "max_prev_volatility_20d": 0.20,
            },
        },
    ]


def build_walkforward_folds(years: list[int], *, min_train_years: int = 1) -> list[dict[str, Any]]:
    uniq = sorted({int(y) for y in years})
    folds: list[dict[str, Any]] = []
    for idx in range(min_train_years, len(uniq)):
        folds.append({"train_years": uniq[:idx], "test_year": uniq[idx]})
    return folds


def evaluate_variant(
    *,
    picks: pd.DataFrame,
    panel: pd.DataFrame,
    variant: dict[str, Any],
    group_col: str,
) -> dict[str, Any]:
    thresholds = variant.get("thresholds")
    if thresholds is None:
        return {
            "variant_id": variant["variant_id"],
            "thresholds": None,
            "kept_trades": int(len(picks)),
            "blocked_trades": 0,
            "blocked_counts_by_reason": {},
            "performance": summarize_picks_performance(picks, group_col=group_col),
            "kept_picks": picks.copy(),
        }
    result = evaluate_fast_veto_variant(picks=picks, panel=panel, thresholds=thresholds, group_col=group_col)
    result["variant_id"] = variant["variant_id"]
    return result


def run_walkforward_variant_selection(
    *,
    picks: pd.DataFrame,
    panel: pd.DataFrame,
    variants: list[dict[str, Any]],
    group_col: str,
    min_train_years: int = 1,
) -> dict[str, Any]:
    data = picks.copy()
    data["Date"] = pd.to_datetime(data["Date"])
    data["year"] = data["Date"].dt.year
    folds = build_walkforward_folds(sorted(data["year"].dropna().astype(int).unique().tolist()), min_train_years=min_train_years)
    fold_results: list[dict[str, Any]] = []
    oos_kept_parts: list[pd.DataFrame] = []
    for fold in folds:
        train = data[data["year"].isin(fold["train_years"])].copy()
        test = data[data["year"] == int(fold["test_year"])].copy()
        train_branches = []
        train_eval_by_variant: dict[str, dict[str, Any]] = {}
        for variant in variants:
            evaluated = evaluate_variant(picks=train, panel=panel, variant=variant, group_col=group_col)
            train_eval_by_variant[variant["variant_id"]] = evaluated
            train_branches.append(
                {
                    "branch_id": variant["variant_id"],
                    "cycle": "monfri",
                    "method": "walkforward_train",
                    "thresholds": evaluated.get("thresholds"),
                    "performance": evaluated["performance"],
                }
            )
        selected = choose_best_branch(train_branches)
        chosen_variant_id = selected["branch_id"]
        chosen_variant = next(v for v in variants if v["variant_id"] == chosen_variant_id)
        test_eval = evaluate_variant(picks=test, panel=panel, variant=chosen_variant, group_col=group_col)
        kept_test = test_eval.get("kept_picks", pd.DataFrame()).copy()
        if not kept_test.empty:
            oos_kept_parts.append(kept_test)
        fold_results.append(
            {
                "train_years": fold["train_years"],
                "test_year": fold["test_year"],
                "selected_variant_id": chosen_variant_id,
                "selected_train_score": selected["score"],
                "train_performance": train_eval_by_variant[chosen_variant_id]["performance"],
                "test_performance": test_eval["performance"],
                "test_kept_trades": test_eval["kept_trades"],
                "test_blocked_trades": test_eval["blocked_trades"],
                "test_blocked_counts_by_reason": test_eval.get("blocked_counts_by_reason", {}),
            }
        )
    oos_kept = pd.concat(oos_kept_parts, ignore_index=True) if oos_kept_parts else data.head(0).copy()
    return {
        "folds": fold_results,
        "aggregate_oos": summarize_picks_performance(oos_kept, group_col=group_col),
        "aggregate_oos_kept_picks": oos_kept,
    }


def evaluate_fixed_variant_walkforward(
    *,
    picks: pd.DataFrame,
    panel: pd.DataFrame,
    variant: dict[str, Any],
    group_col: str,
    min_train_years: int = 1,
) -> dict[str, Any]:
    data = picks.copy()
    data["Date"] = pd.to_datetime(data["Date"])
    data["year"] = data["Date"].dt.year
    folds = build_walkforward_folds(sorted(data["year"].dropna().astype(int).unique().tolist()), min_train_years=min_train_years)
    fold_results: list[dict[str, Any]] = []
    oos_kept_parts: list[pd.DataFrame] = []
    negative_test_years = 0
    positive_test_years = 0
    for fold in folds:
        train = data[data["year"].isin(fold["train_years"])].copy()
        test = data[data["year"] == int(fold["test_year"])].copy()
        train_eval = evaluate_variant(picks=train, panel=panel, variant=variant, group_col=group_col)
        test_eval = evaluate_variant(picks=test, panel=panel, variant=variant, group_col=group_col)
        kept_test = test_eval.get("kept_picks", pd.DataFrame()).copy()
        if not kept_test.empty:
            oos_kept_parts.append(kept_test)
        test_return = float(test_eval["performance"].get("total_return_pct", 0.0))
        if test_return > 0:
            positive_test_years += 1
        elif test_return < 0:
            negative_test_years += 1
        fold_results.append(
            {
                "train_years": fold["train_years"],
                "test_year": fold["test_year"],
                "variant_id": variant["variant_id"],
                "train_performance": train_eval["performance"],
                "test_performance": test_eval["performance"],
                "test_kept_trades": test_eval["kept_trades"],
                "test_blocked_trades": test_eval["blocked_trades"],
                "test_blocked_counts_by_reason": test_eval.get("blocked_counts_by_reason", {}),
            }
        )
    oos_kept = pd.concat(oos_kept_parts, ignore_index=True) if oos_kept_parts else data.head(0).copy()
    total_folds = len(folds)
    return {
        "variant_id": variant["variant_id"],
        "thresholds": variant.get("thresholds"),
        "folds": fold_results,
        "aggregate_oos": summarize_picks_performance(oos_kept, group_col=group_col),
        "aggregate_oos_kept_picks": oos_kept,
        "negative_test_years": negative_test_years,
        "positive_test_years": positive_test_years,
        "consistency_ratio": (positive_test_years / total_folds) if total_folds else 0.0,
    }


def walkforward_candidate_gate(
    candidate: dict[str, Any],
    *,
    min_oos_trades: int = 60,
    max_oos_drawdown_pct: float = -30.0,
    max_negative_years: int = 0,
    min_consistency_ratio: float = 0.67,
    min_oos_total_return_pct: float = 0.0,
) -> dict[str, Any]:
    perf = candidate.get("aggregate_oos", {})
    reasons: list[str] = []
    if float(perf.get("total_return_pct", 0.0)) <= min_oos_total_return_pct:
        reasons.append("non_positive_oos_return")
    if int(perf.get("total_trades", 0)) < min_oos_trades:
        reasons.append("insufficient_oos_trades")
    if float(perf.get("max_drawdown_pct", 0.0)) < max_oos_drawdown_pct:
        reasons.append("oos_drawdown_too_deep")
    if int(candidate.get("negative_test_years", 0)) > max_negative_years:
        reasons.append("negative_test_years_exceeded")
    if float(candidate.get("consistency_ratio", 0.0)) < min_consistency_ratio:
        reasons.append("consistency_ratio_too_low")
    return {
        "approved": not reasons,
        "reasons": reasons,
        "variant_id": candidate.get("variant_id"),
    }


def branch_score(branch: dict[str, Any]) -> float:
    perf = branch["performance"]
    trades_penalty = 0.0 if perf.get("total_trades", 0) >= 30 else 15.0
    return float(perf.get("total_return_pct", 0.0)) + 10.0 * float(perf.get("sharpe_proxy", 0.0)) + float(perf.get("max_drawdown_pct", 0.0)) - trades_penalty


def choose_best_branch(branches: list[dict[str, Any]]) -> dict[str, Any]:
    if not branches:
        raise ValueError("no branches to choose from")
    ranked = sorted(branches, key=branch_score, reverse=True)
    best = dict(ranked[0])
    best["score"] = round(branch_score(best), 3)
    best["recommendation"] = "promote_to_next_replay" if best["performance"].get("total_return_pct", 0.0) > 0 else "reject"
    return best


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _scenario_perf(bundle: dict[str, Any], key: str, default: Any = 0.0) -> Any:
    scenarios = bundle.get("scenarios") or []
    if not scenarios:
        return default
    return min(float(row.get("aggregate_oos", {}).get(key, default)) for row in scenarios)


def derive_alert_level(*, robust_winner: dict[str, Any] | None, all_scenarios_approved: bool, worst_case_return_pct: float) -> str:
    """Classify the run into a single human-facing alert level.

    GREEN  — robust winner passes every stress scenario with a positive worst-case return.
    AMBER  — a winner exists but either fails some scenarios or has a non-positive worst case.
    RED    — no robust winner at all.
    """
    if robust_winner is None:
        return "RED"
    if all_scenarios_approved and float(worst_case_return_pct) > 0.0:
        return "GREEN"
    return "AMBER"


def build_profit_snapshot(
    *,
    robust_winner: dict[str, Any] | None,
    runner_up: dict[str, Any] | None,
    generated_at_utc: str,
    ttak_status: str = "NO_TRADE",
    candidate_count: int = 0,
    stress_report_md: str = "",
    stress_report_json: str = "",
    promoted_policy_json: str = "",
    cron_state_json: str = "",
    disclaimer: str = "Research-only artifacts. Not investment advice. Live orders not submitted.",
) -> dict[str, Any]:
    """Flatten a stress-research outcome into the 17-metric sheet snapshot.

    All missing values degrade gracefully so a run with no robust winner still
    produces a valid (RED) snapshot rather than raising.
    """
    base = (robust_winner or {}).get("base_scenario", {}) or {}
    base_perf = base.get("aggregate_oos", {}) or {}
    all_approved = bool((robust_winner or {}).get("all_scenarios_approved", False))
    worst_return = float((robust_winner or {}).get("worst_case_return_pct", 0.0))
    alert_level = derive_alert_level(
        robust_winner=robust_winner,
        all_scenarios_approved=all_approved,
        worst_case_return_pct=worst_return,
    )
    return {
        "generated_at_utc": generated_at_utc,
        "robust_winner": (robust_winner or {}).get("variant_id", ""),
        "alert_level": alert_level,
        "worst_case_scenario_id": (robust_winner or {}).get("worst_case_scenario_id", ""),
        "worst_case_return_pct": round(worst_return, 2),
        "worst_case_mdd_pct": round(float((robust_winner or {}).get("worst_case_mdd_pct", _scenario_perf(robust_winner or {}, "max_drawdown_pct"))), 2),
        "worst_case_sharpe_proxy": round(float((robust_winner or {}).get("worst_case_sharpe_proxy", _scenario_perf(robust_winner or {}, "sharpe_proxy"))), 3),
        "stress_pass_count": int((robust_winner or {}).get("stress_pass_count", 0)),
        "all_scenarios_approved": all_approved,
        "base_return_pct": round(float(base_perf.get("total_return_pct", 0.0)), 2),
        "base_mdd_pct": round(float(base_perf.get("max_drawdown_pct", 0.0)), 2),
        "base_sharpe_proxy": round(float(base_perf.get("sharpe_proxy", 0.0)), 3),
        "base_trades": int(base_perf.get("total_trades", 0)),
        "consistency_ratio": round(float(base.get("consistency_ratio", 0.0)), 3),
        "negative_test_years": int(base.get("negative_test_years", 0)),
        "runner_up_variant": (runner_up or {}).get("variant_id", ""),
        "runner_up_worst_case_return_pct": round(float((runner_up or {}).get("worst_case_return_pct", 0.0)), 2),
        "ttak_status": ttak_status,
        "candidate_count": int(candidate_count),
        "stress_report_md": stress_report_md,
        "stress_report_json": stress_report_json,
        "promoted_policy_json": promoted_policy_json,
        "cron_state_json": cron_state_json,
        "disclaimer": disclaimer,
    }
