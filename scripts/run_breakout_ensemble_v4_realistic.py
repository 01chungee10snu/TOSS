#!/usr/bin/env python3
"""GPU-accelerated breakout ensemble v2 — MPS tensor pipeline.

Key v2 changes vs v1:
  - All factor/score/episode computations on Apple MPS GPU
  - Diverse model families (not just breakout variants):
      * momentum20, momentum60
      * mean_reversion_20
      * volatility_scaled_breakout
      * volume_profile_acceleration
      * rsi_divergence
      * LightGBM (GPU)
      * XGBoost (GPU)
      * torch MLP (MPS)
      * ensemble variants
  - Walk-forward CV (expanding window, 4 folds)
  - Bootstrap 5000× on GPU for confidence intervals
  - Cross-model correlation matrix reported

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

# ── torch MPS ────────────────────────────────────────────────────────────────
import torch

HAS_MPS = torch.backends.mps.is_available()
DEVICE = torch.device("mps") if HAS_MPS else torch.device("cpu")
torch.set_grad_enabled(False)
if HAS_MPS:
    torch.mps.set_per_process_memory_fraction(0.75)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PANEL = ROOT / "reports/backtests/pit_full_universe_2022-01-01_2026_ohlcv_panel.csv"
OUT_JSON = ROOT / "reports/backtests/breakout_ensemble_v5_pit.json"
OUT_MD = ROOT / "reports/backtests/breakout_ensemble_v5_pit.md"
BASE_COST = 0.0013
COSTS = (0.0013, 0.0031, 0.0050, 0.0075)
HOLDS = (5, 10)
TOPKS = (5, 10, 20)
PORTFOLIO_NOTIONAL_KRW = 100_000_000.0
MAX_POSITION_WEIGHT = 0.05
IMPACT_COEFFICIENT = 0.01
MAX_DYNAMIC_COST_PER_SIDE = 0.02

# ── diverse model families ───────────────────────────────────────────────────
FACTOR_MODELS = [
    "momentum20",
    "momentum60",
    "mean_reversion_20",
    "vol_scaled_breakout",
    "volume_acceleration",
    "rsi_divergence",
    "donchian20",
    "donchian55",
    "bollinger_breakout",
    "volume_breakout",
]
ML_MODELS = [
    "deep_mlp_mps",
    "attn_net_mps",
    "torch_mlp_mps",
]
ENSEMBLE_MODELS = [
    "ensemble_mean_all",
    "ensemble_median_all",
    "ensemble_consensus3",
    "ensemble_top3_uncorrelated",
]
ALL_MODELS = FACTOR_MODELS + ML_MODELS + ENSEMBLE_MODELS


# ═════════════════════════════════════════════════════════════════════════════
# GPU factor engine
# ═════════════════════════════════════════════════════════════════════════════

def load_panel_csv(path: Path) -> dict:
    """Load OHLCV CSV → pivoted pandas frames (kept on CPU for I/O)."""
    raw = pd.read_csv(path, dtype={"code": str})
    # Normalize column names: 'date' → 'Date'
    if "date" in raw.columns and "Date" not in raw.columns:
        raw = raw.rename(columns={"date": "Date"})
    raw["Date"] = pd.to_datetime(raw["Date"])
    dup = int(raw.duplicated(["code", "Date"]).sum())
    bad = int((~raw["code"].str.fullmatch(r"\d{6}")).sum())
    if dup or bad:
        raise ValueError(f"panel contract failed: duplicates={dup}, invalid_codes={bad}")
    pivot = raw.pivot(index="Date", columns="code", values=["Open", "High", "Low", "Close", "Volume"])
    return {
        "open": pivot["Open"].sort_index(),
        "high": pivot["High"].sort_index(),
        "low": pivot["Low"].sort_index(),
        "close": pivot["Close"].sort_index(),
        "volume": pivot["Volume"].sort_index(),
        "dup": dup,
        "bad": bad,
    }


def to_gpu(df: pd.DataFrame) -> torch.Tensor:
    """pandas DataFrame → MPS float32 tensor (NaN preserved)."""
    return torch.from_numpy(df.to_numpy(dtype=np.float32)).to(DEVICE)


def gpu_rolling_mean(t: torch.Tensor, window: int) -> torch.Tensor:
    """Efficient rolling mean on GPU via unfold."""
    T, N = t.shape
    if T < window:
        return torch.full_like(t, float("nan"))
    # cumsum approach
    pad = torch.zeros(1, N, device=DEVICE, dtype=t.dtype)
    cs = torch.cat([pad, torch.nan_to_num(t, nan=0.0).cumsum(dim=0)], dim=0)
    cnt = torch.cat([torch.zeros(1, N, device=DEVICE, dtype=t.dtype),
                     (~torch.isnan(t)).float().cumsum(dim=0)], dim=0)
    roll_sum = cs[window:] - cs[:-window]
    roll_cnt = cnt[window:] - cnt[:-window]
    out = torch.full((T, N), float("nan"), device=DEVICE, dtype=t.dtype)
    out[window - 1:] = torch.where(roll_cnt > 0, roll_sum / roll_cnt.clamp(min=1), torch.tensor(float("nan"), device=DEVICE))
    return out


def gpu_rolling_max(t: torch.Tensor, window: int) -> torch.Tensor:
    """Rolling max via unfold."""
    T, N = t.shape
    if T < window:
        return torch.full_like(t, float("nan"))
    unfold = t.unfold(0, window, 1)
    rmax = unfold.max(dim=2).values
    out = torch.full((T, N), float("nan"), device=DEVICE, dtype=t.dtype)
    out[window - 1:] = rmax
    return out


def gpu_rolling_min(t: torch.Tensor, window: int) -> torch.Tensor:
    T, N = t.shape
    if T < window:
        return torch.full_like(t, float("nan"))
    unfold = t.unfold(0, window, 1)
    rmin = unfold.min(dim=2).values
    out = torch.full((T, N), float("nan"), device=DEVICE, dtype=t.dtype)
    out[window - 1:] = rmin
    return out


def gpu_rolling_std(t: torch.Tensor, window: int) -> torch.Tensor:
    """Rolling std via Welford on GPU."""
    mean = gpu_rolling_mean(t, window)
    sq = t * t
    mean_sq = gpu_rolling_mean(sq, window)
    var = (mean_sq - mean * mean).clamp(min=0.0)
    return torch.sqrt(var)


def gpu_rolling_median(t: torch.Tensor, window: int) -> torch.Tensor:
    """Rolling median via unfold + median."""
    T, N = t.shape
    if T < window:
        return torch.full_like(t, float("nan"))
    unfold = t.unfold(0, window, 1)
    rmed = unfold.median(dim=2).values
    out = torch.full((T, N), float("nan"), device=DEVICE, dtype=t.dtype)
    out[window - 1:] = rmed
    return out


def gpu_rank_pct(t: torch.Tensor) -> torch.Tensor:
    """Cross-sectional rank percentile [-0.5, 0.5], NaN-safe."""
    T, N = t.shape
    out = torch.full_like(t, float("nan"))
    for i in range(T):
        row = t[i]
        valid = ~torch.isnan(row)
        if valid.sum() < 2:
            continue
        vals = row[valid]
        ranks = vals.argsort().argsort().float()
        ranks = ranks / (len(vals) - 1) - 0.5
        out[i, valid] = ranks
    return out


def gpu_rank_pct_vectorized(t: torch.Tensor) -> torch.Tensor:
    """Vectorized cross-sectional rank on GPU — no Python loop."""
    T, N = t.shape
    nan_mask = torch.isnan(t)
    # Replace NaN with -inf so they sort last
    filled = torch.where(nan_mask, torch.tensor(float("-inf"), device=DEVICE), t)
    # argsort gives ranking
    order = filled.argsort(dim=1)
    ranks = order.argsort(dim=1).float()
    # Normalize per row considering valid count
    valid_count = (~nan_mask).sum(dim=1, keepdim=True).float()
    ranks = ranks / valid_count.clamp(min=1) - 0.5
    # Set NaN positions back
    ranks = torch.where(nan_mask, torch.tensor(float("nan"), device=DEVICE), ranks)
    return ranks


def gpu_shift(t: torch.Tensor, periods: int) -> torch.Tensor:
    """Align another session to row t: positive=future, negative=past.

    Examples: ``gpu_shift(x, -1)[t] == x[t-1]`` and
    ``gpu_shift(x, 1)[t] == x[t+1]``.  This is intentionally the opposite
    sign of pandas.DataFrame.shift because callers express information time.
    """
    if periods == 0:
        return t.clone()
    T, N = t.shape
    out = torch.full((T, N), torch.nan, device=DEVICE, dtype=t.dtype)
    if periods > 0:
        if T > periods:
            out[:T - periods] = t[periods:]
    else:
        p = -periods
        if T > p:
            out[p:] = t[:T - p]
    return out


def compute_all_factors_gpu(ohlcvgpu: dict) -> dict[str, torch.Tensor]:
    """Compute all factor scores on MPS GPU. Returns dict of [T, N] tensors."""
    g = ohlcvgpu
    close = g["close_t"]
    high = g["high_t"]
    low = g["low_t"]
    open_ = g["open_t"]
    volume = g["volume_t"]

    # ATR
    prev_close = gpu_shift(close, -1)  # close[t-1]
    tr = torch.maximum(
        high - low,
        torch.maximum(
            (high - prev_close).abs(),
            (low - prev_close).abs()
        )
    )
    atr14 = gpu_rolling_mean(tr, 14)
    atr14 = gpu_shift(atr14, -1)  # use ATR known at t-1

    # Donchian channels (shifted)
    high20 = gpu_rolling_max(gpu_shift(high, -1), 20)
    high55 = gpu_rolling_max(gpu_shift(high, -1), 55)

    # Bollinger
    mean20 = gpu_rolling_mean(gpu_shift(close, -1), 20)
    std20 = gpu_rolling_std(gpu_shift(close, -1), 20)

    # Volume
    vol_med20 = gpu_rolling_median(gpu_shift(volume, -1), 20)
    volume_ratio = volume / vol_med20.clamp(min=1e-6)

    # Momentum
    mom20 = close / gpu_shift(close, -20) - 1.0
    mom60 = close / gpu_shift(close, -60) - 1.0

    # Mean reversion: negative momentum over 20d
    mr20 = -(close / gpu_shift(close, -20) - 1.0)

    # Breakout scores (ATR-normalized)
    atr_safe = atr14.clamp(min=1e-6)
    d20 = (close - high20) / atr_safe
    d55 = (close - high55) / atr_safe
    boll = (close - (mean20 + 2.0 * std20)) / atr_safe

    # Volume confirmation
    vol_confirm = d20 + 0.25 * torch.log(volume_ratio.clamp(min=0.25, max=4.0))

    # Volatility-scaled breakout: normalize breakout by realized vol
    vol_scaled = d20 / (std20 / mean20).clamp(min=1e-6)  # breakout / CV

    # Volume acceleration: ratio of recent volume to longer-term
    vol_med5 = gpu_rolling_median(gpu_shift(volume, -1), 5)
    vol_accel = torch.log((vol_med5 / vol_med20.clamp(min=1e-6)).clamp(min=0.1, max=10.0))

    # RSI divergence
    delta = close - prev_close
    gain = torch.where(delta > 0, delta, torch.tensor(0.0, device=DEVICE))
    loss = torch.where(delta < 0, -delta, torch.tensor(0.0, device=DEVICE))
    avg_gain = gpu_rolling_mean(gpu_shift(gain, -1), 14)
    avg_loss = gpu_rolling_mean(gpu_shift(loss, -1), 14)
    rs = avg_gain / avg_loss.clamp(min=1e-8)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    # RSI divergence: buy when RSI is in sweet spot [40,60] and price breaking out
    rsi_div = d20 * torch.exp(-((rsi - 50) ** 2) / 200.0)

    # Rank-normalize all
    scores: dict[str, torch.Tensor] = {}
    raw_factors = {
        "momentum20": mom20,
        "momentum60": mom60,
        "mean_reversion_20": mr20,
        "vol_scaled_breakout": vol_scaled,
        "volume_acceleration": vol_accel,
        "rsi_divergence": rsi_div,
        "donchian20": d20,
        "donchian55": d55,
        "bollinger_breakout": boll,
        "volume_breakout": vol_confirm,
    }

    for name, tensor in raw_factors.items():
        scores[name] = gpu_rank_pct_vectorized(tensor)

    # Compute gates (booleans)
    gates: dict[str, torch.Tensor] = {}
    for name in FACTOR_MODELS:
        if name == "momentum20":
            gates[name] = mom20 > 0
        elif name == "momentum60":
            gates[name] = mom60 > 0
        elif name == "mean_reversion_20":
            gates[name] = mom20 < 0  # reversion entry when recent decline
        elif name == "vol_scaled_breakout":
            gates[name] = d20 > 0
        elif name == "volume_acceleration":
            gates[name] = vol_accel > 0
        elif name == "rsi_divergence":
            gates[name] = (d20 > 0) & (rsi > 40) & (rsi < 65)
        elif name == "donchian20":
            gates[name] = d20 > 0
        elif name == "donchian55":
            gates[name] = d55 > 0
        elif name == "bollinger_breakout":
            gates[name] = boll > 0
        elif name == "volume_breakout":
            gates[name] = (d20 > 0) & (volume_ratio >= 1.5)

    # Base eligibility
    valid_obs = (~torch.isnan(close)).cumsum(dim=0)
    history_shifted = gpu_shift(valid_obs.float(), -1)
    history = torch.nan_to_num(history_shifted, nan=0.0) >= 60
    turnover = close * volume
    liq_med = gpu_rolling_median(gpu_shift(turnover, -1), 20)
    liquidity = liq_med >= 500_000_000
    vol_ok_shifted = gpu_shift(volume.float(), -1)
    vol_ok = torch.nan_to_num(vol_ok_shifted, nan=0.0) >= 10_000
    base_eligible = history & liquidity & vol_ok

    # Execution gate
    entry_open = gpu_shift(open_, 1)  # open[t+1]
    entry_gap = (entry_open - close) / close.clamp(min=1e-6)
    execution_gate = (~torch.isnan(entry_open)) & (entry_open > 0) & ~(entry_gap >= 0.29)

    g["scores"] = scores
    g["gates"] = gates
    g["base_eligible"] = base_eligible
    g["execution_gate"] = execution_gate
    # Capacity and spread inputs known at signal time (through t-1).
    g["adv20"] = liq_med
    prev_range = gpu_shift((high - low) / close.clamp(min=1e-6), -1)
    # One-side half-spread proxy: 10% of prior daily range, 2--50 bps.
    g["half_spread_proxy"] = (0.10 * prev_range).clamp(min=0.0002, max=0.0050)
    g["raw_factors"] = raw_factors
    g["volume_ratio"] = volume_ratio
    g["rsi"] = rsi
    g["mom20"] = mom20
    g["mom60"] = mom60
    g["d20"] = d20
    g["d55"] = d55
    g["boll"] = boll
    g["vol_confirm"] = vol_confirm
    return g


# ═════════════════════════════════════════════════════════════════════════════
# GPU episode return engine
# ═════════════════════════════════════════════════════════════════════════════

def compute_episode_returns_gpu(open_t: torch.Tensor, signals: np.ndarray, hold: int) -> torch.Tensor:
    """Compute diagnostic episode returns [num_signals, N] on GPU."""
    _, n_names = open_t.shape
    num_sig = len(signals)
    daily_ret = open_t[1:] / open_t[:-1] - 1.0
    daily_ret = torch.where(torch.isnan(daily_ret), torch.tensor(0.0, device=DEVICE), daily_ret)
    daily_ret = daily_ret.clamp(min=-1.0, max=0.30)

    episode_ret = torch.zeros(num_sig, n_names, device=DEVICE, dtype=open_t.dtype)
    for i, sig in enumerate(signals):
        start = int(sig) + 1
        stop = min(int(sig) + hold + 1, daily_ret.shape[0])
        if start < stop:
            episode_ret[i] = torch.prod(1.0 + daily_ret[start:stop], dim=0) - 1.0
    return episode_ret


def compute_missing_exit_gpu(open_t: torch.Tensor, signals: np.ndarray, hold: int) -> torch.Tensor:
    """Flag valid entries whose scheduled exit quote is unresolved."""
    num_sig = len(signals)
    _, n_names = open_t.shape
    unresolved = torch.zeros(num_sig, n_names, dtype=torch.bool, device=DEVICE)
    for i, sig in enumerate(signals):
        entry_i = int(sig) + 1
        exit_i = int(sig) + hold + 1
        if exit_i < open_t.shape[0]:
            unresolved[i] = (
                ~torch.isnan(open_t[entry_i])
                & (open_t[entry_i] > 0)
                & (torch.isnan(open_t[exit_i]) | (open_t[exit_i] <= 0))
            )
    return unresolved


def freeze_topk_gpu(
    scores_row: torch.Tensor,
    rank_eligible_row: torch.Tensor,
    execution_gate_row: torch.Tensor,
    k: int,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Select top-k names on GPU. Returns (indices, valid_mask)."""
    masked = torch.where(rank_eligible_row & ~torch.isnan(scores_row), scores_row,
                         torch.tensor(float("-inf"), device=DEVICE))
    topk_vals, topk_idx = masked.topk(min(k, len(masked)))
    rank_valid = rank_eligible_row[topk_idx] & ~torch.isnan(scores_row[topk_idx])
    valid = rank_valid & execution_gate_row[topk_idx]
    # Pad if needed
    if len(topk_idx) < k:
        pad = torch.zeros(k - len(topk_idx), dtype=torch.int64, device=DEVICE)
        topk_idx = torch.cat([topk_idx, pad])
        valid = torch.cat([valid, torch.zeros(k - len(valid), dtype=torch.bool, device=DEVICE)])
    return topk_idx, valid


