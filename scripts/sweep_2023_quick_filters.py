from __future__ import annotations

import itertools
import json
from pathlib import Path

import pandas as pd

from toss_alpha.daily.replay import ReplayEngine

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "reports/backtests/random500_seed20260607_2022-01-01_2026-latest_ohlcv_panel.csv"
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
GRID_2023 = {
    "score_threshold": [55, 60, 65],
    "stop_loss_pct": [0.08, 0.10, 0.12],
    "max_positions": [3, 4],
    "min_volume": [0, 300_000, 500_000],
}


def symbols(panel: pd.DataFrame) -> list[str]:
    return sorted(panel["code"].astype(str).str.zfill(6).unique().tolist())


def run(panel: pd.DataFrame, cfg: dict, *, cost_bps: float = 30.0) -> dict:
    c = dict(cfg)
    step = int(c.pop("step"))
    engine = ReplayEngine(panel=panel, symbols=symbols(panel), transaction_cost_bps=cost_bps, **c)
    return engine.run(step=step)


def config_name(cfg: dict) -> str:
    return f"s{cfg['step']}_t{cfg['score_threshold']}_sl{int(cfg['stop_loss_pct']*100)}_tp{int(cfg['take_profit_pct']*100)}_h{cfg['max_holding_steps']}_mp{cfg['max_positions']}_mv{int(cfg['min_volume'])}"


def main() -> None:
    panel = pd.read_csv(PANEL, dtype={"code": str}, parse_dates=["Date"])
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    p2023 = panel[panel["Date"].dt.year == 2023].copy()
    rows = []
    keys = list(GRID_2023)
    combos = list(itertools.product(*[GRID_2023[k] for k in keys]))
    for i, vals in enumerate(combos, 1):
        cfg = dict(BASE); cfg.update(dict(zip(keys, vals)))
        name = config_name(cfg)
        s = run(p2023, cfg, cost_bps=30)["summary"]
        row = {"config_name": name, **cfg, "y2023_return_30bps": s["total_return_pct"], "y2023_mdd_30bps": s["max_drawdown_pct"], "y2023_sharpe_30bps": s["sharpe_ratio"], "y2023_trades_30bps": s["total_trades"]}
        rows.append(row)
        print(f"[{i}/{len(combos)}] {name} y2023={s['total_return_pct']} sharpe={s['sharpe_ratio']}", flush=True)
    df = pd.DataFrame(rows).sort_values(["y2023_return_30bps", "y2023_sharpe_30bps"], ascending=False)
    # Verify top 12 plus baseline on full extended panel and yearly splits.
    candidates = df.head(12).to_dict(orient="records")
    base_row = {"config_name": config_name(BASE), **BASE}
    candidates.append(base_row)
    out_rows = []
    years = sorted(panel["Date"].dt.year.unique().tolist())
    for cand in candidates:
        cfg = {k: cand[k] for k in BASE.keys()}
        full0 = run(panel, cfg, cost_bps=0)["summary"]
        full30 = run(panel, cfg, cost_bps=30)["summary"]
        row = {"config_name": config_name(cfg), **cfg, "full_return_0bps": full0["total_return_pct"], "full_sharpe_0bps": full0["sharpe_ratio"], "full_mdd_0bps": full0["max_drawdown_pct"], "full_trades_0bps": full0["total_trades"], "full_return_30bps": full30["total_return_pct"], "full_sharpe_30bps": full30["sharpe_ratio"], "full_mdd_30bps": full30["max_drawdown_pct"], "full_trades_30bps": full30["total_trades"]}
        yr_returns = []
        for y in years:
            yp = panel[panel["Date"].dt.year == y].copy()
            ys = run(yp, cfg, cost_bps=30)["summary"]
            row[f"y{y}_return_30bps"] = ys["total_return_pct"]
            row[f"y{y}_trades_30bps"] = ys["total_trades"]
            yr_returns.append(ys["total_return_pct"])
        row["worst_year_30bps"] = min(yr_returns)
        row["objective"] = row["full_return_30bps"] + 5 * row["worst_year_30bps"]
        out_rows.append(row)
    verify = pd.DataFrame(out_rows).drop_duplicates("config_name")
    csv1 = OUT_DIR / "frontier_2023_quick_filter_stage1_20260621.csv"
    csv2 = OUT_DIR / "frontier_2023_quick_filter_verified_20260621.csv"
    df.to_csv(csv1, index=False)
    verify.sort_values("objective", ascending=False).to_csv(csv2, index=False)
    md = OUT_DIR / "frontier_2023_quick_filter_verified_20260621.md"
    lines = [
        "# Frontier 2023 quick defense filter verification — 2026-06-21",
        "",
        "Paper/research only. live_order_submitted: False.",
        "",
        f"- stage1_csv: `{csv1}`",
        f"- verified_csv: `{csv2}`",
        "",
        "## Stage1 best 2023-only candidates",
        df.head(15).to_markdown(index=False),
        "",
        "## Verified candidates sorted by objective = full_return_30bps + 5 * worst_year_30bps",
        verify.sort_values("objective", ascending=False).to_markdown(index=False),
        "",
        "## Verified candidates sorted by full 30bps return",
        verify.sort_values("full_return_30bps", ascending=False).to_markdown(index=False),
    ]
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({
        "stage1_csv": str(csv1),
        "verified_csv": str(csv2),
        "report_md": str(md),
        "best_objective": verify.sort_values("objective", ascending=False).head(5).to_dict(orient="records"),
        "best_return": verify.sort_values("full_return_30bps", ascending=False).head(5).to_dict(orient="records"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
