from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.generate_contextual_daily_candidates import (  # noqa: E402
    buy_limit_price,
    prepare_features,
    score,
)

REPORT_DIR = ROOT / "reports" / "harness"
OUT_JSON = REPORT_DIR / "fill_regime_research_20260703.json"
OUT_MD = REPORT_DIR / "fill_regime_research_20260703.md"
PANEL = ROOT / "reports" / "backtests" / "random500_seed20260607_2022-01-01_2026-latest_ohlcv_panel.csv"
AGGRESSIVE_POLICY = ROOT / "config" / "generated_policies" / "contextual_mon_fri_policy_seed20260607_aggressive_small_account.json"
PROMOTED_POLICY = ROOT / "config" / "generated_policies" / "contextual_mon_fri_policy_seed20260607_walkforward_promoted.json"
TODAY_CANDIDATES = ROOT / "reports" / "trade_candidates" / "candidates_2026-07-03_contextual_mon_fri_policy_seed20260607_aggressive_small_account.json"
TODAY_ORDERS = ROOT / "reports" / "harness" / "kis_order_status_20260703_161011.json"
TODAY_BALANCE = ROOT / "reports" / "harness" / "kis_live_status_20260703_160238.json"

AGGRESSIVENESS = [0.0, 0.003, 0.005, 0.01]
ANALYSIS_START = "2024-01-01"


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"_missing": str(path)}
    return json.loads(path.read_text(encoding="utf-8"))


def fnum(x: Any) -> float | None:
    try:
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return None
        return float(x)
    except Exception:
        return None


def summarize_returns(values: list[float]) -> dict[str, Any]:
    s = pd.Series(values, dtype="float64").dropna()
    if s.empty:
        return {"n": 0, "avg_pct": None, "median_pct": None, "win_rate_pct": None, "min_pct": None, "max_pct": None}
    return {
        "n": int(s.shape[0]),
        "avg_pct": round(float(s.mean() * 100.0), 3),
        "median_pct": round(float(s.median() * 100.0), 3),
        "win_rate_pct": round(float((s > 0).mean() * 100.0), 2),
        "min_pct": round(float(s.min() * 100.0), 3),
        "max_pct": round(float(s.max() * 100.0), 3),
    }


def build_signal_rows(data: pd.DataFrame, policy: dict[str, Any], *, only_situation: str | None = None) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    situations = policy.get("situations", {})
    if only_situation and only_situation not in situations:
        return pd.DataFrame()
    dates = sorted(data.loc[data["Date"] >= pd.Timestamp(ANALYSIS_START), "Date"].dropna().unique())
    for date in dates:
        todays = data[data["Date"] == date].copy()
        if todays.empty or not todays["situation"].notna().any():
            continue
        situation = str(todays["situation"].dropna().iloc[0])
        if only_situation and situation != only_situation:
            continue
        if situation not in situations:
            continue
        params = situations[situation]
        todays["score"] = score(todays, params)
        eligible = todays[
            todays["score"].notna()
            & todays["dollar_volume"].ge(float(params["min_dollar_volume"]))
            & todays["Open"].gt(0)
            & todays["Close"].gt(0)
            & todays["Low"].gt(0)
            & todays["High"].gt(0)
        ].copy()
        if eligible.empty:
            continue
        mom = params["momentum_col"]
        if params["mode"] == "momentum":
            eligible = eligible[eligible[mom] >= float(params["min_abs_momentum"])].copy()
        else:
            eligible = eligible[eligible[mom] <= -float(params["min_abs_momentum"])].copy()
        picks = eligible.sort_values("score", ascending=False).head(int(params["top_n"])).copy()
        if picks.empty:
            continue
        picks["rank"] = range(1, len(picks) + 1)
        picks["policy_id"] = policy.get("policy_id")
        picks["situation"] = situation
        rows.append(picks)
    if not rows:
        return pd.DataFrame()
    signals = pd.concat(rows, ignore_index=True)
    keep = [
        "Date", "code", "name", "Open", "High", "Low", "Close", "Volume", "dollar_volume",
        "situation", "score", "rank", "policy_id", "mom_20d", "vol_20d", "market_mom_20d", "market_vol_20d",
    ]
    return signals[[c for c in keep if c in signals.columns]].copy()


