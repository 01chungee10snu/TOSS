from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from toss_alpha.daily.replay import ReplayEngine

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "reports/backtests/random500_seed20260607_2022-01-01_2026-latest_ohlcv_panel.csv"
OUT_DIR = ROOT / "reports/harness"
OUT_DIR.mkdir(parents=True, exist_ok=True)
PRED_FILES = {
    "extratrees": OUT_DIR / "ml_direct_pred_extratrees_20260621.csv",
    "lgbm": OUT_DIR / "ml_direct_pred_lgbm_20260621.csv",
}
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


def symbols(panel: pd.DataFrame) -> list[str]:
    return sorted(panel["code"].astype(str).str.zfill(6).unique().tolist())


def load_prediction_map(path: Path, *, score_mode: str = "ml", top_n_per_date: int | None = None) -> dict[str, dict[str, float]]:
    pred = pd.read_csv(path, dtype={"code": str}, parse_dates=["Date"])
    pred["code"] = pred["code"].astype(str).str.zfill(6)
    if score_mode == "ml":
        pred["rank_score"] = pred["ml_pred"].astype(float)
    elif score_mode == "hybrid":
        pred["rank_score"] = pred["ml_pred"].astype(float) + pred["base_score"].astype(float) / 100.0 * 0.03
    elif score_mode == "penalized_tail":
        volume_surge = pred.get("volume_surge20", pd.Series(0.0, index=pred.index)).astype(float).clip(0, 20)
        vol20 = pred.get("vol20", pd.Series(0.0, index=pred.index)).astype(float).clip(0, 1)
        pred["rank_score"] = pred["ml_pred"].astype(float) + pred["base_score"].astype(float) / 100.0 * 0.02 - volume_surge * 0.001 - vol20 * 0.20
    else:
        raise ValueError(score_mode)
    if top_n_per_date is not None:
        pred = pred.sort_values(["Date", "rank_score", "base_score"], ascending=[True, False, False]).groupby("Date", as_index=False).head(top_n_per_date)
    result: dict[str, dict[str, float]] = {}
    for dt, group in pred.groupby("Date"):
        result[pd.Timestamp(dt).date().isoformat()] = dict(zip(group["code"], group["rank_score"].astype(float)))
    return result


def run_engine(panel: pd.DataFrame, cfg: dict[str, Any], *, prediction_map=None, cost_bps=30.0, years: list[int] | None = None) -> dict[str, Any]:
    p = panel.copy()
    if years is not None:
        p = p[p["Date"].dt.year.isin(years)].copy()
    c = dict(cfg)
    step = int(c.pop("step"))
    engine = ReplayEngine(
        panel=p,
        symbols=symbols(p),
        initial_cash_krw=1_000_000,
        max_notional_krw=100_000,
        transaction_cost_bps=cost_bps,
        prediction_map=prediction_map,
        **c,
    )
    return engine.run(step=step)


def summarize_trades(result: dict[str, Any]) -> dict[str, Any]:
    trades = pd.DataFrame(result.get("trades", []))
    if trades.empty:
        return {"stop_loss": 0, "take_profit": 0, "time_exit": 0, "avg_ml_prediction": None}
    reasons = trades["exit_reason"].value_counts().to_dict()
    return {
        "stop_loss": int(reasons.get("stop_loss", 0)),
        "take_profit": int(reasons.get("take_profit", 0)),
        "time_exit": int(reasons.get("time_exit", 0)),
        "avg_ml_prediction": round(float(trades["ml_prediction"].dropna().mean()), 6) if "ml_prediction" in trades else None,
    }


