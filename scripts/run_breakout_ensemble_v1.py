#!/usr/bin/env python3
"""Research-only breakout model ensemble frontier.

Signal: close[t]. Eligibility: data through t-1. Entry: open[t+1].
Exit: open[t+1+hold]. Entry failures remain cash without replacement.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from toss_alpha.research.breakout_ensemble import (  # noqa: E402
    freeze_topk,
    nonoverlap_signal_indices,
    traded_sides,
)

PANEL = ROOT / "reports/backtests/practical_universe_400_2022-01-01_2026-latest_ohlcv_panel.csv"
OUT_JSON = ROOT / "reports/backtests/breakout_ensemble_v1.json"
OUT_MD = ROOT / "reports/backtests/breakout_ensemble_v1.md"
BASE_COST = 0.0013
COSTS = (0.0013, 0.0031, 0.0050, 0.0075)
HOLDS = (5, 10)
TOPKS = (5, 10, 20)
MODEL_NAMES = (
    "momentum20_baseline",
    "donchian20",
    "donchian55",
    "bollinger_breakout",
    "volume_breakout",
    "ensemble_mean",
    "ensemble_median",
    "ensemble_consensus",
)


def rank_pct(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.rank(axis=1, pct=True) - 0.5


def metrics(returns: np.ndarray, hold_days: int) -> dict:
    r = np.asarray(returns, dtype=float)
    if not len(r):
        return {"episodes": 0, "cumulative_return": 0.0, "annual_return": 0.0,
                "sharpe": 0.0, "max_drawdown": 0.0, "win_rate": 0.0}
    wealth = np.cumprod(1.0 + np.clip(r, -0.999999, None))
    cumulative = float(wealth[-1] - 1.0)
    years = max(len(r) * hold_days / 252.0, 1 / 252)
    annual = float((max(wealth[-1], 1e-12) ** (1.0 / years)) - 1.0)
    std = float(np.std(r, ddof=1)) if len(r) > 1 else 0.0
    sharpe = float(np.mean(r) / std * math.sqrt(252.0 / hold_days)) if std > 0 else 0.0
    running_max = np.maximum.accumulate(wealth)
    mdd = float(np.min((wealth - running_max) / running_max))
    return {
        "episodes": int(len(r)),
        "cumulative_return": cumulative,
        "annual_return": annual,
        "sharpe": sharpe,
        "max_drawdown": mdd,
        "win_rate": float(np.mean(r > 0)),
    }


def build_episode_returns(open_: pd.DataFrame, signals: np.ndarray, hold_days: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    raw_open_ret = open_.pct_change(fill_method=None)
    extreme = (raw_open_ret > 0.30) | (raw_open_ret < -0.30)
    missing = raw_open_ret.isna()
    sanitized = raw_open_ret.clip(lower=-1.0, upper=0.30).fillna(0.0)
    episode_ret = np.zeros((len(signals), open_.shape[1]), dtype=np.float64)
    episode_extreme = np.zeros_like(episode_ret, dtype=np.int16)
    episode_missing = np.zeros_like(episode_ret, dtype=np.int16)
    values = sanitized.to_numpy()
    extreme_values = extreme.to_numpy(dtype=np.int16)
    missing_values = missing.to_numpy(dtype=np.int16)
    for row, signal in enumerate(signals):
        start = signal + 2
        stop = signal + hold_days + 2
        episode_ret[row] = np.prod(1.0 + values[start:stop], axis=0) - 1.0
        episode_extreme[row] = extreme_values[start:stop].sum(axis=0)
        episode_missing[row] = missing_values[start:stop].sum(axis=0)
    return episode_ret, episode_extreme, episode_missing


def evaluate(
    score: np.ndarray,
    rank_eligible: np.ndarray,
    execution_gate: np.ndarray,
    episode_ret: np.ndarray,
    episode_extreme: np.ndarray,
    episode_missing: np.ndarray,
    signals: np.ndarray,
    selected_signal_rows: np.ndarray,
    top_k: int,
    hold_days: int,
    per_side_cost: float,
) -> tuple[dict, np.ndarray, dict]:
    returns: list[float] = []
    positions: list[tuple[np.ndarray, np.ndarray]] = []
    selected_extreme = 0
    selected_missing = 0
    executed_slots = 0

    for signal_row in selected_signal_rows:
        signal = int(signals[signal_row])
        idx, valid = freeze_topk(
            score[signal], rank_eligible[signal], execution_gate[signal], top_k
        )
        gross = float(np.where(valid, episode_ret[signal_row, idx], 0.0).sum() / top_k)
        returns.append(gross)
        positions.append((idx, valid))
        executed_slots += int(valid.sum())
        selected_extreme += int(np.where(valid, episode_extreme[signal_row, idx], 0).sum())
        selected_missing += int(np.where(valid, episode_missing[signal_row, idx], 0).sum())

    r = np.asarray(returns, dtype=float)
    if len(r) and per_side_cost > 0:
        r[0] -= positions[0][1].sum() / top_k * per_side_cost
        for i in range(1, len(r)):
            sides = traded_sides(*positions[i - 1], *positions[i])
            r[i] -= sides / top_k * per_side_cost
        r[-1] -= positions[-1][1].sum() / top_k * per_side_cost
    r = np.maximum(r, -0.999999)
    quality = {
        "executed_slots": executed_slots,
        "cash_slots": int(len(selected_signal_rows) * top_k - executed_slots),
        "selected_extreme_open_transitions": selected_extreme,
        "selected_missing_open_transitions_marked_flat": selected_missing,
    }
    return metrics(r, hold_days), r, quality


def bootstrap_positive_rate(returns: np.ndarray, samples: int = 2000) -> float:
    if not len(returns):
        return 0.0
    rng = np.random.default_rng(20260721)
    idx = rng.integers(0, len(returns), size=(samples, len(returns)))
    terminal = np.prod(1.0 + returns[idx], axis=1) - 1.0
    return float(np.mean(terminal > 0))


def main() -> None:
    started = time.time()
    raw = pd.read_csv(PANEL, dtype={"code": str})
    raw["Date"] = pd.to_datetime(raw["Date"])
    duplicate_rows = int(raw.duplicated(["code", "Date"]).sum())
    invalid_codes = int((~raw["code"].str.fullmatch(r"\d{6}")).sum())
    if duplicate_rows or invalid_codes:
        raise ValueError(f"panel contract failed: duplicates={duplicate_rows}, invalid_codes={invalid_codes}")
    pivot = raw.pivot(index="Date", columns="code", values=["Open", "High", "Low", "Close", "Volume"])
    open_ = pivot["Open"].sort_index()
    high = pivot["High"].sort_index()
    low = pivot["Low"].sort_index()
    close = pivot["Close"].sort_index()
    volume = pivot["Volume"].sort_index()
    if not close.index.is_unique or not close.index.is_monotonic_increasing:
        raise ValueError("date index must be unique and sorted")

    prev_close = close.shift(1)
    true_range = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=0, keys=["hl", "hc", "lc"]
    ).groupby(level=1).max()
    atr14 = true_range.rolling(14).mean().shift(1)
    high20 = high.shift(1).rolling(20, min_periods=20).max()
    high55 = high.shift(1).rolling(55, min_periods=55).max()
    mean20 = close.shift(1).rolling(20, min_periods=20).mean()
    std20 = close.shift(1).rolling(20, min_periods=20).std()
    median_volume20 = volume.shift(1).rolling(20, min_periods=20).median()
    volume_ratio = volume / median_volume20.replace(0, np.nan)

    d20 = (close - high20) / atr14.replace(0, np.nan)
    d55 = (close - high55) / atr14.replace(0, np.nan)
    boll = (close - (mean20 + 2.0 * std20)) / atr14.replace(0, np.nan)
    vol_confirm = d20 + 0.25 * np.log(volume_ratio.clip(lower=0.25, upper=4.0))
    momentum20 = close / close.shift(20) - 1.0

    components = {"d20": d20, "d55": d55, "boll": boll, "volume": vol_confirm}
    ranks = {name: rank_pct(frame) for name, frame in components.items()}
    rank_stack = np.stack([ranks[name].to_numpy() for name in ("d20", "d55", "boll", "volume")], axis=2)
    mean_rank = np.nanmean(rank_stack, axis=2)
    median_rank = np.nanmedian(rank_stack, axis=2)
    breakout_flags = np.stack(
        [(d20 > 0).to_numpy(), (d55 > 0).to_numpy(), (boll > 0).to_numpy(),
         ((d20 > 0) & (volume_ratio >= 1.5)).to_numpy()], axis=2
    )
    consensus_count = breakout_flags.sum(axis=2)

    scores = {
        "momentum20_baseline": rank_pct(momentum20).to_numpy(),
        "donchian20": ranks["d20"].to_numpy(),
        "donchian55": ranks["d55"].to_numpy(),
        "bollinger_breakout": ranks["boll"].to_numpy(),
        "volume_breakout": ranks["volume"].to_numpy(),
        "ensemble_mean": mean_rank,
        "ensemble_median": median_rank,
        "ensemble_consensus": mean_rank + 0.10 * consensus_count,
    }
    model_gates = {
        "momentum20_baseline": (momentum20 > 0).to_numpy(),
        "donchian20": (d20 > 0).to_numpy(),
        "donchian55": (d55 > 0).to_numpy(),
        "bollinger_breakout": (boll > 0).to_numpy(),
        "volume_breakout": ((d20 > 0) & (volume_ratio >= 1.5)).to_numpy(),
        "ensemble_mean": consensus_count >= 2,
        "ensemble_median": consensus_count >= 2,
        "ensemble_consensus": consensus_count >= 3,
    }

    history = close.notna().cumsum().shift(1).fillna(0) >= 60
    turnover = close * volume
    liquidity = turnover.shift(1).rolling(20, min_periods=20).median().fillna(0) >= 500_000_000
    volume_ok = volume.shift(1).fillna(0) >= 10_000
    base_eligible = (history & liquidity & volume_ok).to_numpy()
    entry_open = open_.shift(-1)
    entry_gap = (entry_open - close) / close.replace(0, np.nan)
    execution_gate = (entry_open.notna() & (entry_open > 0) & ~(entry_gap >= 0.29)).to_numpy()

    all_rows = []
    details: dict[str, dict] = {}
    cache = {}
    for hold in HOLDS:
        valid_start = 60
        signals = nonoverlap_signal_indices(valid_start, len(close) - hold - 1, hold)
        episode_ret, episode_extreme, episode_missing = build_episode_returns(open_, signals, hold)
        entry_dates = close.index[signals + 1]
        exit_dates = close.index[signals + 1 + hold]
        train_rows = np.flatnonzero(exit_dates <= pd.Timestamp("2024-12-31"))
        test_rows = np.flatnonzero(entry_dates >= pd.Timestamp("2025-01-01"))
        cache[hold] = (signals, episode_ret, episode_extreme, episode_missing, train_rows, test_rows, entry_dates)

        for model in MODEL_NAMES:
            finite = np.isfinite(scores[model])
            rank_eligible = base_eligible & model_gates[model] & finite
            for top_k in TOPKS:
                config = f"{model}|h{hold}|k{top_k}"
                train_metrics, _, train_quality = evaluate(
                    scores[model], rank_eligible, execution_gate, episode_ret, episode_extreme,
                    episode_missing, signals, train_rows, top_k, hold, BASE_COST
                )
                test_metrics, test_returns, test_quality = evaluate(
                    scores[model], rank_eligible, execution_gate, episode_ret, episode_extreme,
                    episode_missing, signals, test_rows, top_k, hold, BASE_COST
                )
                stress = {}
                for cost in COSTS:
                    sm, _, _ = evaluate(
                        scores[model], rank_eligible, execution_gate, episode_ret, episode_extreme,
                        episode_missing, signals, test_rows, top_k, hold, cost
                    )
                    stress[f"{round(cost * 10000)}bps_per_side"] = sm
                yearly = {}
                for year in sorted(set(entry_dates[test_rows].year)):
                    year_rows = test_rows[entry_dates[test_rows].year == year]
                    ym, _, _ = evaluate(
                        scores[model], rank_eligible, execution_gate, episode_ret, episode_extreme,
                        episode_missing, signals, year_rows, top_k, hold, BASE_COST
                    )
                    yearly[str(year)] = ym
                row = {
                    "config": config, "model": model, "hold_days": hold, "top_k": top_k,
                    "train_sharpe": train_metrics["sharpe"], "train_cumulative": train_metrics["cumulative_return"],
                    "train_mdd": train_metrics["max_drawdown"], "test_sharpe": test_metrics["sharpe"],
                    "test_cumulative": test_metrics["cumulative_return"], "test_mdd": test_metrics["max_drawdown"],
                }
                all_rows.append(row)
                details[config] = {
                    "train": train_metrics, "test": test_metrics, "test_years": yearly,
                    "cost_stress_test": stress, "train_quality": train_quality,
                    "test_quality": test_quality,
                    "bootstrap_test_positive_rate": bootstrap_positive_rate(test_returns),
                }

    leaderboard = pd.DataFrame(all_rows).sort_values(["train_sharpe", "train_cumulative"], ascending=False)
    train_gate = leaderboard[
        (leaderboard.train_cumulative > 0) & (leaderboard.train_mdd >= -0.35)
    ]
    selection_pool = train_gate if len(train_gate) else leaderboard
    best_overall = str(selection_pool.iloc[0].config)
    ensemble_pool = selection_pool[selection_pool.model.str.startswith("ensemble_")]
    if not len(ensemble_pool):
        ensemble_pool = leaderboard[leaderboard.model.str.startswith("ensemble_")]
    best_ensemble = str(ensemble_pool.iloc[0].config)

    def promotion_checks(config: str) -> dict:
        d = details[config]
        checks = {
            "train_cumulative_positive": d["train"]["cumulative_return"] > 0,
            "train_sharpe_positive": d["train"]["sharpe"] > 0,
            "test_cumulative_positive": d["test"]["cumulative_return"] > 0,
            "test_mdd_at_least_minus_25pct": d["test"]["max_drawdown"] >= -0.25,
            "all_test_years_positive": all(v["cumulative_return"] > 0 for v in d["test_years"].values()),
            "31bps_test_positive": d["cost_stress_test"]["31bps_per_side"]["cumulative_return"] > 0,
            "bootstrap_positive_rate_at_least_90pct": d["bootstrap_test_positive_rate"] >= 0.90,
        }
        return {"checks": checks, "metrics_passed": all(checks.values())}

    with PANEL.open("rb") as handle:
        panel_hash = hashlib.sha256(handle.read()).hexdigest()
    report = {
        "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "research_only": True,
        "panel": str(PANEL),
        "panel_sha256": panel_hash,
        "panel_period": f"{close.index.min().date()} ~ {close.index.max().date()}",
        "panel_shape": [int(close.shape[0]), int(close.shape[1])],
        "data_contract": {
            "duplicate_code_date_rows": duplicate_rows,
            "invalid_code_rows": invalid_codes,
            "eligibility": "60 valid observations, previous-session volume>=10000, previous 20-session median turnover>=KRW500m",
            "signal": "factor score at close[t] against highs/bands ending t-1",
            "entry": "open[t+1]; failed/missing/limit-up slot remains cash without replacement",
            "exit": "open[t+1+hold_days] on non-overlapping episodes",
            "corporate_action_proxy": "each positive open-to-open transition capped at +30%; negative transitions uncapped; missing transition marked flat",
            "position_accounting": "net repeated names at episode boundary; charge actual valid entry/exit sides",
        },
        "models": list(MODEL_NAMES),
        "holds": list(HOLDS),
        "topks": list(TOPKS),
        "base_cost_per_side": BASE_COST,
        "selection": "highest Train Sharpe among Train cumulative>0 and Train MDD>=-35%; Test unused",
        "best_overall_train_selected": best_overall,
        "best_ensemble_train_selected": best_ensemble,
        "promotion": {
            "best_overall": promotion_checks(best_overall),
            "best_ensemble": promotion_checks(best_ensemble),
            "verdict": "BLOCKED_RESEARCH_ONLY",
            "blocking_reasons": [
                "current-400 panel is not a point-in-time universe",
                "corporate actions are capped proxies rather than ledger-adjusted prices",
                "missing/suspended exits use mark-flat instead of carry-to-next-tradable-session",
            ],
        },
        "leaderboard_train_order": leaderboard.to_dict(orient="records"),
        "details": details,
        "elapsed_seconds": round(time.time() - started, 3),
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    top = leaderboard.head(15).copy()
    lines = [
        "# Breakout Ensemble v1", "", f"- Best overall: `{best_overall}`",
        f"- Best ensemble: `{best_ensemble}`", "- Verdict: `BLOCKED_RESEARCH_ONLY`", "",
        "| Config | Train Sharpe | Train Cum | Train MDD | Test Sharpe | Test Cum | Test MDD |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in top.itertuples():
        lines.append(
            f"| {row.config} | {row.train_sharpe:.3f} | {row.train_cumulative:.1%} | {row.train_mdd:.1%} | "
            f"{row.test_sharpe:.3f} | {row.test_cumulative:.1%} | {row.test_mdd:.1%} |"
        )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({
        "best_overall": best_overall,
        "best_ensemble": best_ensemble,
        "best_overall_detail": details[best_overall],
        "best_ensemble_detail": details[best_ensemble],
        "verdict": report["promotion"]["verdict"],
        "json": str(OUT_JSON), "markdown": str(OUT_MD),
        "elapsed_seconds": report["elapsed_seconds"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
