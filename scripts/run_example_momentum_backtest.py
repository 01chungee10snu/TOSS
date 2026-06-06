from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf
import yaml

ROOT = Path(__file__).resolve().parents[1]
GOAL_PATH = ROOT / "goals" / "example_momentum.yaml"
OUT_DIR = ROOT / "reports" / "backtests"
OUT_DIR.mkdir(parents=True, exist_ok=True)

with GOAL_PATH.open("r", encoding="utf-8") as f:
    goal = yaml.safe_load(f)

symbols = goal["universe"]["symbols"]
start = goal["period"]["start"]
end = goal["period"]["end"]
params = goal["strategy"]["params"]
short_window = int(params.get("short_window", 20))
long_window = int(params.get("long_window", 60))

# Conservative Korean cash-equity assumptions for research-only simulation.
starting_cash = 1_000_000.0
fee_bps = 1.5      # broker fee per buy/sell side, approximate
slippage_bps = 5.0 # conservative market-impact/slippage per buy/sell side
sell_tax_bps = 18.0 # Korean stock transaction tax on sells, approximate 2025 KOSPI/KOSDAQ

names = {"005930": "삼성전자", "000660": "SK하이닉스"}
yf_symbols = {s: f"{s}.KS" for s in symbols}


def clean_download(ticker: str) -> pd.DataFrame:
    df = yf.download(ticker, start=start, end="2026-01-01", auto_adjust=True, progress=False, threads=False)
    if df.empty:
        raise RuntimeError(f"no_data:{ticker}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df.reset_index()
    df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)
    # Keep exact requested end period.
    df = df[(df["Date"] >= pd.to_datetime(start)) & (df["Date"] <= pd.to_datetime(end))].copy()
    df = df.sort_values("Date").reset_index(drop=True)
    return df


