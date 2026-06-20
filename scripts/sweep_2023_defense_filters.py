from __future__ import annotations

import itertools
import json
from pathlib import Path

import pandas as pd

from toss_alpha.daily.replay import ReplayEngine

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "reports/backtests/random500_seed20260607_2022-01-01_2025-12-31_ohlcv_panel.csv"
OUT_DIR = ROOT / "reports/harness"
OUT_DIR.mkdir(parents=True, exist_ok=True)

BASE = {
    "step": 5,
    "score_threshold": 55,
    "stop_loss_pct": 0.12,
    "take_profit_pct": 0.20,
    "max_holding_steps": 10,
    "max_positions": 4,
    "trailing_stop_pct": 0.0,
    "sizing_mode": "flat",
    "rebalance_mode": "hold_until_exit",
    "min_volume": 0,
}

GRID = {
    "score_threshold": [55, 60, 65],
    "stop_loss_pct": [0.08, 0.10, 0.12],
    "max_positions": [2, 3, 4],
    "min_volume": [0, 100_000, 300_000, 500_000],
}


def symbols(panel: pd.DataFrame) -> list[str]:
    return sorted(panel["code"].astype(str).str.zfill(6).unique().tolist())


def run(panel: pd.DataFrame, cfg: dict, *, cost_bps: float = 0.0) -> dict:
    c = dict(cfg)
    step = int(c.pop("step"))
    engine = ReplayEngine(panel=panel, symbols=symbols(panel), transaction_cost_bps=cost_bps, **c)
    return engine.run(step=step)


def main() -> None:
    panel = pd.read_csv(PANEL, dtype={"code": str}, parse_dates=["Date"])
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    panels_by_year = {int(y): panel[panel["Date"].dt.year == y].copy() for y in sorted(panel["Date"].dt.year.unique())}
    rows = []
    keys = list(GRID)
    combos = list(itertools.product(*[GRID[k] for k in keys]))
    for idx, vals in enumerate(combos, start=1):
        cfg = dict(BASE)
        cfg.update(dict(zip(keys, vals)))
        name = f"s{cfg['step']}_t{cfg['score_threshold']}_sl{int(cfg['stop_loss_pct']*100)}_tp{int(cfg['take_profit_pct']*100)}_h{cfg['max_holding_steps']}_mp{cfg['max_positions']}_mv{int(cfg['min_volume'])}"
        print(f"[{idx}/{len(combos)}] {name}", flush=True)
        full = run(panel, cfg, cost_bps=0)["summary"]
        full30 = run(panel, cfg, cost_bps=30)["summary"]
        row = {
            "config_name": name,
            **cfg,
            "full_return_pct": full["total_return_pct"],
            "full_mdd_pct": full["max_drawdown_pct"],
            "full_sharpe": full["sharpe_ratio"],
            "full_trades": full["total_trades"],
            "cost30_return_pct": full30["total_return_pct"],
            "cost30_sharpe": full30["sharpe_ratio"],
        }
        yearly_returns = []
        for year, yp in panels_by_year.items():
            ys = run(yp, cfg, cost_bps=30)["summary"]
            row[f"y{year}_return_30bps"] = ys["total_return_pct"]
            row[f"y{year}_mdd_30bps"] = ys["max_drawdown_pct"]
            row[f"y{year}_trades_30bps"] = ys["total_trades"]
            yearly_returns.append(ys["total_return_pct"])
        row["worst_year_return_30bps"] = min(yearly_returns)
        row["score"] = row["cost30_return_pct"] + 3 * row["worst_year_return_30bps"] + row["full_mdd_pct"]
        rows.append(row)
    df = pd.DataFrame(rows)
    csv = OUT_DIR / "frontier_2023_filter_sweep_20260621.csv"
    df.sort_values(["worst_year_return_30bps", "cost30_return_pct"], ascending=[False, False]).to_csv(csv, index=False)
    robust = df[(df["y2023_return_30bps"] >= 0) & (df["cost30_return_pct"] >= 20)].sort_values("cost30_return_pct", ascending=False)
    by_score = df.sort_values("score", ascending=False)
    md = OUT_DIR / "frontier_2023_filter_sweep_20260621.md"
    lines = [
        "# Frontier 2023 defense filter sweep — 2026-06-21",
        "",
        "Paper/research only. live_order_submitted: False.",
        "",
        f"- grid_size: {len(df)}",
        f"- csv: `{csv}`",
        "",
        "## Best configs with 2023 >= 0 and cost30 return >= 20",
        robust.head(20).to_markdown(index=False) if not robust.empty else "- none",
        "",
        "## Best composite score configs",
        by_score.head(20).to_markdown(index=False),
        "",
        "## Best raw cost30 return configs",
        df.sort_values("cost30_return_pct", ascending=False).head(20).to_markdown(index=False),
    ]
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({
        "csv": str(csv),
        "report_md": str(md),
        "robust_count": int(len(robust)),
        "best_robust": robust.head(5).to_dict(orient="records"),
        "best_score": by_score.head(5).to_dict(orient="records"),
        "best_raw": df.sort_values("cost30_return_pct", ascending=False).head(5).to_dict(orient="records"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
