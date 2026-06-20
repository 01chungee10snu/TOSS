from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from toss_alpha.daily.replay import ReplayEngine

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "reports/backtests/random500_seed20260607_2022-01-01_2025-12-31_ohlcv_panel.csv"
OUT_DIR = ROOT / "reports/harness"
OUT_DIR.mkdir(parents=True, exist_ok=True)

CONFIG = {
    "step": 5,
    "score_threshold": 55,
    "stop_loss_pct": 0.12,
    "take_profit_pct": 0.20,
    "max_holding_steps": 10,
    "max_positions": 4,
    "trailing_stop_pct": 0.0,
    "sizing_mode": "flat",
    "rebalance_mode": "hold_until_exit",
}


def main() -> None:
    panel = pd.read_csv(PANEL, dtype={"code": str}, parse_dates=["Date"])
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    symbols = sorted(panel["code"].unique().tolist())
    engine = ReplayEngine(panel=panel, symbols=symbols, **{k: v for k, v in CONFIG.items() if k != "step"})
    result = engine.run(step=CONFIG["step"])
    trades = pd.DataFrame(result["trades"])
    name_map = panel.sort_values("Date").groupby("code")["name"].last().to_dict() if "name" in panel.columns else {}
    if trades.empty:
        raise SystemExit("no trades")
    trades["exit_year"] = pd.to_datetime(trades["exit_date"]).dt.year
    trades["entry_year"] = pd.to_datetime(trades["entry_date"]).dt.year
    trades["name"] = trades["symbol"].map(name_map).fillna("")
    trades["is_loss"] = trades["pnl_krw"] < 0

    all_year = trades.groupby("exit_year").agg(
        trades=("symbol", "count"),
        pnl_krw=("pnl_krw", "sum"),
        avg_pnl_pct=("pnl_pct", "mean"),
        win_rate_pct=("is_loss", lambda s: round((~s).mean() * 100, 2)),
    ).reset_index()

    y2023 = trades[trades["exit_year"] == 2023].copy()
    by_reason = y2023.groupby("exit_reason").agg(
        trades=("symbol", "count"),
        pnl_krw=("pnl_krw", "sum"),
        avg_pnl_pct=("pnl_pct", "mean"),
        median_pnl_pct=("pnl_pct", "median"),
        win_rate_pct=("is_loss", lambda s: round((~s).mean() * 100, 2)),
    ).sort_values("pnl_krw").reset_index()

    by_symbol = y2023.groupby(["symbol", "name"]).agg(
        trades=("symbol", "count"),
        pnl_krw=("pnl_krw", "sum"),
        avg_pnl_pct=("pnl_pct", "mean"),
        worst_pnl_pct=("pnl_pct", "min"),
    ).sort_values("pnl_krw").reset_index()

    worst = y2023.sort_values("pnl_krw").head(20)
    best = y2023.sort_values("pnl_krw", ascending=False).head(20)

    paths = {
        "all_trades_csv": OUT_DIR / "replay_frontier_all_trades_20260621.csv",
        "y2023_trades_csv": OUT_DIR / "replay_frontier_2023_trades_20260621.csv",
        "reason_csv": OUT_DIR / "replay_frontier_2023_by_exit_reason_20260621.csv",
        "symbol_csv": OUT_DIR / "replay_frontier_2023_by_symbol_20260621.csv",
        "report_md": OUT_DIR / "replay_frontier_2023_loss_diagnosis_20260621.md",
    }
    trades.to_csv(paths["all_trades_csv"], index=False)
    y2023.to_csv(paths["y2023_trades_csv"], index=False)
    by_reason.to_csv(paths["reason_csv"], index=False)
    by_symbol.to_csv(paths["symbol_csv"], index=False)

    lines = [
        "# Replay frontier 2023 loss diagnosis — 2026-06-21",
        "",
        "Paper/research only. live_order_submitted: False.",
        "",
        "## Config",
        "```json",
        json.dumps(CONFIG, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Full-period summary",
        f"- total_return_pct: {result['summary']['total_return_pct']}%",
        f"- max_drawdown_pct: {result['summary']['max_drawdown_pct']}%",
        f"- sharpe_ratio: {result['summary']['sharpe_ratio']}",
        f"- total_trades: {result['summary']['total_trades']}",
        "",
        "## PnL by exit year",
        all_year.to_markdown(index=False),
        "",
        "## 2023 by exit reason",
        by_reason.to_markdown(index=False),
        "",
        "## 2023 worst symbols",
        by_symbol.head(20).to_markdown(index=False),
        "",
        "## 2023 worst trades",
        worst[["symbol", "name", "entry_date", "exit_date", "pnl_krw", "pnl_pct", "holding_steps", "exit_reason"]].to_markdown(index=False),
        "",
        "## 2023 best trades",
        best[["symbol", "name", "entry_date", "exit_date", "pnl_krw", "pnl_pct", "holding_steps", "exit_reason"]].to_markdown(index=False),
        "",
        "## Evidence files",
    ]
    for k, p in paths.items():
        if k != "report_md":
            lines.append(f"- {k}: `{p}`")
    paths["report_md"].write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({
        "summary": result["summary"],
        "by_year": all_year.to_dict(orient="records"),
        "by_reason_2023": by_reason.to_dict(orient="records"),
        "worst_symbols_2023": by_symbol.head(10).to_dict(orient="records"),
        "report_md": str(paths["report_md"]),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
