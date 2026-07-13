"""Inverse ETF sleeve candidate builder for guarded TOSS live loop.

Research/live-guard bridge only. This module never submits broker orders; it only
turns a bad-regime long candidate payload into a guarded BUY candidate for a
configured inverse ETF. The existing live-submit phase still applies market-time,
freshness, duplicate-ledger, risk, and quality gates.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import pandas as pd

DEFAULT_TRIGGER_SITUATIONS = {"down_high_vol", "flat_high_vol", "risk_off"}
DEFAULT_ETF_CODE = "114800"
DEFAULT_ETF_NAME = "KODEX 인버스"
DEFAULT_ETF_YF_TICKER = "114800.KS"
LEVERAGED_INVERSE_ETF_CODES = {"252670", "251340"}


@dataclass(frozen=True)
class InverseSleeveSettings:
    enabled: bool = False
    etf_code: str = DEFAULT_ETF_CODE
    etf_name: str = DEFAULT_ETF_NAME
    yf_ticker: str = DEFAULT_ETF_YF_TICKER
    notional_krw: float = 50_000.0
    buy_aggressiveness_pct: float = 0.005
    spread_pct_proxy: float = 0.001
    trigger_situations: frozenset[str] = frozenset(DEFAULT_TRIGGER_SITUATIONS)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "InverseSleeveSettings":
        source = os.environ if env is None else env
        return cls(
            enabled=_env_true(source.get("TOSS_INVERSE_SLEEVE_ENABLED")),
            etf_code=str(source.get("TOSS_INVERSE_ETF_CODE", DEFAULT_ETF_CODE)).zfill(6),
            etf_name=source.get("TOSS_INVERSE_ETF_NAME", DEFAULT_ETF_NAME),
            yf_ticker=source.get("TOSS_INVERSE_ETF_YF_TICKER", DEFAULT_ETF_YF_TICKER),
            notional_krw=float(source.get("TOSS_INVERSE_SLEEVE_NOTIONAL_KRW", "50000")),
            buy_aggressiveness_pct=float(source.get("TOSS_INVERSE_BUY_AGGRESSIVENESS_PCT", "0.005")),
            spread_pct_proxy=float(source.get("TOSS_INVERSE_SPREAD_PCT_PROXY", "0.001")),
            trigger_situations=frozenset(
                item.strip()
                for item in source.get("TOSS_INVERSE_TRIGGER_SITUATIONS", "down_high_vol,flat_high_vol,risk_off").split(",")
                if item.strip()
            ),
        )


def maybe_apply_inverse_sleeve(
    candidate_payload: dict[str, Any],
    *,
    out_dir: Path,
    env: Mapping[str, str] | None = None,
    realtime_quote: Mapping[str, Any] | None = None,
    price_provider: Callable[[str, str | None], dict[str, Any]] | None = None,
    original_candidate_json: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return possibly replaced payload and an audit record.

    Activates only when explicitly enabled and the candidate payload's situation
    is in the configured bad-regime set. This includes aggressive payloads that
    still produce long candidates during ``down_high_vol``.
    """
    settings = InverseSleeveSettings.from_env(env)
    audit: dict[str, Any] = {
        "enabled": settings.enabled,
        "applied": False,
        "reason": None,
        "trigger_situations": sorted(settings.trigger_situations),
        "source_situation": candidate_payload.get("situation"),
        "source_status": candidate_payload.get("status"),
        "source_policy_id": candidate_payload.get("policy_id"),
    }
    if not settings.enabled:
        audit["reason"] = "inverse_sleeve_disabled"
        return candidate_payload, audit
    eligibility_block = inverse_etf_eligibility_block(settings, env=env)
    if eligibility_block:
        blocked = dict(candidate_payload)
        blocked["status"] = "NO_TRADE"
        blocked["reason"] = eligibility_block
        blocked["orders"] = []
        audit["reason"] = eligibility_block
        return blocked, audit

    situation = str(candidate_payload.get("situation") or "").strip()
    if not situation:
        situation = _situation_from_reason(str(candidate_payload.get("reason") or ""))
    if situation not in settings.trigger_situations:
        audit["reason"] = f"situation_not_triggered:{situation or 'unknown'}"
        return candidate_payload, audit
    intraday_decision = candidate_payload.get("intraday_decision")
    verdict = str(intraday_decision.get("verdict") or "") if isinstance(intraday_decision, Mapping) else ""
    if verdict != "INVERSE_BUY":
        blocked = dict(candidate_payload)
        blocked["status"] = "NO_TRADE"
        blocked["reason"] = f"inverse_sleeve_blocked:intraday_decision:{verdict or 'missing'}"
        blocked["orders"] = []
        audit["reason"] = blocked["reason"]
        return blocked, audit

    as_of = str(candidate_payload.get("as_of") or candidate_payload.get("generated_for") or "")[:10] or None
    try:
        quote = dict(realtime_quote) if realtime_quote is not None else (price_provider or fetch_yfinance_daily_quote)(settings.yf_ticker, as_of)
        order = build_inverse_order(settings, quote)
    except Exception as exc:
        blocked = dict(candidate_payload)
        blocked["status"] = "NO_TRADE"
        blocked["reason"] = f"inverse_sleeve_quote_failed:{type(exc).__name__}"
        blocked["orders"] = []
        audit["reason"] = blocked["reason"]
        return blocked, audit

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of or quote.get("price_date"),
        "status": "CANDIDATES",
        "policy_id": "inverse_sleeve_risk_off_v1",
        "strategy_type": "inverse_sleeve",
        "situation": "inverse_sleeve_risk_off",
        "source_situation": situation,
        "source_status": candidate_payload.get("status"),
        "source_reason": candidate_payload.get("reason"),
        "source_policy_id": candidate_payload.get("policy_id"),
        "source_candidate_json": original_candidate_json,
        "intraday_decision": dict(intraday_decision),
        "live_order_submitted": False,
        "orders": [order],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"candidates_{payload['as_of']}_inverse_sleeve_risk_off_v1.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    audit.update({
        "applied": True,
        "reason": "inverse_sleeve_replaced_bad_regime_long_payload",
        "candidate_json": str(out_path),
        "etf_code": settings.etf_code,
        "etf_name": settings.etf_name,
        "yf_ticker": settings.yf_ticker,
        "notional_krw": settings.notional_krw,
        "price_date": quote.get("price_date"),
        "quote_source": quote.get("source", "yfinance_daily_adjusted"),
    })
    return payload, audit


