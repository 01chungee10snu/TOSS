#!/usr/bin/env python3
"""Prospective down-high-vol rebound candidate tracker.

This module is intentionally isolated from the live candidate/order path. It may
read KIS quotes, but it never imports or calls any submit/cancel function and
always emits ``orders=[]``. A trigger creates a research artifact only.
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "generated_policies" / "down_high_vol_rebound_candidate_only_v1.json"
LOOP_REPORT_PATH = ROOT / "reports" / "harness" / "latest_loop_report.json"
OUT_DIR = ROOT / "reports" / "harness" / "rebound_candidate_only"
LATEST_PATH = OUT_DIR / "latest.json"
STATE_PATH = OUT_DIR / "state.json"
KST = ZoneInfo("Asia/Seoul")
sys.path.insert(0, str(ROOT / "scripts"))

from generate_contextual_daily_candidates import load_policy, prepare_features, score  # noqa: E402


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_clock(value: str) -> time:
    hour, minute = str(value).split(":", 1)
    return time(int(hour), int(minute))


def evaluate_trigger(
    loop_report: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    """Evaluate only point-in-time evidence available at the decision timestamp."""
    trigger = dict(policy.get("trigger") or {})
    intraday = loop_report.get("intraday") if isinstance(loop_report.get("intraday"), Mapping) else {}
    decision = intraday.get("decision") if isinstance(intraday.get("decision"), Mapping) else {}
    metrics = decision.get("metrics") if isinstance(decision.get("metrics"), Mapping) else {}
    observed = _parse_dt(decision.get("generated_at_utc") or loop_report.get("generated_at_utc"))
    blockers: list[str] = []

    if policy.get("promotion_mode") != "CANDIDATE_ONLY" or policy.get("live_trading_enabled") is not False:
        blockers.append("policy_not_candidate_only")
    if observed is None:
        blockers.append("decision_timestamp_missing")
        age_seconds = None
        observed_kst = None
    else:
        age_seconds = max(0.0, (now.astimezone(timezone.utc) - observed).total_seconds())
        observed_kst = observed.astimezone(KST)
        if age_seconds > float(trigger.get("max_loop_report_age_seconds", 1200)):
            blockers.append("loop_report_stale")
        start = _parse_clock(str(trigger.get("earliest_kst", "11:00")))
        end = _parse_clock(str(trigger.get("latest_kst", "15:20")))
        if not (start <= observed_kst.time().replace(tzinfo=None) <= end):
            blockers.append("outside_trigger_window")

    checks = {
        "daily_regime": str(decision.get("daily_regime") or "") == str(trigger.get("required_daily_regime")),
        "verdict": str(decision.get("verdict") or "") == str(trigger.get("required_intraday_verdict")),
        "market_regime": str(decision.get("market_regime") or "") == str(trigger.get("required_market_regime")),
        "market_override_confirmed": bool(metrics.get("market_override_confirmed")) is bool(trigger.get("required_market_override_confirmed", True)),
        "evidence_fresh": str(decision.get("evidence_status") or "") == str(trigger.get("required_evidence_status", "FRESH")),
        "news_evidence_fresh": str(decision.get("news_evidence_status") or "") == str(trigger.get("required_news_evidence_status", "FRESH")),
        "no_signal_conflict": not bool(decision.get("signal_conflict")),
        "market_return_threshold": _number(metrics.get("market_day_return")) is not None
        and float(metrics.get("market_day_return")) >= float(trigger.get("min_market_day_return", 0.03)),
    }
    blockers.extend(name for name, passed in checks.items() if not passed)
    return {
        "triggered": not blockers,
        "blockers": blockers,
        "checks": checks,
        "decision_id": decision.get("decision_id"),
        "decision_observed_at_utc": observed.isoformat() if observed else None,
        "decision_observed_at_kst": observed_kst.isoformat() if observed_kst else None,
        "decision_age_seconds": age_seconds,
        "market_day_return": _number(metrics.get("market_day_return")),
        "decision": dict(decision),
    }


def select_previous_session_candidates(
    panel: pd.DataFrame,
    base_policy: Mapping[str, Any],
    *,
    decision_date_kst: str,
    top_n: int,
) -> tuple[str, list[dict[str, Any]]]:
    """Rank using the last completed session strictly before the trigger date."""
    features = prepare_features(panel)
    cutoff = pd.Timestamp(decision_date_kst)
    eligible_dates = features.loc[features["Date"] < cutoff, "Date"]
    if eligible_dates.empty:
        raise ValueError("previous_completed_session_missing")
    feature_date = eligible_dates.max()
    frame = features[features["Date"] == feature_date].copy()
    params = dict((base_policy.get("situations") or {}).get("up_low_vol") or {})
    if not params:
        raise ValueError("up_low_vol_parameters_missing")
    frame["score"] = score(frame, params)
    min_dv_col = "avg_dollar_vol_20d"
    mask = (
        frame["score"].notna()
        & frame[min_dv_col].ge(float(params.get("min_dollar_volume", 500_000_000)))
        & frame["Open"].gt(0)
        & frame["Close"].gt(0)
    )
    frame = frame[mask].copy()
    for col, lo, hi in [
        ("mom_5d", params.get("min_mom_5d", -0.15), params.get("max_mom_5d", 0.15)),
        ("rsi_14", params.get("min_rsi", 30), params.get("max_rsi", 70)),
        ("vol_ratio", params.get("min_vol_ratio", 0.8), params.get("max_vol_ratio", 3.0)),
        ("vol_20d", 0, params.get("max_vol_20d", 0.08)),
        ("Close", params.get("min_price", 1000), 1e12),
    ]:
        if col in frame.columns:
            inclusive = "left" if col == "Close" else "both"
            frame = frame[frame[col].between(float(lo), float(hi), inclusive=inclusive)]
    picks = frame.sort_values("score", ascending=False).head(int(top_n))
    candidates = []
    for _, row in picks.iterrows():
        candidates.append({
            "symbol": str(row["code"]).zfill(6),
            "name": str(row.get("name") or ""),
            "feature_date": feature_date.date().isoformat(),
            "reference_close": float(row["Close"]),
            "score": float(row["score"]),
            "mom_5d": _number(row.get("mom_5d")),
            "rsi_14": _number(row.get("rsi_14")),
            "vol_20d": _number(row.get("vol_20d")),
            "avg_dollar_vol_20d": _number(row.get("avg_dollar_vol_20d")),
        })
    return feature_date.date().isoformat(), candidates


def attach_realtime_quotes(
    candidates: list[dict[str, Any]],
    *,
    budget_krw: float,
    now: datetime,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Attach read-only KIS marks. No order or submit API is reachable here."""
    from toss_alpha.connectors.kis_readonly import KisReadOnlyClient
    from toss_alpha.execution.live_ready import LiveExecutionConfig

    cfg = LiveExecutionConfig.from_env(os.environ)
    missing = [name for name, value in {
        "app_key": cfg.app_key,
        "app_secret": cfg.app_secret,
        "cano": cfg.cano,
        "account_product_code": cfg.account_product_code,
    }.items() if not value]
    if cfg.provider != "kis" or missing:
        raise RuntimeError(f"kis_readonly_config_unavailable:{','.join(missing) or cfg.provider}")
    client = KisReadOnlyClient(
        app_key=cfg.app_key,
        app_secret=cfg.app_secret,
        cano=cfg.cano,
        account_product_code=cfg.account_product_code or "01",
        mock_trading=cfg.kis_mock_trading,
        base_url=cfg.base_url,
        timeout=cfg.timeout,
    )
    enriched: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for raw in candidates:
        item = dict(raw)
        symbol = str(item["symbol"]).zfill(6)
        try:
            payload = client.quote(symbol).get("json") or {}
            record = payload.get("output") or payload.get("output1") or payload
            last = _number(record.get("stck_prpr") or record.get("last") or record.get("price"))
            ask = _number(record.get("askp1") or record.get("askp") or record.get("ask_price"))
            if last is None or last <= 0:
                raise RuntimeError("non_positive_last")
            reference = ask if ask and ask > 0 else last
            quantity = int(float(budget_krw) // reference)
            item.update({
                "entry_mark": reference,
                "last": last,
                "best_ask": ask,
                "hypothetical_quantity": quantity,
                "hypothetical_notional_krw": quantity * reference,
                "quote_source": "kis_realtime_quote",
                "quote_observed_at_utc": now.astimezone(timezone.utc).isoformat(),
                "eligible_whole_share": quantity >= 1,
            })
            enriched.append(item)
        except Exception as exc:
            errors[symbol] = f"{type(exc).__name__}:{exc}"
    return enriched, errors


def build_artifact(
    policy: Mapping[str, Any],
    trigger: Mapping[str, Any],
    candidates: list[dict[str, Any]],
    *,
    feature_date: str | None,
    quote_errors: Mapping[str, str] | None,
    now: datetime,
    status: str,
) -> dict[str, Any]:
    return {
        "generated_at_utc": now.astimezone(timezone.utc).isoformat(),
        "generated_at_kst": now.astimezone(KST).isoformat(),
        "policy_id": policy.get("policy_id"),
        "promotion_mode": "CANDIDATE_ONLY",
        "execution_mode": "research_forward_tracking_only",
        "status": status,
        "live_order_submission_prohibited": True,
        "live_order_submitted": False,
        "orders": [],
        "trigger": dict(trigger),
        "feature_cutoff": "previous_completed_krx_session",
        "feature_date": feature_date,
        "research_candidates": candidates,
        "candidate_allocation": {
            **dict(policy.get("sizing") or {}),
            "hypothetical_total_notional_krw": sum(
                float(row.get("hypothetical_notional_krw") or 0) for row in candidates
            ),
        },
        "quote_errors": dict(quote_errors or {}),
        "hypothetical_exit": dict(policy.get("research_exit") or {}),
        "invalidated_backtest_metrics_used": False,
    }


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def candidate_budget_krw(policy: Mapping[str, Any], candidate_count: int) -> float:
    """Return an equal per-symbol research budget under both hard caps."""
    if candidate_count <= 0:
        return 0.0
    sizing = dict(policy.get("sizing") or {})
    total_cap = float(sizing.get("max_total_candidate_allocation_krw", 250000))
    position_cap = float(sizing.get("max_notional_krw_per_position", total_cap))
    return max(0.0, min(position_cap, total_cap / candidate_count))


def main() -> int:
    now = datetime.now(timezone.utc)
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    report = json.loads(LOOP_REPORT_PATH.read_text(encoding="utf-8"))
    trigger = evaluate_trigger(report, policy, now=now)
    decision_kst = _parse_dt(trigger.get("decision_observed_at_utc"))
    decision_date = (decision_kst or now).astimezone(KST).date().isoformat()
    state = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {"triggered_dates": {}}

    if not trigger["triggered"]:
        artifact = build_artifact(policy, trigger, [], feature_date=None, quote_errors=None, now=now, status="WAIT")
        _atomic_json(LATEST_PATH, artifact)
        return 0
    if decision_date in (state.get("triggered_dates") or {}):
        artifact = build_artifact(policy, trigger, [], feature_date=None, quote_errors=None, now=now, status="ALREADY_CAPTURED")
        artifact["existing_artifact"] = state["triggered_dates"][decision_date]
        _atomic_json(LATEST_PATH, artifact)
        return 0

    base_policy = load_policy(ROOT / str(policy["base_policy"]))
    panel = pd.read_csv(ROOT / str(policy["panel_csv"]))
    selection = dict(policy.get("candidate_selection") or {})
    feature_date, candidates = select_previous_session_candidates(
        panel,
        base_policy,
        decision_date_kst=decision_date,
        top_n=int(selection.get("top_n", 3)),
    )
    budget = candidate_budget_krw(policy, len(candidates))
    candidates, quote_errors = attach_realtime_quotes(candidates, budget_krw=budget, now=now)
    status = "CAPTURED" if candidates and not quote_errors else "BLOCKED_QUOTE_DATA"
    artifact = build_artifact(
        policy,
        trigger,
        candidates,
        feature_date=feature_date,
        quote_errors=quote_errors,
        now=now,
        status=status,
    )
    dated_path = OUT_DIR / f"rebound_{decision_date.replace('-', '')}.json"
    _atomic_json(dated_path, artifact)
    _atomic_json(LATEST_PATH, artifact)
    if status == "CAPTURED":
        state.setdefault("triggered_dates", {})[decision_date] = str(dated_path)
        _atomic_json(STATE_PATH, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
