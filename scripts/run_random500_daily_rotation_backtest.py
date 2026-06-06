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

# Daily buy/sell strategy: rank yesterday, buy today's open, sell today's close.
TOP_N = 10
LOOKBACK_DAYS = 5
VOL_WINDOW = 20
MIN_DOLLAR_VOLUME_KRW = 100_000_000
STARTING_CASH = 1_000_000.0
FEE_BPS = 1.5
SLIPPAGE_BPS = 5.0
SELL_TAX_BPS = 18.0


def download_one(ticker: str) -> pd.DataFrame:
    df = yf.download(ticker, start=START, end=YF_END, auto_adjust=True, progress=False, threads=False)
    if df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df.reset_index()
    df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)
    df = df[(df["Date"] >= pd.to_datetime(START)) & (df["Date"] <= pd.to_datetime(END))].copy()
    df = df.sort_values("Date").reset_index(drop=True)
    needed = ["Date", "Open", "High", "Low", "Close", "Volume"]
    for col in needed:
        if col not in df.columns:
            df[col] = pd.NA
    return df[needed]


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


def describe(values: pd.Series) -> dict[str, float]:
    q = values.quantile([0.05, 0.25, 0.5, 0.75, 0.95])
    return {
        "mean": round(float(values.mean()), 4),
        "median": round(float(q.loc[0.5]), 4),
        "p05": round(float(q.loc[0.05]), 4),
        "p25": round(float(q.loc[0.25]), 4),
        "p75": round(float(q.loc[0.75]), 4),
        "p95": round(float(q.loc[0.95]), 4),
    }


def build_panel(sample: pd.DataFrame) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    frames = []
    status = []
    for i, row in sample.iterrows():
        code = str(row["code"]).zfill(6)
        name = row["name"]
        ticker = row["yfinance_symbol"]
        print(f"[{i + 1:03d}/{len(sample)}] {code} {name} {ticker}", flush=True)
        try:
            df = download_one(ticker)
            if df.empty:
                status.append({"code": code, "name": name, "status": "NO_DATA"})
                continue
            if len(df) < VOL_WINDOW + LOOKBACK_DAYS + 2:
                status.append({"code": code, "name": name, "status": "INSUFFICIENT_DATA", "rows": len(df)})
                continue
            df["code"] = code
            df["name"] = name
            frames.append(df)
            status.append({"code": code, "name": name, "status": "PASS", "rows": len(df)})
        except Exception as e:
            status.append({"code": code, "name": name, "status": "ERROR", "error": repr(e)})
    if not frames:
        raise RuntimeError("no valid data")
    return pd.concat(frames, ignore_index=True), status


