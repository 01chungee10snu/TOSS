from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
SAMPLE_CSV = OUT_DIR / f"random500_seed{RANDOM_SEED}_ma20_60_{START}_{END}_sample.csv"
PANEL_CSV = OUT_DIR / f"random500_seed{RANDOM_SEED}_{START}_{END}_ohlcv_panel.csv"
TOP_N = 10
STARTING_CASH = 1_000_000.0
FEE_BPS = 1.5
SLIPPAGE_BPS = 5.0
SELL_TAX_BPS = 18.0
MIN_DOLLAR_VOLUME_KRW = 100_000_000


def download_one(ticker: str) -> pd.DataFrame:
    df = yf.download(ticker, start=START, end=YF_END, auto_adjust=True, progress=False, threads=False)
    if df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df.reset_index()
    df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)
    df = df[(df["Date"] >= pd.to_datetime(START)) & (df["Date"] <= pd.to_datetime(END))].copy()
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col not in df.columns:
            df[col] = pd.NA
    return df[["Date", "Open", "High", "Low", "Close", "Volume"]].sort_values("Date").reset_index(drop=True)


def load_or_build_panel() -> tuple[pd.DataFrame, dict[str, int]]:
    if PANEL_CSV.exists():
        panel = pd.read_csv(PANEL_CSV, dtype={"code": str}, parse_dates=["Date"])
        return panel, {"PANEL_CACHE": int(panel["code"].nunique())}
    sample = pd.read_csv(SAMPLE_CSV, dtype={"code": str})
    frames = []
    counts: dict[str, int] = {}
    for i, row in sample.iterrows():
        code = str(row["code"]).zfill(6)
        ticker = row["yfinance_symbol"]
        print(f"[{i + 1:03d}/{len(sample)}] {code} {row['name']} {ticker}", flush=True)
        try:
            df = download_one(ticker)
            if df.empty:
                counts["NO_DATA"] = counts.get("NO_DATA", 0) + 1
                continue
            if len(df) < 25:
                counts["INSUFFICIENT_DATA"] = counts.get("INSUFFICIENT_DATA", 0) + 1
                continue
            df["code"] = code
            df["name"] = row["name"]
            frames.append(df)
            counts["PASS"] = counts.get("PASS", 0) + 1
        except Exception:
            counts["ERROR"] = counts.get("ERROR", 0) + 1
    panel = pd.concat(frames, ignore_index=True)
    panel.to_csv(PANEL_CSV, index=False)
    return panel, counts


def max_dd(series: pd.Series) -> float:
    peak = series.cummax()
    return float((series / peak - 1).min())


def sharpe(ret: pd.Series) -> float:
    sd = float(ret.std())
    if sd == 0 or math.isnan(sd):
        return 0.0
    return float(ret.mean() / sd * math.sqrt(252))


def prepare(panel: pd.DataFrame) -> pd.DataFrame:
    data = panel.copy()
    data["Date"] = pd.to_datetime(data["Date"])
    data = data.sort_values(["code", "Date"]).reset_index(drop=True)
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        data[col] = pd.to_numeric(data[col], errors="coerce")
    g = data.groupby("code", group_keys=False)
    data["prev_close"] = g["Close"].shift(1)
    data["ret_cc"] = g["Close"].pct_change()
    data["ret_1d_prev"] = g["Close"].shift(1) / g["Close"].shift(2) - 1
    data["mom_5d_prev"] = g["Close"].shift(1) / g["Close"].shift(6) - 1
    data["mom_20d_prev"] = g["Close"].shift(1) / g["Close"].shift(21) - 1
    data["vol_20d_prev"] = g["ret_cc"].transform(lambda s: s.shift(1).rolling(20).std())
    data["dollar_volume_prev"] = g.apply(lambda x: (x["Close"] * x["Volume"]).shift(1)).reset_index(level=0, drop=True)
    data["open_close_ret"] = data["Close"] / data["Open"] - 1
    data["open_open_ret"] = g["Open"].shift(-1) / data["Open"] - 1
    data["close_next_close_ret"] = g["Close"].shift(-1) / data["Close"] - 1
    data["close_next_open_ret"] = g["Open"].shift(-1) / data["Close"] - 1
    return data