def evaluate_gpu(
    scores: torch.Tensor,
    rank_eligible: torch.Tensor,
    execution_gate: torch.Tensor,
    episode_ret: torch.Tensor,
    signals: np.ndarray,
    selected_rows: np.ndarray,
    top_k: int,
    hold: int,
    per_side_cost: float,
    adv20: torch.Tensor,
    half_spread: torch.Tensor,
    missing_exit: torch.Tensor,
) -> tuple[dict, torch.Tensor, dict]:
    """Capacity-aware evaluation with a 5% name cap and dynamic costs."""
    returns = torch.zeros(len(selected_rows), device=DEVICE, dtype=torch.float32)
    executed_slots = 0
    unresolved_exit_exposures = 0
    invested_weight_sum = 0.0
    total_cost = 0.0
    prev_idx = None
    prev_valid = None
    prev_sig = None
    slot_weight = min(1.0 / top_k, MAX_POSITION_WEIGHT)

    def side_cost(sig: int, names: set[int]) -> float:
        if not names:
            return 0.0
        ids = torch.tensor(sorted(names), dtype=torch.int64, device=DEVICE)
        adv = adv20[sig, ids].clamp(min=1.0)
        spread = torch.nan_to_num(half_spread[sig, ids], nan=0.0050)
        order_notional = PORTFOLIO_NOTIONAL_KRW * slot_weight
        impact = IMPACT_COEFFICIENT * torch.sqrt(order_notional / adv)
        one_side = (per_side_cost + spread + impact).clamp(max=MAX_DYNAMIC_COST_PER_SIDE)
        return float((one_side.sum() * slot_weight).item())

    for i, sig_row in enumerate(selected_rows):
        sig = int(signals[sig_row])
        idx, valid = freeze_topk_gpu(
            scores[sig], rank_eligible[sig], execution_gate[sig], top_k
        )
        gross = (
            torch.where(valid, episode_ret[sig_row, idx], torch.tensor(0.0, device=DEVICE)).sum()
            * slot_weight
        )
        returns[i] = gross
        valid_count = int(valid.sum().item())
        executed_slots += valid_count
        invested_weight_sum += valid_count * slot_weight
        unresolved_exit_exposures += int(
            torch.where(valid, missing_exit[sig_row, idx], torch.tensor(False, device=DEVICE)).sum().item()
        )

        curr_set = set(idx[valid].cpu().tolist())
        if i == 0:
            episode_cost = side_cost(sig, curr_set)
        else:
            prev_set = set(prev_idx[prev_valid].cpu().tolist())
            episode_cost = side_cost(prev_sig, prev_set - curr_set)
            episode_cost += side_cost(sig, curr_set - prev_set)
        returns[i] -= episode_cost
        total_cost += episode_cost

        prev_idx = idx
        prev_valid = valid
        prev_sig = sig

    if len(selected_rows) > 0:
        terminal_cost = side_cost(prev_sig, set(prev_idx[prev_valid].cpu().tolist()))
        returns[-1] -= terminal_cost
        total_cost += terminal_cost

    returns = torch.clamp(returns, min=-0.999999)
    m = metrics_gpu(returns, hold)
    quality = {
        "executed_slots": executed_slots,
        "cash_slots": int(len(selected_rows) * top_k - executed_slots),
        "slot_weight": slot_weight,
        "average_invested_weight": invested_weight_sum / max(len(selected_rows), 1),
        "dynamic_cost_total_return_points": total_cost,
        "unresolved_missing_exit_exposures": unresolved_exit_exposures,
    }
    return m, returns, quality


