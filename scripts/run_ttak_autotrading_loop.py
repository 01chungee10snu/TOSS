from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
REPORT_DIR = ROOT / "reports" / "harness"
REPORT_DIR.mkdir(parents=True, exist_ok=True)
STATE_PATH = REPORT_DIR / "loop_state.json"
JSON_REPORT = REPORT_DIR / "latest_loop_report.json"
MD_REPORT = REPORT_DIR / "latest_loop_report.md"

DEFAULT_PANEL_CSV = ROOT / "reports" / "backtests" / "random500_seed20260607_2022-01-01_2026-latest_ohlcv_panel.csv"
FALLBACK_PANEL_CSV = ROOT / "reports" / "backtests" / "random500_seed20260607_2022-01-01_2025-12-31_ohlcv_panel.csv"
PANEL_CSV = Path(os.getenv("TOSS_PANEL_CSV", str(DEFAULT_PANEL_CSV))).expanduser()
POLICY_CANDIDATES = [
    ROOT / "config" / "generated_policies" / "contextual_mon_fri_policy_seed20260607_walkforward_promoted.json",
    ROOT / "config" / "generated_policies" / "contextual_mon_fri_policy_seed20260607.json",
    ROOT / "config" / "generated_policies" / "contextual_daily_policy_seed20260607.json",
]
CANDIDATE_DIR = ROOT / "reports" / "trade_candidates"
SAMPLE_CSV = ROOT / "reports" / "backtests" / "random500_seed20260607_ma20_60_2022-01-01_2025-12-31_sample.csv"
DART_CONNECTOR = ROOT / "src" / "toss_alpha" / "connectors" / "dart_events.py"
NEWS_EVENTS_JSON = ROOT / "reports" / "harness" / "manual_news_events.json"


def resolve_policy_json(candidates: list[Path] | None = None) -> Path:
    env_policy = os.getenv("TOSS_POLICY_JSON")
    if env_policy:
        return Path(env_policy).expanduser()
    for candidate in candidates or POLICY_CANDIDATES:
        if Path(candidate).exists():
            return Path(candidate)
    return Path((candidates or POLICY_CANDIDATES)[0])


POLICY_JSON = resolve_policy_json()


def load_policy(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_fast_veto_thresholds(path: Path) -> dict[str, float]:
    policy = load_policy(path)
    thresholds = ((policy.get("fast_veto_overlay") or {}).get("thresholds")) or {}
    return {
        "max_gap_pct": float(thresholds.get("max_gap_pct", 0.08)),
        "max_intraday_range_pct": float(thresholds.get("max_intraday_range_pct", 0.15)),
        "min_dollar_volume_krw": float(thresholds.get("min_dollar_volume_krw", 10_000_000.0)),
        "max_prev_volatility_20d": float(thresholds.get("max_prev_volatility_20d", 0.10)),
    }


def load_news_events(path: Path | None = None) -> tuple[list[dict[str, Any]], str | None, str | None]:
    raw_path = os.getenv("TOSS_NEWS_EVENTS_JSON")
    event_path = Path(raw_path).expanduser() if raw_path else (path or NEWS_EVENTS_JSON)
    if not event_path.exists():
        return [], str(event_path), None
    try:
        payload = json.loads(event_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [], str(event_path), repr(exc)
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)], str(event_path), None
    if isinstance(payload, dict):
        events = payload.get("events") or payload.get("news_events") or []
        if isinstance(events, list):
            return [item for item in events if isinstance(item, dict)], str(event_path), None
    return [], str(event_path), "unsupported_news_events_shape"


def run_cmd(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True, check=False)


def parse_marker(output: str, prefix: str) -> str | None:
    for line in output.splitlines():
        if line.startswith(prefix):
            return line.split("=", 1)[1].strip()
    return None


