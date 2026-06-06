from __future__ import annotations

import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import FinanceDataReader as fdr
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "reports" / "backtests"
OUT_DIR.mkdir(parents=True, exist_ok=True)

START = "2022-01-01"
END = "2025-12-31"
YF_END = "2026-01-01"
RANDOM_SEED = 20260607
SAMPLE_N = 500
EXCLUDE_CODES = {"005930", "000660"}
SHORT_WINDOW = 20
LONG_WINDOW = 60
STARTING_CASH = 1_000_000.0
FEE_BPS = 1.5
SLIPPAGE_BPS = 5.0
SELL_TAX_BPS = 18.0


def yf_suffix(row: pd.Series) -> str:
    market_id = str(row.get("MarketId", ""))
    market = str(row.get("Market", ""))
    if market_id == "KSQ" or market == "KOSDAQ":
        return ".KQ"
    return ".KS"


def build_universe() -> pd.DataFrame:
    listing = fdr.StockListing("KRX")
    listing = listing[listing["Market"].isin(["KOSPI", "KOSDAQ"])].copy()
    listing["Code"] = listing["Code"].astype(str).str.zfill(6)
    listing = listing[~listing["Code"].isin(EXCLUDE_CODES)].copy()
    # Avoid obvious non-common-stock noise while preserving randomness:
    # SPACs/REITs plus preferred shares (often non-numeric KRX codes or names ending in '우').
    listing = listing[listing["Code"].str.match(r"^\d{6}$")].copy()
    listing = listing[~listing["Name"].astype(str).str.contains("스팩|SPAC|리츠", case=False, regex=True)].copy()
    listing = listing[~listing["Name"].astype(str).str.endswith("우")].copy()
    listing["YFTicker"] = listing.apply(lambda r: r["Code"] + yf_suffix(r), axis=1)
    return listing.reset_index(drop=True)


def select_random(universe: pd.DataFrame) -> pd.DataFrame:
    rng = random.Random(RANDOM_SEED)
    idx = rng.sample(range(len(universe)), SAMPLE_N)
    return universe.iloc[idx].reset_index(drop=True)


def download_one(ticker: str) -> pd.DataFrame:
    df = yf.download(ticker, start=START, end=YF_END, auto_adjust=True, progress=False, threads=False)
    if df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df.reset_index()
    df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)
    df = df[(df["Date"] >= pd.to_datetime(START)) & (df["Date"] <= pd.to_datetime(END))].copy()
    return df.sort_values("Date").reset_index(drop=True)


def max_dd(series: pd.Series) -> float:
    peak = series.cummax()
    dd = series / peak - 1
    return float(dd.min()) if len(dd) else 0.0


def sharpe(ret: pd.Series) -> float:
    r = ret.dropna()
    sd = float(r.std())
    if sd == 0 or math.isnan(sd):
        return 0.0
    return float((r.mean() / sd) * math.sqrt(252))


def backtest_df(df: pd.DataFrame) -> tuple[dict[str, Any], pd.DataFrame | None]:
    if df.empty:
        return {"status": "NO_DATA", "violations": ["no_yfinance_data"]}, None
    if len(df) < LONG_WINDOW + 1:
        return {"status": "INSUFFICIENT_DATA", "rows": int(len(df)), "violations": [f"need_{LONG_WINDOW+1}_rows"]}, None
    data = df[["Date", "Close"]].copy()
    data["ma_short"] = data["Close"].rolling(SHORT_WINDOW).mean()
    data["ma_long"] = data["Close"].rolling(LONG_WINDOW).mean()
    data["signal"] = (data["ma_short"] > data["ma_long"]).astype(int)
    data.loc[data["ma_long"].isna(), "signal"] = 0
    data["position"] = data["signal"].shift(1).fillna(0)
    data["ret"] = data["Close"].pct_change().fillna(0.0)
    data["turnover"] = data["position"].diff().abs().fillna(data["position"].abs())
    data["sell_turnover"] = (data["position"].shift(1).fillna(0) - data["position"]).clip(lower=0)
    fee_rate = FEE_BPS / 10000.0
    slip_rate = SLIPPAGE_BPS / 10000.0
    sell_tax_rate = SELL_TAX_BPS / 10000.0
    data["cost"] = data["turnover"] * (fee_rate + slip_rate) + data["sell_turnover"] * sell_tax_rate
    data["strategy_ret"] = data["position"] * data["ret"] - data["cost"]
    data["buyhold_ret"] = data["ret"]
    data["equity"] = STARTING_CASH * (1 + data["strategy_ret"]).cumprod()
    data["buyhold_equity"] = STARTING_CASH * (1 + data["buyhold_ret"]).cumprod()
    final = float(data["equity"].iloc[-1])
    bh_final = float(data["buyhold_equity"].iloc[-1])
    years = max((data["Date"].iloc[-1] - data["Date"].iloc[0]).days / 365.25, 1e-9)
    gross_profit = float(data.loc[data["strategy_ret"] > 0, "strategy_ret"].sum())
    gross_loss = abs(float(data.loc[data["strategy_ret"] < 0, "strategy_ret"].sum()))
    summary = {
        "status": "PASS",
        "rows": int(len(data)),
        "first_date": data["Date"].iloc[0].strftime("%Y-%m-%d"),
        "last_date": data["Date"].iloc[-1].strftime("%Y-%m-%d"),
        "final_value_krw": round(final, 2),
        "total_return_pct": round((final / STARTING_CASH - 1) * 100, 2),
        "cagr_pct": round(((final / STARTING_CASH) ** (1 / years) - 1) * 100, 2),
        "max_drawdown_pct": round(max_dd(data["equity"]) * 100, 2),
        "sharpe": round(sharpe(data["strategy_ret"]), 3),
        "trades": int((data["turnover"] > 0).sum()),
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else None,
        "buyhold_return_pct": round((bh_final / STARTING_CASH - 1) * 100, 2),
        "invested_days_pct": round(float(data["position"].mean()) * 100, 2),
        "outperformed_buyhold": (final > bh_final),
    }
    return summary, data[["Date", "equity", "buyhold_equity", "position"]]


