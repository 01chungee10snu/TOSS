#!/usr/bin/env python3
"""Strict executable trend-sleeve research on the current ETF pair.

Contract:
- KODEX 코스피 (226490) + KODEX 단기채권 (153130) only.
- Signal uses information available at close[t].
- Any allocation change executes no earlier than open[t+1].
- Whole shares only; 50% per-ETF position cap; no borrowing.
- Per-traded-notional cost stresses: 31/50/75 bps on every buy/sell leg.
- Variant selection uses 2015-2021 train only; 2022+ is reporting-only holdout.
- Research-only. No broker calls and no order submission.
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from validate_executable_etf_portfolio import allocate_integer_shares, build_rebalance_orders  # noqa: E402

PANEL = ROOT / "reports" / "backtests" / "executable_etf" / "verified_etf_unadjusted_panel_2015_2026.csv"
OUT = ROOT / "reports" / "validation" / "executable_etf_trend_sleeve_latest.json"
OUT_CURVE = ROOT / "reports" / "validation" / "executable_etf_trend_sleeve_selected_curve.csv"

EQUITY = "226490"
BOND = "153130"
INITIAL_CAPITAL = 391_722.0
MAX_POSITION_PCT = 0.50
COST_LEVELS_BPS = (31, 50, 75)
TRAIN_END = pd.Timestamp("2021-12-31")
HOLDOUT_START = pd.Timestamp("2022-01-01")
REBALANCE_INTERVAL = 20
MAX_INDEPENDENT_CORRELATION = 0.80
MIN_COMPLETED_YEAR_POSITIVE_SHARE = 0.75

# All targets respect the same 50% single-position cap as the executable ETF validator.
VARIANTS: dict[str, dict[str, Any]] = {
    "trend200_50_10": {
        "signal": "price_vs_ma",
        "slow_ma": 200,
        "weights_up": {EQUITY: 0.50, BOND: 0.10},
        "weights_down": {EQUITY: 0.10, BOND: 0.50},
    },
    "trend200_50_0": {
        "signal": "price_vs_ma",
        "slow_ma": 200,
        "weights_up": {EQUITY: 0.50, BOND: 0.00},
        "weights_down": {EQUITY: 0.00, BOND: 0.50},
    },
    "trend120_50_10": {
        "signal": "price_vs_ma",
        "slow_ma": 120,
        "weights_up": {EQUITY: 0.50, BOND: 0.10},
        "weights_down": {EQUITY: 0.10, BOND: 0.50},
    },
    "dual60_200_50_10": {
        "signal": "fast_vs_slow",
        "fast_ma": 60,
        "slow_ma": 200,
        "weights_up": {EQUITY: 0.50, BOND: 0.10},
        "weights_down": {EQUITY: 0.10, BOND: 0.50},
    },
}


def load_panel(path: Path = PANEL) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"code": str}, parse_dates=["date"])
    df["code"] = df["code"].astype(str).str.zfill(6)
    df = df[df["code"].isin([EQUITY, BOND])].copy()
    for c in ["open", "close", "dividends"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values(["date", "code"]).reset_index(drop=True)


def common_frames(panel: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    opens = panel.pivot(index="date", columns="code", values="open").sort_index()
    closes = panel.pivot(index="date", columns="code", values="close").sort_index()
    dividends = panel.pivot(index="date", columns="code", values="dividends").sort_index().fillna(0.0)
    common = opens[[EQUITY, BOND]].dropna().index.intersection(closes[[EQUITY, BOND]].dropna().index)
    return opens.loc[common, [EQUITY, BOND]], closes.loc[common, [EQUITY, BOND]], dividends.reindex(common).fillna(0.0)[[EQUITY, BOND]]


def signal_series(closes: pd.DataFrame, config: Mapping[str, Any]) -> pd.Series:
    """Return close-of-day regime labels; execution must occur next session."""
    eq = closes[EQUITY].astype(float)
    slow_n = int(config["slow_ma"])
    slow = eq.rolling(slow_n, min_periods=slow_n).mean()
    if config["signal"] == "price_vs_ma":
        bull = eq >= slow
    elif config["signal"] == "fast_vs_slow":
        fast_n = int(config["fast_ma"])
        fast = eq.rolling(fast_n, min_periods=fast_n).mean()
        bull = fast >= slow
    else:
        raise ValueError(config["signal"])
    signal = pd.Series(index=eq.index, dtype="object")
    signal.loc[slow.notna()] = np.where(bull.loc[slow.notna()], "up", "down")
    return signal


@dataclass
class Result:
    variant: str
    cost_bps: int
    curve: pd.DataFrame
    trades: list[dict[str, Any]]
    rebalances: list[dict[str, Any]]


def run_backtest(panel: pd.DataFrame, *, variant: str, cost_bps: int) -> Result:
    if variant not in VARIANTS:
        raise KeyError(variant)
    config = VARIANTS[variant]
    opens, closes, dividends = common_frames(panel)
    signals = signal_series(closes, config)
    holdings = {EQUITY: 0, BOND: 0}
    cash = INITIAL_CAPITAL
    side_rate = float(cost_bps) / 10_000.0
    last_rebalance_idx: int | None = None
    last_state: str | None = None
    trades: list[dict[str, Any]] = []
    rebalances: list[dict[str, Any]] = []
    curve_rows: list[dict[str, Any]] = []

    dates = list(opens.index)
    for i, date in enumerate(dates):
        open_px = {c: float(opens.at[date, c]) for c in (EQUITY, BOND)}
        close_px = {c: float(closes.at[date, c]) for c in (EQUITY, BOND)}
        # Ex-date cash belongs to shares held before today's open rebalance.
        dividend_cash = sum(holdings[c] * float(dividends.at[date, c]) for c in (EQUITY, BOND))

        signal_date = dates[i - 1] if i > 0 else None
        state = str(signals.loc[signal_date]) if signal_date is not None and pd.notna(signals.loc[signal_date]) else None
        due_interval = last_rebalance_idx is None or (i - last_rebalance_idx) >= REBALANCE_INTERVAL
        state_changed = state is not None and state != last_state
        should_rebalance = state is not None and (due_interval or state_changed)

        if should_rebalance:
            equity_open = cash + sum(holdings[c] * open_px[c] for c in (EQUITY, BOND))
            weights = config[f"weights_{state}"]
            allocation = allocate_integer_shares(
                equity=equity_open,
                prices=open_px,
                target_weights=weights,
                cost_bps_per_side=float(cost_bps),
                max_position_pct=MAX_POSITION_PCT,
            )
            target = {c: int(allocation["quantities"].get(c, 0)) for c in (EQUITY, BOND)}
            orders = build_rebalance_orders(current=holdings, target=target)
            for order in orders:
                code = str(order["code"])
                qty = int(order["quantity"])
                px = open_px[code]
                notional = qty * px
                cost = notional * side_rate
                if order["side"] == "SELL":
                    if qty > holdings[code]:
                        raise RuntimeError("sell exceeds holdings")
                    cash += notional - cost
                    holdings[code] -= qty
                else:
                    debit = notional + cost
                    if debit > cash + 1e-7:
                        raise RuntimeError("cash overspend")
                    cash -= debit
                    holdings[code] += qty
                trades.append({
                    "date": date.date().isoformat(),
                    "signal_date": signal_date.date().isoformat(),
                    "code": code,
                    "side": order["side"],
                    "quantity": qty,
                    "price": px,
                    "cost_krw": cost,
                })
            rebalances.append({
                "date": date.date().isoformat(),
                "signal_date": signal_date.date().isoformat(),
                "state": state,
                "target_quantities": dict(holdings),
            })
            last_rebalance_idx = i
            last_state = state

        cash += dividend_cash
        equity_close = cash + sum(holdings[c] * close_px[c] for c in (EQUITY, BOND))
        if cash < -1e-6 or any(not isinstance(q, int) or q < 0 for q in holdings.values()):
            raise RuntimeError("portfolio invariant violated")
        curve_rows.append({
            "date": date,
            "equity": float(equity_close),
            "cash": float(cash),
            "qty_equity": holdings[EQUITY],
            "qty_bond": holdings[BOND],
            "executed_state": last_state,
            "signal_state_previous_close": state,
        })

    return Result(variant=variant, cost_bps=int(cost_bps), curve=pd.DataFrame(curve_rows), trades=trades, rebalances=rebalances)


def metrics(curve: pd.DataFrame, *, start: pd.Timestamp | None = None, end: pd.Timestamp | None = None) -> dict[str, Any]:
    d = curve.copy()
    if start is not None:
        d = d[d["date"] >= start]
    if end is not None:
        d = d[d["date"] <= end]
    d = d.sort_values("date").reset_index(drop=True)
    if len(d) < 2:
        return {"days": len(d), "total_return_pct": 0.0, "cagr_pct": 0.0, "sharpe": 0.0, "max_drawdown_pct": 0.0}
    ret = d["equity"].pct_change().fillna(0.0)
    total = float(d["equity"].iloc[-1] / d["equity"].iloc[0] - 1.0)
    years = max((d["date"].iloc[-1] - d["date"].iloc[0]).days / 365.2425, 1 / 365.2425)
    cagr = (1.0 + total) ** (1.0 / years) - 1.0 if total > -1 else -1.0
    sd = float(ret.std(ddof=1))
    sharpe = float(ret.mean() / sd * math.sqrt(252)) if sd > 0 and math.isfinite(sd) else 0.0
    peak = d["equity"].cummax()
    mdd = float((d["equity"] / peak - 1.0).min())
    yearly: dict[str, float] = {}
    years = d.assign(year=d["date"].dt.year)
    for year, group in years.groupby("year"):
        if len(group) > 1:
            yearly[str(int(year))] = round((float(group["equity"].iloc[-1]) / float(group["equity"].iloc[0]) - 1.0) * 100.0, 2)
    return {
        "days": int(len(d)),
        "total_return_pct": round(total * 100.0, 2),
        "cagr_pct": round(cagr * 100.0, 2),
        "sharpe": round(sharpe, 4),
        "max_drawdown_pct": round(mdd * 100.0, 2),
        "yearly_returns_pct": yearly,
    }


def select_variant_train_only(rows: list[dict[str, Any]]) -> str | None:
    stress = [r for r in rows if int(r["cost_bps"]) == max(COST_LEVELS_BPS)]
    eligible = [r for r in stress if float(r["train"]["total_return_pct"]) > 0 and float(r["train"]["sharpe"]) > 0]
    if not eligible:
        return None
    # Holdout values are intentionally absent from the sort key.
    best = max(eligible, key=lambda r: (float(r["train"]["sharpe"]), float(r["train"]["cagr_pct"]), float(r["train"]["max_drawdown_pct"])))
    return str(best["variant"])


def pair_correlation(a: pd.DataFrame, b: pd.DataFrame, *, start: pd.Timestamp | None = None) -> dict[str, Any]:
    aa = a[["date", "equity"]].copy().rename(columns={"equity": "a"})
    bb = b[["date", "equity"]].copy().rename(columns={"equity": "b"})
    x = aa.merge(bb, on="date", how="inner").sort_values("date")
    if start is not None:
        x = x[x["date"] >= start]
    rets = x.set_index("date")[["a", "b"]].pct_change().dropna()
    if len(rets) < 2:
        return {"observations": int(len(rets)), "pearson": None, "downside": None}
    pearson = float(rets["a"].corr(rets["b"]))
    downside_rows = rets[(rets["a"] < 0) | (rets["b"] < 0)]
    downside = float(downside_rows["a"].corr(downside_rows["b"])) if len(downside_rows) >= 2 else None
    return {
        "observations": int(len(rets)),
        "pearson": pearson if math.isfinite(pearson) else None,
        "downside": downside if downside is not None and math.isfinite(downside) else None,
    }


def independent_alpha_gate(
    *,
    selected_holdout: Mapping[str, Any],
    baseline_holdout: Mapping[str, Any],
    correlation: Mapping[str, Any],
    completed_year_positive_share: float,
) -> dict[str, Any]:
    reasons: list[str] = []
    if float(selected_holdout.get("total_return_pct", 0.0) or 0.0) <= 0.0:
        reasons.append("non_positive_holdout_return")
    if float(selected_holdout.get("sharpe", 0.0) or 0.0) <= 0.0:
        reasons.append("non_positive_holdout_sharpe")
    if float(selected_holdout.get("max_drawdown_pct", -100.0) or -100.0) <= -30.0:
        reasons.append("holdout_drawdown_breaches_30pct")
    pearson = correlation.get("pearson")
    downside = correlation.get("downside")
    if pearson is None or downside is None or max(abs(float(pearson)), abs(float(downside))) >= MAX_INDEPENDENT_CORRELATION:
        reasons.append("insufficient_independence_from_static_etf_sleeve")
    if float(completed_year_positive_share) < MIN_COMPLETED_YEAR_POSITIVE_SHARE:
        reasons.append("insufficient_positive_completed_holdout_years")
    if float(selected_holdout.get("total_return_pct", 0.0)) <= float(baseline_holdout.get("total_return_pct", 0.0)):
        reasons.append("does_not_outperform_static_50_50_return")
    if float(selected_holdout.get("sharpe", 0.0)) <= float(baseline_holdout.get("sharpe", 0.0)):
        reasons.append("does_not_improve_static_50_50_sharpe")
    return {"passed": not reasons, "reasons": reasons}


def static_baseline(panel: pd.DataFrame, *, cost_bps: int) -> Result:
    # Represent the existing 50/50 sleeve through the same engine using an always-up signal.
    name = "__static_50_50"
    config = {
        "signal": "price_vs_ma",
        "slow_ma": 1,
        "weights_up": {EQUITY: 0.50, BOND: 0.50},
        "weights_down": {EQUITY: 0.50, BOND: 0.50},
    }
    VARIANTS[name] = config
    try:
        return run_backtest(panel, variant=name, cost_bps=cost_bps)
    finally:
        VARIANTS.pop(name, None)


def build_report(panel: pd.DataFrame) -> tuple[dict[str, Any], pd.DataFrame | None]:
    rows: list[dict[str, Any]] = []
    results: dict[tuple[str, int], Result] = {}
    for variant in VARIANTS:
        for cost in COST_LEVELS_BPS:
            result = run_backtest(panel, variant=variant, cost_bps=cost)
            results[(variant, cost)] = result
            rows.append({
                "variant": variant,
                "cost_bps": cost,
                "train": metrics(result.curve, end=TRAIN_END),
                "holdout": metrics(result.curve, start=HOLDOUT_START),
                "all": metrics(result.curve),
                "rebalances": len(result.rebalances),
                "trade_legs": len(result.trades),
            })
    selected = select_variant_train_only(rows)
    selected_curve: pd.DataFrame | None = None
    correlation: dict[str, Any] | None = None
    baseline_metrics: dict[str, Any] | None = None
    completed_year_positive_share: float | None = None
    holdout_gate = {"passed": False, "reasons": ["no_train_positive_variant"]}
    if selected is not None:
        chosen = results[(selected, max(COST_LEVELS_BPS))]
        selected_curve = chosen.curve.copy()
        baseline = static_baseline(panel, cost_bps=max(COST_LEVELS_BPS))
        correlation = {
            "all": pair_correlation(chosen.curve, baseline.curve),
            "holdout": pair_correlation(chosen.curve, baseline.curve, start=HOLDOUT_START),
        }
        h = metrics(chosen.curve, start=HOLDOUT_START)
        baseline_metrics = metrics(baseline.curve, start=HOLDOUT_START)
        yearly = h.get("yearly_returns_pct", {})
        last_year = int(chosen.curve["date"].max().year)
        completed = [float(v) for y, v in yearly.items() if int(y) < last_year]
        completed_year_positive_share = (
            sum(v > 0 for v in completed) / len(completed) if completed else 0.0
        )
        holdout_gate = independent_alpha_gate(
            selected_holdout=h,
            baseline_holdout=baseline_metrics,
            correlation=correlation["holdout"],
            completed_year_positive_share=completed_year_positive_share,
        )

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "order_submission": False,
        "execution_contract": "close[t] signal -> open[t+1] execution; whole shares; 50% per-ETF cap; sell-before-buy",
        "selection_contract": {
            "train_end": TRAIN_END.date().isoformat(),
            "holdout_start": HOLDOUT_START.date().isoformat(),
            "selection_uses_holdout": False,
            "selection_cost_stress_bps": max(COST_LEVELS_BPS),
        },
        "cost_levels_bps_per_traded_notional": list(COST_LEVELS_BPS),
        "variants": VARIANTS,
        "results": rows,
        "selected_train_only_variant": selected,
        "selected_vs_static_50_50_correlation": correlation,
        "static_50_50_holdout_metrics_at_selection_cost": baseline_metrics,
        "completed_holdout_year_positive_share": completed_year_positive_share,
        "independent_alpha_thresholds": {
            "max_abs_pearson_and_downside_correlation": MAX_INDEPENDENT_CORRELATION,
            "min_completed_year_positive_share": MIN_COMPLETED_YEAR_POSITIVE_SHARE,
            "must_beat_static_50_50_total_return": True,
            "must_beat_static_50_50_sharpe": True,
        },
        "holdout_independent_alpha_gate": holdout_gate,
        "live_promotion": "BLOCKED_RESEARCH_ONLY",
    }, selected_curve


def main() -> None:
    panel = load_panel()
    report, curve = build_report(panel)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if curve is not None:
        curve.to_csv(OUT_CURVE, index=False)
    selected = report["selected_train_only_variant"]
    print("order_submission=False")
    print(f"selected_train_only_variant={selected}")
    if selected:
        row = next(r for r in report["results"] if r["variant"] == selected and r["cost_bps"] == max(COST_LEVELS_BPS))
        print(f"holdout_return_pct={row['holdout']['total_return_pct']}")
        print(f"holdout_sharpe={row['holdout']['sharpe']}")
        print(f"holdout_mdd_pct={row['holdout']['max_drawdown_pct']}")
        print(f"holdout_corr_vs_static={report['selected_vs_static_50_50_correlation']['holdout']['pearson']}")
    print(f"independent_alpha_gate={report['holdout_independent_alpha_gate']['passed']}")
    print(f"output={OUT}")


if __name__ == "__main__":
    main()