def quant_phase(build_missing_panel: bool, rebuild_policy_if_missing: bool, as_of: str | None) -> dict[str, Any]:
    phase: dict[str, Any] = {
        "status": "BLOCKED_QUANT_DATA",
        "panel_csv": str(PANEL_CSV),
        "panel_exists": PANEL_CSV.exists(),
        "sample_csv_exists": SAMPLE_CSV.exists(),
        "policy_json": str(POLICY_JSON),
        "policy_exists": POLICY_JSON.exists(),
        "candidate_json": None,
        "steps": [],
    }

    if not PANEL_CSV.exists():
        if build_missing_panel and SAMPLE_CSV.exists():
            step = run_cmd([str(VENV_PYTHON), "scripts/run_random500_daily_strategy_sweep.py"])
            phase["steps"].append({
                "name": "build_panel_via_daily_strategy_sweep",
                "exit_code": step.returncode,
                "stdout_tail": step.stdout.splitlines()[-20:],
                "stderr_tail": step.stderr.splitlines()[-20:],
            })
            phase["panel_exists"] = PANEL_CSV.exists()
        else:
            phase["reason"] = "missing_panel_csv"
            return phase

    if not POLICY_JSON.exists():
        if rebuild_policy_if_missing and PANEL_CSV.exists():
            step = run_cmd([str(VENV_PYTHON), "scripts/optimize_contextual_daily_strategy.py"])
            phase["steps"].append({
                "name": "rebuild_policy",
                "exit_code": step.returncode,
                "stdout_tail": step.stdout.splitlines()[-20:],
                "stderr_tail": step.stderr.splitlines()[-20:],
            })
            phase["policy_exists"] = POLICY_JSON.exists()
        else:
            phase["reason"] = "missing_policy_json"
            return phase

    if not PANEL_CSV.exists() or not POLICY_JSON.exists():
        phase["reason"] = "missing_quant_inputs_after_recovery"
        return phase

    cmd = [str(VENV_PYTHON), "scripts/generate_contextual_daily_candidates.py", "--policy", str(POLICY_JSON), "--panel", str(PANEL_CSV)]
    if as_of:
        cmd.extend(["--as-of", as_of])
    gen = run_cmd(cmd)
    candidate_json = parse_marker(gen.stdout, "CANDIDATES_JSON=")
    phase["steps"].append({
        "name": "generate_candidates",
        "exit_code": gen.returncode,
        "stdout_tail": gen.stdout.splitlines()[-20:],
        "stderr_tail": gen.stderr.splitlines()[-20:],
    })
    phase["candidate_json"] = candidate_json

    if gen.returncode != 0:
        phase["reason"] = "candidate_generation_failed"
        return phase

    payload: dict[str, Any] = {}
    if candidate_json and Path(candidate_json).exists():
        payload = json.loads(Path(candidate_json).read_text(encoding="utf-8"))
    phase["candidate_payload"] = payload
    status = payload.get("status", "UNKNOWN")
    if status == "CANDIDATES":
        phase["status"] = "ACTIONABLE_CANDIDATES"
    elif status == "NO_TRADE":
        phase["status"] = "NO_TRADE"
    else:
        phase["status"] = status
    return phase