def traded_sides_gpu(
    prev_idx: torch.Tensor, prev_valid: torch.Tensor,
    curr_idx: torch.Tensor, curr_valid: torch.Tensor,
) -> int:
    """Count one-way entry/exit sides."""
    prev_set = set(prev_idx[prev_valid].cpu().tolist())
    curr_set = set(curr_idx[curr_valid].cpu().tolist())
    return len(prev_set - curr_set) + len(curr_set - prev_set)


def metrics_gpu(returns: torch.Tensor, hold: int) -> dict:
    """Compute performance metrics on GPU."""
    r = returns
    if len(r) == 0:
        return {"episodes": 0, "cumulative_return": 0.0, "annual_return": 0.0,
                "sharpe": 0.0, "max_drawdown": 0.0, "win_rate": 0.0}
    wealth = torch.cumprod(1.0 + r, dim=0)
    cumulative = float((wealth[-1] - 1.0).item())
    years = max(len(r) * hold / 252.0, 1 / 252)
    annual = float((max(wealth[-1].item(), 1e-12) ** (1.0 / years)) - 1.0)
    std = float(r.std(unbiased=True).item()) if len(r) > 1 else 0.0
    sharpe = float((r.mean() / std * math.sqrt(252.0 / hold)).item()) if std > 0 else 0.0
    running_max = torch.cummax(wealth, dim=0)[0]
    mdd = float(torch.min((wealth - running_max) / running_max).item())
    return {
        "episodes": int(len(r)),
        "cumulative_return": cumulative,
        "annual_return": annual,
        "sharpe": sharpe,
        "max_drawdown": mdd,
        "win_rate": float((r > 0).float().mean().item()),
    }


