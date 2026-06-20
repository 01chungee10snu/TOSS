from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from toss_alpha.daily.replay import ReplayEngine

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "reports/backtests/random500_seed20260607_2022-01-01_2026-latest_ohlcv_panel.csv"
OUT_DIR = ROOT / "reports/harness"
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


def load_prediction_map(path: Path, *, score_mode: str, top_n_per_date: int | None) -> dict[str, dict[str, float]]:
    pred = pd.read_csv(path, dtype={"code": str}, parse_dates=["Date"])
    pred["code"] = pred["code"].astype(str).str.zfill(6)
    if score_mode == "ml":
        pred["rank_score"] = pred["ml_pred"].astype(float)
    elif score_mode == "hybrid":
        pred["rank_score"] = pred["ml_pred"].astype(float) + pred["base_score"].astype(float) / 100.0 * 0.03
    else:
        raise ValueError(score_mode)
    if top_n_per_date is not None:
        pred = pred.sort_values(["Date", "rank_score", "base_score"], ascending=[True, False, False]).groupby("Date", as_index=False).head(top_n_per_date)
    return {
        pd.Timestamp(dt).date().isoformat(): dict(zip(group["code"], group["rank_score"].astype(float)))
        for dt, group in pred.groupby("Date")
    }


def run_engine(panel: pd.DataFrame, *, prediction_map=None, cost_bps=30.0, year: int | None = None) -> dict[str, Any]:
    p = panel if year is None else panel[panel["Date"].dt.year == year].copy()
    c = dict(BASE)
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


def filter_year_map(prediction_map: dict[str, dict[str, float]], year: int) -> dict[str, dict[str, float]]:
    return {date: scores for date, scores in prediction_map.items() if pd.Timestamp(date).year == year}


def trade_breakdown(result: dict[str, Any]) -> dict[str, Any]:
    trades = pd.DataFrame(result.get("trades", []))
    if trades.empty:
        return {"stop_loss": 0, "take_profit": 0, "time_exit": 0, "avg_ml_prediction": None}
    vc = trades["exit_reason"].value_counts().to_dict()
    return {
        "stop_loss": int(vc.get("stop_loss", 0)),
        "take_profit": int(vc.get("take_profit", 0)),
        "time_exit": int(vc.get("time_exit", 0)),
        "avg_ml_prediction": round(float(trades["ml_prediction"].dropna().mean()), 6) if "ml_prediction" in trades and trades["ml_prediction"].notna().any() else None,
    }


def main() -> None:
    panel = pd.read_csv(PANEL, dtype={"code": str}, parse_dates=["Date"])
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    years = sorted(panel["Date"].dt.year.unique().tolist())
    candidates = [("canonical_base", None, None, None)]
    for model, mode, top in [
        ("extratrees", "ml", None),
        ("extratrees", "ml", 4),
        ("lgbm", "ml", None),
        ("lgbm", "ml", 4),
        ("lgbm", "hybrid", None),
        ("lgbm", "hybrid", 4),
    ]:
        candidates.append((f"{model}_{mode}_top{top or 'all'}", model, mode, top))

    pred_maps = {}
    for name, model, mode, top in candidates:
        if model is None:
            pred_maps[name] = None
        else:
            pred_maps[name] = load_prediction_map(PRED_FILES[model], score_mode=mode, top_n_per_date=top)

    rows = []
    trade_frames = []
    for name, model, mode, top in candidates:
        pred_map = pred_maps[name]
        for cost in [0.0, 10.0, 20.0, 30.0]:
            print("RUN", name, "cost", cost, flush=True)
            result = run_engine(panel, prediction_map=pred_map, cost_bps=cost)
            row = {"candidate": name, "model": model or "base", "score_mode": mode or "base", "top_n_pred": top or "all", "cost_bps": cost, **result["summary"], **trade_breakdown(result)}
            if cost == 30.0:
                yr_returns = []
                for y in years:
                    yr_map = None if pred_map is None else filter_year_map(pred_map, y)
                    yr = run_engine(panel, prediction_map=yr_map, cost_bps=cost, year=y)["summary"]
                    row[f"y{y}_return"] = yr["total_return_pct"]
                    row[f"y{y}_trades"] = yr["total_trades"]
                    yr_returns.append(yr["total_return_pct"])
                row["worst_year_return"] = min(yr_returns)
                row["objective"] = row["total_return_pct"] + 4 * row["worst_year_return"] - abs(row["max_drawdown_pct"])
                trades = pd.DataFrame(result.get("trades", []))
                if not trades.empty:
                    trades.insert(0, "candidate", name)
                    trade_frames.append(trades)
            rows.append(row)
            print(json.dumps({"candidate": name, "cost": cost, "return": row["total_return_pct"], "mdd": row["max_drawdown_pct"], "trades": row["total_trades"], "worst_year": row.get("worst_year_return")}, ensure_ascii=False), flush=True)

    df = pd.DataFrame(rows)
    result_csv = OUT_DIR / "ml_canonical_replay_validation_focused_20260621.csv"
    trades_csv = OUT_DIR / "ml_canonical_replay_validation_focused_trades_20260621.csv"
    report_md = OUT_DIR / "ml_canonical_replay_validation_focused_20260621.md"
    df.to_csv(result_csv, index=False)
    if trade_frames:
        pd.concat(trade_frames, ignore_index=True).to_csv(trades_csv, index=False)
    else:
        pd.DataFrame().to_csv(trades_csv, index=False)
    cost30 = df[df["cost_bps"] == 30.0].copy()
    lines = [
        "# Focused ML canonical ReplayEngine validation — 2026-06-21",
        "",
        "Paper/research only. live_order_submitted: False.",
        "",
        "## Method",
        "- Canonical ReplayEngine with ML prediction ranking injection.",
        "- Focused candidates: ExtraTrees raw max-return and LightGBM robust-signal candidates.",
        "- Cost stress full-period: 0/10/20/30bps.",
        "- Year split at 30bps.",
        "",
        "## Files",
        f"- results: `{result_csv}`",
        f"- trades: `{trades_csv}`",
        "",
        "## 30bps results by return",
        cost30.sort_values(["total_return_pct", "objective"], ascending=False).to_markdown(index=False),
        "",
        "## 30bps results by robustness objective",
        cost30.sort_values(["objective", "total_return_pct"], ascending=False).to_markdown(index=False),
    ]
    report_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({
        "result_csv": str(result_csv),
        "trades_csv": str(trades_csv),
        "report_md": str(report_md),
        "cost30_by_return": cost30.sort_values(["total_return_pct", "objective"], ascending=False).to_dict(orient="records"),
        "cost30_by_objective": cost30.sort_values(["objective", "total_return_pct"], ascending=False).to_dict(orient="records"),
    }, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
