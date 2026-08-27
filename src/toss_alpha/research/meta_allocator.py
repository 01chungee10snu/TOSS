"""Evidence-aware cross-strategy correlation and meta-allocation helpers.

This module is deliberately fail-closed for live capital.  Historical
performance may influence research prioritization, but only strategies already
marked LIVE_ELIGIBLE by the tournament can receive non-zero live allocation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

LIVE_STATUS = "LIVE_ELIGIBLE"
RESEARCH_STATUSES = {"PAPER_CANDIDATE", "FORWARD_PAPER_PASSED", "LIVE_ELIGIBLE"}
GRADE_FACTOR = {"A": 1.00, "B": 0.90, "C": 0.60, "D": 0.25, "E": 0.0}
STATUS_FACTOR = {"LIVE_ELIGIBLE": 1.00, "FORWARD_PAPER_PASSED": 0.90, "PAPER_CANDIDATE": 0.75}


@dataclass(frozen=True)
class RiskMetrics:
    observations: int
    annual_vol: float | None
    sharpe_zero_rf: float | None
    max_drawdown: float | None
    end_drawdown: float | None
    downside_vol: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "observations": self.observations,
            "annual_vol": self.annual_vol,
            "sharpe_zero_rf": self.sharpe_zero_rf,
            "max_drawdown": self.max_drawdown,
            "end_drawdown": self.end_drawdown,
            "downside_vol": self.downside_vol,
        }


def _finite_series(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna().astype(float)
    return s


def risk_metrics(returns: pd.Series) -> RiskMetrics:
    r = _finite_series(returns)
    n = len(r)
    if n == 0:
        return RiskMetrics(0, None, None, None, None, None)
    std = float(r.std(ddof=1)) if n > 1 else 0.0
    annual_vol = std * math.sqrt(252.0) if std > 0 else 0.0
    sharpe = float(r.mean() * 252.0 / annual_vol) if annual_vol > 0 else None
    equity = (1.0 + r).cumprod()
    peak = equity.cummax()
    dd = equity / peak - 1.0
    downside = r[r < 0]
    downside_std = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
    return RiskMetrics(
        observations=n,
        annual_vol=annual_vol,
        sharpe_zero_rf=sharpe,
        max_drawdown=float(dd.min()),
        end_drawdown=float(dd.iloc[-1]),
        downside_vol=downside_std * math.sqrt(252.0) if downside_std > 0 else 0.0,
    )


def pairwise_correlation(a: pd.Series, b: pd.Series, *, min_obs: int = 20) -> dict[str, Any]:
    pair = pd.concat([_finite_series(a).rename("a"), _finite_series(b).rename("b")], axis=1, join="inner").dropna()
    if len(pair) < min_obs:
        return {"observations": int(len(pair)), "pearson": None, "downside": None}
    pearson = float(pair["a"].corr(pair["b"]))
    downside = pair[(pair["a"] < 0) | (pair["b"] < 0)]
    downside_corr = float(downside["a"].corr(downside["b"])) if len(downside) >= min_obs else None
    return {
        "observations": int(len(pair)),
        "pearson": pearson if math.isfinite(pearson) else None,
        "downside": downside_corr if downside_corr is not None and math.isfinite(downside_corr) else None,
    }


def correlation_matrix(returns: Mapping[str, pd.Series], *, min_obs: int = 20, windows: Iterable[int] = (60, 120)) -> dict[str, Any]:
    ids = sorted(returns)
    pairs: dict[str, Any] = {}
    for i, left in enumerate(ids):
        for right in ids[i + 1 :]:
            base = pairwise_correlation(returns[left], returns[right], min_obs=min_obs)
            rolling: dict[str, float | None] = {}
            aligned = pd.concat([_finite_series(returns[left]), _finite_series(returns[right])], axis=1, join="inner").dropna()
            aligned.columns = ["a", "b"]
            for window in windows:
                if len(aligned) < int(window):
                    rolling[str(window)] = None
                else:
                    corr = float(aligned.iloc[-int(window):]["a"].corr(aligned.iloc[-int(window):]["b"]))
                    rolling[str(window)] = corr if math.isfinite(corr) else None
            base["rolling_latest"] = rolling
            pairs[f"{left}|{right}"] = base
    return {"strategies": ids, "pairs": pairs}


def drawdown_scale(current_drawdown: float | None, *, soft_stop: float = -0.10, hard_stop: float = -0.20) -> float:
    if current_drawdown is None or not math.isfinite(float(current_drawdown)):
        return 0.0
    dd = float(current_drawdown)
    if dd <= hard_stop:
        return 0.0
    if dd <= soft_stop:
        return 0.5
    return 1.0


def _pair_key(a: str, b: str) -> str:
    return "|".join(sorted((a, b)))


def prune_correlated(
    candidates: Iterable[str],
    *,
    correlations: Mapping[str, Any],
    rank: Mapping[str, int],
    protected: Iterable[str] = (),
    threshold: float = 0.95,
) -> dict[str, Any]:
    protected_set = set(protected)
    ordered = sorted(set(candidates), key=lambda x: (0 if x in protected_set else 1, int(rank.get(x, 10**9)), x))
    kept: list[str] = []
    removed: dict[str, Any] = {}
    pair_map = correlations.get("pairs", {}) if isinstance(correlations, Mapping) else {}
    for candidate in ordered:
        duplicate = None
        for existing in kept:
            row = pair_map.get(_pair_key(candidate, existing), {})
            pearson = row.get("pearson")
            downside = row.get("downside")
            high = any(v is not None and float(v) >= threshold for v in (pearson, downside))
            if high:
                duplicate = {
                    "duplicate_of": existing,
                    "pearson": pearson,
                    "downside": downside,
                    "threshold": threshold,
                }
                break
        if duplicate is None:
            kept.append(candidate)
        else:
            removed[candidate] = duplicate
    return {"kept": kept, "removed": removed, "protected": sorted(protected_set), "threshold": threshold}


def _capped_weights(scores: Mapping[str, float], *, total_budget: float, max_weight: float) -> dict[str, float]:
    active = {k: max(0.0, float(v)) for k, v in scores.items() if float(v) > 0}
    budget = max(0.0, min(1.0, float(total_budget)))
    cap = max(0.0, min(1.0, float(max_weight)))
    if not active or budget <= 0 or cap <= 0:
        return {}
    weights = {k: 0.0 for k in active}
    remaining = budget
    open_ids = set(active)
    for _ in range(len(active) + 2):
        if remaining <= 1e-12 or not open_ids:
            break
        score_sum = sum(active[k] for k in open_ids)
        if score_sum <= 0:
            break
        allocations = {k: remaining * active[k] / score_sum for k in open_ids}
        hit_cap = False
        for k, add in allocations.items():
            room = max(0.0, cap - weights[k])
            if add >= room - 1e-12:
                weights[k] += room
                remaining -= room
                open_ids.remove(k)
                hit_cap = True
                break
        if not hit_cap:
            for k, add in allocations.items():
                weights[k] += add
            remaining = 0.0
            break
    return {k: v for k, v in weights.items() if v > 1e-12}


def allocate_candidates(
    candidates: Iterable[Mapping[str, Any]],
    *,
    metrics: Mapping[str, RiskMetrics],
    correlations: Mapping[str, Any],
    mode: str,
    protected: Iterable[str] = (),
    correlation_threshold: float = 0.95,
    soft_drawdown: float = -0.10,
    hard_drawdown: float = -0.20,
    max_strategy_weight: float = 0.50,
    min_observations: int = 252,
    current_drawdowns: Mapping[str, float | None] | None = None,
) -> dict[str, Any]:
    rows = {str(row.get("strategy_id")): dict(row) for row in candidates if row.get("strategy_id")}
    current_drawdowns = dict(current_drawdowns or {})
    rank = {sid: int(row.get("rank") or 10**9) for sid, row in rows.items()}
    eligible: list[str] = []
    blocked: dict[str, list[str]] = {}

    for sid, row in rows.items():
        reasons: list[str] = []
        status = str(row.get("status") or "")
        grade = str(row.get("evidence_grade") or "E")
        m = metrics.get(sid)
        if mode == "live":
            if status != LIVE_STATUS:
                reasons.append("status_not_live_eligible")
            if grade not in {"A", "B"}:
                reasons.append("evidence_grade_below_live_floor")
        elif mode == "research_shadow":
            if status not in RESEARCH_STATUSES:
                reasons.append("status_below_paper_candidate")
            if GRADE_FACTOR.get(grade, 0.0) <= 0:
                reasons.append("evidence_grade_not_sizeable")
        else:
            raise ValueError("mode must be live or research_shadow")
        if m is None or m.observations < min_observations:
            reasons.append("insufficient_daily_return_history")
        elif m.annual_vol is None or m.annual_vol <= 0:
            reasons.append("non_positive_or_missing_volatility")
        if mode == "live" and (sid not in current_drawdowns or current_drawdowns.get(sid) is None):
            reasons.append("current_drawdown_evidence_missing")
        if reasons:
            blocked[sid] = reasons
        else:
            eligible.append(sid)

    prune = prune_correlated(
        eligible,
        correlations=correlations,
        rank=rank,
        protected=protected,
        threshold=correlation_threshold,
    )
    for sid, details in prune["removed"].items():
        blocked.setdefault(sid, []).append(f"correlation_duplicate_of:{details['duplicate_of']}")

    pre_scores: dict[str, float] = {}
    scaled_scores: dict[str, float] = {}
    risk_scales: dict[str, float] = {}
    for sid in prune["kept"]:
        row = rows[sid]
        m = metrics[sid]
        grade_factor = GRADE_FACTOR.get(str(row.get("evidence_grade") or "E"), 0.0)
        status_factor = STATUS_FACTOR.get(str(row.get("status") or ""), 0.0)
        inv_vol = 1.0 / max(float(m.annual_vol or 0.0), 1e-9)
        pre = grade_factor * status_factor * inv_vol
        if sid in current_drawdowns and current_drawdowns.get(sid) is not None:
            scale = drawdown_scale(current_drawdowns[sid], soft_stop=soft_drawdown, hard_stop=hard_drawdown)
        else:
            # A historical backtest's final drawdown is not current risk evidence.
            # Research-shadow candidates remain at neutral scale unless a current
            # forward/live drawdown observation is supplied explicitly.
            scale = 1.0
        pre_scores[sid] = pre
        risk_scales[sid] = scale
        scaled_scores[sid] = pre * scale
        if scale <= 0:
            blocked.setdefault(sid, []).append("hard_drawdown_stop_or_missing_drawdown")

    denom = sum(pre_scores.values())
    total_budget = sum(pre_scores[k] * risk_scales[k] for k in pre_scores) / denom if denom > 0 else 0.0
    weights = _capped_weights(scaled_scores, total_budget=total_budget, max_weight=max_strategy_weight)
    cash = max(0.0, 1.0 - sum(weights.values()))
    return {
        "mode": mode,
        "weights": {k: round(v, 8) for k, v in sorted(weights.items())},
        "cash_weight": round(cash, 8),
        "invested_weight": round(1.0 - cash, 8),
        "eligible_before_correlation": sorted(eligible, key=lambda x: rank.get(x, 10**9)),
        "selected_after_correlation": prune["kept"],
        "correlation_pruning": prune,
        "risk_scales": {k: risk_scales[k] for k in sorted(risk_scales)},
        "current_drawdown_evidence": {k: current_drawdowns.get(k) for k in sorted(current_drawdowns)},
        "blocked": blocked,
        "policy": {
            "correlation_threshold": correlation_threshold,
            "soft_drawdown": soft_drawdown,
            "hard_drawdown": hard_drawdown,
            "max_strategy_weight": max_strategy_weight,
            "min_observations": min_observations,
            "sizing": "inverse_volatility * evidence_factor * status_factor * drawdown_scale",
            "performance_score_used_for_sizing": False,
            "live_requires_current_drawdown_evidence": True,
        },
    }