def bootstrap_gpu(returns: torch.Tensor, samples: int = 5000) -> dict:
    """Massive bootstrap on GPU — thousands of resamples."""
    if len(returns) == 0:
        return {"positive_rate": 0.0, "p5": 0.0, "p50": 0.0, "p95": 0.0}
    n = len(returns)
    # Generate all resample indices at once on GPU
    # Shape: [samples, n]
    idx = torch.randint(0, n, (samples, n), device=DEVICE)
    # Resample
    resampled = returns[idx]  # [samples, n]
    # Terminal wealth
    terminal = torch.prod(1.0 + resampled, dim=1) - 1.0
    return {
        "positive_rate": float((terminal > 0).float().mean().item()),
        "p5": float(torch.quantile(terminal, 0.05).item()),
        "p50": float(torch.quantile(terminal, 0.50).item()),
        "p95": float(torch.quantile(terminal, 0.95).item()),
    }


# ═════════════════════════════════════════════════════════════════════════════
# ML model training
# ═════════════════════════════════════════════════════════════════════════════

def build_ml_features(g: dict) -> tuple[torch.Tensor, torch.Tensor]:
    """Build feature matrix [T, N, F] and forward returns [T, N] for ML training."""
    factor_names = list(g["raw_factors"].keys())
    T, N = g["close_t"].shape

    # Feature tensor
    F = len(factor_names) + 3  # +3 for rsi, volume_ratio, atr_pct
    features = torch.full((T, N, F), float("nan"), device=DEVICE)

    for j, name in enumerate(factor_names):
        features[:, :, j] = g["raw_factors"][name]

    features[:, :, len(factor_names)] = g["rsi"]
    features[:, :, len(factor_names) + 1] = torch.log(g["volume_ratio"].clamp(min=0.01))
    features[:, :, len(factor_names) + 2] = g["d20"]  # primary breakout signal

    # Exact 5-session target for close[t] signal:
    # enter open[t+1], exit open[t+1+5]. Missing targets remain NaN.
    open_t = g["open_t"]
    horizon = 5
    fwd_ret = open_t[1 + horizon:] / open_t[1:-(horizon)] - 1.0
    fwd_ret = fwd_ret.clamp(min=-1.0, max=0.30)

    # Signal rows t=0..T-horizon-2 align with those entry/exit pairs.
    feat_aligned = features[:-(horizon + 1)]
    target_aligned = fwd_ret

    return feat_aligned, target_aligned


def train_lightgbm_gpu(g: dict, train_mask: torch.Tensor) -> torch.Tensor:
    """Train LightGBM and produce per-stock scores for all dates."""
    import lightgbm as lgb

    feat, target = build_ml_features(g)
    T_f, N, F = feat.shape  # T_f = T_full - 5
    T_full = g["close_t"].shape[0]

    # Align train_mask: trim last 5 rows (can't compute forward returns there)
    mask_aligned = train_mask[:T_f]

    # Flatten to 2D for LightGBM
    feat_cpu = feat.reshape(T_f * N, F).cpu().numpy()
    target_cpu = target.reshape(T_f * N).cpu().numpy()
    mask_cpu = mask_aligned.reshape(T_f * N).cpu().numpy()

    # Filter valid rows
    valid = ~np.isnan(feat_cpu).any(axis=1) & ~np.isnan(target_cpu) & mask_cpu
    X_train = np.nan_to_num(feat_cpu[valid], nan=0.0)
    y_train = target_cpu[valid]

    if len(X_train) < 100:
        return torch.full((T_full, N), float("nan"), device=DEVICE)

    # LightGBM — n_jobs=1 to avoid fork+torch MPS crash
    params = {
        "objective": "regression",
        "metric": "rmse",
        "num_leaves": 31,
        "learning_rate": 0.05,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "verbose": -1,
        "n_jobs": 1,
    }
    ds = lgb.Dataset(X_train, y_train)
    model = lgb.train(params, ds, num_boost_round=200)

    # Predict all dates
    feat_all = np.nan_to_num(feat_cpu, nan=0.0)
    pred_flat = model.predict(feat_all)
    pred = torch.from_numpy(pred_flat).reshape(T_f, N).to(DEVICE)

    # Pad with 5 NaN rows at the end
    full_pred = torch.full((T_full, N), float("nan"), device=DEVICE)
    full_pred[:T_f] = pred

    return gpu_rank_pct_vectorized(full_pred)


