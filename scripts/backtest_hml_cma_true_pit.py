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


def run_backtest(
    fundamentals: pd.DataFrame,
    panel: pd.DataFrame,
    *,
    strategy: str,
    cost_bps: int,
    min_candidates: int = MIN_CANDIDATES,
    max_names: int = MAX_NAMES,
) -> BacktestResult:
    prices = normalize_price_panel(panel)
    opens = prices.pivot_table(index="Date", columns="code", values="Open", aggfunc="last").sort_index()
    closes = prices.pivot_table(index="Date", columns="code", values="Close", aggfunc="last").sort_index()
    dates = list(opens.index.intersection(closes.index))
    signals = month_end_signal_dates(dates)
    side_rate = (float(cost_bps) / 2.0) / 10_000.0

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
            codes = select_factor_codes(
                snapshot,
                close_prices,
                strategy=strategy,
                min_candidates=min_candidates,
                max_names=max_names,
            )
            pending = (dates[i + 1], codes)

    return BacktestResult(strategy=strategy, cost_bps=int(cost_bps), equity=pd.DataFrame(curve), rebalances=rebalances)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fundamentals", type=Path, default=FUND_CSV)
    parser.add_argument("--panel", type=Path, default=PANEL_CSV)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
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
    contract = validate_pit_contract(
        fundamentals,
        panel,
        required_value_columns=("bps", "assets", "operating_profitability_proxy"),
    )
    if not contract.eligible:
        print(f"BLOCKED_PIT_CONTRACT:{','.join(contract.reasons)}")
        return 3

    rows: list[dict[str, Any]] = []
    details: dict[str, Any] = {}
    for strategy in STRATEGIES:
        for cost_bps in COST_LEVELS_BPS:
            result = run_backtest(fundamentals, panel, strategy=strategy, cost_bps=cost_bps)
            summary = result.summary()
            rows.append(summary)
            details[f"{strategy}_{cost_bps}bp"] = {"summary": summary, "rebalances": result.rebalances}

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "live_promotion_allowed": False,
        "validation_state": "RESEARCH_ONLY_REQUIRES_INDEPENDENT_OOS",
        "pit_contract": contract.to_dict(),
        "execution": "month-end close signal -> next trading-day open; fractional research sizing",
        "round_trip_cost_stress_bps": list(COST_LEVELS_BPS),
        "results": rows,
        "details": details,
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    out = args.out_dir / "hml_cma_true_pit_latest.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    pd.DataFrame(rows).to_csv(args.out_dir / "hml_cma_true_pit_latest.csv", index=False)
    print(pd.DataFrame(rows).to_string(index=False))
    print(f"report={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
