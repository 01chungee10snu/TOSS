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
    "extratrees_ml_all": OUT_DIR / "ml_direct_pred_extratrees_20260621.csv",
    "lgbm_ml_all": OUT_DIR / "ml_direct_pred_lgbm_20260621.csv",
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


def load_prediction_map(path: Path, *, year: int | None = None) -> dict[str, dict[str, float]]:
    usecols = ["Date", "code", "ml_pred"]
    pred = pd.read_csv(path, dtype={"code": str}, parse_dates=["Date"], usecols=usecols)
    if year is not None:
        pred = pred[pred["Date"].dt.year == year].copy()
    pred["code"] = pred["code"].astype(str).str.zfill(6)
    result = {}
    for dt, group in pred.groupby("Date"):
        result[pd.Timestamp(dt).date().isoformat()] = dict(zip(group["code"], group["ml_pred"].astype(float)))
    return result


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


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    trades = pd.DataFrame(result.get("trades", []))
    row = dict(result["summary"])
    if trades.empty:
        row.update({"stop_loss": 0, "take_profit": 0, "time_exit": 0, "avg_ml_prediction": None})
    else:
        vc = trades["exit_reason"].value_counts().to_dict()
        row.update({
            "stop_loss": int(vc.get("stop_loss", 0)),
            "take_profit": int(vc.get("take_profit", 0)),
            "time_exit": int(vc.get("time_exit", 0)),
            "avg_ml_prediction": round(float(trades["ml_prediction"].dropna().mean()), 6) if trades["ml_prediction"].notna().any() else None,
        })
    return row


def main() -> None:
    panel = pd.read_csv(PANEL, dtype={"code": str}, parse_dates=["Date"])
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    years = sorted(panel["Date"].dt.year.unique().tolist())
    rows = []
    trade_frames = []

    candidates = [("canonical_base", None)] + list(PRED_FILES.items())
    for candidate, pred_path in candidates:
        print("FULL", candidate, flush=True)
        pred_map = None if pred_path is None else load_prediction_map(pred_path)
        result = run_engine(panel, prediction_map=pred_map, cost_bps=30.0)
        row = {"candidate": candidate, "scope": "full", "cost_bps": 30.0, **summarize(result)}
        rows.append(row)
        trades = pd.DataFrame(result.get("trades", []))
        if not trades.empty:
            trades.insert(0, "candidate", candidate)
            trade_frames.append(trades)
        print(json.dumps({"candidate": candidate, "scope": "full", "return": row["total_return_pct"], "mdd": row["max_drawdown_pct"], "trades": row["total_trades"]}, ensure_ascii=False), flush=True)
        for y in years:
            print("YEAR", candidate, y, flush=True)
            yr_map = None if pred_path is None else load_prediction_map(pred_path, year=y)
            yr_result = run_engine(panel, prediction_map=yr_map, cost_bps=30.0, year=y)
            yr_row = {"candidate": candidate, "scope": str(y), "cost_bps": 30.0, **summarize(yr_result)}
            rows.append(yr_row)
            print(json.dumps({"candidate": candidate, "scope": y, "return": yr_row["total_return_pct"], "mdd": yr_row["max_drawdown_pct"], "trades": yr_row["total_trades"]}, ensure_ascii=False), flush=True)

    df = pd.DataFrame(rows)
    result_csv = OUT_DIR / "ml_canonical_replay_validation_ultrafocused_20260621.csv"
    trades_csv = OUT_DIR / "ml_canonical_replay_validation_ultrafocused_trades_20260621.csv"
    report_md = OUT_DIR / "ml_canonical_replay_validation_ultrafocused_20260621.md"
    df.to_csv(result_csv, index=False)
    if trade_frames:
        pd.concat(trade_frames, ignore_index=True).to_csv(trades_csv, index=False)
    else:
        pd.DataFrame().to_csv(trades_csv, index=False)
    full = df[df["scope"] == "full"].copy()
    years_df = df[df["scope"] != "full"].copy()
    lines = [
        "# Ultra-focused ML canonical ReplayEngine validation — 2026-06-21",
        "",
        "Paper/research only. live_order_submitted: False.",
        "",
        "## Method",
        "- Canonical ReplayEngine with ML prediction map injected into candidate ranking.",
        "- Candidates: canonical base, ExtraTrees ml_all, LightGBM ml_all.",
        "- 30bps only for first validation; cost stress comes after a candidate survives.",
        "",
        "## Files",
        f"- results: `{result_csv}`",
        f"- trades: `{trades_csv}`",
        "",
        "## Full period",
        full.sort_values("total_return_pct", ascending=False).to_markdown(index=False),
        "",
        "## Year split",
        years_df.to_markdown(index=False),
    ]
    report_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"result_csv": str(result_csv), "trades_csv": str(trades_csv), "report_md": str(report_md), "full": full.to_dict(orient="records"), "years": years_df.to_dict(orient="records")}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