def main() -> None:
    panel = pd.read_csv(PANEL, dtype={"code": str}, parse_dates=["Date"])
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    rows = []
    trade_frames = []
    years = sorted(panel["Date"].dt.year.unique().tolist())

    for cost in [0.0, 10.0, 20.0, 30.0]:
        base_result = run_engine(panel, BASE, cost_bps=cost)
        row = {"candidate": "canonical_base", "model": "base", "score_mode": "base", "top_n_pred": None, "cost_bps": cost, **base_result["summary"], **summarize_trades(base_result)}
        for y in years:
            yr = run_engine(panel, BASE, cost_bps=cost, years=[y])["summary"]
            row[f"y{y}_return"] = yr["total_return_pct"]
            row[f"y{y}_trades"] = yr["total_trades"]
        rows.append(row)

    for model_name, pred_path in PRED_FILES.items():
        for score_mode in ["ml", "hybrid", "penalized_tail"]:
            for top_n_pred in [4, 8, 16, 32, None]:
                pred_map = load_prediction_map(pred_path, score_mode=score_mode, top_n_per_date=top_n_pred)
                for cost in [0.0, 10.0, 20.0, 30.0]:
                    result = run_engine(panel, BASE, prediction_map=pred_map, cost_bps=cost)
                    candidate = f"{model_name}_{score_mode}_top{top_n_pred or 'all'}"
                    row = {"candidate": candidate, "model": model_name, "score_mode": score_mode, "top_n_pred": top_n_pred or "all", "cost_bps": cost, **result["summary"], **summarize_trades(result)}
                    yr_returns = []
                    for y in years:
                        yr_map = {d: scores for d, scores in pred_map.items() if pd.Timestamp(d).year == y}
                        yr_result = run_engine(panel, BASE, prediction_map=yr_map, cost_bps=cost, years=[y])
                        yr_summary = yr_result["summary"]
                        row[f"y{y}_return"] = yr_summary["total_return_pct"]
                        row[f"y{y}_trades"] = yr_summary["total_trades"]
                        yr_returns.append(yr_summary["total_return_pct"])
                    row["worst_year_return"] = min(yr_returns) if yr_returns else None
                    row["objective"] = row["total_return_pct"] + 4 * row["worst_year_return"] - abs(row["max_drawdown_pct"])
                    rows.append(row)
                    trades = pd.DataFrame(result.get("trades", []))
                    if not trades.empty and cost == 30.0:
                        trades.insert(0, "candidate", candidate)
                        trade_frames.append(trades)
                    print(json.dumps({"candidate": candidate, "cost": cost, "return": row["total_return_pct"], "worst_year": row.get("worst_year_return"), "mdd": row["max_drawdown_pct"], "trades": row["total_trades"]}, ensure_ascii=False), flush=True)

    df = pd.DataFrame(rows)
    result_csv = OUT_DIR / "ml_canonical_replay_validation_20260621.csv"
    trades_csv = OUT_DIR / "ml_canonical_replay_validation_trades_20260621.csv"
    report_md = OUT_DIR / "ml_canonical_replay_validation_20260621.md"
    df.to_csv(result_csv, index=False)
    if trade_frames:
        pd.concat(trade_frames, ignore_index=True).to_csv(trades_csv, index=False)
    else:
        pd.DataFrame().to_csv(trades_csv, index=False)
    cost30 = df[df["cost_bps"] == 30.0].copy()
    lines = [
        "# ML prediction canonical ReplayEngine validation — 2026-06-21",
        "",
        "Paper/research only. live_order_submitted: False.",
        "",
        "## Method",
        "- Uses the canonical `ReplayEngine` exit/cost/position accounting.",
        "- Injects ML prediction CSVs as candidate ranking overlays, not a parallel simulator.",
        "- Tests ExtraTrees and LightGBM predictions with `ml`, `hybrid`, and `penalized_tail` scoring.",
        "- Cost stress: 0/10/20/30 bps.",
        "",
        "## Files",
        f"- results: `{result_csv}`",
        f"- trades: `{trades_csv}`",
        "",
        "## 30bps best by full return",
        cost30.sort_values(["total_return_pct", "objective"], ascending=False).head(20).to_markdown(index=False),
        "",
        "## 30bps best by robustness objective",
        cost30.sort_values(["objective", "total_return_pct"], ascending=False).head(20).to_markdown(index=False),
        "",
        "## Verdict template",
        "- PROMOTE only if candidate beats canonical_base on full return and does not degrade worst-year / drawdown materially.",
    ]
    report_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({
        "result_csv": str(result_csv),
        "trades_csv": str(trades_csv),
        "report_md": str(report_md),
        "best_30bps_return": cost30.sort_values(["total_return_pct", "objective"], ascending=False).head(8).to_dict(orient="records"),
        "best_30bps_objective": cost30.sort_values(["objective", "total_return_pct"], ascending=False).head(8).to_dict(orient="records"),
    }, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