def inverse_etf_eligibility_block(settings: InverseSleeveSettings, *, env: Mapping[str, str] | None = None) -> str | None:
    """Return fail-closed reason when the account cannot trade the configured inverse ETF.

    KIS rejected leveraged inverse ETFs such as 252670 for this account because
    leveraged ETP education was not registered/approved.  Keep leveraged hedge
    candidates as cash unless the operator explicitly records that the education/
    eligibility gate is approved.  Plain KODEX inverse 114800 is allowed.
    """
    source = os.environ if env is None else env
    code = str(settings.etf_code).zfill(6)
    if code in LEVERAGED_INVERSE_ETF_CODES and not _env_true(source.get("TOSS_LEVERAGED_ETP_EDUCATION_APPROVED")):
        return f"inverse_sleeve_blocked:leveraged_etp_education_not_approved:{code}"
    if _env_true(source.get("TOSS_INVERSE_ETF_ACCOUNT_INELIGIBLE")):
        return f"inverse_sleeve_blocked:account_ineligible:{code}"
    return None


def build_inverse_order(settings: InverseSleeveSettings, quote: Mapping[str, Any]) -> dict[str, Any]:
    close = _positive_float(quote.get("close"), "close")
    volume = _positive_float(quote.get("volume"), "volume")
    limit_price = buy_limit_price(close, aggressiveness_pct=settings.buy_aggressiveness_pct)
    quantity = whole_share_quantity(settings.notional_krw, limit_price)
    if quantity <= 0:
        raise ValueError("inverse_sleeve_quantity_zero")
    notional = quantity * limit_price
    return {
        "symbol": settings.etf_code,
        "name": settings.etf_name,
        "side": "BUY",
        "order_type": "LIMIT",
        "quantity": quantity,
        "limit_price": limit_price,
        "notional_krw": notional,
        "mode": "live_auto_guarded",
        "reason": "inverse_sleeve:risk_off_bad_regime",
        "current_price": close,
        "last_price": close,
        "dollar_volume": close * volume,
        "spread_pct": settings.spread_pct_proxy,
        "quote_source": quote.get("source", "yfinance_daily_adjusted"),
        "quote_price_date": quote.get("price_date"),
        "quote_observed_at": quote.get("observed_at"),
        "spread_pct_source": "configured_proxy:TOSS_INVERSE_SPREAD_PCT_PROXY",
    }


def fetch_yfinance_daily_quote(ticker: str, as_of: str | None = None) -> dict[str, Any]:
    import yfinance as yf

    df = yf.download(ticker, period="30d", auto_adjust=True, progress=False, threads=False)
    if df.empty:
        raise RuntimeError(f"empty_yfinance_quote:{ticker}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] for col in df.columns]
    df = df.sort_index()
    if as_of:
        df = df[df.index.tz_localize(None) <= pd.Timestamp(as_of)]
    if df.empty:
        raise RuntimeError(f"no_quote_on_or_before_as_of:{ticker}:{as_of}")
    row = df.iloc[-1]
    idx = pd.Timestamp(df.index[-1]).tz_localize(None)
    return {
        "ticker": ticker,
        "price_date": idx.date().isoformat(),
        "close": float(row["Close"]),
        "open": float(row.get("Open", row["Close"])),
        "high": float(row.get("High", row["Close"])),
        "low": float(row.get("Low", row["Close"])),
        "volume": float(row.get("Volume", 0.0)),
        "source": "yfinance_daily_adjusted",
    }


def buy_limit_price(reference_close: float, *, aggressiveness_pct: float = 0.005) -> int:
    return round_up_to_tick(float(reference_close) * (1.0 + aggressiveness_pct))


def round_up_to_tick(price: float) -> int:
    tick = krx_tick_size(price)
    return int(math.ceil(float(price) / tick) * tick)


def krx_tick_size(price: float) -> int:
    if price < 2_000:
        return 1
    if price < 5_000:
        return 5
    if price < 20_000:
        return 10
    if price < 50_000:
        return 50
    if price < 200_000:
        return 100
    if price < 500_000:
        return 500
    return 1_000


def whole_share_quantity(budget_krw: float, limit_price: float) -> int:
    if budget_krw <= 0 or limit_price <= 0:
        return 0
    return int(float(budget_krw) // float(limit_price))


def _situation_from_reason(reason: str) -> str:
    prefix = "situation_not_approved:"
    if reason.startswith(prefix):
        return reason.split(":", 1)[1].strip()
    return ""


def _positive_float(value: Any, name: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"invalid_{name}")
    return result


def _env_true(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}
