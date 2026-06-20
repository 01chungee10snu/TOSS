"""Parallel ML overlay validation: ML as auxiliary overlay on canonical base scoring.

Tests rerank, gate, and penalty overlay modes with ExtraTrees and LightGBM predictions.
Uses pre-computed score cache + 15-core fork parallelism.

Paper/research only. live_order_submitted: False.
"""
from __future__ import annotations

import itertools
import json
import multiprocessing as mp
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from toss_alpha.daily import decision as _decision_mod
from toss_alpha.daily import replay as _replay_mod
from toss_alpha.daily.replay import ReplayEngine

PANEL_CSV = ROOT / "reports/backtests/random500_seed20260607_2022-01-01_2026-latest_ohlcv_panel.csv"
OUT_DIR = ROOT / "reports/harness"
PRED_FILES = {
    "extratrees": OUT_DIR / "ml_direct_pred_extratrees_20260621.csv",
    "lgbm": OUT_DIR / "ml_direct_pred_lgbm_20260621.csv",
}
ENGINE_BASE = {
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

_PANEL: pd.DataFrame | None = None
_PRED_MAPS: dict[str, dict[str, dict[str, float]] | None] = {}
_SCORE_CACHE: dict[str, tuple[dict, list[dict]]] = {}
_STEP = 5


def _compute_score_cache(panel: pd.DataFrame, step: int) -> dict[str, tuple[dict, list[dict]]]:
    all_dates = sorted(panel["Date"].unique())
    replay_dates = all_dates[::step]
    cache: dict[str, tuple[dict, list[dict]]] = {}
    for i, ts in enumerate(replay_dates):
        date_str = pd.Timestamp(ts).date().isoformat()
        sub = panel[panel["Date"] <= ts]
        regime = _decision_mod._classify_regime(sub)
        candidates = _decision_mod._score_candidates(sub, regime=regime)
        candidates.sort(key=lambda c: c["final_score"], reverse=True)
        cache[date_str] = (regime, candidates)
        if (i + 1) % 20 == 0 or i == 0 or i == len(replay_dates) - 1:
            print(f"  score-cache {i+1}/{len(replay_dates)}", flush=True)
    return cache


def _patched_classify_regime(panel: pd.DataFrame) -> dict[str, Any]:
    last_date = pd.Timestamp(panel["Date"].iloc[-1]).date().isoformat()
    if last_date in _SCORE_CACHE:
        return _SCORE_CACHE[last_date][0]
    return _decision_mod._classify_regime_original(panel)


def _patched_score_candidates(panel: pd.DataFrame, *, regime: dict[str, Any]) -> list[dict[str, Any]]:
    last_date = pd.Timestamp(panel["Date"].iloc[-1]).date().isoformat()
    if last_date in _SCORE_CACHE:
        return list(_SCORE_CACHE[last_date][1])
    return _decision_mod._score_candidates_original(panel, regime=regime)


def _install_patches() -> None:
    if not hasattr(_decision_mod, "_classify_regime_original"):
        _decision_mod._classify_regime_original = _decision_mod._classify_regime
    if not hasattr(_decision_mod, "_score_candidates_original"):
        _decision_mod._score_candidates_original = _decision_mod._score_candidates
    _decision_mod._classify_regime = _patched_classify_regime
    _decision_mod._score_candidates = _patched_score_candidates
    _replay_mod._classify_regime = _patched_classify_regime
    _replay_mod._score_candidates = _patched_score_candidates


def symbols_of(panel: pd.DataFrame) -> list[str]:
    return sorted(panel["code"].astype(str).str.zfill(6).unique().tolist())


def load_prediction_map(path: Path) -> dict[str, dict[str, float]]:
    pred = pd.read_csv(path, dtype={"code": str}, parse_dates=["Date"], usecols=["Date", "code", "ml_pred"])
    pred["code"] = pred["code"].astype(str).str.zfill(6)
    return {
        pd.Timestamp(dt).date().isoformat(): dict(zip(g["code"], g["ml_pred"].astype(float)))
        for dt, g in pred.groupby("Date")
    }


def _worker(task: dict[str, Any]) -> dict[str, Any]:
    _install_patches()
    candidate = task["candidate"]
    scope = task["scope"]
    year = task.get("year")
    cost = task.get("cost", 30.0)
    pred_map = task["pred_map"]
    overlay_mode = task.get("overlay_mode")
    overlay_alpha = task.get("overlay_alpha", 10.0)
    pred_min = task.get("pred_min")

    panel = _PANEL
    if year is not None:
        panel = panel[panel["Date"].dt.year == year].copy()

    cfg = dict(ENGINE_BASE)
    step = int(cfg.pop("step"))

    engine_kwargs = dict(
        panel=panel,
        symbols=symbols_of(panel),
        initial_cash_krw=1_000_000,
        max_notional_krw=100_000,
        transaction_cost_bps=cost,
        prediction_map=pred_map,
    )
    if overlay_mode:
        engine_kwargs["prediction_overlay_mode"] = overlay_mode
        engine_kwargs["prediction_alpha"] = overlay_alpha
    if pred_min is not None:
        engine_kwargs["prediction_min_score"] = pred_min

    engine = ReplayEngine(**engine_kwargs, **cfg)
    result = engine.run(step=step)
    summary = dict(result["summary"])

    trades = pd.DataFrame(result.get("trades", []))
    if not trades.empty:
        vc = trades["exit_reason"].value_counts().to_dict()
        summary["stop_loss"] = int(vc.get("stop_loss", 0))
        summary["take_profit"] = int(vc.get("take_profit", 0))
        summary["time_exit"] = int(vc.get("time_exit", 0))
    else:
        summary["stop_loss"] = summary["take_profit"] = summary["time_exit"] = 0

    trades_out = trades.to_dict(orient="records") if cost == 30.0 and scope == "full" and not trades.empty else None

    return {
        "candidate": candidate,
        "scope": scope,
        "cost_bps": cost,
        **summary,
        "trades": trades_out,
    }


def main() -> None:
    global _PANEL

    print("Loading panel...", flush=True)
    _PANEL = pd.read_csv(PANEL_CSV, dtype={"code": str}, parse_dates=["Date"])
    _PANEL["code"] = _PANEL["code"].astype(str).str.zfill(6)
    years = sorted(_PANEL["Date"].dt.year.unique().tolist())
    print(f"Panel: {len(_PANEL)} rows, {len(_PANEL['code'].unique())} symbols", flush=True)

    print("Pre-computing score cache...", flush=True)
    t0 = time.time()
    _SCORE_CACHE.update(_compute_score_cache(_PANEL, _STEP))
    print(f"Score cache: {len(_SCORE_CACHE)} dates in {time.time()-t0:.1f}s", flush=True)

    print("Loading prediction maps...", flush=True)
    for name, path in PRED_FILES.items():
        _PRED_MAPS[name] = load_prediction_map(path)
        print(f"  {name}: {len(_PRED_MAPS[name])} dates", flush=True)

    # Build task list
    tasks: list[dict[str, Any]] = []

    # 1. Canonical base: cost stress + year split
    for cost in [0.0, 10.0, 20.0, 30.0]:
        tasks.append({"candidate": "canonical_base", "scope": "full", "cost": cost, "pred_map": None})
    for y in years:
        tasks.append({"candidate": "canonical_base", "scope": str(y), "year": y, "cost": 30.0, "pred_map": None})

    # 2. Overlay candidates: for each model × overlay_mode × params × cost
    overlay_configs = []
    # rerank: no extra params needed
    for model in PRED_FILES:
        overlay_configs.append({"model": model, "overlay_mode": "rerank", "overlay_alpha": 0, "pred_min": None,
                                "label": f"{model}_rerank"})
    # gate: with pred_min = 0.0
    for model in PRED_FILES:
        overlay_configs.append({"model": model, "overlay_mode": "gate", "overlay_alpha": 0, "pred_min": 0.0,
                                "label": f"{model}_gate0"})
    # penalty: alpha = 5, 10, 20
    for model, alpha in itertools.product(PRED_FILES, [5.0, 10.0, 20.0]):
        overlay_configs.append({"model": model, "overlay_mode": "penalty", "overlay_alpha": alpha, "pred_min": None,
                                "label": f"{model}_penalty_a{alpha:.0f}"})

    for oc in overlay_configs:
        label = oc["label"]
        pred_map = _PRED_MAPS[oc["model"]]
        # Year filter for year-specific tasks
        for cost in [0.0, 10.0, 20.0, 30.0]:
            tasks.append({
                "candidate": label,
                "scope": "full",
                "cost": cost,
                "pred_map": pred_map,
                "overlay_mode": oc["overlay_mode"],
                "overlay_alpha": oc["overlay_alpha"],
                "pred_min": oc["pred_min"],
            })
        for y in years:
            yr_map = {d: s for d, s in pred_map.items() if pd.Timestamp(d).year == y}
            tasks.append({
                "candidate": label,
                "scope": str(y),
                "year": y,
                "cost": 30.0,
                "pred_map": yr_map,
                "overlay_mode": oc["overlay_mode"],
                "overlay_alpha": oc["overlay_alpha"],
                "pred_min": oc["pred_min"],
            })

    n_workers = min(15, len(tasks))
    print(f"\n{len(tasks)} tasks, {n_workers} workers\n", flush=True)

    ctx = mp.get_context("fork")
    with ctx.Pool(processes=n_workers) as pool:
        results = pool.map(_worker, tasks)

    df = pd.DataFrame([{k: v for k, v in r.items() if k != "trades"} for r in results])
    result_csv = OUT_DIR / "ml_overlay_validation_parallel_20260621.csv"
    report_md = OUT_DIR / "ml_overlay_validation_parallel_20260621.md"
    trades_csv = OUT_DIR / "ml_overlay_validation_parallel_trades_20260621.csv"
    df.to_csv(result_csv, index=False)

    trade_frames = []
    for r in results:
        if r.get("trades"):
            tf = pd.DataFrame(r["trades"])
            tf.insert(0, "candidate", r["candidate"])
            trade_frames.append(tf)
    if trade_frames:
        pd.concat(trade_frames, ignore_index=True).to_csv(trades_csv, index=False)
    else:
        pd.DataFrame().to_csv(trades_csv, index=False)

    full_30 = df[(df["scope"] == "full") & (df["cost_bps"] == 30.0)].copy()
    years_df = df[df["scope"] != "full"].copy()

    # Compute worst-year for each candidate
    worst_year = {}
    for c in full_30["candidate"].unique():
        yr_returns = years_df[years_df["candidate"] == c]["total_return_pct"].tolist()
        worst_year[c] = min(yr_returns) if yr_returns else None
    full_30["worst_year_return"] = full_30["candidate"].map(worst_year)
    full_30["objective"] = full_30["total_return_pct"] + 4 * full_30["worst_year_return"].fillna(0) - full_30["max_drawdown_pct"].abs()

    # Cost stress for all candidates
    cost_stress = df[df["scope"] == "full"].copy()

    lines = [
        "# ML overlay validation (parallel) — 2026-06-21",
        "",
        "Paper/research only. live_order_submitted: False.",
        "",
        "## Method",
        "- Canonical ReplayEngine with ML prediction overlay modes.",
        "- Overlay modes: rerank (ML re-orders base survivors), gate (base + ML confirmation), penalty (base_score + alpha * ml_pred).",
        "- Models: ExtraTrees, LightGBM.",
        "- Cost stress: 0/10/20/30 bps. Year split at 30bps.",
        "- Pre-computed score cache + 15-core fork parallel.",
        "",
        "## Files",
        f"- results: `{result_csv}`",
        f"- trades: `{trades_csv}`",
        "",
        "## 30bps full-period — sorted by objective",
        full_30.sort_values(["objective", "total_return_pct"], ascending=False).to_markdown(index=False),
        "",
        "## 30bps full-period — sorted by return",
        full_30.sort_values(["total_return_pct", "objective"], ascending=False).to_markdown(index=False),
        "",
        "## Year split (30bps)",
        years_df.to_markdown(index=False),
        "",
        "## Cost stress (full period)",
        cost_stress.sort_values(["candidate", "cost_bps"]).to_markdown(index=False),
    ]
    report_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps({
        "result_csv": str(result_csv),
        "trades_csv": str(trades_csv),
        "report_md": str(report_md),
        "full_30_by_objective": full_30.sort_values(["objective", "total_return_pct"], ascending=False).head(15).to_dict(orient="records"),
        "full_30_by_return": full_30.sort_values(["total_return_pct", "objective"], ascending=False).head(15).to_dict(orient="records"),
    }, ensure_ascii=False, indent=2, default=str), flush=True)


if __name__ == "__main__":
    main()