def run_daily_rotation(panel: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    data = panel.copy()
    data["Date"] = pd.to_datetime(data["Date"])
    data = data.sort_values(["code", "Date"]).reset_index(drop=True)
    for col in ["Open", "Close", "Volume"]:
        data[col] = pd.to_numeric(data[col], errors="coerce")

    g = data.groupby("code", group_keys=False)
    data["prev_close"] = g["Close"].shift(1)
    data["ret_close_to_close"] = g["Close"].pct_change()
    data["momentum_5d_prev"] = g["Close"].shift(1) / g["Close"].shift(1 + LOOKBACK_DAYS) - 1
    data["vol_20d_prev"] = g["ret_close_to_close"].transform(lambda s: s.shift(1).rolling(VOL_WINDOW).std())
    data["dollar_volume_prev"] = g.apply(lambda x: (x["Close"] * x["Volume"]).shift(1)).reset_index(level=0, drop=True)
    data["overnight_gap"] = data["Open"] / data["prev_close"] - 1
    data["intraday_ret"] = data["Close"] / data["Open"] - 1
    data["rank_score"] = data["momentum_5d_prev"] / data["vol_20d_prev"].replace(0, pd.NA)

    eligible = data[
        data["Open"].notna()
        & data["Close"].notna()
        & data["rank_score"].notna()
        & (data["momentum_5d_prev"] > 0)
        & (data["dollar_volume_prev"] >= MIN_DOLLAR_VOLUME_KRW)
        & (data["Open"] > 0)
        & (data["Close"] > 0)
    ].copy()

    eligible["rank"] = eligible.groupby("Date")["rank_score"].rank(method="first", ascending=False)
    picks = eligible[eligible["rank"] <= TOP_N].copy()
    round_trip_cost = (FEE_BPS + SLIPPAGE_BPS + FEE_BPS + SLIPPAGE_BPS + SELL_TAX_BPS) / 10000.0
    picks["trade_return"] = picks["intraday_ret"] - round_trip_cost
    picks["notional_krw"] = STARTING_CASH / TOP_N

    daily = picks.groupby("Date").agg(
        picks=("code", "count"),
        daily_return=("trade_return", "mean"),
        avg_intraday_ret=("intraday_ret", "mean"),
        avg_momentum_5d_prev=("momentum_5d_prev", "mean"),
    ).reset_index()
    all_dates = pd.DataFrame({"Date": sorted(data["Date"].dropna().unique())})
    daily = all_dates.merge(daily, on="Date", how="left")
    daily["picks"] = daily["picks"].fillna(0).astype(int)
    daily["daily_return"] = daily["daily_return"].fillna(0.0)
    daily["avg_intraday_ret"] = daily["avg_intraday_ret"].fillna(0.0)
    daily["equity"] = STARTING_CASH * (1 + daily["daily_return"]).cumprod()

    years = max((daily["Date"].iloc[-1] - daily["Date"].iloc[0]).days / 365.25, 1e-9)
    final = float(daily["equity"].iloc[-1])
    ret = daily["daily_return"]
    win_days = int((ret > 0).sum())
    loss_days = int((ret < 0).sum())
    gross_profit = float(ret[ret > 0].sum())
    gross_loss = abs(float(ret[ret < 0].sum()))
    summary = {
        "strategy_name": "daily_open_to_close_rotation_top10_by_prev_5d_momentum_vol_adjusted",
        "no_lookahead": "rank uses prior close-derived features only; buy today open and sell today close",
        "top_n": TOP_N,
        "lookback_days": LOOKBACK_DAYS,
        "vol_window": VOL_WINDOW,
        "min_prev_dollar_volume_krw": MIN_DOLLAR_VOLUME_KRW,
        "round_trip_cost_bps": round_trip_cost * 10000,
        "trading_days": int(len(daily)),
        "active_days": int((daily["picks"] > 0).sum()),
        "total_trades": int(len(picks)),
        "avg_picks_per_active_day": round(float(daily.loc[daily["picks"] > 0, "picks"].mean()), 2),
        "final_value_krw": round(final, 2),
        "total_return_pct": round((final / STARTING_CASH - 1) * 100, 2),
        "cagr_pct": round(((final / STARTING_CASH) ** (1 / years) - 1) * 100, 2),
        "max_drawdown_pct": round(max_dd(daily["equity"]) * 100, 2),
        "sharpe": round(sharpe(ret), 3),
        "win_days": win_days,
        "loss_days": loss_days,
        "win_rate_pct": round(win_days / max(win_days + loss_days, 1) * 100, 2),
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else None,
        "daily_return_distribution_pct": describe(ret * 100),
    }
    return daily, picks, summary


def main() -> None:
    if not SAMPLE_CSV.exists():
        raise FileNotFoundError(f"sample file missing: {SAMPLE_CSV}")
    sample = pd.read_csv(SAMPLE_CSV, dtype={"code": str})
    panel, data_status = build_panel(sample)
    daily, picks, summary = run_daily_rotation(panel)

    status_df = pd.DataFrame(data_status)
    stem = f"random500_seed{RANDOM_SEED}_daily_open_close_top{TOP_N}_mom{LOOKBACK_DAYS}_{START}_{END}"
    md_path = OUT_DIR / f"{stem}.md"
    json_path = OUT_DIR / f"{stem}.json"
    daily_csv = OUT_DIR / f"{stem}_daily_curve.csv"
    picks_csv = OUT_DIR / f"{stem}_picks.csv"
    status_csv = OUT_DIR / f"{stem}_data_status.csv"

    daily.to_csv(daily_csv, index=False)
    picks.to_csv(picks_csv, index=False)
    status_df.to_csv(status_csv, index=False)

    top_symbols = picks.groupby(["code", "name"]).agg(
        trades=("Date", "count"),
        avg_trade_return_pct=("trade_return", lambda s: round(float(s.mean() * 100), 4)),
        total_trade_return_pct=("trade_return", lambda s: round(float(s.sum() * 100), 4)),
        win_rate_pct=("trade_return", lambda s: round(float((s > 0).mean() * 100), 2)),
    ).reset_index().sort_values("total_trade_return_pct", ascending=False)

    bottom_symbols = top_symbols.sort_values("total_trade_return_pct", ascending=True)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sample_csv": str(SAMPLE_CSV),
        "period": {"start": START, "end": END},
        "sample_n": SAMPLE_N,
        "data_status_counts": status_df["status"].value_counts().to_dict(),
        "costs": {"fee_bps_each_side": FEE_BPS, "slippage_bps_each_side": SLIPPAGE_BPS, "sell_tax_bps_on_sells": SELL_TAX_BPS},
        "summary": summary,
        "top10_symbols_by_cumulative_trade_return": top_symbols.head(10).to_dict(orient="records"),
        "bottom10_symbols_by_cumulative_trade_return": bottom_symbols.head(10).to_dict(orient="records"),
        "outputs": {"daily_csv": str(daily_csv), "picks_csv": str(picks_csv), "status_csv": str(status_csv)},
        "disclaimer": "Research-only daily buy/sell backtest; not investment advice; live orders not submitted.",
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    lines = [
        f"# Random 500 daily open-close rotation backtest — seed {RANDOM_SEED}",
        "",
        "Research-only. 실주문 없음. 투자 조언 아님.",
        "",
        "## Strategy",
        f"- Universe/sample: previous random {SAMPLE_N} Korean equities sample, excluding Samsung Electronics and SK hynix from the original sample flow",
        f"- Buy/sell cadence: every trading day, buy selected names at open and sell at close",
        f"- Ranking: previous {LOOKBACK_DAYS}-day close momentum / previous {VOL_WINDOW}-day volatility",
        f"- Eligibility: positive previous {LOOKBACK_DAYS}-day momentum and previous dollar volume >= {MIN_DOLLAR_VOLUME_KRW:,.0f} KRW",
        f"- Position count: top {TOP_N}, equal-weight",
        "- No-lookahead: all ranking inputs are from prior closes; today open/close only used for simulated execution/result",
        f"- Round-trip cost: {summary['round_trip_cost_bps']} bps",
        "",
        "## Aggregate result",
    ]
    for key in [
        "trading_days", "active_days", "total_trades", "avg_picks_per_active_day", "final_value_krw",
        "total_return_pct", "cagr_pct", "max_drawdown_pct", "sharpe", "win_days", "loss_days", "win_rate_pct", "profit_factor",
    ]:
        lines.append(f"- {key}: {summary[key]}")
    lines.append(f"- daily_return_distribution_pct: {summary['daily_return_distribution_pct']}")
    lines.append(f"- data_status_counts: {payload['data_status_counts']}")
    lines.extend(["", "## Top 10 symbols by cumulative selected-trade return"])
    for r in payload["top10_symbols_by_cumulative_trade_return"]:
        lines.append(f"- {r['code']} {r['name']}: trades {r['trades']}, avg {r['avg_trade_return_pct']}%, cumulative {r['total_trade_return_pct']}%, win {r['win_rate_pct']}%")
    lines.extend(["", "## Bottom 10 symbols by cumulative selected-trade return"])
    for r in payload["bottom10_symbols_by_cumulative_trade_return"]:
        lines.append(f"- {r['code']} {r['name']}: trades {r['trades']}, avg {r['avg_trade_return_pct']}%, cumulative {r['total_trade_return_pct']}%, win {r['win_rate_pct']}%")
    lines.extend(["", "## Outputs", f"- json: {json_path}", f"- daily_curve: {daily_csv}", f"- picks: {picks_csv}", f"- data_status: {status_csv}"])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    print(f"REPORT_MD={md_path}")
    print(f"REPORT_JSON={json_path}")
    print(f"DAILY_CSV={daily_csv}")
    print(f"PICKS_CSV={picks_csv}")
    print(f"STATUS_CSV={status_csv}")


if __name__ == "__main__":
    main()
