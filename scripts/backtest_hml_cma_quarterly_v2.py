"""Legacy HML + revenue-growth proxy quarterly backtest (v2 - PIT-timed).

Key fix vs v1: Instead of using Naver's current PBR snapshot (which embeds
current market price = look-ahead bias), we compute time-aligned B/M ratio:
  B/M = BPS_at_assumed_availability_date / close_price_at_rebal_date

Forecast columns marked ``(E)`` are excluded.  However, Naver exposes a
current-view fundamentals table rather than historical filing snapshots, so
this script is *not* fully PIT-correct. Historical revisions and universe
survivorship remain validation risks until filing-date data are used.

Strategy:
  - HML (High Minus Low): Book-to-Market ratio. Long high B/M stocks.
  - Legacy 'CMA' label: revenue-growth proxy only; NOT canonical Fama-French CMA.
    Canonical CMA requires total-asset growth and is implemented in the strict true-PIT pipeline.
  - Combined intersection: Must rank well on BOTH factors.

Cost: 31bp, 50bp, 75bp round-trip.
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

FUND_CSV = ROOT / "reports" / "backtests" / "fundamental" / "naver_quarterly_fundamentals.csv"
PANEL_CSV = ROOT / "reports" / "backtests" / "practical_universe_400_2022-01-01_2026-latest_ohlcv_panel.csv"
OUT_DIR = ROOT / "reports" / "validation"
OUT_DIR.mkdir(parents=True, exist_ok=True)

INITIAL_CAPITAL = 1_000_000.0
COST_LEVELS = [31, 50, 75]
TOP_QUANTILE_PCT = 0.20
BOTTOM_QUANTILE_PCT = 0.20
PIT_DELAY_ANNUAL_DAYS = 90
PIT_DELAY_QUARTERLY_DAYS = 60


def compute_signal_date(year: int, month: int, period_type: str) -> pd.Timestamp:
    period_end = pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(1)
    delay = PIT_DELAY_ANNUAL_DAYS if period_type == "annual" else PIT_DELAY_QUARTERLY_DAYS
    return period_end + pd.Timedelta(days=delay)


def estimate_mask(fund: pd.DataFrame) -> pd.Series:
    """Return a conservative estimate/forecast mask for old and new CSVs."""
    from_period = fund.get("period", pd.Series("", index=fund.index)).astype(str).str.contains(
        r"\(E\)", regex=True, case=False, na=False
    )
    if "is_estimate" not in fund.columns:
        return from_period

    explicit = fund["is_estimate"]
    if explicit.dtype == bool:
        explicit_bool = explicit.fillna(False)
    else:
        explicit_bool = (
            explicit.fillna(False)
            .astype(str)
            .str.strip()
            .str.lower()
            .isin({"true", "1", "yes", "y"})
        )
    return from_period | explicit_bool


def normalize_period_metadata(fund: pd.DataFrame) -> pd.DataFrame:
    """Repair legacy annual/quarterly labels, including duplicated Q4 YYYY.12.

    Older collector output inferred ``month == 12`` as annual, which mislabeled
    the quarterly Q4 column. Naver displays annual columns before quarterly
    columns, so for duplicated code/year/month December rows the first is annual
    and later occurrence(s) are quarterly.
    """
    fund = fund.copy()
    fund["code"] = fund["code"].astype(str).str.zfill(6)
    fund = fund.dropna(subset=["year", "month"])
    fund["year"] = fund["year"].astype(int)
    fund["month"] = fund["month"].astype(int)
    fund["period_type"] = fund.get("period_type", "unknown").astype(str).str.lower()

    non_december = fund["month"] != 12
    fund.loc[non_december, "period_type"] = "quarterly"

    december = fund["month"] == 12
    if december.any():
        occurrence = fund.groupby(["code", "year", "month"]).cumcount()
        group_size = fund.groupby(["code", "year", "month"])["code"].transform("size")
        duplicated_q4 = december & (group_size > 1) & (occurrence > 0)
        fund.loc[december & ~duplicated_q4, "period_type"] = "annual"
        fund.loc[duplicated_q4, "period_type"] = "quarterly"

    return fund


def add_revenue_yoy(fund: pd.DataFrame) -> pd.DataFrame:
    """Compute true year-over-year revenue growth by matching same period type/month."""
    fund = fund.copy()
    prev = fund[["code", "period_type", "year", "month", "revenue"]].copy()
    prev["year"] = prev["year"] + 1
    prev = prev.rename(columns={"revenue": "revenue_prev_year"})
    fund = fund.merge(
        prev,
        on=["code", "period_type", "year", "month"],
        how="left",
        validate="many_to_one",
    )
    fund["rev_yoy"] = fund["revenue"] / fund["revenue_prev_year"] - 1.0
    return fund


def prepare_fundamentals(fund: pd.DataFrame) -> pd.DataFrame:
    fund = normalize_period_metadata(fund)
    fund["is_estimate"] = estimate_mask(fund)
    fund = fund[~fund["is_estimate"]].copy()

    # Defensive de-duplication after repairing legacy Q4 period labels.
    fund = fund.drop_duplicates(
        subset=["code", "year", "month", "period_type"],
        keep="last",
    )
    fund = add_revenue_yoy(fund)
    fund["signal_date"] = fund.apply(
        lambda r: compute_signal_date(r["year"], r["month"], r["period_type"]),
        axis=1,
    )
    today = pd.Timestamp.now(tz=None).normalize()
    fund = fund[fund["signal_date"] <= today]
    fund = fund.sort_values(["code", "signal_date", "period_type"]).reset_index(drop=True)
    return fund


def get_pit_snapshot(fund: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    avail = fund[fund["signal_date"] <= as_of]
    if avail.empty:
        return pd.DataFrame()
    return avail.sort_values("signal_date").groupby("code").last().reset_index()


def get_close_prices(panel: pd.DataFrame) -> pd.DataFrame:
    p = panel.copy()
    p["Date"] = pd.to_datetime(p["Date"])
    p["code"] = p["code"].astype(str).str.zfill(6)
    return p.pivot_table(index="Date", columns="code", values="Close", aggfunc="first").sort_index()


def select_portfolio_pit(
    fund_snap: pd.DataFrame,
    closes: pd.DataFrame,
    rebal_date: pd.Timestamp,
    strategy: str,
) -> list[str]:
    if fund_snap.empty:
        return []
    snap = fund_snap[["code", "bps", "rev_yoy", "revenue"]].copy()
    snap = snap[snap["bps"].notna() & (snap["bps"] > 0)]
    snap = snap[snap["rev_yoy"].notna()]
    if len(snap) < 10:
        return []

    available_dates = closes.index[closes.index <= rebal_date]
    if available_dates.empty:
        return []
    price_date = available_dates[-1]
    prices = closes.loc[price_date]

    snap["close"] = snap["code"].map(prices).astype(float)
    snap = snap.dropna(subset=["close"])
    snap = snap[snap["close"] > 0]
    snap["bm"] = snap["bps"] / snap["close"]

    n = len(snap)
    snap["bm_rank"] = snap["bm"].rank(ascending=True, pct=True)
    snap["growth_rank"] = snap["rev_yoy"].rank(ascending=True, pct=True)

    if strategy == "hml_only":
        sel = snap[snap["bm_rank"] >= 1 - TOP_QUANTILE_PCT]
    elif strategy == "cma_only":
        sel = snap[snap["growth_rank"] <= BOTTOM_QUANTILE_PCT]
    elif strategy == "hml_cma_intersection":
        val = snap[snap["bm_rank"] >= 1 - TOP_QUANTILE_PCT]
        cons = snap[snap["growth_rank"] <= BOTTOM_QUANTILE_PCT]
        sel = val[val["code"].isin(cons["code"])]
    elif strategy == "hml_cma_composite":
        snap["score"] = 0.5 * snap["bm_rank"] + 0.5 * (1 - snap["growth_rank"])
        sel = snap.nlargest(max(5, int(n * TOP_QUANTILE_PCT)), "score")
    else:
        return []

    codes = sel["code"].tolist()
    return codes[:20]


@dataclass
class BT:
    strategy: str
    cost_bps: int
    equity_curve: list = field(default_factory=list)
    rebalances: list = field(default_factory=list)
    final_equity: float = INITIAL_CAPITAL

    def summary(self) -> dict:
        if not self.equity_curve:
            return {"error": "empty"}
        df = pd.DataFrame(self.equity_curve)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        final = float(df["equity"].iloc[-1])
        total_ret = (final - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
        df["ret"] = df["equity"].pct_change()
        rets = df["ret"].dropna()
        df["peak"] = df["equity"].cummax()
        df["dd"] = df["equity"] / df["peak"] - 1
        mdd = float(df["dd"].min()) * 100
        n_days = len(df)
        years = n_days / 252
        cagr = ((final / INITIAL_CAPITAL) ** (1 / years) - 1) * 100 if years > 0 else 0
        rf_d = 0.03 / 252
        excess = rets - rf_d
        sharpe = float(excess.mean() / excess.std() * math.sqrt(252)) if excess.std() > 0 else 0
        downside = excess[excess < 0]
        sortino = float(excess.mean() / downside.std() * math.sqrt(252)) if len(downside) > 1 and downside.std() > 0 else 0
        calmar = cagr / abs(mdd) if mdd < 0 else 0
        df["year"] = df["date"].dt.year
        yearly = {}
        for yr in sorted(df["year"].unique()):
            yd = df[df["year"] == yr]
            if len(yd) > 1:
                yearly[str(int(yr))] = round((float(yd["equity"].iloc[-1]) / float(yd["equity"].iloc[0]) - 1) * 100, 2)
        return {
            "strategy": self.strategy,
            "cost_bps": int(self.cost_bps),
            "total_return_pct": round(total_ret, 2),
            "cagr_pct": round(cagr, 2),
            "sharpe_ratio": round(sharpe, 3),
            "sortino_ratio": round(sortino, 3),
            "max_drawdown_pct": round(mdd, 2),
            "calmar_ratio": round(calmar, 3),
            "trading_days": int(n_days),
            "rebalances": int(len(self.rebalances)),
            "final_equity_krw": round(final, 0),
            "yearly_returns_pct": yearly,
            "holdings_per_rebalance": [int(len(r)) for r in self.rebalances],
        }


def run_backtest(
    fund: pd.DataFrame,
    closes: pd.DataFrame,
    strategy: str,
    cost_bps: int,
) -> BT:
    result = BT(strategy=strategy, cost_bps=cost_bps)
    all_dates = closes.index.tolist()

    seen = set()
    rebal_dates = []
    for d in all_dates:
        ym = (d.year, d.month)
        if d.month in [1, 4, 7, 10] and ym not in seen:
            rebal_dates.append(d)
            seen.add(ym)

    if len(rebal_dates) < 3:
        return result

    equity = INITIAL_CAPITAL
    prev_codes: list[str] = []
    cost_rate = cost_bps / 10_000.0

    for i, rd in enumerate(rebal_dates):
        if i < 1:
            continue
        snap = get_pit_snapshot(fund, rd)
        if snap.empty:
            continue
        codes = select_portfolio_pit(snap, closes, rd, strategy)
        if not codes:
            continue

        next_rd = rebal_dates[i + 1] if i + 1 < len(rebal_dates) else all_dates[-1]
        mask = (closes.index >= rd) & (closes.index < next_rd)
        period = closes.loc[mask, [c for c in codes if c in closes.columns]]
        if period.empty or len(period) < 2:
            continue

        valid = [c for c in codes if c in period.columns and not period[c].dropna().empty]
        if not valid:
            continue

        n_hold = len(valid)
        w = 1.0 / n_hold
        new_set, old_set = set(valid), set(prev_codes)
        turnover = len(new_set ^ old_set) / max(len(new_set | old_set), 1) if old_set else 1.0
        equity -= equity * turnover * cost_rate
        per_stock = equity * w

        entry = period[valid].iloc[0]
        exit_p = period[valid].iloc[-1]
        entry = entry.ffill()
        exit_p = exit_p.ffill()
        stock_rets = (exit_p / entry - 1).fillna(0)
        port_ret = float((stock_rets * w).sum())
        equity *= (1 + port_ret)

        result.rebalances.append(valid)

        daily = period[valid].ffill()
        if not daily.empty:
            base = daily.iloc[0]
            base_equity = equity / (1 + port_ret)
            for dt, row in daily.iterrows():
                dr = (row / base - 1).fillna(0)
                dpr = float((dr * w).sum())
                result.equity_curve.append({"date": dt.strftime("%Y-%m-%d"), "equity": round(base_equity * (1 + dpr), 2)})

        prev_codes = valid

    result.final_equity = equity
    return result


def main() -> None:
    print("=== Legacy HML + revenue-growth proxy backtest (v2 PIT-timed, estimates excluded) ===")
    fund = pd.read_csv(FUND_CSV)
    raw_estimates = int(estimate_mask(fund).sum())
    fund_prep = prepare_fundamentals(fund)
    print(f"Fundamentals: {len(fund_prep)} usable records, {fund_prep['code'].nunique()} stocks")
    print(f"Forecast/estimate rows excluded: {raw_estimates}")
    print("PIT caveat: current-view Naver fundamentals; historical filing snapshots not yet used")
    print(f"Signal dates: {sorted(fund_prep['signal_date'].dt.to_period('M').unique().astype(str))}")

    panel = pd.read_csv(PANEL_CSV)
    closes = get_close_prices(panel)
    print(f"Price panel: {closes.shape[0]} days, {closes.shape[1]} stocks")
    print(f"Date range: {closes.index.min().date()} ~ {closes.index.max().date()}")

    strategies = ["hml_only", "cma_only", "hml_cma_intersection", "hml_cma_composite"]
    all_results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config": {"initial_capital": INITIAL_CAPITAL, "cost_levels_bps": COST_LEVELS},
        "data_quality": {
            "forecast_estimates_excluded": True,
            "forecast_rows_excluded": raw_estimates,
            "q4_legacy_period_type_repaired": True,
            "revenue_growth_same_period_yoy": True,
            "canonical_cma_asset_growth": False,
            "legacy_cma_label_is_revenue_growth_proxy": True,
            "historical_filing_snapshot_pit": False,
            "survivorship_bias_resolved": False,
        },
    }

    for strat in strategies:
        print(f"\n--- {strat} ---")
        strat_res = {}
        for cb in COST_LEVELS:
            bt = run_backtest(fund_prep, closes, strat, cb)
            s = bt.summary()
            strat_res[f"{cb}bp"] = s
            avg_h = np.mean(s.get("holdings_per_rebalance", [0])) if s.get("holdings_per_rebalance") else 0
            print(f"  {cb}bp: ret={s.get('total_return_pct','?')}% cagr={s.get('cagr_pct','?')}% "
                  f"sharpe={s.get('sharpe_ratio','?')} mdd={s.get('max_drawdown_pct','?')}% "
                  f"rebal={s.get('rebalances',0)} avg_holdings={avg_h:.1f}")
        all_results[strat] = strat_res

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = OUT_DIR / f"hml_cma_quarterly_v2_{ts}.json"
    with open(out, "w") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nSaved: {out}")

    print(f"\n=== SUMMARY (31bp) ===")
    print(f"{'Strategy':<25} {'CAGR%':>8} {'Sharpe':>8} {'MDD%':>8} {'Ret%':>8}")
    for strat in strategies:
        s = all_results[strat].get("31bp", {})
        print(f"{strat:<25} {s.get('cagr_pct','?'):>8} {s.get('sharpe_ratio','?'):>8} "
              f"{s.get('max_drawdown_pct','?'):>8} {s.get('total_return_pct','?'):>8}")


if __name__ == "__main__":
    main()
