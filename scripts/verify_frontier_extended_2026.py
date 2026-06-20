from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from toss_alpha.daily.replay import ReplayEngine
from toss_alpha.daily.verify import run_cost_stress, run_yearly_split

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "reports/backtests/random500_seed20260607_2022-01-01_2026-latest_ohlcv_panel.csv"
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


def symbols(panel: pd.DataFrame) -> list[str]:
    return sorted(panel["code"].astype(str).str.zfill(6).unique().tolist())


def run_strategy(panel: pd.DataFrame, cost_bps: float) -> dict:
    cfg = dict(CONFIG)
    step = cfg.pop("step")
    engine = ReplayEngine(panel=panel, symbols=symbols(panel), transaction_cost_bps=cost_bps, **cfg)
    return engine.run(step=step)


def bnh_stats(panel: pd.DataFrame) -> dict:
    close = panel.pivot_table(index="Date", columns="code", values="Close", aggfunc="last").sort_index()
    vol = panel.pivot_table(index="Date", columns="code", values="Volume", aggfunc="last").sort_index()
    first_date = close.index.min(); last_date = close.index.max()
    start = close.loc[first_date]; end = close.ffill().loc[last_date]
    end_vol = vol.ffill().loc[last_date]

    def stats(codes: list[str]) -> dict:
        codes = [c for c in codes if pd.notna(start.get(c)) and pd.notna(end.get(c)) and start.get(c) > 0]
        rel = close[codes].ffill() / start[codes]
        curve = rel.mean(axis=1).dropna()
        r = curve.pct_change().fillna(0)
        dd = curve / curve.cummax() - 1
        sharpe = float((r.mean() / r.std()) * np.sqrt(252)) if float(r.std()) != 0 else 0.0
        rets = end[codes] / start[codes] - 1
        return {
            "n": len(codes),
            "return_pct": round(float((curve.iloc[-1] - 1) * 100), 4),
            "final_equity_1m": round(float(curve.iloc[-1] * 1_000_000)),
            "mdd_pct": round(float(dd.min() * 100), 4),
            "sharpe": round(sharpe, 4),
            "median_stock_return_pct": round(float(rets.median() * 100), 4),
            "win_rate_pct": round(float((rets > 0).mean() * 100), 4),
        }

    base_codes = start.dropna().index.tolist()
    end_volume_positive = [c for c in base_codes if end_vol.get(c, 0) > 0]
    exclude_000300 = [c for c in base_codes if c != "000300"]
    sanity = [c for c in base_codes if pd.notna(start.get(c)) and pd.notna(end.get(c)) and start.get(c) > 0 and (end.get(c) / start.get(c) - 1) < 10]
    return {
        "all_start_available": stats(base_codes),
        "end_volume_positive": stats(end_volume_positive),
        "exclude_000300_only": stats(exclude_000300),
        "exclude_return_over_1000pct": stats(sanity),
    }


def main() -> None:
    panel = pd.read_csv(PANEL, dtype={"code": str}, parse_dates=["Date"])
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    cost = run_cost_stress(panel=panel, config=CONFIG, cost_bps_values=[0, 10, 20, 30], out_dir=OUT_DIR)
    y0 = run_yearly_split(panel=panel, config=CONFIG, cost_bps=0, out_dir=OUT_DIR)
    y30 = run_yearly_split(panel=panel, config=CONFIG, cost_bps=30, out_dir=OUT_DIR)
    full0 = run_strategy(panel, 0)
    trades = pd.DataFrame(full0["trades"])
    trades_csv = OUT_DIR / "frontier_extended_2026_all_trades_20260621.csv"
    trades.to_csv(trades_csv, index=False)
    bnh = bnh_stats(panel)
    result = {
        "panel": {
            "path": str(PANEL),
            "rows": int(len(panel)),
            "codes": int(panel["code"].nunique()),
            "start": str(panel["Date"].min().date()),
            "end": str(panel["Date"].max().date()),
        },
        "config": CONFIG,
        "cost_stress": cost["rows"],
        "yearly_0bps": y0["rows"],
        "yearly_30bps": y30["rows"],
        "bnh": bnh,
        "artifacts": {"cost_csv": cost["csv_path"], "yearly_0_csv": y0["csv_path"], "yearly_30_csv": y30["csv_path"], "trades_csv": str(trades_csv)},
    }
    json_path = OUT_DIR / "frontier_extended_2026_verification_20260621.json"
    md_path = OUT_DIR / "frontier_extended_2026_verification_20260621.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Frontier extended 2026 verification — 2026-06-21",
        "",
        "Paper/research only. live_order_submitted: False.",
        "",
        "## Panel",
        json.dumps(result["panel"], ensure_ascii=False, indent=2),
        "",
        "## Cost stress",
        pd.DataFrame(cost["rows"]).to_markdown(index=False),
        "",
        "## Yearly split 0bps",
        pd.DataFrame(y0["rows"]).to_markdown(index=False),
        "",
        "## Yearly split 30bps",
        pd.DataFrame(y30["rows"]).to_markdown(index=False),
        "",
        "## Buy-hold benchmarks",
        pd.DataFrame([{"benchmark": k, **v} for k, v in bnh.items()]).to_markdown(index=False),
        "",
        "## Artifacts",
    ]
    for k, v in result["artifacts"].items():
        lines.append(f"- {k}: `{v}`")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"json": str(json_path), "md": str(md_path), "cost_stress": cost["rows"], "yearly_30bps": y30["rows"], "bnh": bnh}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
