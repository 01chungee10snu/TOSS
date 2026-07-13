"""Backtest inverse-ETF sleeve overlays on the current forward paper strategy.

Research/paper only. No live orders.

The base strategy is the existing counterfactual forward paper ledger.  Overlay
variants answer: if the macro regime says risk_off, should the system hold cash
or replace the long book with a fixed allocation to inverse ETFs?
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
BASE_DAILY = ROOT / "reports/harness/paper_portfolio/full_sim_2024_to_today/daily_equity.csv"
OUT_DIR = ROOT / "reports/harness"
OUT_CSV = OUT_DIR / "inverse_sleeve_overlay_20260703.csv"
OUT_MD = OUT_DIR / "inverse_sleeve_overlay_20260703.md"
OUT_JSON = OUT_DIR / "inverse_sleeve_overlay_20260703.json"

ETF_TICKERS = {
    "kodex_inverse": "114800.KS",
    "kodex_200_futures_inverse_2x": "252670.KS",
    "kodex_kosdaq150_futures_inverse": "251340.KS",
}

TRIGGERS = {
    "risk_off_only": {"risk_off"},
    "neutral_or_risk_off": {"neutral", "risk_off"},
}
ALLOCATIONS = [0.10, 0.20, 0.30, 0.50, 1.00]
STARTING_EQUITY = 1_000_000.0


def flatten_download(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    return df


def load_etf_returns(start: str, end: str) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for name, ticker in ETF_TICKERS.items():
        df = yf.download(ticker, start=start, end=end, auto_adjust=True, progress=False, threads=False)
        df = flatten_download(df)
        if df.empty:
            raise RuntimeError(f"empty yfinance data: {ticker}")
        close = df[["Close"]].rename(columns={"Close": name}).copy()
        close.index = pd.to_datetime(close.index).tz_localize(None)
        frames.append(close)
    px = pd.concat(frames, axis=1).sort_index()
    return px.pct_change().fillna(0.0)


def metrics(curve: pd.Series) -> dict[str, float]:
    rets = curve.pct_change().dropna()
    peak = curve.cummax()
    dd = curve / peak - 1.0
    sharpe = 0.0
    if len(rets) > 1 and rets.std(ddof=0) > 0:
        sharpe = float(rets.mean() / rets.std(ddof=0) * math.sqrt(252))
    return {
        "final_equity": float(curve.iloc[-1]),
        "total_return_pct": float((curve.iloc[-1] / curve.iloc[0] - 1.0) * 100.0),
        "max_drawdown_pct": float(dd.min() * 100.0),
        "sharpe": sharpe,
        "daily_mean_pct": float(rets.mean() * 100.0) if len(rets) else 0.0,
        "daily_median_pct": float(rets.median() * 100.0) if len(rets) else 0.0,
        "daily_win_rate_pct": float((rets > 0).mean() * 100.0) if len(rets) else 0.0,
    }


def build_curve(base: pd.DataFrame, daily_ret: pd.Series, name: str, trigger_statuses: set[str], allocation: float) -> pd.Series:
    equity = [STARTING_EQUITY]
    statuses = base["macro_status"].tolist()
    base_rets = base["base_daily_ret"].tolist()
    inv_rets = daily_ret.reindex(base.index).fillna(0.0).tolist()
    # start from row 1 because row 0 is the first mark after initial deployment
    for i in range(1, len(base)):
        if statuses[i] in trigger_statuses:
            r = allocation * inv_rets[i]
        else:
            r = base_rets[i]
        equity.append(equity[-1] * (1.0 + r))
    return pd.Series(equity, index=base.index, name=name)


def main() -> None:
    if not BASE_DAILY.exists():
        raise SystemExit(f"missing base daily ledger: {BASE_DAILY}")
    base = pd.read_csv(BASE_DAILY, parse_dates=["date"])
    base = base.sort_values("date").reset_index(drop=True)
    base.index = pd.to_datetime(base["date"])
    base["base_daily_ret"] = base["total_equity"].pct_change().fillna(base["total_equity"].iloc[0] / STARTING_EQUITY - 1.0)

    etf_rets = load_etf_returns(base.index.min().date().isoformat(), (base.index.max() + pd.Timedelta(days=3)).date().isoformat())
    etf_rets = etf_rets.reindex(base.index).fillna(0.0)

    rows: list[dict[str, object]] = []
    curves: dict[str, pd.Series] = {}

    base_curve = pd.Series(base["total_equity"].to_numpy(), index=base.index, name="base_long_strategy")
    base_m = metrics(base_curve)
    rows.append({
        "strategy": "base_long_strategy",
        "etf": "none",
        "ticker": "none",
        "trigger": "existing",
        "allocation": 0.0,
        "trigger_days": 0,
        **base_m,
    })
    curves["base_long_strategy"] = base_curve

    # Cash defensive baselines: replace long returns by zero on bad-regime days.
    for trigger_name, statuses in TRIGGERS.items():
        c = build_curve(base, pd.Series(0.0, index=base.index), f"cash_{trigger_name}", statuses, 0.0)
        rows.append({
            "strategy": f"cash_{trigger_name}",
            "etf": "cash",
            "ticker": "cash",
            "trigger": trigger_name,
            "allocation": 0.0,
            "trigger_days": int(base["macro_status"].isin(statuses).sum()),
            **metrics(c),
        })
        curves[c.name] = c

    for etf_name, ticker in ETF_TICKERS.items():
        for trigger_name, statuses in TRIGGERS.items():
            for allocation in ALLOCATIONS:
                curve_name = f"{etf_name}_{trigger_name}_{int(allocation*100)}pct"
                c = build_curve(base, etf_rets[etf_name], curve_name, statuses, allocation)
                rows.append({
                    "strategy": curve_name,
                    "etf": etf_name,
                    "ticker": ticker,
                    "trigger": trigger_name,
                    "allocation": allocation,
                    "trigger_days": int(base["macro_status"].isin(statuses).sum()),
                    **metrics(c),
                })
                curves[curve_name] = c

    result = pd.DataFrame(rows).sort_values(["sharpe", "total_return_pct"], ascending=False).reset_index(drop=True)
    result.to_csv(OUT_CSV, index=False)

    best = result.iloc[0].to_dict()
    best_return = result.sort_values("total_return_pct", ascending=False).iloc[0].to_dict()
    base_row = result[result["strategy"] == "base_long_strategy"].iloc[0].to_dict()

    # Save top curve points for audit.
    top_names = result.head(8)["strategy"].tolist()
    curve_df = pd.DataFrame({name: curves[name] for name in top_names if name in curves})
    curve_csv = OUT_DIR / "inverse_sleeve_overlay_curves_20260703.csv"
    curve_df.to_csv(curve_csv, index_label="date")

    payload = {
        "paper_only": True,
        "live_order_submitted": False,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "base_daily": str(BASE_DAILY),
        "period": {"start": base.index.min().date().isoformat(), "end": base.index.max().date().isoformat(), "days": int(len(base))},
        "etf_tickers": ETF_TICKERS,
        "triggers": {k: sorted(v) for k, v in TRIGGERS.items()},
        "best_by_sharpe": best,
        "best_by_total_return": best_return,
        "base": base_row,
        "files": {"csv": str(OUT_CSV), "md": str(OUT_MD), "json": str(OUT_JSON), "curves": str(curve_csv)},
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    top = result.head(12).copy()
    lines = [
        "# Inverse sleeve overlay backtest — 2026-07-03",
        "",
        "Research/paper only. No live orders.",
        "",
        f"- Base ledger: `{BASE_DAILY}`",
        f"- Period: {payload['period']['start']} ~ {payload['period']['end']} ({payload['period']['days']} trading marks)",
        f"- ETFs: {', '.join(f'{k}={v}' for k, v in ETF_TICKERS.items())}",
        "- Interpretation: on trigger days, replace the long-book daily return with `allocation × inverse ETF daily return`; non-trigger days keep the base long strategy.",
        "",
        "## Baseline vs winners",
        "",
        f"- Base total_return: {base_row['total_return_pct']:.2f}%, MDD {base_row['max_drawdown_pct']:.2f}%, Sharpe {base_row['sharpe']:.2f}",
        f"- Best Sharpe: `{best['strategy']}` total_return {best['total_return_pct']:.2f}%, MDD {best['max_drawdown_pct']:.2f}%, Sharpe {best['sharpe']:.2f}",
        f"- Best return: `{best_return['strategy']}` total_return {best_return['total_return_pct']:.2f}%, MDD {best_return['max_drawdown_pct']:.2f}%, Sharpe {best_return['sharpe']:.2f}",
        "",
        "## Top 12 by Sharpe",
        "",
        "| rank | strategy | ETF | trigger | alloc | return % | MDD % | Sharpe | daily mean % | win % |",
        "|---:|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for i, r in top.iterrows():
        lines.append(
            f"| {i+1} | {r['strategy']} | {r['ticker']} | {r['trigger']} | {float(r['allocation']):.2f} | "
            f"{float(r['total_return_pct']):.2f} | {float(r['max_drawdown_pct']):.2f} | {float(r['sharpe']):.2f} | "
            f"{float(r['daily_mean_pct']):.3f} | {float(r['daily_win_rate_pct']):.1f} |"
        )
    lines += [
        "",
        "## Files",
        "",
        f"- CSV: `{OUT_CSV}`",
        f"- JSON: `{OUT_JSON}`",
        f"- Curves: `{curve_csv}`",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"best_by_sharpe": best, "best_by_total_return": best_return, "base": base_row, "files": payload["files"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
