"""Strict true-PIT domestic HML/CMA/profitability-proxy research backtest.

Preconditions:
- receipt-versioned OpenDART fundamentals pass the shared PIT contract;
- historical price panel contains delisted names;
- factor snapshot uses only filings available by each signal close;
- CMA uses revision-aware total-asset growth, not a revenue-growth proxy;
- rebalance executes at the next trading day's open;
- 31/50/75bp round-trip cost stress is charged on actual turnover.

This is research-only.  Fractional research sizing is used to isolate factor
edge from account-size constraints; it is not an executable order generator.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from toss_alpha.research.factor_pit import pit_factor_snapshot, validate_pit_contract

ROOT = Path(__file__).resolve().parents[1]
FUND_CSV = ROOT / "reports" / "backtests" / "fundamental" / "opendart_pit_fundamentals.csv"
PANEL_CSV = ROOT / "reports" / "backtests" / "pit_full_universe_2022-01-01_2026_ohlcv_panel.csv"
OUT_DIR = ROOT / "reports" / "validation"
INITIAL_CAPITAL = 100_000_000.0
COST_LEVELS_BPS = (31, 50, 75)
TOP_QUANTILE = 0.20
MIN_CANDIDATES = 20
MAX_NAMES = 30
STRATEGIES = (
    "hml_only",
    "cma_only",
    "profitability_proxy",
    "hml_cma_intersection",
    "hml_cma_composite",
    "hml_cma_profitability_composite",
)
STRATEGY_FACTOR_COLUMNS = {
    "hml_only": ("bps",),
    "cma_only": ("asset_growth",),
    "profitability_proxy": ("operating_profitability_proxy",),
    "hml_cma_intersection": ("bps", "asset_growth"),
    "hml_cma_composite": ("bps", "asset_growth"),
    "hml_cma_profitability_composite": ("bps", "asset_growth", "operating_profitability_proxy"),
}
REQUIRED_REBALANCE_START = pd.Timestamp("2022-06-30")
MIN_FACTOR_READY_CODES_PER_REBALANCE = 100


@dataclass
class BacktestResult:
    strategy: str
    cost_bps: int
    equity: pd.DataFrame
    rebalances: list[dict[str, Any]]

    def summary(self) -> dict[str, Any]:
        if self.equity.empty:
            return {
                "strategy": self.strategy,
                "cost_bps": self.cost_bps,
                "total_return_pct": 0.0,
                "cagr_pct": 0.0,
                "sharpe_ratio": 0.0,
                "max_drawdown_pct": 0.0,
                "rebalances": 0,
                "positive_year_share": 0.0,
            }
        eq = self.equity.copy()
        values = eq["equity"].astype(float)
        daily = values.pct_change(fill_method=None).dropna()
        total = values.iloc[-1] / INITIAL_CAPITAL - 1.0
        years = max((eq["date"].iloc[-1] - eq["date"].iloc[0]).days / 365.2425, 1 / 365.2425)
        cagr = (values.iloc[-1] / INITIAL_CAPITAL) ** (1 / years) - 1.0 if values.iloc[-1] > 0 else -1.0
        peak = values.cummax()
        dd = values / peak - 1.0
        sharpe = 0.0
        if len(daily) > 1 and daily.std(ddof=1) > 0:
            sharpe = float(daily.mean() / daily.std(ddof=1) * np.sqrt(252))
        yearly = {}
        temp = eq.set_index("date")["equity"].astype(float)
        for year, group in temp.groupby(temp.index.year):
            if len(group) > 1:
                yearly[str(int(year))] = float(group.iloc[-1] / group.iloc[0] - 1.0)
        positive_year_share = sum(value > 0 for value in yearly.values()) / max(1, len(yearly))
        return {
            "strategy": self.strategy,
            "cost_bps": int(self.cost_bps),
            "start": eq["date"].iloc[0].date().isoformat(),
            "end": eq["date"].iloc[-1].date().isoformat(),
            "initial_capital_krw": INITIAL_CAPITAL,
            "final_equity_krw": round(float(values.iloc[-1]), 0),
            "total_return_pct": round(total * 100, 2),
            "cagr_pct": round(cagr * 100, 2),
            "sharpe_ratio": round(sharpe, 4),
            "max_drawdown_pct": round(float(dd.min()) * 100, 2),
            "rebalances": len(self.rebalances),
            "positive_year_share": round(float(positive_year_share), 3),
            "yearly_returns_pct": {key: round(value * 100, 2) for key, value in yearly.items()},
            "avg_names": round(float(np.mean([r["name_count"] for r in self.rebalances])), 2) if self.rebalances else 0.0,
            "total_cost_krw": round(float(sum(r["cost_krw"] for r in self.rebalances)), 0),
        }


def normalize_price_panel(panel: pd.DataFrame) -> pd.DataFrame:
    frame = panel.copy()
    date_col = "date" if "date" in frame.columns else "Date" if "Date" in frame.columns else None
    if date_col is None:
        raise ValueError("price panel missing date/Date")
    frame["Date"] = pd.to_datetime(frame[date_col], errors="coerce").dt.normalize()
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    for name in ("Open", "Close"):
        if name not in frame.columns:
            alt = name.lower()
            if alt in frame.columns:
                frame[name] = frame[alt]
            else:
                raise ValueError(f"price panel missing {name}")
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    return frame.dropna(subset=["Date", "code", "Open", "Close"])


def month_end_signal_dates(dates: list[pd.Timestamp]) -> set[pd.Timestamp]:
    if not dates:
        return set()
    series = pd.Series(pd.DatetimeIndex(dates), index=pd.DatetimeIndex(dates))
    return set(series.groupby(series.index.to_period("M")).max().tolist())


def select_factor_codes(
    snapshot: pd.DataFrame,
    close_prices: Mapping[str, float],
    *,
    strategy: str,
    min_candidates: int = MIN_CANDIDATES,
    max_names: int = MAX_NAMES,
) -> list[str]:
    if snapshot.empty or strategy not in STRATEGIES:
        return []
    snap = snapshot.copy()
    snap["code"] = snap["code"].astype(str).str.zfill(6)
    snap["bps"] = pd.to_numeric(snap.get("bps"), errors="coerce")
    snap["asset_growth"] = pd.to_numeric(snap.get("asset_growth"), errors="coerce")
    snap["operating_profitability_proxy"] = pd.to_numeric(snap.get("operating_profitability_proxy"), errors="coerce")
    snap["close"] = snap["code"].map({str(k).zfill(6): float(v) for k, v in close_prices.items()})

    needs_value = strategy in {"hml_only", "hml_cma_intersection", "hml_cma_composite", "hml_cma_profitability_composite"}
    needs_investment = strategy in {"cma_only", "hml_cma_intersection", "hml_cma_composite", "hml_cma_profitability_composite"}
    needs_profitability = strategy in {"profitability_proxy", "hml_cma_profitability_composite"}
    required = []
    if needs_value:
        required.extend(["bps", "close"])
    if needs_investment:
        required.append("asset_growth")
    if needs_profitability:
        required.append("operating_profitability_proxy")
    snap = snap.dropna(subset=required)
    if needs_value:
        snap = snap[(snap["bps"] > 0) & (snap["close"] > 0)]
    if len(snap) < int(min_candidates):
        return []

    if needs_value:
        snap["bm"] = snap["bps"] / snap["close"]
        snap["bm_rank"] = snap["bm"].rank(pct=True, method="average")
    if needs_investment:
        snap["investment_rank"] = snap["asset_growth"].rank(pct=True, method="average")
    if needs_profitability:
        snap["profitability_rank"] = snap["operating_profitability_proxy"].rank(pct=True, method="average")
    n_select = max(1, min(int(max_names), int(np.ceil(len(snap) * TOP_QUANTILE))))

    if strategy == "hml_only":
        selected = snap.nlargest(n_select, "bm")
    elif strategy == "cma_only":
        selected = snap.nsmallest(n_select, "asset_growth")
    elif strategy == "profitability_proxy":
        selected = snap.nlargest(n_select, "operating_profitability_proxy")
    elif strategy == "hml_cma_intersection":
        value_codes = set(snap.nlargest(n_select, "bm")["code"])
        selected = snap[snap["code"].isin(value_codes)].nsmallest(n_select, "asset_growth")
    elif strategy == "hml_cma_composite":
        snap["composite"] = 0.5 * snap["bm_rank"] + 0.5 * (1.0 - snap["investment_rank"])
        selected = snap.nlargest(n_select, "composite")
    else:
        snap["composite"] = (
            snap["bm_rank"] + (1.0 - snap["investment_rank"]) + snap["profitability_rank"]
        ) / 3.0
        selected = snap.nlargest(n_select, "composite")
    return selected["code"].astype(str).tolist()[: int(max_names)]


def select_profitability_variant_codes(
    snapshot: pd.DataFrame,
    close_prices: Mapping[str, float],
    *,
    strategy: str,
    min_candidates: int = MIN_CANDIDATES,
    max_names: int = MAX_NAMES,
    variant: str,
) -> list[str]:
    del close_prices, strategy
    snap = snapshot.copy()
    if snap.empty or "operating_profitability_proxy" not in snap.columns:
        return []
    snap["code"] = snap["code"].astype(str).str.zfill(6)
    snap["operating_profitability_proxy"] = pd.to_numeric(
        snap["operating_profitability_proxy"], errors="coerce"
    )
    snap = snap.dropna(subset=["operating_profitability_proxy"])
    if len(snap) < int(min_candidates):
        return []
    if variant == "all":
        return snap["code"].astype(str).tolist()
    n_select = max(1, min(int(max_names), int(np.ceil(len(snap) * TOP_QUANTILE))))
    if variant == "high":
        selected = snap.nlargest(n_select, "operating_profitability_proxy")
    elif variant == "low":
        selected = snap.nsmallest(n_select, "operating_profitability_proxy")
    else:
        raise ValueError(f"unknown profitability variant: {variant}")
    return selected["code"].astype(str).tolist()[: int(max_names)]


def run_backtest(
    fundamentals: pd.DataFrame,
    panel: pd.DataFrame,
    *,
    strategy: str,
    cost_bps: int,
    min_candidates: int = MIN_CANDIDATES,
    max_names: int = MAX_NAMES,
    start_date: pd.Timestamp | None = None,
    selector: Any | None = None,
) -> BacktestResult:
    prices = normalize_price_panel(panel)
    opens = prices.pivot_table(index="Date", columns="code", values="Open", aggfunc="last").sort_index()
    closes = prices.pivot_table(index="Date", columns="code", values="Close", aggfunc="last").sort_index()
    dates = list(opens.index.intersection(closes.index))
    if start_date is not None:
        cutoff = pd.Timestamp(start_date).normalize()
        dates = [day for day in dates if pd.Timestamp(day).normalize() >= cutoff]
    signals = month_end_signal_dates(dates)
    side_rate = (float(cost_bps) / 2.0) / 10_000.0
    selection_fn = selector or select_factor_codes

    quantities: dict[str, float] = {}
    cash = INITIAL_CAPITAL
    pending: tuple[pd.Timestamp, list[str]] | None = None
    curve: list[dict[str, Any]] = []
    rebalances: list[dict[str, Any]] = []

    for i, day in enumerate(dates):
        open_row = opens.loc[day].dropna()
        close_row = closes.loc[day].dropna()

        if pending is not None and pending[0] == day:
            target_codes = [code for code in pending[1] if code in open_row.index and float(open_row[code]) > 0]
            equity_open = cash + sum(qty * float(open_row.get(code, np.nan)) for code, qty in quantities.items() if code in open_row.index)
            current_values = {code: qty * float(open_row[code]) for code, qty in quantities.items() if code in open_row.index}
            target_weight = 1.0 / len(target_codes) if target_codes else 0.0
            first_target = {code: equity_open * target_weight for code in target_codes}
            turnover = sum(abs(first_target.get(code, 0.0) - current_values.get(code, 0.0)) for code in set(first_target) | set(current_values))
            cost = turnover * side_rate
            investable = max(0.0, equity_open - cost)
            target_values = {code: investable * target_weight for code in target_codes}
            quantities = {code: target_values[code] / float(open_row[code]) for code in target_codes}
            cash = max(0.0, investable - sum(target_values.values()))
            rebalances.append(
                {
                    "execution_date": day.date().isoformat(),
                    "signal_date": dates[i - 1].date().isoformat() if i > 0 else None,
                    "name_count": len(target_codes),
                    "codes": target_codes,
                    "turnover_krw": round(float(turnover), 2),
                    "cost_krw": round(float(cost), 2),
                }
            )
            pending = None

        equity_close = cash
        for code, qty in quantities.items():
            price = close_row.get(code)
            if pd.notna(price) and float(price) > 0:
                equity_close += qty * float(price)
            elif code in open_row.index:
                equity_close += qty * float(open_row[code])
        curve.append({"date": day, "equity": float(equity_close), "cash": float(cash), "positions": len(quantities)})

        if day in signals and i + 1 < len(dates):
            snapshot = pit_factor_snapshot(fundamentals, day, universe_panel=prices, require_revision_safe=True)
            close_prices = {str(code): float(value) for code, value in close_row.items() if pd.notna(value) and float(value) > 0}
            codes = selection_fn(
                snapshot,
                close_prices,
                strategy=strategy,
                min_candidates=min_candidates,
                max_names=max_names,
            )
            pending = (dates[i + 1], codes)

    return BacktestResult(strategy=strategy, cost_bps=int(cost_bps), equity=pd.DataFrame(curve), rebalances=rebalances)


def _relative_curve_metrics(left: pd.DataFrame, right: pd.DataFrame) -> dict[str, float]:
    merged = left[["date", "equity"]].merge(
        right[["date", "equity"]], on="date", suffixes=("_left", "_right")
    )
    if len(merged) < 3:
        return {"cumulative_relative_return_pct": 0.0, "relative_sharpe": 0.0, "mean_daily_bp": 0.0}
    left_return = merged["equity_left"].astype(float).pct_change(fill_method=None)
    right_return = merged["equity_right"].astype(float).pct_change(fill_method=None)
    relative = (left_return - right_return).dropna()
    if relative.empty:
        return {"cumulative_relative_return_pct": 0.0, "relative_sharpe": 0.0, "mean_daily_bp": 0.0}
    cumulative = float((1.0 + relative).prod() - 1.0)
    sharpe = 0.0
    if len(relative) > 1 and relative.std(ddof=1) > 0:
        sharpe = float(relative.mean() / relative.std(ddof=1) * np.sqrt(252))
    return {
        "cumulative_relative_return_pct": round(cumulative * 100.0, 2),
        "relative_sharpe": round(sharpe, 4),
        "mean_daily_bp": round(float(relative.mean()) * 10_000.0, 3),
    }


def build_profitability_diagnostic(
    fundamentals: pd.DataFrame,
    panel: pd.DataFrame,
    *,
    high_result: BacktestResult,
    cost_bps: int = 75,
    min_candidates: int = MIN_CANDIDATES,
    max_names: int = MAX_NAMES,
) -> dict[str, Any]:
    def selector_for(variant: str):
        def _selector(snapshot, close_prices, *, strategy, min_candidates=MIN_CANDIDATES, max_names=MAX_NAMES):
            return select_profitability_variant_codes(
                snapshot,
                close_prices,
                strategy=strategy,
                min_candidates=min_candidates,
                max_names=max_names,
                variant=variant,
            )
        return _selector

    low = run_backtest(
        fundamentals,
        panel,
        strategy="profitability_proxy",
        cost_bps=cost_bps,
        min_candidates=min_candidates,
        max_names=max_names,
        start_date=REQUIRED_REBALANCE_START,
        selector=selector_for("low"),
    )
    all_names = run_backtest(
        fundamentals,
        panel,
        strategy="profitability_proxy",
        cost_bps=cost_bps,
        min_candidates=min_candidates,
        start_date=REQUIRED_REBALANCE_START,
        max_names=10_000,
        selector=selector_for("all"),
    )
    high_summary = high_result.summary()
    low_summary = low.summary()
    all_summary = all_names.summary()
    high_minus_low = _relative_curve_metrics(high_result.equity, low.equity)
    high_minus_all = _relative_curve_metrics(high_result.equity, all_names.equity)
    passed = bool(
        float(high_summary.get("total_return_pct", 0.0)) > float(all_summary.get("total_return_pct", 0.0))
        and float(high_minus_low.get("relative_sharpe", 0.0)) > 0.0
        and float(high_minus_all.get("relative_sharpe", 0.0)) > 0.0
    )
    return {
        "cost_bps": int(cost_bps),
        "diagnostic_only": True,
        "theoretical_high_minus_low_is_not_executable_shorting_evidence": True,
        "passed_directional_factor_check": passed,
        "high_profitability": high_summary,
        "low_profitability": low_summary,
        "equal_weight_eligible_universe": all_summary,
        "high_minus_low": high_minus_low,
        "high_minus_all": high_minus_all,
    }


def assess_strategy_asof_coverage(
    fundamentals: pd.DataFrame,
    panel: pd.DataFrame,
    *,
    strategies: tuple[str, ...],
    required_rebalance_start: pd.Timestamp = REQUIRED_REBALANCE_START,
    min_factor_ready_codes: int = MIN_FACTOR_READY_CODES_PER_REBALANCE,
) -> dict[str, Any]:
    prices = normalize_price_panel(panel)
    dates = sorted(set(prices["Date"]))
    signals = sorted(day for day in month_end_signal_dates(dates) if day >= pd.Timestamp(required_rebalance_start))
    checkpoints: list[dict[str, Any]] = []
    failures: dict[str, int] = {strategy: 0 for strategy in strategies}
    minimums: dict[str, int] = {strategy: 10**9 for strategy in strategies}
    for day in signals:
        snapshot = pit_factor_snapshot(fundamentals, day, universe_panel=prices, require_revision_safe=True)
        row: dict[str, Any] = {"date": pd.Timestamp(day).date().isoformat()}
        for strategy in strategies:
            required = STRATEGY_FACTOR_COLUMNS[strategy]
            if snapshot.empty or any(column not in snapshot.columns for column in required):
                count = 0
            else:
                mask = pd.Series(True, index=snapshot.index, dtype=bool)
                for column in required:
                    numeric = pd.to_numeric(snapshot[column], errors="coerce")
                    mask &= numeric.notna()
                    if column == "bps":
                        mask &= numeric > 0
                count = int(mask.sum())
            passed = count >= int(min_factor_ready_codes)
            failures[strategy] += int(not passed)
            minimums[strategy] = min(minimums[strategy], count)
            row[f"{strategy}_ready_codes"] = count
            row[f"{strategy}_passed"] = passed
        checkpoints.append(row)
    if not checkpoints:
        return {
            "passed": False,
            "reasons": ["no_historical_rebalance_checkpoints"],
            "minimum_ready_codes": {strategy: 0 for strategy in strategies},
            "checkpoint_count": 0,
            "checkpoints": [],
        }
    reasons = [
        f"{strategy}_rebalance_checkpoints_below_minimum:{failures[strategy]}/{len(checkpoints)}"
        for strategy in strategies
        if failures[strategy]
    ]
    return {
        "passed": not reasons,
        "reasons": reasons,
        "thresholds": {
            "required_rebalance_start_on_or_after": pd.Timestamp(required_rebalance_start).date().isoformat(),
            "min_factor_ready_codes_per_rebalance": int(min_factor_ready_codes),
        },
        "minimum_ready_codes": {strategy: int(minimums[strategy]) for strategy in strategies},
        "checkpoint_count": len(checkpoints),
        "checkpoints": checkpoints,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fundamentals", type=Path, default=FUND_CSV)
    parser.add_argument("--panel", type=Path, default=PANEL_CSV)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--strategies", nargs="+", choices=STRATEGIES, default=list(STRATEGIES))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.fundamentals.exists():
        print(f"BLOCKED_MISSING_TRUE_PIT_FUNDAMENTALS:{args.fundamentals}")
        return 2
    if not args.panel.exists():
        print(f"BLOCKED_MISSING_PIT_PANEL:{args.panel}")
        return 2

    fundamentals = pd.read_csv(args.fundamentals, dtype={"code": str, "rcept_no": str, "reprt_code": str})
    panel = pd.read_csv(args.panel, dtype={"code": str})
    selected_strategies = tuple(dict.fromkeys(args.strategies))
    contract = validate_pit_contract(fundamentals, panel, required_value_columns=())
    if not contract.eligible:
        print(f"BLOCKED_PIT_CONTRACT:{','.join(contract.reasons)}")
        return 3
    asof_coverage = assess_strategy_asof_coverage(
        fundamentals,
        panel,
        strategies=selected_strategies,
    )
    if not asof_coverage["passed"]:
        print(f"BLOCKED_ASOF_FACTOR_COVERAGE:{','.join(asof_coverage['reasons'])}")
        return 4

    rows: list[dict[str, Any]] = []
    details: dict[str, Any] = {}
    result_objects: dict[tuple[str, int], BacktestResult] = {}
    for strategy in selected_strategies:
        for cost_bps in COST_LEVELS_BPS:
            result = run_backtest(
                fundamentals,
                panel,
                strategy=strategy,
                cost_bps=cost_bps,
                start_date=REQUIRED_REBALANCE_START,
            )
            summary = result.summary()
            rows.append(summary)
            result_objects[(strategy, int(cost_bps))] = result
            details[f"{strategy}_{cost_bps}bp"] = {"summary": summary, "rebalances": result.rebalances}

    profitability_diagnostic = None
    if "profitability_proxy" in selected_strategies and ("profitability_proxy", 75) in result_objects:
        profitability_diagnostic = build_profitability_diagnostic(
            fundamentals,
            panel,
            high_result=result_objects[("profitability_proxy", 75)],
            cost_bps=75,
        )

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "live_promotion_allowed": False,
        "validation_state": "RESEARCH_ONLY_REQUIRES_INDEPENDENT_OOS",
        "pit_contract": contract.to_dict(),
        "strategy_asof_coverage": asof_coverage,
        "selected_strategies": list(selected_strategies),
        "execution": "month-end close signal -> next trading-day open; fractional research sizing",
        "round_trip_cost_stress_bps": list(COST_LEVELS_BPS),
        "results": rows,
        "profitability_diagnostic_75bp": profitability_diagnostic,
        "details": details,
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    out = args.out_dir / "hml_cma_true_pit_latest.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    pd.DataFrame(rows).to_csv(args.out_dir / "hml_cma_true_pit_latest.csv", index=False)
    curve_paths: dict[str, str] = {}
    for strategy in selected_strategies:
        result = result_objects.get((strategy, 75))
        if result is None or result.equity.empty:
            continue
        curve = result.equity[["date", "equity"]].copy().sort_values("date")
        curve["date"] = pd.to_datetime(curve["date"]).dt.date.astype(str)
        curve["daily_return"] = pd.to_numeric(curve["equity"], errors="coerce").pct_change(fill_method=None)
        curve_path = args.out_dir / f"hml_cma_true_pit_{strategy}_75bp_curve.csv"
        curve.to_csv(curve_path, index=False)
        curve_paths[strategy] = str(curve_path)
    payload["daily_curve_files_75bp"] = curve_paths
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(pd.DataFrame(rows).to_string(index=False))
    if profitability_diagnostic is not None:
        print("profitability_directional_factor_check=", profitability_diagnostic["passed_directional_factor_check"])
        print("profitability_high_minus_all=", profitability_diagnostic["high_minus_all"])
        print("profitability_high_minus_low=", profitability_diagnostic["high_minus_low"])
    print(f"report={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