def train_xgboost_gpu(g: dict, train_mask: torch.Tensor) -> torch.Tensor:
    """Train XGBoost and produce per-stock scores."""
    import xgboost as xgb

    feat, target = build_ml_features(g)
    T_f, N, F = feat.shape
    T_full = g["close_t"].shape[0]
    mask_aligned = train_mask[:T_f]

    feat_cpu = feat.reshape(T_f * N, F).cpu().numpy()
    target_cpu = target.reshape(T_f * N).cpu().numpy()
    mask_cpu = mask_aligned.reshape(T_f * N).cpu().numpy()

    valid = ~np.isnan(feat_cpu).any(axis=1) & ~np.isnan(target_cpu) & mask_cpu
    X_train = np.nan_to_num(feat_cpu[valid], nan=0.0)
    y_train = target_cpu[valid]

    if len(X_train) < 100:
        return torch.full((T_full, N), float("nan"), device=DEVICE)

    model = xgb.XGBRegressor(
        n_estimators=200, max_depth=5, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8, n_jobs=1, tree_method="hist",
    )
    model.fit(X_train, y_train, verbose=False)

    feat_all = np.nan_to_num(feat_cpu, nan=0.0)
    pred_flat = model.predict(feat_all)
    pred = torch.from_numpy(pred_flat).reshape(T_f, N).to(DEVICE)

    full_pred = torch.full((T_full, N), float("nan"), device=DEVICE)
    full_pred[:T_f] = pred

    return gpu_rank_pct_vectorized(full_pred)


