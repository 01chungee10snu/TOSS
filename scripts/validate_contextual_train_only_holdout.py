#!/usr/bin/env python3
"""Revalidate contextual stock-selection research with a true train/holdout split.

Selection contract:
- 2022-2024 only: parameter ranking and situation approval.
- 2025 only: reporting-only holdout evaluation.
- Holdout metrics never alter the chosen parameters or approved situations.

Research-only. No broker calls and no live orders.
"""
from __future__ import annotations

import itertools
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import optimize_contextual_daily_strategy as daily_opt  # noqa: E402
import analyze_contextual_mon_fri_cycle as monfri_opt  # noqa: E402

OUT_JSON = ROOT / "reports" / "validation" / "contextual_train_only_holdout_latest.json"
OUT_DAILY = ROOT / "reports" / "validation" / "contextual_train_only_holdout_daily_curve.csv"
OUT_MONFRI = ROOT / "reports" / "validation" / "contextual_train_only_holdout_monfri_curve.csv"

PARAM_KEYS = [
    "momentum_col",
    "vol_col",
    "mode",
    "return_col",
    "top_n",
    "min_dollar_volume",
    "min_abs_momentum",
]


def daily_grid() -> list[dict[str, Any]]:
    return [
        {
            "momentum_col": momentum_col,
            "vol_col": vol_col,
            "mode": mode,
            "return_col": return_col,
            "top_n": top_n,
            "min_dollar_volume": min_dv,
            "min_abs_momentum": min_abs_mom,
        }
        for momentum_col, vol_col, mode, return_col, top_n, min_dv, min_abs_mom in itertools.product(
            ["mom_1d", "mom_5d", "mom_20d"],
            ["vol_20d"],
            ["momentum", "reversal"],
            ["oc_ret", "oo_ret", "cc_next_ret"],
            [3, 10],
            [100_000_000, 1_000_000_000],
            [0.0, 0.03],
        )
    ]


def monfri_grid() -> list[dict[str, Any]]:
    return [
        {
            "momentum_col": momentum_col,
            "vol_col": vol_col,
            "mode": mode,
            "return_col": "monfri_open_to_fri_close_ret",
            "top_n": top_n,
            "min_dollar_volume": min_dv,
            "min_abs_momentum": min_abs_mom,
        }
        for momentum_col, vol_col, mode, top_n, min_dv, min_abs_mom in itertools.product(
            ["mom_1d", "mom_5d", "mom_20d"],
            ["vol_20d"],
            ["momentum", "reversal"],
            [3, 10],
            [100_000_000, 1_000_000_000],
            [0.0, 0.03],
        )
    ]


def holdout_verdict(perf: dict[str, Any], *, min_trades: int) -> dict[str, Any]:
    reasons: list[str] = []
    if int(perf.get("total_trades", 0) or 0) < int(min_trades):
        reasons.append("insufficient_holdout_trades")
    if float(perf.get("total_return_pct", 0.0) or 0.0) <= 0.0:
        reasons.append("non_positive_holdout_return")
    if float(perf.get("sharpe", 0.0) or 0.0) <= 0.0:
        reasons.append("non_positive_holdout_sharpe")
    if float(perf.get("max_drawdown_pct", -100.0) or -100.0) <= -30.0:
        reasons.append("holdout_drawdown_breaches_30pct")
    return {"passed": not reasons, "reasons": reasons}