def describe(values: pd.Series) -> dict[str, float]:
    q = values.quantile([0.05, 0.25, 0.5, 0.75, 0.95])
    return {
        "mean": round(float(values.mean()), 2),
        "median": round(float(q.loc[0.5]), 2),
        "p05": round(float(q.loc[0.05]), 2),
        "p25": round(float(q.loc[0.25]), 2),
        "p75": round(float(q.loc[0.75]), 2),
        "p95": round(float(q.loc[0.95]), 2),
    }


def main() -> None:
    universe = build_universe()
    sample = select_random(universe)
    sample_records = []
    summaries = []
    curves = []
    for i, row in sample.iterrows():
        code = row["Code"]
        name = row["Name"]
        ticker = row["YFTicker"]
        print(f"[{i+1:03d}/{SAMPLE_N}] {code} {name} {ticker}", flush=True)
        try:
            df = download_one(ticker)
            summary, curve = backtest_df(df)
        except Exception as e:
            summary, curve = {"status": "ERROR", "violations": [repr(e)]}, None
        summary.update({"code": code, "name": name, "market": row["Market"], "yfinance_symbol": ticker})
        summaries.append(summary)
        sample_records.append({"code": code, "name": name, "market": row["Market"], "yfinance_symbol": ticker})
        if curve is not None and summary["status"] == "PASS":
            curves.append(curve[["Date", "equity"]].rename(columns={"equity": code}).set_index("Date"))

    summary_df = pd.DataFrame(summaries)
    pass_df = summary_df[summary_df["status"] == "PASS"].copy()
    portfolio_summary: dict[str, Any] = {}
    if curves:
        portfolio = pd.concat(curves, axis=1).sort_index()
        portfolio["equity"] = portfolio.mean(axis=1, skipna=True)
        portfolio = portfolio.dropna(subset=["equity"])
        port_ret = portfolio["equity"].pct_change().fillna(0.0)
        years = max((portfolio.index[-1] - portfolio.index[0]).days / 365.25, 1e-9)
        final = float(portfolio["equity"].iloc[-1])
        portfolio_summary = {
            "construction": "equal-weight average of valid sampled strategy equity curves; missing listing days ignored per date",
            "first_date": portfolio.index[0].strftime("%Y-%m-%d"),
            "last_date": portfolio.index[-1].strftime("%Y-%m-%d"),
            "valid_symbols": int(len(curves)),
            "final_value_krw": round(final, 2),
            "total_return_pct": round((final / STARTING_CASH - 1) * 100, 2),
            "cagr_pct": round(((final / STARTING_CASH) ** (1 / years) - 1) * 100, 2),
            "max_drawdown_pct": round(max_dd(portfolio["equity"]) * 100, 2),
            "sharpe": round(sharpe(port_ret), 3),
        }
        portfolio.to_csv(OUT_DIR / f"random500_seed{RANDOM_SEED}_portfolio_curve.csv")

    aggregate = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "universe_size_after_exclusions": int(len(universe)),
        "random_seed": RANDOM_SEED,
        "sample_n": SAMPLE_N,
        "excluded_codes": sorted(EXCLUDE_CODES),
        "period": {"start": START, "end": END},
        "strategy": {
            "name": "long_when_MA20_above_MA60_else_cash",
            "no_lookahead": "signal at close, position applied next trading day",
            "short_window": SHORT_WINDOW,
            "long_window": LONG_WINDOW,
        },
        "costs": {"fee_bps_each_side": FEE_BPS, "slippage_bps_each_side": SLIPPAGE_BPS, "sell_tax_bps_on_sells": SELL_TAX_BPS},
        "status_counts": summary_df["status"].value_counts().to_dict(),
        "valid_count": int(len(pass_df)),
        "return_pct_distribution": describe(pass_df["total_return_pct"]) if len(pass_df) else {},
        "mdd_pct_distribution": describe(pass_df["max_drawdown_pct"]) if len(pass_df) else {},
        "sharpe_distribution": describe(pass_df["sharpe"]) if len(pass_df) else {},
        "positive_return_count": int((pass_df["total_return_pct"] > 0).sum()) if len(pass_df) else 0,
        "outperformed_buyhold_count": int(pass_df["outperformed_buyhold"].sum()) if len(pass_df) else 0,
        "portfolio_equal_weight": portfolio_summary,
        "top10_by_total_return": pass_df.sort_values("total_return_pct", ascending=False).head(10).to_dict(orient="records"),
        "bottom10_by_total_return": pass_df.sort_values("total_return_pct", ascending=True).head(10).to_dict(orient="records"),
        "disclaimer": "Research-only random-sample backtest; not investment advice; live orders not submitted.",
    }

    stem = f"random500_seed{RANDOM_SEED}_ma{SHORT_WINDOW}_{LONG_WINDOW}_{START}_{END}"
    summary_csv = OUT_DIR / f"{stem}_summary.csv"
    sample_csv = OUT_DIR / f"{stem}_sample.csv"
    json_path = OUT_DIR / f"{stem}.json"
    md_path = OUT_DIR / f"{stem}.md"
    summary_df.to_csv(summary_csv, index=False)
    pd.DataFrame(sample_records).to_csv(sample_csv, index=False)
    json_path.write_text(json.dumps(aggregate, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    lines = [
        f"# Random 500 Korean equities momentum backtest — seed {RANDOM_SEED}",
        "",
        "Research-only. 실주문 없음. 투자 조언 아님.",
        "",
        "## Setup",
        f"- Universe: KOSPI/KOSDAQ common-stock-like names from FinanceDataReader KRX listing, excluding {', '.join(sorted(EXCLUDE_CODES))}",
        f"- Random seed: {RANDOM_SEED}",
        f"- Sample: {SAMPLE_N}",
        f"- Period: {START} ~ {END}",
        f"- Strategy: MA{SHORT_WINDOW} > MA{LONG_WINDOW}이면 다음 거래일부터 long, 아니면 cash",
        f"- Cost: fee {FEE_BPS} bps/side + slippage {SLIPPAGE_BPS} bps/side + sell tax {SELL_TAX_BPS} bps on sells",
        "",
        "## Aggregate",
        f"- Status counts: {aggregate['status_counts']}",
        f"- Valid symbols: {aggregate['valid_count']} / {SAMPLE_N}",
        f"- Positive return count: {aggregate['positive_return_count']} / {aggregate['valid_count']}",
        f"- Outperformed buy-and-hold count: {aggregate['outperformed_buyhold_count']} / {aggregate['valid_count']}",
        f"- Return pct distribution: {aggregate['return_pct_distribution']}",
        f"- MDD pct distribution: {aggregate['mdd_pct_distribution']}",
        f"- Sharpe distribution: {aggregate['sharpe_distribution']}",
        "",
        "## Equal-weight portfolio",
    ]
    for k, v in portfolio_summary.items():
        lines.append(f"- {k}: {v}")
    lines.extend(["", "## Top 10 by total_return_pct"])
    for r in aggregate["top10_by_total_return"]:
        lines.append(f"- {r['code']} {r['name']} ({r['market']}): {r['total_return_pct']}%, MDD {r['max_drawdown_pct']}%, Sharpe {r['sharpe']}, buyhold {r['buyhold_return_pct']}%")
    lines.extend(["", "## Bottom 10 by total_return_pct"])
    for r in aggregate["bottom10_by_total_return"]:
        lines.append(f"- {r['code']} {r['name']} ({r['market']}): {r['total_return_pct']}%, MDD {r['max_drawdown_pct']}%, Sharpe {r['sharpe']}, buyhold {r['buyhold_return_pct']}%")
    lines.extend(["", "## Outputs", f"- summary_csv: {summary_csv}", f"- sample_csv: {sample_csv}", f"- json: {json_path}"])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(aggregate, ensure_ascii=False, indent=2, default=str))
    print(f"REPORT_MD={md_path}")
    print(f"SUMMARY_CSV={summary_csv}")
    print(f"SAMPLE_CSV={sample_csv}")
    print(f"REPORT_JSON={json_path}")


if __name__ == "__main__":
    main()