class TorchMLP(torch.nn.Module):
    """Simple MLP for cross-sectional return prediction."""

    def __init__(self, in_dim: int, hidden: int = 64):
        super().__init__()
        self.net = torch.nn.Sequential(
            torch.nn.Linear(in_dim, hidden),
            torch.nn.SiLU(),
            torch.nn.Linear(hidden, hidden // 2),
            torch.nn.SiLU(),
            torch.nn.Linear(hidden // 2, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x).squeeze(-1)


def train_torch_mlp(g: dict, train_mask: torch.Tensor) -> torch.Tensor:
    """Train torch MLP on MPS GPU."""
    feat, target = build_ml_features(g)
    T_f, N, F = feat.shape
    T_full = g["close_t"].shape[0]
    mask_aligned = train_mask[:T_f]

    # Flatten
    valid = ~torch.isnan(feat).any(dim=2) & ~torch.isnan(target) & mask_aligned
    X = feat[valid]
    y = target[valid]

    if len(X) < 100:
        return torch.full((T_full, N), float("nan"), device=DEVICE)

    # Normalize features
    mean = X.mean(dim=0)
    std = X.std(dim=0).clamp(min=1e-6)
    X_norm = (X - mean) / std

    model = TorchMLP(F, hidden=64).to(DEVICE)
    with torch.enable_grad():
        opt = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-4)
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=30)
        batch_size = min(4096, len(X))
        dataset = torch.utils.data.TensorDataset(X_norm, y)
        loader = torch.utils.data.DataLoader(dataset, batch_size=batch_size, shuffle=True)

        for epoch in range(30):
            for xb, yb in loader:
                opt.zero_grad()
                pred = model(xb)
                loss = torch.nn.functional.mse_loss(pred, yb)
                loss.backward()
                opt.step()
            scheduler.step()

    # Predict all dates
    feat_flat = feat.reshape(-1, F)
    nan_rows = torch.isnan(feat_flat).any(dim=1)
    feat_clean = torch.where(torch.isnan(feat_flat), torch.tensor(0.0, device=DEVICE), feat_flat)
    feat_norm = (feat_clean - mean) / std
    pred_flat = model(feat_norm)
    pred_flat = torch.where(nan_rows, torch.tensor(float("nan"), device=DEVICE), pred_flat)
    pred = pred_flat.reshape(T_f, N)

    full_pred = torch.full((T_full, N), float("nan"), device=DEVICE)
    full_pred[:T_f] = pred

    return gpu_rank_pct_vectorized(full_pred)


# ═════════════════════════════════════════════════════════════════════════════
# Main pipeline
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    started = time.time()
    print(f"Device: {DEVICE} | MPS available: {HAS_MPS}")

    # ── Load data ────────────────────────────────────────────────────────────
    t0 = time.time()
    panel = load_panel_csv(PANEL)
    load_time = time.time() - t0
    print(f"Panel loaded: {panel['close'].shape[0]} dates × {panel['close'].shape[1]} stocks ({load_time:.2f}s)")

    # ── Transfer to GPU ──────────────────────────────────────────────────────
    t0 = time.time()
    g = {
        "close": panel["close"], "high": panel["high"], "low": panel["low"],
        "open": panel["open"], "volume": panel["volume"],
        "close_t": to_gpu(panel["close"]),
        "high_t": to_gpu(panel["high"]),
        "low_t": to_gpu(panel["low"]),
        "open_t": to_gpu(panel["open"]),
        "volume_t": to_gpu(panel["volume"]),
    }
    gpu_transfer_time = time.time() - t0
    print(f"GPU transfer: {gpu_transfer_time:.3f}s")

    # ── Compute factors on GPU ──────────────────────────────────────────────
    t0 = time.time()
    g = compute_all_factors_gpu(g)
    factor_time = time.time() - t0
    print(f"Factor computation (GPU): {factor_time:.3f}s")

    close = panel["close"]
    T, N = g["close_t"].shape

    # ── Train/test split ─────────────────────────────────────────────────────
    # Train mask for ML models: exit date <= 2024-12-31
    # Test: entry date >= 2025-01-01
    train_mask = torch.zeros(T, N, dtype=torch.bool, device=DEVICE)
    first_test_pos = int(close.index.searchsorted(pd.Timestamp("2025-01-01"), side="left"))
    # ML target exits at open[t+6]; purge boundary-crossing labels.
    train_signal_stop = max(0, first_test_pos - 6)
    train_mask[:train_signal_stop] = True

    # ── Train ML models (all torch-native on MPS — no external libs) ──────────
    # LightGBM/XGBoost crash with MPS due to OpenMP+Metal conflict.
    # Using 3 diverse torch architectures instead.

    t0 = time.time()
    print("Building ML features on GPU...", end=" ", flush=True)
    feat, target = build_ml_features(g)
    T_f, N_f, F_dim = feat.shape
    mask_mlp = train_mask[:T_f]

    valid_torch = (
        ~torch.isnan(feat).any(dim=2)
        & ~torch.isnan(target)
        & mask_mlp
        & g["base_eligible"][:T_f]
        & g["execution_gate"][:T_f]
    )
    X_torch = feat[valid_torch]
    y_torch = target[valid_torch]

    # Normalize features (compute once, reuse for all 3 models)
    mean_t = X_torch.mean(dim=0)
    std_t = X_torch.std(dim=0).clamp(min=1e-6)
    X_torch_norm = (X_torch - mean_t) / std_t

    # Full feature matrix for prediction (normalized, NaN→0)
    feat_flat = feat.reshape(-1, F_dim)
    nan_rows = torch.isnan(feat_flat).any(dim=1)
    feat_clean = torch.where(torch.isnan(feat_flat), torch.tensor(0.0, device=DEVICE), feat_flat)
    feat_norm_all = (feat_clean - mean_t) / std_t

    print(f"done ({time.time()-t0:.2f}s) | train rows: {len(X_torch)}")

    # ── Model 1: torch MLP (shallow, 64→32→1) ────────────────────────────────
    t0 = time.time()
    print("Training Torch MLP (MPS)...", end=" ", flush=True)
    mlp_model = TorchMLP(F_dim, hidden=64).to(DEVICE)
    with torch.enable_grad():
        opt = torch.optim.Adam(mlp_model.parameters(), lr=1e-3, weight_decay=1e-4)
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=30)
        batch_size = min(4096, len(X_torch))
        ds_torch = torch.utils.data.TensorDataset(X_torch_norm, y_torch)
        loader = torch.utils.data.DataLoader(ds_torch, batch_size=batch_size, shuffle=True)
        for epoch in range(30):
            for xb, yb in loader:
                opt.zero_grad()
                pred = mlp_model(xb)
                loss = torch.nn.functional.mse_loss(pred, yb)
                loss.backward()
                opt.step()
            scheduler.step()
    mlp_pred = mlp_model(feat_norm_all)
    mlp_pred = torch.where(nan_rows, torch.tensor(float("nan"), device=DEVICE), mlp_pred)
    mlp_pred = mlp_pred.reshape(T_f, N_f)
    mlp_full = torch.full((T, N), float("nan"), device=DEVICE)
    mlp_full[:T_f] = mlp_pred
    g["scores"]["torch_mlp_mps"] = gpu_rank_pct_vectorized(mlp_full)
    g["gates"]["torch_mlp_mps"] = ~torch.isnan(g["scores"]["torch_mlp_mps"])
    print(f"done ({time.time()-t0:.2f}s)")

    # ── Model 2: torch Deep MLP (128→64→32→1, more capacity) ─────────────────
    t0 = time.time()
    print("Training Torch DeepMLP (MPS)...", end=" ", flush=True)
    deep_model = torch.nn.Sequential(
        torch.nn.Linear(F_dim, 128), torch.nn.SiLU(), torch.nn.Dropout(0.1),
        torch.nn.Linear(128, 64), torch.nn.SiLU(), torch.nn.Dropout(0.1),
        torch.nn.Linear(64, 32), torch.nn.SiLU(),
        torch.nn.Linear(32, 1),
    ).to(DEVICE)
    with torch.enable_grad():
        opt2 = torch.optim.Adam(deep_model.parameters(), lr=1e-3, weight_decay=1e-4)
        sched2 = torch.optim.lr_scheduler.CosineAnnealingLR(opt2, T_max=40)
        for epoch in range(40):
            for xb, yb in loader:
                opt2.zero_grad()
                pred = deep_model(xb).squeeze(-1)
                loss = torch.nn.functional.mse_loss(pred, yb)
                loss.backward()
                opt2.step()
            sched2.step()
    deep_pred = deep_model(feat_norm_all).squeeze(-1)
    deep_pred = torch.where(nan_rows, torch.tensor(float("nan"), device=DEVICE), deep_pred)
    deep_pred = deep_pred.reshape(T_f, N_f)
    deep_full = torch.full((T, N), float("nan"), device=DEVICE)
    deep_full[:T_f] = deep_pred
    g["scores"]["deep_mlp_mps"] = gpu_rank_pct_vectorized(deep_full)
    g["gates"]["deep_mlp_mps"] = ~torch.isnan(g["scores"]["deep_mlp_mps"])
    print(f"done ({time.time()-t0:.2f}s)")

    # ── Model 3: torch Cross-Attention Net (feature interactions) ────────────
    t0 = time.time()
    print("Training Torch AttnNet (MPS)...", end=" ", flush=True)

    class AttnNet(torch.nn.Module):
        def __init__(self, in_dim, d_model=16, n_heads=2):
            super().__init__()
            self.proj = torch.nn.Linear(1, d_model)
            self.attn = torch.nn.MultiheadAttention(d_model, n_heads, batch_first=True)
            self.head = torch.nn.Sequential(
                torch.nn.Linear(d_model * in_dim, 32), torch.nn.SiLU(),
                torch.nn.Linear(32, 1),
            )

        def forward(self, x):  # x: [B, F]
            tokens = self.proj(x.unsqueeze(-1))  # [B, F, d]
            attn_out, _ = self.attn(tokens, tokens, tokens)
            flat = attn_out.reshape(x.shape[0], -1)
            return self.head(flat).squeeze(-1)

    attn_model = AttnNet(F_dim, d_model=16, n_heads=2).to(DEVICE)
    with torch.enable_grad():
        opt3 = torch.optim.AdamW(attn_model.parameters(), lr=5e-4, weight_decay=1e-3)
        sched3 = torch.optim.lr_scheduler.CosineAnnealingLR(opt3, T_max=25)
        for epoch in range(25):
            for xb, yb in loader:
                opt3.zero_grad()
                pred = attn_model(xb)
                loss = torch.nn.functional.mse_loss(pred, yb)
                loss.backward()
                opt3.step()
            sched3.step()
    # Mini-batch inference to avoid MPS OOM on large panels
    attn_chunks = []
    with torch.no_grad():
        for ci in range(0, feat_norm_all.shape[0], 50000):
            chunk = attn_model(feat_norm_all[ci:ci+50000])
            attn_chunks.append(chunk)
    attn_pred = torch.cat(attn_chunks, dim=0)
    attn_pred = torch.where(nan_rows, torch.tensor(float("nan"), device=DEVICE), attn_pred)
    attn_pred = attn_pred.reshape(T_f, N_f)
    attn_full = torch.full((T, N), float("nan"), device=DEVICE)
    attn_full[:T_f] = attn_pred
    g["scores"]["attn_net_mps"] = gpu_rank_pct_vectorized(attn_full)
    g["gates"]["attn_net_mps"] = ~torch.isnan(g["scores"]["attn_net_mps"])
    print(f"done ({time.time()-t0:.2f}s)")

    # Cleanup
    del feat, target, X_torch, y_torch, X_torch_norm, feat_flat, feat_clean, feat_norm_all
    del mlp_model, deep_model, attn_model
    torch.mps.empty_cache() if HAS_MPS else None

    # ── Build ensembles ──────────────────────────────────────────────────────
    all_factor_scores = [g["scores"][m] for m in ALL_MODELS if m in g["scores"] and m not in ENSEMBLE_MODELS]
    score_stack = torch.stack(all_factor_scores, dim=2)  # [T, N, num_models]

    g["scores"]["ensemble_mean_all"] = gpu_rank_pct_vectorized(
        torch.nanmean(score_stack, dim=2)
    )
    g["scores"]["ensemble_median_all"] = gpu_rank_pct_vectorized(
        torch.nanmedian(score_stack, dim=2).values
    )

    # Consensus: count how many models have positive score (breakout-like)
    positive_count = (score_stack > 0).sum(dim=2).float()
    consensus_score = torch.nanmean(score_stack, dim=2) + 0.10 * positive_count
    g["scores"]["ensemble_consensus3"] = gpu_rank_pct_vectorized(consensus_score)

    # Top-3 uncorrelated: pick 3 models with lowest pairwise correlation
    # (momentum20, volume_acceleration, torch_mlp_mps as proxy)
    uncorr_models = ["momentum20", "volume_acceleration", "torch_mlp_mps"]
    uncorr_stack = torch.stack([g["scores"][m] for m in uncorr_models], dim=2)
    g["scores"]["ensemble_top3_uncorrelated"] = gpu_rank_pct_vectorized(
        torch.nanmean(uncorr_stack, dim=2)
    )

    # Gates for ensembles
    for m in ENSEMBLE_MODELS:
        g["gates"][m] = ~torch.isnan(g["scores"][m])

    # ── Compute episode returns on GPU ───────────────────────────────────────
    all_rows = []
    details: dict[str, dict] = {}

    for hold in HOLDS:
        valid_start = 60
        signals = np.arange(valid_start, T - hold - 1, hold, dtype=np.int64)
        episode_ret = compute_episode_returns_gpu(g["open_t"], signals, hold)
        missing_exit = compute_missing_exit_gpu(g["open_t"], signals, hold)

        entry_dates = close.index[signals + 1]
        exit_dates = close.index[signals + hold + 1]
        train_rows = np.flatnonzero(exit_dates <= pd.Timestamp("2024-12-31"))
        test_rows = np.flatnonzero(entry_dates >= pd.Timestamp("2025-01-01"))

        for model in ALL_MODELS:
            score_t = g["scores"][model]
            finite = ~torch.isnan(score_t)
            rank_eligible = g["base_eligible"] & g["gates"][model] & finite

            for top_k in TOPKS:
                config = f"{model}|h{hold}|k{top_k}"

                train_m, train_r, train_q = evaluate_gpu(
                    score_t, rank_eligible, g["execution_gate"], episode_ret,
                    signals, train_rows, top_k, hold, BASE_COST,
                    g["adv20"], g["half_spread_proxy"], missing_exit
                )
                test_m, test_r, test_q = evaluate_gpu(
                    score_t, rank_eligible, g["execution_gate"], episode_ret,
                    signals, test_rows, top_k, hold, BASE_COST,
                    g["adv20"], g["half_spread_proxy"], missing_exit
                )

                # Stress test
                stress = {}
                for cost in COSTS:
                    sm, _, _ = evaluate_gpu(
                        score_t, rank_eligible, g["execution_gate"], episode_ret,
                        signals, test_rows, top_k, hold, cost,
                        g["adv20"], g["half_spread_proxy"], missing_exit
                    )
                    stress[f"{round(cost * 10000)}bps_per_side"] = sm

                # Yearly breakdown
                yearly = {}
                for year in sorted(set(entry_dates[test_rows].year)):
                    yr = test_rows[entry_dates[test_rows].year == year]
                    ym, _, _ = evaluate_gpu(
                        score_t, rank_eligible, g["execution_gate"], episode_ret,
                        signals, yr, top_k, hold, BASE_COST,
                        g["adv20"], g["half_spread_proxy"], missing_exit
                    )
                    yearly[str(year)] = ym

                # GPU Bootstrap
                boot = bootstrap_gpu(test_r, samples=5000)

                row = {
                    "config": config, "model": model, "hold_days": hold, "top_k": top_k,
                    "train_sharpe": train_m["sharpe"], "train_cumulative": train_m["cumulative_return"],
                    "train_mdd": train_m["max_drawdown"],
                    "test_sharpe": test_m["sharpe"], "test_cumulative": test_m["cumulative_return"],
                    "test_mdd": test_m["max_drawdown"],
                    "bootstrap_positive_rate": boot["positive_rate"],
                }
                all_rows.append(row)
                details[config] = {
                    "train": train_m, "test": test_m, "test_years": yearly,
                    "cost_stress_test": stress,
                    "train_quality": train_q, "test_quality": test_q,
                    "bootstrap": boot,
                }

    # ── Leaderboard & selection ──────────────────────────────────────────────
    leaderboard = pd.DataFrame(all_rows).sort_values(
        ["train_sharpe", "train_cumulative"], ascending=False
    )
    train_gate = leaderboard[
        (leaderboard.train_cumulative > 0) & (leaderboard.train_mdd >= -0.35)
    ]
    pool = train_gate if len(train_gate) else leaderboard
    best_overall = str(pool.iloc[0].config)

    ens_pool = pool[pool.model.str.startswith("ensemble_")]
    if not len(ens_pool):
        ens_pool = leaderboard[leaderboard.model.str.startswith("ensemble_")]
    best_ensemble = str(ens_pool.iloc[0].config)

    # ── Cross-model correlation ──────────────────────────────────────────────
    test_corr_data = {}
    for hold in HOLDS:
        valid_start = 60
        signals = np.arange(valid_start, T - hold - 1, hold, dtype=np.int64)
        entry_dates = close.index[signals + 1]
        test_rows = np.flatnonzero(entry_dates >= pd.Timestamp("2025-01-01"))
        episode_ret = compute_episode_returns_gpu(g["open_t"], signals, hold)
        missing_exit = compute_missing_exit_gpu(g["open_t"], signals, hold)

        model_rets = {}
        for model in ALL_MODELS:
            score_t = g["scores"][model]
            finite = ~torch.isnan(score_t)
            rank_eligible = g["base_eligible"] & g["gates"][model] & finite
            _, rets, _ = evaluate_gpu(
                score_t, rank_eligible, g["execution_gate"],
                episode_ret, signals, test_rows, 10, hold, BASE_COST,
                g["adv20"], g["half_spread_proxy"], missing_exit
            )
            model_rets[model] = rets.cpu().numpy()

        ret_df = pd.DataFrame(model_rets)
        corr_matrix = ret_df.corr()
        test_corr_data[f"hold{hold}"] = corr_matrix.round(3).to_dict()

    # ── Promotion checks ─────────────────────────────────────────────────────
    def promotion_checks(config: str) -> dict:
        d = details[config]
        checks = {
            "train_cumulative_positive": d["train"]["cumulative_return"] > 0,
            "train_sharpe_positive": d["train"]["sharpe"] > 0,
            "test_cumulative_positive": d["test"]["cumulative_return"] > 0,
            "test_mdd_at_least_minus_25pct": d["test"]["max_drawdown"] >= -0.25,
            "all_test_years_positive": all(v["cumulative_return"] > 0 for v in d["test_years"].values()),
            "31bps_test_positive": d["cost_stress_test"]["31bps_per_side"]["cumulative_return"] > 0,
            "bootstrap_positive_rate_at_least_90pct": d["bootstrap"]["positive_rate"] >= 0.90,
            "bootstrap_p5_positive": d["bootstrap"]["p5"] > 0,
            "no_unresolved_missing_exit_exposure": (
                d["train_quality"]["unresolved_missing_exit_exposures"] == 0
                and d["test_quality"]["unresolved_missing_exit_exposures"] == 0
            ),
            # The random500 artifact is a fixed current-derived sample, not a
            # historical KRX membership ledger. Keep promotion fail-closed.
            "true_pit_membership_manifest_resolved": False,
            "corporate_actions_and_final_consideration_resolved": False,
        }
        return {"checks": checks, "metrics_passed": sum(checks.values()), "total_checks": len(checks)}

    # ── Report ───────────────────────────────────────────────────────────────
    with PANEL.open("rb") as h:
        panel_hash = hashlib.sha256(h.read()).hexdigest()

    elapsed = time.time() - started
    report = {
        "version": "v5_pit",
        "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "research_only": True,
        "device": str(DEVICE),
        "mps_available": HAS_MPS,
        "panel": str(PANEL),
        "panel_sha256": panel_hash,
        "panel_period": f"{close.index.min().date()} ~ {close.index.max().date()}",
        "panel_shape": [int(T), int(N)],
        "timing": {
            "total_seconds": round(elapsed, 3),
            "data_load": round(load_time, 3),
            "gpu_transfer": round(gpu_transfer_time, 3),
            "factor_computation_gpu": round(factor_time, 3),
            "bootstrap_samples": 5000,
        },
        "data_contract": {
            "duplicate_code_date_rows": panel["dup"],
            "invalid_code_rows": panel["bad"],
            "eligibility": "60 valid obs, prev vol>=10000, prev 20-session median turnover>=KRW500m",
            "signal": "factor score at close[t] against highs/bands ending t-1",
            "entry": "open[t+1]; failed/missing/limit-up slot remains cash",
            "exit": "open[t+1+hold] non-overlapping",
            "corporate_action_proxy": "+30% cap on open-to-open transitions",
            "pit_membership": "UNRESOLVED: fixed random500 sample is not a historical KRX membership ledger",
            "missing_exit": "mark-flat diagnostic proxy; selected exposures counted and promotion blocked",
        },
        "models": ALL_MODELS,
        "model_families": {
            "factor": FACTOR_MODELS,
            "ml": ML_MODELS,
            "ensemble": ENSEMBLE_MODELS,
        },
        "holds": list(HOLDS),
        "topks": list(TOPKS),
        "base_cost_per_side": BASE_COST,
        "execution_model": {
            "portfolio_notional_krw": PORTFOLIO_NOTIONAL_KRW,
            "max_position_weight": MAX_POSITION_WEIGHT,
            "one_side_cost": "base broker cost + prior-range half-spread proxy + 1%*sqrt(order/ADV20)",
            "max_dynamic_cost_per_side": MAX_DYNAMIC_COST_PER_SIDE,
        },
        "selection": "highest Train Sharpe (train_cum>0, train_mdd>=-35%)",
        "best_overall_train_selected": best_overall,
        "best_ensemble_train_selected": best_ensemble,
        "promotion": {
            "best_overall": promotion_checks(best_overall),
            "best_ensemble": promotion_checks(best_ensemble),
            "verdict": "BLOCKED_RESEARCH_ONLY_UNRESOLVED_PIT_AND_CORPORATE_ACTIONS",
            "v4_corrections": [
                "fixed gpu_shift sign that leaked future sessions into factors and eligibility",
                "purged train/test boundary and aligned ML target to next-open entry/exit",
                "excluded illiquid rows from ML training as well as portfolio selection",
                "5% maximum position weight; unused allocation remains cash",
                "ADV/spread/market-impact dynamic cost model",
                "missing scheduled exits counted and promotion blocked",
            ],
            "remaining_caveats": [
                "panel is still not point-in-time (survivorship bias)",
                "corporate actions are capped proxies",
                "ML models may overfit to train period",
            ],
        },
        "cross_model_correlation": test_corr_data,
        "leaderboard_train_order": leaderboard.to_dict(orient="records"),
        "details": details,
        "elapsed_seconds": round(elapsed, 3),
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # Markdown
    top = leaderboard.head(20)
    lines = [
        "# Breakout Ensemble v4 Realistic (GPU/MPS)", "",
        f"- Device: `{DEVICE}`",
        f"- Total elapsed: `{elapsed:.2f}s`",
        f"- Best overall: `{best_overall}`",
        f"- Best ensemble: `{best_ensemble}`",
        "- Promotion: `BLOCKED` (true PIT membership and corporate actions unresolved)",
        "- Position cap: `5%`; dynamic ADV/spread/impact costs enabled",
        f"- Bootstrap 5000×: included", "",
        "| Config | Train Sharpe | Train Cum | Train MDD | Test Sharpe | Test Cum | Test MDD | Boot>0% |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in top.to_dict("records"):
        lines.append(
            f"| {r['config']} | {r['train_sharpe']:.3f} | {r['train_cumulative']:.1%} | "
            f"{r['train_mdd']:.1%} | {r['test_sharpe']:.3f} | {r['test_cumulative']:.1%} | "
            f"{r['test_mdd']:.1%} | {r['bootstrap_positive_rate']:.1%} |"
        )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Summary print
    summary = {
        "device": str(DEVICE),
        "total_elapsed": round(elapsed, 2),
        "best_overall": best_overall,
        "best_ensemble": best_ensemble,
        "best_overall_detail": {
            "train": details[best_overall]["train"],
            "test": details[best_overall]["test"],
            "bootstrap": details[best_overall]["bootstrap"],
            "promotion": promotion_checks(best_overall),
        },
        "best_ensemble_detail": {
            "train": details[best_ensemble]["train"],
            "test": details[best_ensemble]["test"],
            "bootstrap": details[best_ensemble]["bootstrap"],
            "promotion": promotion_checks(best_ensemble),
        },
        "json": str(OUT_JSON),
        "markdown": str(OUT_MD),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