def select_train_only(
    *,
    data: pd.DataFrame,
    grid: list[dict[str, Any]],
    simulate: Callable[..., tuple[pd.DataFrame, pd.DataFrame]],
    perf: Callable[..., dict[str, Any]],
    all_dates: pd.DataFrame,
) -> dict[str, Any]:
    situations = sorted(s for s in data["situation"].dropna().unique() if isinstance(s, str))
    selected: dict[str, dict[str, Any]] = {}
    for situation in situations:
        best: dict[str, Any] | None = None
        for params in grid:
            curve, _ = simulate(data, params, situation=situation)
            train = perf(curve, curve["Date"] <= daily_opt.TRAIN_END)
            if int(train.get("total_trades", 0)) < daily_opt.MIN_TRADES_TRAIN:
                continue
            holdout = perf(curve, curve["Date"] >= daily_opt.TEST_START)
            score = daily_opt.objective_score(train)
            row = {
                "situation": situation,
                **params,
                "objective": round(float(score), 6),
                "selection_uses_holdout": False,
                **{f"train_{k}": v for k, v in train.items()},
                **{f"holdout_{k}": v for k, v in holdout.items()},
            }
            approval_row = {
                **row,
                **{f"test_{k}": v for k, v in holdout.items()},
            }
            row["train_approval_passed"] = daily_opt.train_approval_passed(approval_row)
            row["holdout_diagnostic_passed"] = daily_opt.holdout_diagnostic_passed(approval_row)
            if best is None or float(row["objective"]) > float(best["objective"]):
                best = row
        if best is not None:
            selected[situation] = best

    approved = {s: row for s, row in selected.items() if bool(row["train_approval_passed"])}
    curve_parts: list[pd.DataFrame] = []
    for situation, row in approved.items():
        params = {k: row[k] for k in PARAM_KEYS}
        curve, _ = simulate(data, params, situation=situation)
        curve_parts.append(curve)

    if curve_parts:
        combined = (
            pd.concat(curve_parts, ignore_index=True)
            .groupby("Date", as_index=False)
            .agg(picks=("picks", "sum"), daily_return=("daily_return", "mean"))
        )
        combined = all_dates.merge(combined, on="Date", how="left")
        combined["picks"] = combined["picks"].fillna(0).astype(int)
        combined["daily_return"] = combined["daily_return"].fillna(0.0)
    else:
        combined = all_dates.copy()
        combined["picks"] = 0
        combined["daily_return"] = 0.0

    train_perf = perf(combined, combined["Date"] <= daily_opt.TRAIN_END)
    holdout_perf = perf(combined, combined["Date"] >= daily_opt.TEST_START)
    return {
        "selected_by_situation_train_only": selected,
        "approved_situations_train_only": approved,
        "curve": combined,
        "train": train_perf,
        "holdout": holdout_perf,
        "holdout_verdict": holdout_verdict(holdout_perf, min_trades=daily_opt.MIN_TRADES_TEST),
    }


def build_report() -> dict[str, Any]:
    if not daily_opt.PANEL_CSV.exists():
        raise FileNotFoundError(daily_opt.PANEL_CSV)
    panel = pd.read_csv(daily_opt.PANEL_CSV, dtype={"code": str}, parse_dates=["Date"])
    data = daily_opt.prepare(panel)

    daily_dates = pd.DataFrame({"Date": sorted(data["Date"].dropna().unique())})
    daily_result = select_train_only(
        data=data,
        grid=daily_grid(),
        simulate=daily_opt.simulate,
        perf=daily_opt.perf,
        all_dates=daily_dates,
    )

    weekly_data = monfri_opt.add_mon_fri_returns(data)
    monday_dates = pd.DataFrame({"Date": sorted(weekly_data.loc[weekly_data["weekday"] == 0, "Date"].dropna().unique())})
    monfri_result = select_train_only(
        data=weekly_data,
        grid=monfri_grid(),
        simulate=monfri_opt.simulate_mon_fri,
        perf=monfri_opt.safe_perf,
        all_dates=monday_dates,
    )

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "order_submission": False,
        "selection_contract": {
            "train_end": daily_opt.TRAIN_END.date().isoformat(),
            "holdout_start": daily_opt.TEST_START.date().isoformat(),
            "holdout_used_for_parameter_selection": False,
            "holdout_used_for_situation_approval": False,
        },
        "data": {
            "panel": str(daily_opt.PANEL_CSV),
            "universe": "fixed random500 research panel",
            "survivorship_bias_resolved": False,
            "limitations": [
                "fixed random500 universe is not a fully historical membership universe",
                "single 2025 holdout remains a short validation horizon",
                "fixed transaction-cost assumptions may differ from realized execution",
            ],
        },
        "daily_contextual": {k: v for k, v in daily_result.items() if k != "curve"},
        "monfri_contextual": {k: v for k, v in monfri_result.items() if k != "curve"},
        "curves": {
            "daily": daily_result["curve"],
            "monfri": monfri_result["curve"],
        },
    }


def main() -> None:
    report = build_report()
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    curves = report.pop("curves")
    curves["daily"].to_csv(OUT_DAILY, index=False)
    curves["monfri"].to_csv(OUT_MONFRI, index=False)
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    daily_h = report["daily_contextual"]["holdout"]
    monfri_h = report["monfri_contextual"]["holdout"]
    print("order_submission=False")
    print(f"daily_holdout_return_pct={daily_h['total_return_pct']}")
    print(f"daily_holdout_sharpe={daily_h['sharpe']}")
    print(f"daily_holdout_verdict={report['daily_contextual']['holdout_verdict']['passed']}")
    print(f"monfri_holdout_return_pct={monfri_h['total_return_pct']}")
    print(f"monfri_holdout_sharpe={monfri_h['sharpe']}")
    print(f"monfri_holdout_verdict={report['monfri_contextual']['holdout_verdict']['passed']}")
    print(f"output={OUT_JSON}")


if __name__ == "__main__":
    main()
