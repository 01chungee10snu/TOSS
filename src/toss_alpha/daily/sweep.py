"""Parameter sweep — run multiple replay configs and compare performance.

Paper simulation only. No live orders.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from toss_alpha.daily.replay import ReplayEngine

DEFAULT_SWEEP_DIR = Path("reports/sweep")
BEST_METRIC = "sharpe_ratio"


@dataclass
class SweepConfig:
    """One replay configuration to test."""
    name: str
    step: int = 5
    score_threshold: float = 70.0
    stop_loss_pct: float = 0.05
    take_profit_pct: float = 0.08
    max_holding_steps: int = 20
    max_notional_krw: float = 100_000
    max_positions: int = 1
    trailing_stop_pct: float = 0.0
    sizing_mode: str = "flat"
    min_volume: float = 0.0
    rebalance_mode: str = "hold_until_exit"

    def __post_init__(self) -> None:
        if not self.name:
            self.name = f"s{self.step}_t{int(self.score_threshold)}"


def build_grid_configs(
    *,
    steps: list[int] | None = None,
    score_thresholds: list[float] | None = None,
    stop_losses: list[float] | None = None,
    take_profits: list[float] | None = None,
    max_holding_steps: list[int] | None = None,
    max_positions: list[int] | None = None,
    trailing_stops: list[float] | None = None,
    sizing_modes: list[str] | None = None,
    min_volumes: list[float] | None = None,
    rebalance_modes: list[str] | None = None,
) -> list[SweepConfig]:
    """Generate all combinations from parameter grids."""
    steps = steps or [5]
    score_thresholds = score_thresholds or [70.0]
    stop_losses = stop_losses or [0.05]
    take_profits = take_profits or [0.08]
    max_holding_steps = max_holding_steps or [20]
    max_positions = max_positions or [1]
    trailing_stops = trailing_stops or [0.0]
    sizing_modes = sizing_modes or ["flat"]
    min_volumes = min_volumes or [0.0]
    rebalance_modes = rebalance_modes or ["hold_until_exit"]

    configs: list[SweepConfig] = []
    for step in steps:
        for thresh in score_thresholds:
            for sl in stop_losses:
                for tp in take_profits:
                    for mhs in max_holding_steps:
                        for mp in max_positions:
                            for tr in trailing_stops:
                                for sizing in sizing_modes:
                                    for min_vol in min_volumes:
                                        for rebalance_mode in rebalance_modes:
                                            mode_tag = {
                                                "hold_until_exit": "hold",
                                                "top_n_rotation": "topn",
                                                "full_liquidate_every_step": "full",
                                            }.get(rebalance_mode, rebalance_mode)
                                            name = (
                                                f"s{step}_t{int(thresh)}_sl{int(sl*100)}_tp{int(tp*100)}_h{mhs}"
                                                f"_mp{mp}_tr{int(tr*100)}_{sizing}_{mode_tag}"
                                            )
                                            if min_vol > 0:
                                                name += f"_v{int(min_vol)}"
                                            configs.append(SweepConfig(
                                                name=name,
                                                step=step,
                                                score_threshold=thresh,
                                                stop_loss_pct=sl,
                                                take_profit_pct=tp,
                                                max_holding_steps=mhs,
                                                max_positions=mp,
                                                trailing_stop_pct=tr,
                                                sizing_mode=sizing,
                                                min_volume=min_vol,
                                                rebalance_mode=rebalance_mode,
                                            ))
    return configs


def run_sweep(
    *,
    panel: pd.DataFrame,
    configs: list[SweepConfig],
    initial_cash_krw: float = 1_000_000,
    out_dir: str | Path | None = None,
    best_metric: str = BEST_METRIC,
) -> dict[str, Any]:
    """Run multiple replay configs and produce comparison."""
    sweep_dir = Path(out_dir) if out_dir else DEFAULT_SWEEP_DIR
    sweep_dir.mkdir(parents=True, exist_ok=True)

    symbols = sorted(panel["code"].astype(str).str.zfill(6).unique().tolist())

    runs: list[dict[str, Any]] = []
    for cfg in configs:
        engine = ReplayEngine(
            panel=panel,
            symbols=symbols,
            initial_cash_krw=initial_cash_krw,
            max_notional_krw=cfg.max_notional_krw,
            score_threshold=cfg.score_threshold,
            stop_loss_pct=cfg.stop_loss_pct,
            take_profit_pct=cfg.take_profit_pct,
            max_holding_steps=cfg.max_holding_steps,
            max_positions=cfg.max_positions,
            trailing_stop_pct=cfg.trailing_stop_pct,
            sizing_mode=cfg.sizing_mode,
            min_volume=cfg.min_volume,
            rebalance_mode=cfg.rebalance_mode,
        )
        result = engine.run(step=cfg.step)
        runs.append({
            "name": cfg.name,
            "step": cfg.step,
            "score_threshold": cfg.score_threshold,
            "stop_loss_pct": cfg.stop_loss_pct,
            "take_profit_pct": cfg.take_profit_pct,
            "max_holding_steps": cfg.max_holding_steps,
            "max_positions": cfg.max_positions,
            "trailing_stop_pct": cfg.trailing_stop_pct,
            "sizing_mode": cfg.sizing_mode,
            "min_volume": cfg.min_volume,
            "rebalance_mode": cfg.rebalance_mode,
            "summary": result["summary"],
            "total_steps": result["total_steps"],
            "total_trades": len(result["trades"]),
        })

    # sort by best_metric descending
    metric_key = best_metric
    best_run = max(runs, key=lambda r: r["summary"].get(metric_key, -999)) if runs else None

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    stem = f"sweep_{timestamp}"
    comparison_csv = sweep_dir / f"{stem}_comparison.csv"
    report_md = sweep_dir / f"{stem}.md"

    # write comparison CSV
    with open(comparison_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "name", "step", "score_threshold", "stop_loss_pct", "take_profit_pct",
            "max_holding_steps", "max_positions", "trailing_stop_pct", "sizing_mode", "min_volume", "rebalance_mode",
            "total_return_pct", "max_drawdown_pct",
            "sharpe_ratio", "total_trades", "win_rate_pct", "final_equity_krw",
        ])
        writer.writeheader()
        for run in runs:
            s = run["summary"]
            writer.writerow({
                "name": run["name"],
                "step": run["step"],
                "score_threshold": run["score_threshold"],
                "stop_loss_pct": run["stop_loss_pct"],
                "take_profit_pct": run["take_profit_pct"],
                "max_holding_steps": run["max_holding_steps"],
                "max_positions": run["max_positions"],
                "trailing_stop_pct": run["trailing_stop_pct"],
                "sizing_mode": run["sizing_mode"],
                "min_volume": run["min_volume"],
                "rebalance_mode": run["rebalance_mode"],
                "total_return_pct": s["total_return_pct"],
                "max_drawdown_pct": s["max_drawdown_pct"],
                "sharpe_ratio": s["sharpe_ratio"],
                "total_trades": s["total_trades"],
                "win_rate_pct": s["win_rate_pct"],
                "final_equity_krw": s["final_equity_krw"],
            })

    result = {
        "runs": runs,
        "best": {
            "name": best_run["name"],
            "metric": best_metric,
            "summary": best_run["summary"],
            "config": {
                "step": best_run["step"],
                "score_threshold": best_run["score_threshold"],
                "stop_loss_pct": best_run["stop_loss_pct"],
                "take_profit_pct": best_run["take_profit_pct"],
                "max_holding_steps": best_run["max_holding_steps"],
                "max_positions": best_run["max_positions"],
                "trailing_stop_pct": best_run["trailing_stop_pct"],
                "sizing_mode": best_run["sizing_mode"],
                "min_volume": best_run["min_volume"],
                "rebalance_mode": best_run["rebalance_mode"],
            },
        } if best_run else None,
        "total_configs": len(configs),
        "comparison_csv": str(comparison_csv),
        "report_md": str(report_md),
    }

    report_md.write_text(_render_report(result), encoding="utf-8")
    return result


def _render_report(result: dict[str, Any]) -> str:
    runs = result["runs"]
    best = result.get("best")
    lines = [
        "# Parameter Sweep Report\n",
        "Paper simulation only. 실주문 아님. 투자 조언 아님.\n",
        f"## Sweep Summary\n",
        f"- total_configs: {result['total_configs']}\n",
    ]
    if best:
        bs = best["summary"]
        lines.extend([
            f"## Best Config (by {best['metric']})\n",
            f"- name: **{best['name']}**",
            f"- step: {best['config']['step']}",
            f"- score_threshold: {best['config']['score_threshold']}",
            f"- stop_loss: {best['config']['stop_loss_pct']*100:.0f}%",
            f"- take_profit: {best['config']['take_profit_pct']*100:.0f}%",
            f"- max_holding_steps: {best['config']['max_holding_steps']}",
            f"- total_return: {bs['total_return_pct']:.2f}%",
            f"- max_drawdown: {bs['max_drawdown_pct']:.2f}%",
            f"- sharpe: {bs['sharpe_ratio']:.4f}",
            f"- win_rate: {bs['win_rate_pct']:.1f}%",
            f"- trades: {bs['total_trades']}\n",
        ])

    lines.append("## All Configs (sorted by sharpe desc)\n")
    sorted_runs = sorted(runs, key=lambda r: r["summary"].get("sharpe_ratio", -999), reverse=True)
    lines.append(
        f"{'name':<30} {'return%':>8} {'mdd%':>8} {'sharpe':>7} "
        f"{'win%':>6} {'trades':>7}"
    )
    lines.append("-" * 75)
    for run in sorted_runs:
        s = run["summary"]
        lines.append(
            f"{run['name']:<30} {s['total_return_pct']:>8.2f} {s['max_drawdown_pct']:>8.2f} "
            f"{s['sharpe_ratio']:>7.4f} {s['win_rate_pct']:>6.1f} {s['total_trades']:>7}"
        )
    return "\n".join(lines) + "\n"