def add_forward_returns(signals: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
    if signals.empty:
        return signals
    p = panel.sort_values(["code", "Date"]).copy()
    g = p.groupby("code", group_keys=False)
    p["next_open"] = g["Open"].shift(-1)
    p["next_high"] = g["High"].shift(-1)
    p["next_low"] = g["Low"].shift(-1)
    p["next_close"] = g["Close"].shift(-1)
    p["close_5_after_entry"] = g["Close"].shift(-6)
    p["ret_1d_close"] = p["next_close"] / p["Close"] - 1.0
    p["ret_5d_close"] = p["close_5_after_entry"] / p["Close"] - 1.0
    return signals.merge(
        p[[
            "Date", "code", "next_open", "next_high", "next_low", "next_close", "close_5_after_entry",
            "ret_1d_close", "ret_5d_close",
        ]],
        on=["Date", "code"],
        how="left",
    )


def fill_summary(signals: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if signals.empty:
        return rows
    total = len(signals)
    for pct in AGGRESSIVENESS:
        tmp = signals.copy()
        tmp["limit_price"] = tmp["Close"].apply(lambda x: buy_limit_price(float(x), aggressiveness_pct=pct))
        # Realistic proxy: signal is formed on Date close, buy-limit is sent on the next trading day.
        # If next_open <= limit, assume entry at next_open. Else if next_low <= limit, assume entry at limit.
        # This is still not broker queue replay, but avoids the same-day lookahead fill artifact.
        tmp["fill_proxy_next_day"] = tmp["next_low"].notna() & tmp["next_low"].le(tmp["limit_price"])
        tmp["entry_price_proxy"] = tmp["limit_price"].astype(float)
        open_fill = tmp["next_open"].notna() & tmp["next_open"].le(tmp["limit_price"])
        tmp.loc[open_fill, "entry_price_proxy"] = tmp.loc[open_fill, "next_open"]
        tmp["filled_ret_1d_from_entry"] = tmp["next_close"] / tmp["entry_price_proxy"] - 1.0
        tmp["filled_ret_5d_from_entry"] = tmp["close_5_after_entry"] / tmp["entry_price_proxy"] - 1.0
        filled = tmp[tmp["fill_proxy_next_day"]].copy()
        rows.append({
            "aggressiveness_pct": pct,
            "signals": int(total),
            "fill_proxy_count": int(filled.shape[0]),
            "fill_proxy_rate_pct": round(float(filled.shape[0] / total * 100.0), 2) if total else None,
            "avg_limit_vs_close_pct": round(float(((tmp["limit_price"] / tmp["Close"]) - 1.0).mean() * 100.0), 3),
            "all_signal_close_to_next_close_1d": summarize_returns(tmp["ret_1d_close"].dropna().tolist()),
            "all_signal_close_to_5d_after_entry": summarize_returns(tmp["ret_5d_close"].dropna().tolist()),
            "filled_ret_1d_from_entry": summarize_returns(filled["filled_ret_1d_from_entry"].dropna().tolist()),
            "filled_ret_5d_from_entry": summarize_returns(filled["filled_ret_5d_from_entry"].dropna().tolist()),
        })
    return rows


def by_situation_fill(signals: pd.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if signals.empty:
        return out
    for situation, part in signals.groupby("situation"):
        out[str(situation)] = fill_summary(part)
    return out


def daily_portfolio_summary(signals: pd.DataFrame) -> dict[str, Any]:
    if signals.empty:
        return {"days": 0}
    day = signals.groupby("Date").agg(
        picks=("code", "count"),
        avg_ret_1d=("ret_1d_close", "mean"),
        avg_ret_5d=("ret_5d_close", "mean"),
    ).reset_index()
    return {
        "days": int(day.shape[0]),
        "avg_picks_per_day": round(float(day["picks"].mean()), 2),
        "ret_1d_vs_no_trade": summarize_returns(day["avg_ret_1d"].dropna().tolist()),
        "ret_5d_vs_no_trade": summarize_returns(day["avg_ret_5d"].dropna().tolist()),
    }


def extract_today_actual() -> dict[str, Any]:
    cands = load_json(TODAY_CANDIDATES)
    order_status = load_json(TODAY_ORDERS)
    balance = load_json(TODAY_BALANCE)
    cand_orders = cands.get("orders", []) if isinstance(cands.get("orders"), list) else []
    planned = {str(o.get("symbol", "")).zfill(6): o for o in cand_orders}
    # KIS snapshots in this repo have varied shapes; preserve compact top-level and derive what is safe.
    actual_rows: list[dict[str, Any]] = []
    raw_orders = []
    for key in ("orders", "output", "output1", "rt_cd"):
        if isinstance(order_status.get(key), list):
            raw_orders = order_status[key]
            break
    if not raw_orders and isinstance(order_status.get("payload"), dict):
        for key in ("orders", "output", "output1"):
            if isinstance(order_status["payload"].get(key), list):
                raw_orders = order_status["payload"][key]
                break
    for sym, o in planned.items():
        actual_rows.append({
            "symbol": sym,
            "name": o.get("name"),
            "planned_qty": o.get("quantity"),
            "limit_price": o.get("limit_price"),
            "planned_notional_krw": o.get("notional_krw"),
        })
    return {
        "candidate_file": str(TODAY_CANDIDATES),
        "order_status_file": str(TODAY_ORDERS),
        "balance_file": str(TODAY_BALANCE),
        "candidate_status": cands.get("status"),
        "candidate_situation": cands.get("situation"),
        "planned_orders": actual_rows,
        "planned_order_count": len(actual_rows),
        "planned_total_notional_krw": cands.get("planned_total_notional_krw"),
        "raw_order_snapshot_keys": list(order_status.keys())[:30] if isinstance(order_status, dict) else [],
        "raw_balance_snapshot_keys": list(balance.keys())[:30] if isinstance(balance, dict) else [],
        "note": "실제 체결/거부 세부값은 KIS snapshot 원형을 보존하고, 최종 보고에는 기존 read-only 조회 결과와 교차 참조합니다.",
    }


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    panel_raw = pd.read_csv(PANEL, dtype={"code": str}, parse_dates=["Date"])
    panel = prepare_features(panel_raw)
    aggressive = load_json(AGGRESSIVE_POLICY)
    promoted = load_json(PROMOTED_POLICY)

    aggressive_signals = add_forward_returns(build_signal_rows(panel, aggressive), panel)
    down_high_vol_signals = add_forward_returns(build_signal_rows(panel, aggressive, only_situation="down_high_vol"), panel)
    promoted_signals = add_forward_returns(build_signal_rows(panel, promoted), panel)

    payload: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "panel": str(PANEL),
            "analysis_start": ANALYSIS_START,
            "aggressiveness_grid": AGGRESSIVENESS,
            "fill_proxy_definition": "next trading day OHLC proxy: next_low <= limit_price; entry at next_open if open <= limit else limit; not broker queue replay",
        },
        "inputs": {
            "aggressive_policy": str(AGGRESSIVE_POLICY),
            "promoted_policy": str(PROMOTED_POLICY),
        },
        "today_actual": extract_today_actual(),
        "aggressive_all_situations": {
            "signal_count": int(aggressive_signals.shape[0]),
            "trading_days": int(aggressive_signals["Date"].nunique()) if not aggressive_signals.empty else 0,
            "fill_grid": fill_summary(aggressive_signals),
            "by_situation_fill_grid": by_situation_fill(aggressive_signals),
            "daily_portfolio": daily_portfolio_summary(aggressive_signals),
        },
        "down_high_vol_rejected_by_promoted": {
            "status_in_promoted": "NO_TRADE because down_high_vol is outside promoted situations",
            "signal_count_if_aggressive_allowed": int(down_high_vol_signals.shape[0]),
            "trading_days_if_aggressive_allowed": int(down_high_vol_signals["Date"].nunique()) if not down_high_vol_signals.empty else 0,
            "fill_grid": fill_summary(down_high_vol_signals),
            "daily_portfolio_vs_no_trade": daily_portfolio_summary(down_high_vol_signals),
        },
        "promoted_policy_active_only": {
            "signal_count": int(promoted_signals.shape[0]),
            "trading_days": int(promoted_signals["Date"].nunique()) if not promoted_signals.empty else 0,
            "fill_grid": fill_summary(promoted_signals),
            "daily_portfolio": daily_portfolio_summary(promoted_signals),
        },
        "verdict": {
            "fill_model": "0.5% 기본 limit은 체결률을 올리지만 OHLC proxy상으로도 미체결 리스크가 남습니다. live에는 fill-adjusted paper 성과를 별도 추적해야 합니다.",
            "regime": "down_high_vol은 promoted 정책이 거부한 regime이며, aggressive 예외는 표본/체결/forward return을 더 쌓기 전까지 live 기본 정책으로 승격하면 안 됩니다.",
            "live_policy": "promoted NO_TRADE이면 aggressive 후보는 paper/manual-draft만 허용하고, live는 별도 엄격 gate가 필요합니다.",
        },
    }

    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def md_fill_table(rows: list[dict[str, Any]]) -> str:
        lines = ["| limit aggressiveness | signals | fill proxy | fill rate | filled 1d avg | filled 5d avg |", "|---:|---:|---:|---:|---:|---:|"]
        for r in rows:
            lines.append(
                f"| {r['aggressiveness_pct']*100:.1f}% | {r['signals']} | {r['fill_proxy_count']} | {r['fill_proxy_rate_pct']}% | "
                f"{r['filled_ret_1d_from_entry']['avg_pct']}% | {r['filled_ret_5d_from_entry']['avg_pct']}% |"
            )
        return "\n".join(lines)

    dhv_daily = payload["down_high_vol_rejected_by_promoted"]["daily_portfolio_vs_no_trade"]
    md = f"""# Fill model + regime split research — 2026-07-03

Generated: `{payload['generated_at_utc']}`

## Scope

- Panel: `{PANEL}`
- Start: `{ANALYSIS_START}`
- Fill proxy: `next_low <= limit_price` on the next trading day after the signal date. Entry proxy is `next_open` if `next_open <= limit`, otherwise `limit`. This is **not** broker queue replay.

## Today live/paper divergence anchor

- Candidate situation: `{payload['today_actual']['candidate_situation']}`
- Candidate status: `{payload['today_actual']['candidate_status']}`
- Planned orders: `{payload['today_actual']['planned_order_count']}`
- Planned notional: `{payload['today_actual']['planned_total_notional_krw']}` KRW
- Existing read-only performance note: 3 planned orders, only Company K filled; KMW and VIGencell did not become positions.

## Aggressive policy — all approved situations

- Signals: `{payload['aggressive_all_situations']['signal_count']}`
- Trading days: `{payload['aggressive_all_situations']['trading_days']}`

{md_fill_table(payload['aggressive_all_situations']['fill_grid'])}

## down_high_vol — promoted rejects, aggressive would trade

- Promoted status: `{payload['down_high_vol_rejected_by_promoted']['status_in_promoted']}`
- Aggressive signals if allowed: `{payload['down_high_vol_rejected_by_promoted']['signal_count_if_aggressive_allowed']}`
- Aggressive trading days if allowed: `{payload['down_high_vol_rejected_by_promoted']['trading_days_if_aggressive_allowed']}`
- Daily 1d portfolio avg vs no-trade: `{dhv_daily.get('ret_1d_vs_no_trade', {}).get('avg_pct')}`%, win `{dhv_daily.get('ret_1d_vs_no_trade', {}).get('win_rate_pct')}`%
- Daily 5d portfolio avg vs no-trade: `{dhv_daily.get('ret_5d_vs_no_trade', {}).get('avg_pct')}`%, win `{dhv_daily.get('ret_5d_vs_no_trade', {}).get('win_rate_pct')}`%

{md_fill_table(payload['down_high_vol_rejected_by_promoted']['fill_grid'])}

## Promoted policy active-only comparison

- Signals: `{payload['promoted_policy_active_only']['signal_count']}`
- Trading days: `{payload['promoted_policy_active_only']['trading_days']}`

{md_fill_table(payload['promoted_policy_active_only']['fill_grid'])}

## Verdict

1. `down_high_vol`은 promoted가 거부한 regime이므로 live 기본 정책으로 쓰면 안 됩니다.
2. aggressive 후보는 0.5% limit에서도 paper 후보와 실체결 성과가 분리됩니다. 따라서 `candidate_return`과 `fill_adjusted_return`을 별도 ledger로 기록해야 합니다.
3. live 승격 전 조건은 최소 `fill model`, `regime split`, `bad-news veto`, `stale data guard`가 모두 통과해야 합니다.
4. 다음 구현은 `live submit` 앞단에 `promoted_policy_status == NO_TRADE`일 때 aggressive live submit 차단 사유를 명시하는 guard를 넣는 것입니다.

## Artifacts

- JSON: `{OUT_JSON}`
- Markdown: `{OUT_MD}`
"""
    OUT_MD.write_text(md, encoding="utf-8")
    print(f"WROTE_JSON={OUT_JSON}")
    print(f"WROTE_MD={OUT_MD}")
    print(json.dumps({
        "aggressive_signals": payload["aggressive_all_situations"]["signal_count"],
        "down_high_vol_signals": payload["down_high_vol_rejected_by_promoted"]["signal_count_if_aggressive_allowed"],
        "promoted_signals": payload["promoted_policy_active_only"]["signal_count"],
        "down_high_vol_daily_1d_avg_pct": dhv_daily.get("ret_1d_vs_no_trade", {}).get("avg_pct"),
        "down_high_vol_daily_5d_avg_pct": dhv_daily.get("ret_5d_vs_no_trade", {}).get("avg_pct"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
