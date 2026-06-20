"""Parallel ML canonical replay validation with pre-computed scoring cache.

Strategy: the original ReplayEngine recomputes _classify_regime and _score_candidates
on the FULL growing panel at every step. For 496 symbols × ~220 steps that is O(N²)
in dates. We pre-compute (regime, candidates) for every unique replay date ONCE,
cache in RAM, and patch the imported functions so every ReplayEngine instance does
an O(1) dict lookup instead.

We then fan out all (candidate × scope) runs across all available CPU cores via
multiprocessing with fork (COW panel + prediction maps).

Paper/research only. live_order_submitted: False.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd

# Ensure src is importable when run as a script
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from toss_alpha.daily import decision as _decision_mod
from toss_alpha.daily import replay as _replay_mod
from toss_alpha.daily.replay import ReplayEngine

PANEL_CSV = ROOT / "reports/backtests/random500_seed20260607_2022-01-01_2026-latest_ohlcv_panel.csv"
OUT_DIR = ROOT / "reports/harness"
PRED_FILES = {
    "extratrees_ml_all": OUT_DIR / "ml_direct_pred_extratrees_20260621.csv",
    "lgbm_ml_all": OUT_DIR / "ml_direct_pred_lgbm_20260621.csv",
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

# ── Globals shared via fork COW ──────────────────────────────────────────
_PANEL: pd.DataFrame | None = None
_PRED_MAPS: dict[str, dict[str, dict[str, float]] | None] = {}
_SCORE_CACHE: dict[str, tuple[dict, list[dict]]] = {}
_STEP = 5


def _compute_score_cache(panel: pd.DataFrame, step: int) -> dict[str, tuple[dict, list[dict]]]:
    """Pre-compute (regime, candidates) for every replay date ONCE.

    This replaces the O(N²) per-step rescanning of the full panel with a single
    pre-computed lookup. The computation is identical — same _classify_regime
    and _score_candidates on the same sub-panel — just done once per date rather
    than once per step per engine instance.
    """
    all_dates = sorted(panel["Date"].unique())
    replay_dates = all_dates[::step]
    cache: dict[str, tuple[dict, list[dict]]] = {}
    total = len(replay_dates)
    for i, ts in enumerate(replay_dates):
        date_str = pd.Timestamp(ts).date().isoformat()
        sub = panel[panel["Date"] <= ts]
        regime = _decision_mod._classify_regime(sub)
        candidates = _decision_mod._score_candidates(sub, regime=regime)
        candidates.sort(key=lambda c: c["final_score"], reverse=True)
        cache[date_str] = (regime, candidates)
        if (i + 1) % 20 == 0 or i == 0 or i == total - 1:
            print(f"  score-cache {i+1}/{total}", flush=True)
    return cache


def _patched_classify_regime(panel: pd.DataFrame) -> dict[str, Any]:
    """Fallback: use cache by inferring date, else compute."""
    last_date = pd.Timestamp(panel["Date"].iloc[-1]).date().isoformat()
    if last_date in _SCORE_CACHE:
        return _SCORE_CACHE[last_date][0]
    return _decision_mod._classify_regime_original(panel)


def _patched_score_candidates(panel: pd.DataFrame, *, regime: dict[str, Any]) -> list[dict[str, Any]]:
    last_date = pd.Timestamp(panel["Date"].iloc[-1]).date().isoformat()
    if last_date in _SCORE_CACHE:
        return list(_SCORE_CACHE[last_date][1])  # return a copy
    return _decision_mod._score_candidates_original(panel, regime=regime)


def _install_patches() -> None:
    """Patch the decision functions used by replay.py to use our pre-computed cache."""
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


def load_prediction_map(path: Path, *, year: int | None = None) -> dict[str, dict[str, float]]:
    pred = pd.read_csv(path, dtype={"code": str}, parse_dates=["Date"], usecols=["Date", "code", "ml_pred"])
    if year is not None:
        pred = pred[pred["Date"].dt.year == year]
    pred["code"] = pred["code"].astype(str).str.zfill(6)
    return {
        pd.Timestamp(dt).date().isoformat(): dict(zip(g["code"], g["ml_pred"].astype(float)))
        for dt, g in pred.groupby("Date")
    }


def _worker(task: dict[str, Any]) -> dict[str, Any]:
    """Run a single (candidate, scope) replay in a forked worker."""
    _install_patches()
    candidate = task["candidate"]
    scope = task["scope"]
    year = task.get("year")
    pred_map = task["pred_map"]
    cost = task.get("cost", 30.0)

    panel = _PANEL
    if year is not None:
        panel = panel[panel["Date"].dt.year == year].copy()

    cfg = dict(ENGINE_BASE)
    step = int(cfg.pop("step"))
    engine = ReplayEngine(
        panel=panel,
        symbols=symbols_of(panel),
        initial_cash_krw=1_000_000,
        max_notional_krw=100_000,
        transaction_cost_bps=cost,
        prediction_map=pred_map,
        prediction_min_score=0.0,
        **cfg,
    )
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

    trades_out = trades.to_dict(orient="records") if cost == 30.0 and not trades.empty else None

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
    print(f"Panel: {len(_PANEL)} rows, {len(_PANEL['code'].unique())} symbols, years {years}", flush=True)

    # Pre-compute score cache for full panel
    print("Pre-computing score cache for full panel...", flush=True)
    t0 = time.time()
    _SCORE_CACHE.update(_compute_score_cache(_PANEL, _STEP))
    print(f"Score cache: {len(_SCORE_CACHE)} dates in {time.time()-t0:.1f}s", flush=True)

    # Pre-load prediction maps
    print("Loading prediction maps...", flush=True)
    _PRED_MAPS["canonical_base"] = None
    for name, path in PRED_FILES.items():
        _PRED_MAPS[name] = load_prediction_map(path)
        print(f"  {name}: {len(_PRED_MAPS[name])} dates", flush=True)

    # Build task list: each candidate × (full + each year) × cost stress
    tasks: list[dict[str, Any]] = []
    candidate_names = ["canonical_base"] + list(PRED_FILES.keys())
    for candidate in candidate_names:
        pred_map = _PRED_MAPS[candidate]
        # Full period: cost stress 0/10/20/30
        for cost in [0.0, 10.0, 20.0, 30.0]:
            tasks.append({"candidate": candidate, "scope": "full", "cost": cost, "pred_map": pred_map})
        # Year split at 30bps only
        for y in years:
            yr_map = None if pred_map is None else {
                d: s for d, s in pred_map.items() if pd.Timestamp(d).year == y
            }
            tasks.append({"candidate": candidate, "scope": str(y), "year": y, "cost": 30.0, "pred_map": yr_map})

    n_workers = min(15, len(tasks))
    print(f"\nDispatching {len(tasks)} tasks across {n_workers} workers...\n", flush=True)

    # Fork context for COW sharing of _PANEL, _SCORE_CACHE, _PRED_MAPS
    ctx = mp.get_context("fork")
    with ctx.Pool(processes=n_workers) as pool:
        results = pool.map(_worker, tasks)

    # Build report
    df = pd.DataFrame([{k: v for k, v in r.items() if k != "trades"} for r in results])
    result_csv = OUT_DIR / "ml_canonical_replay_validation_parallel_20260621.csv"
    report_md = OUT_DIR / "ml_canonical_replay_validation_parallel_20260621.md"
    trades_csv = OUT_DIR / "ml_canonical_replay_validation_parallel_trades_20260621.csv"

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

    # Compute worst-year for 30bps candidates
    worst_year = {}
    for candidate in candidate_names:
        yr_returns = years_df[years_df["candidate"] == candidate]["total_return_pct"].tolist()
        worst_year[candidate] = min(yr_returns) if yr_returns else None

    full_30["worst_year_return"] = full_30["candidate"].map(worst_year)
    full_30["objective"] = full_30["total_return_pct"] + 4 * full_30["worst_year_return"].fillna(0) - full_30["max_drawdown_pct"].abs()

    # Cost stress table
    cost_stress = df[(df["scope"] == "full")].copy()

    lines = [
        "# Parallel ML canonical ReplayEngine validation — 2026-06-21",
        "",
        "Paper/research only. live_order_submitted: False.",
        "",
        "## Method",
        "- Canonical ReplayEngine with ML prediction ranking injection (prediction_map).",
        "- Pre-computed score cache: all (regime, candidates) computed once, O(1) lookup per step.",
        "- Parallel: all candidate × scope runs dispatched across 15 cores via multiprocessing fork.",
        "- Cost stress: 0/10/20/30 bps full period.",
        "- Year split at 30 bps.",
        "",
        "## Files",
        f"- results: `{result_csv}`",
        f"- trades: `{trades_csv}`",
        "",
        "## 30bps full-period comparison",
        full_30.sort_values(["total_return_pct", "objective"], ascending=False).to_markdown(index=False),
        "",
        "## 30bps best by robustness objective",
        full_30.sort_values(["objective", "total_return_pct"], ascending=False).to_markdown(index=False),
        "",
        "## Year split (30bps)",
        years_df.to_markdown(index=False),
        "",
        "## Cost stress (full period, all costs)",
        cost_stress.sort_values(["candidate", "cost_bps"]).to_markdown(index=False),
    ]
    report_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps({
        "result_csv": str(result_csv),
        "trades_csv": str(trades_csv),
        "report_md": str(report_md),
        "full_30_sorted": full_30.sort_values(["total_return_pct", "objective"], ascending=False).to_dict(orient="records"),
        "year_split": years_df.to_dict(orient="records"),
    }, ensure_ascii=False, indent=2, default=str), flush=True)


if __name__ == "__main__":
    main()