def intraday_phase(candidate_payload: dict[str, Any], *, now: datetime | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Collect fresh KIS evidence and apply the unified intraday verdict."""
    from toss_alpha.connectors.kis_readonly import KisReadOnlyClient
    from toss_alpha.execution.intraday_decision import apply_intraday_decision, evaluate_intraday_decision
    from toss_alpha.execution.inverse_sleeve import maybe_apply_inverse_sleeve
    from toss_alpha.execution.live_ready import LiveExecutionConfig

    now = now or datetime.now(timezone.utc)
    source_regime = str(candidate_payload.get("source_situation") or candidate_payload.get("situation") or "unknown")
    issue_path = REPORT_DIR / "current_issues" / f"current_issue_risk_report_{now.astimezone(ZoneInfo('Asia/Seoul')).strftime('%Y%m%d')}.json"
    issue = {}
    try:
        issue = json.loads(issue_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    news_severity = str(issue.get("severity") or "unknown")
    audit: dict[str, Any] = {
        "generated_at_utc": now.isoformat(),
        "daily_regime": source_regime,
        "news_severity": news_severity,
        "issue_path": str(issue_path),
        "inverse_sleeve": None,
    }
    inverse_realtime_quote: dict[str, Any] | None = None
    try:
        cfg = LiveExecutionConfig.from_env(os.environ)
        missing = [name for name, value in {
            "app_key": cfg.app_key,
            "app_secret": cfg.app_secret,
            "cano": cfg.cano,
            "account_product_code": cfg.account_product_code,
        }.items() if not value]
        if cfg.provider != "kis" or missing:
            raise RuntimeError(f"intraday_kis_config_unavailable:{','.join(missing) or cfg.provider}")
        client = KisReadOnlyClient(
            app_key=cfg.app_key,
            app_secret=cfg.app_secret,
            cano=cfg.cano,
            account_product_code=cfg.account_product_code or "01",
            mock_trading=cfg.kis_mock_trading,
            base_url=cfg.base_url,
            timeout=cfg.timeout,
        )
        broker_positions = [p for p in client.position_snapshots() if float(p.quantity or 0) > 0]
        inverse_symbol = str(os.environ.get("TOSS_INVERSE_ETF_CODE", "114800")).strip().zfill(6)
        symbols = {"069500", inverse_symbol, *(str(p.symbol).zfill(6) for p in broker_positions)}
        quotes = {symbol: _kis_quote_evidence(client.quote(symbol), symbol=symbol, observed_at=now) for symbol in symbols}
        inverse_quote = quotes.get(inverse_symbol) or {}
        inverse_realtime_quote = {
            "close": inverse_quote.get("last"),
            "volume": inverse_quote.get("volume"),
            "price_date": now.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat(),
            "observed_at": inverse_quote.get("observed_at"),
            "source": inverse_quote.get("source"),
        }
        positions = []
        for position in broker_positions:
            symbol = str(position.symbol).zfill(6)
            quote = quotes.get(symbol) or {}
            positions.append({
                "symbol": symbol,
                "quantity": position.quantity,
                "sellable_quantity": position.sellable_quantity,
                "avg_price": position.avg_price,
                "last": quote.get("last"),
                "open": quote.get("open"),
                "prev_close": quote.get("prev_close"),
            })
        decision = evaluate_intraday_decision(
            daily_regime=source_regime,
            news_severity=news_severity,
            market_quotes=quotes,
            positions=positions,
            now=now,
            max_quote_age_seconds=int(os.environ.get("TOSS_INTRADAY_MAX_QUOTE_AGE_SECONDS", "300")),
            news_observed_at=issue.get("generated_at_utc") or issue.get("generated_at_kst"),
            max_news_age_seconds=int(os.environ.get("TOSS_INTRADAY_MAX_NEWS_AGE_SECONDS", "1200")),
            require_fresh_news=True,
            inverse_symbol=inverse_symbol,
        )
        audit["quote_symbols"] = sorted(quotes)
        audit["position_symbols"] = sorted(str(p.symbol).zfill(6) for p in broker_positions)
    except Exception as exc:
        decision = {
            "generated_at_utc": now.isoformat(),
            "verdict": "NO_TRADE",
            "reason": "intraday_collection_failed",
            "evidence_status": "MISSING",
            "signal_conflict": False,
            "regime_liquidation_allowed": False,
            "market_regime": None,
            "sell_symbols": [],
            "exception_type": type(exc).__name__,
            "exception": str(exc),
        }
    filtered = apply_intraday_decision(candidate_payload, decision)
    transformed, inverse_audit = maybe_apply_inverse_sleeve(
        filtered,
        out_dir=CANDIDATE_DIR,
        env=os.environ,
        realtime_quote=inverse_realtime_quote,
        original_candidate_json=None,
    )
    audit["decision"] = decision
    audit["inverse_sleeve"] = inverse_audit
    audit["status"] = decision.get("verdict")
    return transformed, audit


def _kis_quote_evidence(payload: Mapping[str, Any], *, symbol: str, observed_at: datetime) -> dict[str, Any]:
    body = payload.get("json") if isinstance(payload, Mapping) else None
    record: Mapping[str, Any] = {}
    if isinstance(body, Mapping):
        output = body.get("output")
        if isinstance(output, Mapping):
            record = output
        elif isinstance(output, list) and output and isinstance(output[0], Mapping):
            record = output[0]
    def number(*keys: str) -> float | None:
        for key in keys:
            value = record.get(key)
            if value not in (None, ""):
                try:
                    return float(str(value).replace(",", ""))
                except ValueError:
                    continue
        return None
    last = number("stck_prpr", "last", "price")
    prev_close = number("stck_sdpr", "prev_close")
    if prev_close is None and last is not None:
        change = number("prdy_vrss")
        if change is not None:
            prev_close = last - change
    return {
        "symbol": str(symbol).zfill(6),
        "last": last,
        "open": number("stck_oprc", "open"),
        "prev_close": prev_close,
        "volume": number("acml_vol", "volume"),
        "observed_at": observed_at.isoformat(),
        "source": "kis_realtime_quote",
    }


def fast_phase(candidate_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    from toss_alpha.execution.fast_veto import evaluate_fast_veto

    candidate_payload = candidate_payload or {}
    orders = candidate_payload.get("orders", []) if isinstance(candidate_payload, dict) else []
    if candidate_payload.get("strategy_type") == "inverse_sleeve":
        decision = candidate_payload.get("intraday_decision") if isinstance(candidate_payload.get("intraday_decision"), dict) else {}
        min_dollar_volume = float(os.getenv("TOSS_MIN_LIVE_DOLLAR_VOLUME_KRW", "1000000000"))
        reasons_by_symbol: dict[str, list[str]] = {}
        allowed_orders: list[dict[str, Any]] = []
        for order in orders:
            symbol = str(order.get("symbol", "")).zfill(6)
            reasons: list[str] = []
            if str(order.get("quote_source") or "") != "kis_realtime_quote":
                reasons.append("inverse_quote_source_not_kis_realtime")
            try:
                if float(order.get("current_price") or 0) <= 0:
                    reasons.append("inverse_current_price_missing")
                if float(order.get("dollar_volume_krw") or order.get("dollar_volume") or 0) < min_dollar_volume:
                    reasons.append("inverse_dollar_volume_below_minimum")
            except (TypeError, ValueError):
                reasons.append("inverse_quote_metrics_invalid")
            if str(decision.get("evidence_status") or "").upper() != "FRESH" or str(decision.get("verdict") or "").upper() != "INVERSE_BUY":
                reasons.append("inverse_intraday_authorization_missing")
            if reasons:
                reasons_by_symbol[symbol] = reasons
            else:
                allowed_orders.append(dict(order))
        blocked = bool(reasons_by_symbol)
        effective = dict(candidate_payload)
        effective["orders"] = [] if blocked else allowed_orders
        if blocked:
            effective["status"] = "NO_TRADE"
            effective["reason"] = "inverse_fast_veto_blocked"
        return {
            "status": "BLOCKED_FAST_VETO_DATA" if blocked else "READY",
            "reasons": sorted({reason for values in reasons_by_symbol.values() for reason in values}),
            "checked_symbols": [str(order.get("symbol", "")).zfill(6) for order in orders if isinstance(order, dict)],
            "vetoed_symbols": sorted(reasons_by_symbol),
            "reasons_by_symbol": reasons_by_symbol,
            "allowed_orders": [] if blocked else allowed_orders,
            "effective_candidate_payload": effective,
            "panel_csv": str(PANEL_CSV),
            "panel_exists": PANEL_CSV.exists(),
            "policy_json": str(POLICY_JSON),
            "allowed_count": 0 if blocked else len(allowed_orders),
            "original_order_count": len(orders),
            "thresholds": {"min_dollar_volume_krw": min_dollar_volume},
            "bypass_reason": "inverse_sleeve_uses_realtime_etf_gate_not_universe_panel",
        }
    if not PANEL_CSV.exists():
        return {
            "status": "BLOCKED_FAST_VETO_DATA",
            "reasons": ["missing_panel_csv"],
            "checked_symbols": [],
            "vetoed_symbols": [],
            "reasons_by_symbol": {},
            "allowed_orders": [],
            "effective_candidate_payload": {**candidate_payload, "orders": []},
        }
    panel = pd.read_csv(PANEL_CSV, dtype={"code": str}, parse_dates=["Date"], usecols=["Date", "code", "Open", "High", "Low", "Close", "Volume"])
    as_of = str(candidate_payload.get("as_of") or panel["Date"].max().date())
    thresholds = load_fast_veto_thresholds(POLICY_JSON) if POLICY_JSON.exists() else {
        "max_gap_pct": 0.08,
        "max_intraday_range_pct": 0.15,
        "min_dollar_volume_krw": 10_000_000.0,
        "max_prev_volatility_20d": 0.10,
    }
    gate = evaluate_fast_veto(candidate_payload=candidate_payload, panel=panel, as_of=as_of, **thresholds)
    effective = dict(candidate_payload)
    effective["orders"] = list(gate.get("allowed_orders", []))
    if candidate_payload.get("status") == "CANDIDATES" and not effective["orders"]:
        effective["status"] = "NO_TRADE"
        effective["reason"] = "fast_veto_blocked_all_candidates"
    elif candidate_payload.get("status") == "CANDIDATES" and effective["orders"]:
        effective["status"] = "CANDIDATES"
    return {
        "panel_csv": str(PANEL_CSV),
        "panel_exists": True,
        **gate,
        "policy_json": str(POLICY_JSON),
        "allowed_count": len(gate.get("allowed_orders", [])),
        "original_order_count": len(orders),
        "effective_candidate_payload": effective,
    }


def qual_phase(candidate_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    from toss_alpha.execution.qual_gate import evaluate_multi_source_qual_gate

    connector_exists = DART_CONNECTOR.exists()
    api_key_present = bool(os.getenv("OPENDART_API_KEY"))
    require_opendart = os.getenv("TOSS_REQUIRE_OPENDART", "0").strip().lower() in {"1", "true", "yes", "y"}
    news_events, news_events_path, news_events_error = load_news_events()
    candidate_payload = candidate_payload or {}
    orders = candidate_payload.get("orders", []) if isinstance(candidate_payload, dict) else []
    symbols = [str(order.get("symbol", "")).zfill(6) for order in orders if isinstance(order, dict) and order.get("symbol")]
    if candidate_payload.get("strategy_type") == "inverse_sleeve":
        decision = candidate_payload.get("intraday_decision") if isinstance(candidate_payload.get("intraday_decision"), dict) else {}
        evidence_ready = (
            str(decision.get("evidence_status") or "").upper() == "FRESH"
            and str(decision.get("news_evidence_status") or "").upper() == "FRESH"
            and str(decision.get("verdict") or "").upper() == "INVERSE_BUY"
            and not bool(decision.get("signal_conflict"))
        )
        reasons = [] if evidence_ready else ["inverse_intraday_or_news_evidence_not_ready"]
        return {
            "status": "READY" if evidence_ready else "BLOCKED_QUAL_DATA",
            "connector_path": str(DART_CONNECTOR),
            "connector_exists": connector_exists,
            "opendart_api_key_present": api_key_present,
            "require_opendart": require_opendart,
            "news_events_path": str(NEWS_EVENTS_JSON),
            "news_events_count": 0,
            "news_events_error": None,
            "reasons": reasons,
            "checked_symbols": symbols,
            "pending_symbols": [] if evidence_ready else symbols,
            "blocked_symbols": [] if evidence_ready else symbols,
            "review_required_symbols": [],
            "event_counts": {},
            "sources": {"inverse_sleeve": {"status": "READY" if evidence_ready else "BLOCKED", "reason": "etf_sleeve_bypasses_single_stock_dart_only_after_fresh_intraday_news_gate"}},
        }

    fetcher = None
    import_error = None
    if api_key_present and connector_exists and symbols:
        try:
            from toss_alpha.dart_adapter import recent_filings
            fetcher = recent_filings
        except Exception as exc:
            import_error = repr(exc)

    gate = evaluate_multi_source_qual_gate(
        symbols=symbols,
        opendart_api_key_present=api_key_present,
        fetch_recent_filings=fetcher,
        news_events=news_events,
        require_opendart=require_opendart,
    )
    if import_error:
        gate.setdefault("sources", {}).setdefault("opendart", {})["fetch_errors"] = {"_import": import_error}
    if news_events_error:
        gate.setdefault("sources", {}).setdefault("news_events", {})["load_error"] = news_events_error

    return {
        "connector_path": str(DART_CONNECTOR),
        "connector_exists": connector_exists,
        "opendart_api_key_present": api_key_present,
        "require_opendart": require_opendart,
        "news_events_path": news_events_path,
        "news_events_count": len(news_events),
        "news_events_error": news_events_error,
        **gate,
    }


def live_phase() -> dict[str, Any]:
    cmd = [str(VENV_PYTHON), "-m", "toss_alpha.cli", "live-readiness"]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=False, env=env)
    missing = []
    ready = None
    default_mode = None
    dry_run_available = None
    in_missing = False
    for line in proc.stdout.splitlines():
        if line.startswith("ready:"):
            ready = line.split(":", 1)[1].strip()
        elif line.startswith("default_mode:"):
            default_mode = line.split(":", 1)[1].strip()
        elif line.startswith("missing:"):
            in_missing = True
        elif in_missing and line.startswith("- "):
            missing.append(line[2:].strip())
        elif line.startswith("dry_run_available:"):
            dry_run_available = line.split(":", 1)[1].strip()
            in_missing = False
    return {
        "status": "LIVE_READY" if ready == "True" else "LIVE_BLOCKED",
        "ready": ready,
        "default_mode": default_mode,
        "missing": missing,
        "dry_run_available": dry_run_available,
        "exit_code": proc.returncode,
        "stdout_tail": proc.stdout.splitlines()[-20:],
        "stderr_tail": proc.stderr.splitlines()[-20:],
    }


def position_exit_phase(candidate_payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    from toss_alpha.execution.position_exit import append_position_exit_orders

    return append_position_exit_orders(candidate_payload, report_dir=REPORT_DIR, env=os.environ)


def live_submit_phase(candidate_payload: dict[str, Any], qual: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
    from toss_alpha.execution.live_submit import run_live_submit_phase

    return run_live_submit_phase(
        candidate_payload=candidate_payload,
        qual=qual,
        live=live,
        report_dir=REPORT_DIR,
        env=os.environ,
    )


def choose_overall(quant: dict[str, Any], fast: dict[str, Any], qual: dict[str, Any], live: dict[str, Any], submit: dict[str, Any] | None = None, position_exit: dict[str, Any] | None = None) -> str:
    submit_status = str((submit or {}).get("status") or "")
    if submit_status and ("UNKNOWN" in submit_status or "ERROR" in submit_status):
        return submit_status
    if submit_status.startswith("BLOCKED") or submit_status == "LIVE_SUBMIT_BLOCKED":
        return submit_status
    if submit_status == "LIVE_SUBMITTED":
        return submit_status
    if quant["status"].startswith("BLOCKED"):
        return quant["status"]
    if fast["status"].startswith("BLOCKED"):
        return fast["status"]
    if qual["status"].startswith("BLOCKED"):
        return qual["status"]
    if position_exit and int(position_exit.get("sell_order_count") or 0) > 0:
        return "ACTIONABLE_CANDIDATES"
    if quant["status"] == "ACTIONABLE_CANDIDATES":
        return "ACTIONABLE_CANDIDATES"
    if quant["status"] == "NO_TRADE":
        return "NO_TRADE"
    return live["status"]


def summarize(payload: dict[str, Any]) -> str:
    quant = payload["quant"]
    fast = payload["fast"]
    qual = payload["qual"]
    live = payload["live"]
    submit = payload.get("live_submit") or {}
    position_exit = payload.get("position_exit") or {}
    lines = [
        f"# TOSS ttak autotrading loop report",
        "",
        f"- generated_at_utc: {payload['generated_at_utc']}",
        f"- overall_status: {payload['overall_status']}",
        "",
        "## Quant",
        f"- status: {quant['status']}",
        f"- panel_exists: {quant['panel_exists']}",
        f"- policy_exists: {quant['policy_exists']}",
        f"- policy_json: {quant.get('policy_json')}",
        f"- candidate_json: {quant.get('candidate_json')}",
    ]
    candidate_payload = quant.get("candidate_payload") or {}
    if candidate_payload:
        lines.append(f"- candidate_status: {candidate_payload.get('status')}")
        lines.append(f"- candidate_situation: {candidate_payload.get('situation')}")
        lines.append(f"- strategy_type: {candidate_payload.get('strategy_type')}")
        lines.append(f"- order_count: {len(candidate_payload.get('orders', []))}")
    inverse = quant.get("inverse_sleeve") or {}
    if inverse:
        lines.append(f"- inverse_sleeve: applied={inverse.get('applied')} reason={inverse.get('reason')}")
        if inverse.get("candidate_json"):
            lines.append(f"- inverse_candidate_json: {inverse.get('candidate_json')}")
    if quant.get("reason"):
        lines.append(f"- reason: {quant['reason']}")
    lines.extend([
        "",
        "## Fast veto",
        f"- status: {fast['status']}",
        f"- policy_json: {fast.get('policy_json')}",
        f"- thresholds: {fast.get('thresholds', {})}",
        f"- reasons: {fast.get('reasons', [])}",
        f"- checked_symbols: {fast.get('checked_symbols', [])}",
        f"- vetoed_symbols: {fast.get('vetoed_symbols', [])}",
        f"- allowed_count: {fast.get('allowed_count', 0)} / {fast.get('original_order_count', 0)}",
        f"- reasons_by_symbol: {fast.get('reasons_by_symbol', {})}",
        "",
        "## Position exit",
        f"- enabled: {position_exit.get('enabled')}",
        f"- status_reason: {position_exit.get('reason')}",
        f"- positions_checked: {position_exit.get('positions_checked', 0)}",
        f"- sell_order_count: {position_exit.get('sell_order_count', 0)}",
        f"- stop_loss_pct: {position_exit.get('stop_loss_pct')}",
        f"- take_profit_pct: {position_exit.get('take_profit_pct')}",
        f"- trailing_stop_pct: {position_exit.get('trailing_stop_pct')}",
        f"- max_holding_trading_days: {position_exit.get('max_holding_trading_days')}",
        f"- max_positions_limit: {position_exit.get('max_positions_limit')}",
        f"- equity_guard: {(position_exit.get('equity_guard') or {}).get('status')}",
        f"- equity_guard_threshold_pct: {(position_exit.get('equity_guard') or {}).get('threshold_pct')}",
        f"- equity_guard_cooldown_seconds: {(position_exit.get('equity_guard') or {}).get('cooldown_seconds')}",
        f"- equity_guard_cooldown_unit: {(position_exit.get('equity_guard') or {}).get('cooldown_unit')}",
        f"- equity_guard_drawdown_pct: {(position_exit.get('equity_guard') or {}).get('drawdown_pct')}",
        f"- equity_guard_block_new_buys: {(position_exit.get('equity_guard') or {}).get('block_new_buys')}",
        f"- equity_guard_liquidation_required: {(position_exit.get('equity_guard') or {}).get('liquidation_required')}",
        f"- report_path: {position_exit.get('report_path')}",
        "",
        "## Qual",
        f"- status: {qual['status']}",
        f"- connector_exists: {qual['connector_exists']}",
        f"- opendart_api_key_present: {qual['opendart_api_key_present']}",
        f"- require_opendart: {qual.get('require_opendart')}",
        f"- news_events_path: {qual.get('news_events_path')}",
        f"- news_events_count: {qual.get('news_events_count')}",
        f"- news_events_error: {qual.get('news_events_error')}",
        f"- reasons: {qual['reasons']}",
        f"- checked_symbols: {qual.get('checked_symbols', [])}",
        f"- pending_symbols: {qual.get('pending_symbols', [])}",
        f"- blocked_symbols: {qual.get('blocked_symbols', [])}",
        f"- review_required_symbols: {qual.get('review_required_symbols', [])}",
        f"- event_counts: {qual.get('event_counts', {})}",
        f"- source_statuses: { {name: source.get('status') for name, source in qual.get('sources', {}).items()} }",
        "",
        "## Live readiness",
        f"- status: {live['status']}",
        f"- ready: {live['ready']}",
        f"- default_mode: {live['default_mode']}",
        f"- dry_run_available: {live['dry_run_available']}",
        f"- missing: {live['missing']}",
        "",
        "## Live submit",
        f"- status: {submit.get('status')}",
        f"- dry_run: {submit.get('dry_run')}",
        f"- submit_enabled: {submit.get('submit_enabled')}",
        f"- order_count: {submit.get('order_count')}",
        f"- attempted_count: {submit.get('attempted_count')}",
        f"- submitted_count: {submit.get('submitted_count')}",
        f"- blocked_count: {submit.get('blocked_count')}",
        f"- violations: {submit.get('violations', [])}",
        f"- artifact_path: {submit.get('artifact_path')}",
        f"- ledger_path: {submit.get('ledger_path')}",
        "",
        "## Notes",
        "- 정량은 엔진, 정성은 gate/veto, live는 readiness, live-submit은 triple opt-in guarded executor다.",
        "- 기본값은 실주문 미제출이며 dry-run/disabled artifact만 남긴다.",
    ])
    return "\n".join(lines) + "\n"


def should_emit(new_payload: dict[str, Any], previous: dict[str, Any] | None, force_emit: bool) -> bool:
    if force_emit or previous is None:
        return True
    tracked_new = {
        "overall_status": new_payload["overall_status"],
        "quant_status": new_payload["quant"]["status"],
        "fast_status": new_payload["fast"]["status"],
        "qual_status": new_payload["qual"]["status"],
        "live_status": new_payload["live"]["status"],
        "live_submit_status": new_payload.get("live_submit", {}).get("status"),
        "position_exit_sell_order_count": new_payload.get("position_exit", {}).get("sell_order_count"),
        "quant_reason": new_payload["quant"].get("reason"),
        "candidate_json": new_payload["quant"].get("candidate_json"),
    }
    tracked_old = {
        "overall_status": previous.get("overall_status"),
        "quant_status": previous.get("quant", {}).get("status"),
        "fast_status": previous.get("fast", {}).get("status"),
        "qual_status": previous.get("qual", {}).get("status"),
        "live_status": previous.get("live", {}).get("status"),
        "live_submit_status": previous.get("live_submit", {}).get("status"),
        "position_exit_sell_order_count": previous.get("position_exit", {}).get("sell_order_count"),
        "quant_reason": previous.get("quant", {}).get("reason"),
        "candidate_json": previous.get("quant", {}).get("candidate_json"),
    }
    if tracked_new != tracked_old:
        return True
    if new_payload["overall_status"] == "ACTIONABLE_CANDIDATES":
        return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Run TOSS ttak research harness loop. No live orders.")
    parser.add_argument("--build-missing-panel", action="store_true")
    parser.add_argument("--rebuild-policy-if-missing", action="store_true")
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--force-emit", action="store_true")
    args = parser.parse_args()

    quant = quant_phase(args.build_missing_panel, args.rebuild_policy_if_missing, args.as_of)
    intraday_candidate_payload, intraday = intraday_phase(quant.get("candidate_payload") or {})
    quant["candidate_payload"] = intraday_candidate_payload
    inverse_audit = intraday.get("inverse_sleeve") or {}
    quant["inverse_sleeve"] = inverse_audit
    if inverse_audit.get("applied") and inverse_audit.get("candidate_json"):
        quant["candidate_json"] = str(inverse_audit["candidate_json"])
    candidate_status = str(intraday_candidate_payload.get("status") or "UNKNOWN")
    quant["status"] = "ACTIONABLE_CANDIDATES" if candidate_status == "CANDIDATES" else candidate_status
    fast = fast_phase(intraday_candidate_payload)
    execution_candidate_payload, position_exit = position_exit_phase(fast.get("effective_candidate_payload") or {})
    qual = qual_phase(execution_candidate_payload)
    live = live_phase()
    submit = live_submit_phase(execution_candidate_payload, qual, live)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "overall_status": choose_overall(quant, fast, qual, live, submit, position_exit),
        "quant": quant,
        "intraday": intraday,
        "fast": fast,
        "position_exit": position_exit,
        "execution_candidate_payload": execution_candidate_payload,
        "qual": qual,
        "live": live,
        "live_submit": submit,
    }
    JSON_REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    MD_REPORT.write_text(summarize(payload), encoding="utf-8")

    previous = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else None
    STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if should_emit(payload, previous, args.force_emit):
        print(f"TOSS loop status: {payload['overall_status']}")
        print(f"REPORT_JSON={JSON_REPORT}")
        print(f"REPORT_MD={MD_REPORT}")
        print(f"QUANT_STATUS={quant['status']}")
        print(f"FAST_STATUS={fast['status']}")
        print(f"QUAL_STATUS={qual['status']}")
        print(f"LIVE_STATUS={live['status']}")
        print(f"LIVE_SUBMIT_STATUS={submit['status']}")
        print(f"POSITION_EXIT_SELL_COUNT={position_exit.get('sell_order_count', 0)}")
        if quant.get('reason'):
            print(f"QUANT_REASON={quant['reason']}")
        candidate_payload = quant.get('candidate_payload') or {}
        if candidate_payload:
            print(f"CANDIDATE_STATUS={candidate_payload.get('status')}")
            print(f"STRATEGY_TYPE={candidate_payload.get('strategy_type')}")
            print(f"CANDIDATE_COUNT={len(candidate_payload.get('orders', []))}")
            print(f"FAST_ALLOWED_COUNT={fast.get('allowed_count', 0)}")


if __name__ == "__main__":
    main()
