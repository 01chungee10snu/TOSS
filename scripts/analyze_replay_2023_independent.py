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


def run_panel(panel: pd.DataFrame, *, cost_bps: float = 0.0) -> dict:
    symbols = sorted(panel["code"].astype(str).str.zfill(6).unique().tolist())
    cfg = {k: v for k, v in CONFIG.items() if k != "step"}
    engine = ReplayEngine(panel=panel, symbols=symbols, transaction_cost_bps=cost_bps, **cfg)
    return engine.run(step=CONFIG["step"])


def main() -> None:
    panel = pd.read_csv(PANEL, dtype={"code": str}, parse_dates=["Date"])
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    p2023 = panel[panel["Date"].dt.year == 2023].copy()
    name_map = panel.sort_values("Date").groupby("code")["name"].last().to_dict() if "name" in panel.columns else {}
    rows = []
    for cost in [0, 10, 20, 30]:
        result = run_panel(p2023, cost_bps=cost)
        s = result["summary"]
        rows.append({"cost_bps": cost, **s})
        if cost == 0:
            trades = pd.DataFrame(result["trades"])
            trades["name"] = trades["symbol"].map(name_map).fillna("")
            trades["is_loss"] = trades["pnl_krw"] < 0
            by_reason = trades.groupby("exit_reason").agg(
                trades=("symbol", "count"),
                pnl_krw=("pnl_krw", "sum"),
                avg_pnl_pct=("pnl_pct", "mean"),
                median_pnl_pct=("pnl_pct", "median"),
                win_rate_pct=("is_loss", lambda s: round((~s).mean() * 100, 2)),
            ).sort_values("pnl_krw").reset_index()
            by_symbol = trades.groupby(["symbol", "name"]).agg(
                trades=("symbol", "count"),
                pnl_krw=("pnl_krw", "sum"),
                avg_pnl_pct=("pnl_pct", "mean"),
                worst_pnl_pct=("pnl_pct", "min"),
            ).sort_values("pnl_krw").reset_index()
            trades.to_csv(OUT_DIR / "replay_frontier_2023_independent_trades_20260621.csv", index=False)
            by_reason.to_csv(OUT_DIR / "replay_frontier_2023_independent_by_reason_20260621.csv", index=False)
            by_symbol.to_csv(OUT_DIR / "replay_frontier_2023_independent_by_symbol_20260621.csv", index=False)
    cost_df = pd.DataFrame(rows)
    cost_df.to_csv(OUT_DIR / "replay_frontier_2023_independent_costs_20260621.csv", index=False)
    report = OUT_DIR / "replay_frontier_2023_independent_diagnosis_20260621.md"
    lines = [
        "# Replay frontier 2023 independent-year diagnosis — 2026-06-21",
        "",
        "Paper/research only. live_order_submitted: False.",
        "",
        "## Config",
        "```json",
        json.dumps(CONFIG, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Cost stress on 2023-only panel",
        cost_df.to_markdown(index=False),
        "",
        "## 2023-only by exit reason — 0bps",
        by_reason.to_markdown(index=False),
        "",
        "## 2023-only worst symbols — 0bps",
        by_symbol.head(20).to_markdown(index=False),
        "",
        "## Worst trades",
        trades.sort_values("pnl_krw").head(20)[["symbol", "name", "entry_date", "exit_date", "pnl_krw", "pnl_pct", "holding_steps", "exit_reason"]].to_markdown(index=False),
    ]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({
        "costs": cost_df.to_dict(orient="records"),
        "by_reason": by_reason.to_dict(orient="records"),
        "worst_symbols": by_symbol.head(10).to_dict(orient="records"),
        "report_md": str(report),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