def backtest_variant(data: pd.DataFrame, *, name: str, score_col: str, ascending: bool, return_col: str) -> tuple[dict[str, Any], pd.DataFrame]:
    eligible = data[
        data[score_col].notna()
        & data[return_col].notna()
        & data["dollar_volume_prev"].ge(MIN_DOLLAR_VOLUME_KRW)
        & data["Open"].gt(0)
        & data["Close"].gt(0)
    ].copy()
    if score_col.startswith("mom"):
        # Momentum variants only buy positive momentum. Reversal variants use ascending=True and keep all scores.
        if not ascending:
            eligible = eligible[eligible[score_col] > 0].copy()
    if score_col == "ret_1d_prev" and not ascending:
        eligible = eligible[eligible[score_col] > 0].copy()

    eligible["rank"] = eligible.groupby("Date")[score_col].rank(method="first", ascending=ascending)
    picks = eligible[eligible["rank"] <= TOP_N].copy()
    round_trip_cost = (FEE_BPS + SLIPPAGE_BPS + FEE_BPS + SLIPPAGE_BPS + SELL_TAX_BPS) / 10000.0
    picks["trade_return"] = picks[return_col] - round_trip_cost

    daily = picks.groupby("Date").agg(picks=("code", "count"), daily_return=("trade_return", "mean")).reset_index()
    all_dates = pd.DataFrame({"Date": sorted(data["Date"].dropna().unique())})
    daily = all_dates.merge(daily, on="Date", how="left")
    daily["picks"] = daily["picks"].fillna(0).astype(int)
    daily["daily_return"] = daily["daily_return"].fillna(0.0)
    daily["equity"] = STARTING_CASH * (1 + daily["daily_return"]).cumprod()
    years = max((daily["Date"].iloc[-1] - daily["Date"].iloc[0]).days / 365.25, 1e-9)
    final = float(daily["equity"].iloc[-1])
    ret = daily["daily_return"]
    wins = int((ret > 0).sum())
    losses = int((ret < 0).sum())
    gross_profit = float(ret[ret > 0].sum())
    gross_loss = abs(float(ret[ret < 0].sum()))
    summary = {
        "name": name,
        "score_col": score_col,
        "ascending": ascending,
        "return_col": return_col,
        "active_days": int((daily["picks"] > 0).sum()),
        "total_trades": int(len(picks)),
        "final_value_krw": round(final, 2),
        "total_return_pct": round((final / STARTING_CASH - 1) * 100, 2),
        "cagr_pct": round(((final / STARTING_CASH) ** (1 / years) - 1) * 100, 2),
        "max_drawdown_pct": round(max_dd(daily["equity"]) * 100, 2),
        "sharpe": round(sharpe(ret), 3),
        "win_rate_pct": round(wins / max(wins + losses, 1) * 100, 2),
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else None,
    }
    return summary, picks


def main() -> None:
    panel, counts = load_or_build_panel()
    data = prepare(panel)
    variants = [
        ("intraday_top_5d_momentum", "mom_5d_prev", False, "open_close_ret"),
        ("intraday_bottom_5d_reversal", "mom_5d_prev", True, "open_close_ret"),
        ("intraday_top_1d_momentum", "ret_1d_prev", False, "open_close_ret"),
        ("intraday_bottom_1d_reversal", "ret_1d_prev", True, "open_close_ret"),
        ("open_to_next_open_top_5d_momentum", "mom_5d_prev", False, "open_open_ret"),
        ("open_to_next_open_bottom_5d_reversal", "mom_5d_prev", True, "open_open_ret"),
        ("close_to_next_close_top_5d_momentum", "mom_5d_prev", False, "close_next_close_ret"),
        ("close_to_next_close_bottom_5d_reversal", "mom_5d_prev", True, "close_next_close_ret"),
    ]
    summaries = []
    best_picks = pd.DataFrame()
    for name, score_col, ascending, return_col in variants:
        summary, picks = backtest_variant(data, name=name, score_col=score_col, ascending=ascending, return_col=return_col)
        summaries.append(summary)
        if not summaries or summary["total_return_pct"] >= max(s["total_return_pct"] for s in summaries):
            best_picks = picks.copy()
    summary_df = pd.DataFrame(summaries).sort_values("total_return_pct", ascending=False)
    best = summary_df.iloc[0].to_dict()

    stem = f"random500_seed{RANDOM_SEED}_daily_strategy_sweep_{START}_{END}"
    summary_csv = OUT_DIR / f"{stem}_summary.csv"
    picks_csv = OUT_DIR / f"{stem}_best_picks.csv"
    json_path = OUT_DIR / f"{stem}.json"
    md_path = OUT_DIR / f"{stem}.md"
    summary_df.to_csv(summary_csv, index=False)
    best_picks.to_csv(picks_csv, index=False)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "panel_csv": str(PANEL_CSV),
        "data_counts": counts,
        "period": {"start": START, "end": END},
        "top_n": TOP_N,
        "costs": {"round_trip_bps": FEE_BPS + SLIPPAGE_BPS + FEE_BPS + SLIPPAGE_BPS + SELL_TAX_BPS},
        "variants": summary_df.to_dict(orient="records"),
        "best_variant": best,
        "outputs": {"summary_csv": str(summary_csv), "best_picks_csv": str(picks_csv)},
        "disclaimer": "Research-only daily buy/sell strategy sweep; not investment advice; live orders not submitted.",
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    lines = [
        f"# Daily buy/sell strategy sweep — random 500 seed {RANDOM_SEED}",
        "",
        "Research-only. 실주문 없음. 투자 조언 아님.",
        "",
        "## Setup",
        f"- Sample: previous random {SAMPLE_N}; period {START} ~ {END}",
        f"- Top N: {TOP_N}; min previous dollar volume: {MIN_DOLLAR_VOLUME_KRW:,.0f} KRW",
        f"- Round-trip cost: {payload['costs']['round_trip_bps']} bps",
        "- No-lookahead: ranking features use previous close history only",
        "",
        "## Variants ranked by total_return_pct",
    ]
    for row in summary_df.to_dict(orient="records"):
        lines.append(f"- {row['name']}: return {row['total_return_pct']}%, CAGR {row['cagr_pct']}%, MDD {row['max_drawdown_pct']}%, Sharpe {row['sharpe']}, win {row['win_rate_pct']}%, trades {row['total_trades']}")
    lines.extend(["", "## Best variant", f"- {best}", "", "## Outputs", f"- summary_csv: {summary_csv}", f"- best_picks_csv: {picks_csv}", f"- json: {json_path}"])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    print(f"REPORT_MD={md_path}")
    print(f"SUMMARY_CSV={summary_csv}")
    print(f"BEST_PICKS_CSV={picks_csv}")
    print(f"REPORT_JSON={json_path}")


if __name__ == "__main__":
    main()