def run_ma_backtest(df: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    data = df[["Date", "Close"]].copy()
    data["ma_short"] = data["Close"].rolling(short_window).mean()
    data["ma_long"] = data["Close"].rolling(long_window).mean()
    data["signal"] = (data["ma_short"] > data["ma_long"]).astype(int)
    data.loc[data["ma_long"].isna(), "signal"] = 0
    data["position"] = data["signal"].shift(1).fillna(0)  # no look-ahead: trade next day
    data["ret"] = data["Close"].pct_change().fillna(0.0)
    data["turnover"] = data["position"].diff().abs().fillna(data["position"].abs())
    data["sell_turnover"] = ((data["position"].shift(1).fillna(0) - data["position"]).clip(lower=0))
    fee_rate = fee_bps / 10000.0
    slip_rate = slippage_bps / 10000.0
    sell_tax_rate = sell_tax_bps / 10000.0
    data["cost"] = data["turnover"] * (fee_rate + slip_rate) + data["sell_turnover"] * sell_tax_rate
    data["strategy_ret"] = data["position"] * data["ret"] - data["cost"]
    data["buyhold_ret"] = data["ret"]
    data["equity"] = starting_cash * (1 + data["strategy_ret"]).cumprod()
    data["buyhold_equity"] = starting_cash * (1 + data["buyhold_ret"]).cumprod()

    def max_dd(series: pd.Series) -> float:
        peak = series.cummax()
        return float((series / peak - 1).min())

    def sharpe(ret: pd.Series) -> float:
        r = ret.dropna()
        sd = float(r.std())
        if sd == 0 or math.isnan(sd):
            return 0.0
        return float((r.mean() / sd) * math.sqrt(252))

    years = max((data["Date"].iloc[-1] - data["Date"].iloc[0]).days / 365.25, 1e-9)
    final = float(data["equity"].iloc[-1])
    bh_final = float(data["buyhold_equity"].iloc[-1])
    trades = int((data["turnover"] > 0).sum())
    wins = int((data.loc[data["strategy_ret"] > 0, "strategy_ret"]).count())
    losses = int((data.loc[data["strategy_ret"] < 0, "strategy_ret"]).count())
    gross_profit = float(data.loc[data["strategy_ret"] > 0, "strategy_ret"].sum())
    gross_loss = abs(float(data.loc[data["strategy_ret"] < 0, "strategy_ret"].sum()))
    summary = {
        "rows": int(len(data)),
        "first_date": data["Date"].iloc[0].strftime("%Y-%m-%d"),
        "last_date": data["Date"].iloc[-1].strftime("%Y-%m-%d"),
        "final_value_krw": round(final, 2),
        "total_return_pct": round((final / starting_cash - 1) * 100, 2),
        "cagr_pct": round(((final / starting_cash) ** (1 / years) - 1) * 100, 2),
        "max_drawdown_pct": round(max_dd(data["equity"]) * 100, 2),
        "sharpe": round(sharpe(data["strategy_ret"]), 3),
        "trades": trades,
        "win_days": wins,
        "loss_days": losses,
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else None,
        "buyhold_final_krw": round(bh_final, 2),
        "buyhold_return_pct": round((bh_final / starting_cash - 1) * 100, 2),
        "invested_days_pct": round(float(data["position"].mean()) * 100, 2),
    }
    return summary, data

results = {}
equities = []
for symbol, ticker in yf_symbols.items():
    df = clean_download(ticker)
    summary, curve = run_ma_backtest(df)
    summary["name"] = names.get(symbol, symbol)
    summary["yfinance_symbol"] = ticker
    results[symbol] = summary
    equities.append(curve[["Date", "equity"]].rename(columns={"equity": symbol}).set_index("Date"))
    curve.to_csv(OUT_DIR / f"{symbol}_ma{short_window}_{long_window}_curve.csv", index=False)

portfolio = pd.concat(equities, axis=1).dropna()
portfolio["equity"] = portfolio.mean(axis=1)
peak = portfolio["equity"].cummax()
port_ret = portfolio["equity"].pct_change().fillna(0)
port_final = float(portfolio["equity"].iloc[-1])
years = max((portfolio.index[-1] - portfolio.index[0]).days / 365.25, 1e-9)
portfolio_summary = {
    "construction": "equal-weight average of per-symbol 1,000,000 KRW strategy equity curves",
    "first_date": portfolio.index[0].strftime("%Y-%m-%d"),
    "last_date": portfolio.index[-1].strftime("%Y-%m-%d"),
    "final_value_krw": round(port_final, 2),
    "total_return_pct": round((port_final / starting_cash - 1) * 100, 2),
    "cagr_pct": round(((port_final / starting_cash) ** (1 / years) - 1) * 100, 2),
    "max_drawdown_pct": round(float((portfolio["equity"] / peak - 1).min()) * 100, 2),
    "sharpe": round(float(port_ret.mean() / port_ret.std() * math.sqrt(252)), 3),
}
portfolio.to_csv(OUT_DIR / f"portfolio_equal_weight_ma{short_window}_{long_window}_curve.csv")

payload = {
    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    "goal_path": str(GOAL_PATH),
    "period": {"start": start, "end": end},
    "strategy": {
        "name": "long_when_MA20_above_MA60_else_cash",
        "no_lookahead": "signal at close, position applied next trading day",
        "short_window": short_window,
        "long_window": long_window,
    },
    "costs": {"fee_bps_each_side": fee_bps, "slippage_bps_each_side": slippage_bps, "sell_tax_bps_on_sells": sell_tax_bps},
    "starting_cash_krw_per_symbol": starting_cash,
    "symbols": results,
    "portfolio_equal_weight": portfolio_summary,
    "disclaimer": "Research-only backtest; not investment advice; live orders not submitted.",
}
json_path = OUT_DIR / f"example_momentum_{start}_{end}.json"
md_path = OUT_DIR / f"example_momentum_{start}_{end}.md"
json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

lines = []
lines.append(f"# Example momentum backtest ({start} ~ {end})")
lines.append("")
lines.append("Research-only. 실주문 없음. 투자 조언 아님.")
lines.append("")
lines.append("## Assumptions")
lines.append(f"- Strategy: MA{short_window} > MA{long_window}이면 다음 거래일부터 long, 아니면 cash")
lines.append("- Data: yfinance adjusted daily close")
lines.append(f"- Cost: fee {fee_bps} bps/side + slippage {slippage_bps} bps/side + sell tax {sell_tax_bps} bps on sells")
lines.append(f"- Starting cash: {starting_cash:,.0f} KRW per symbol")
lines.append("")
lines.append("## Results")
for symbol, summary in results.items():
    lines.append(f"### {summary['name']} ({symbol})")
    for key in ["first_date", "last_date", "rows", "final_value_krw", "total_return_pct", "cagr_pct", "max_drawdown_pct", "sharpe", "trades", "profit_factor", "buyhold_return_pct", "invested_days_pct"]:
        lines.append(f"- {key}: {summary[key]}")
    lines.append("")
lines.append("### Equal-weight portfolio")
for key, value in portfolio_summary.items():
    lines.append(f"- {key}: {value}")
md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False, indent=2))
print(f"\nREPORT_MD={md_path}")
print(f"REPORT_JSON={json_path}")
