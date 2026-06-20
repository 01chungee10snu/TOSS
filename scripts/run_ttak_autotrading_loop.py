from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

PANEL_CSV = ROOT / "reports" / "backtests" / "random500_seed20260607_2022-01-01_2025-12-31_ohlcv_panel.csv"
POLICY_CANDIDATES = [
    ROOT / "config" / "generated_policies" / "contextual_mon_fri_policy_seed20260607_walkforward_promoted.json",
    ROOT / "config" / "generated_policies" / "contextual_mon_fri_policy_seed20260607.json",
    ROOT / "config" / "generated_policies" / "contextual_daily_policy_seed20260607.json",
]
CANDIDATE_DIR = ROOT / "reports" / "trade_candidates"
SAMPLE_CSV = ROOT / "reports" / "backtests" / "random500_seed20260607_ma20_60_2022-01-01_2025-12-31_sample.csv"
DART_CONNECTOR = ROOT / "src" / "toss_alpha" / "connectors" / "dart_events.py"


def resolve_policy_json(candidates: list[Path] | None = None) -> Path:
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


def fast_phase(candidate_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    from toss_alpha.execution.fast_veto import evaluate_fast_veto

    candidate_payload = candidate_payload or {}
    orders = candidate_payload.get("orders", []) if isinstance(candidate_payload, dict) else []
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
    from toss_alpha.execution.qual_gate import evaluate_disclosure_gate

    connector_exists = DART_CONNECTOR.exists()
    api_key_present = bool(os.getenv("OPENDART_API_KEY"))
    candidate_payload = candidate_payload or {}
    orders = candidate_payload.get("orders", []) if isinstance(candidate_payload, dict) else []
    symbols = [str(order.get("symbol", "")).zfill(6) for order in orders if isinstance(order, dict) and order.get("symbol")]

    if symbols and not connector_exists:
        gate = {
            "status": "BLOCKED_QUAL_DATA",
            "reasons": ["missing_dart_connector"],
            "checked_symbols": [],
            "pending_symbols": symbols,
            "event_counts": {},
            "review_required_symbols": [],
            "fetch_errors": {},
        }
    else:
        fetcher = None
        if api_key_present and connector_exists and symbols:
            try:
                from toss_alpha.dart_adapter import recent_filings
                fetcher = recent_filings
            except Exception as exc:
                gate = {
                    "status": "BLOCKED_QUAL_DATA",
                    "reasons": ["missing_disclosure_fetcher"],
                    "checked_symbols": [],
                    "pending_symbols": symbols,
                    "event_counts": {},
                    "review_required_symbols": [],
                    "fetch_errors": {"_import": repr(exc)},
                }
            else:
                gate = evaluate_disclosure_gate(symbols=symbols, api_key_present=api_key_present, fetch_recent_filings=fetcher)
        else:
            gate = evaluate_disclosure_gate(symbols=symbols, api_key_present=api_key_present, fetch_recent_filings=fetcher)

    return {
        "connector_path": str(DART_CONNECTOR),
        "connector_exists": connector_exists,
        "opendart_api_key_present": api_key_present,
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


def choose_overall(quant: dict[str, Any], fast: dict[str, Any], qual: dict[str, Any], live: dict[str, Any]) -> str:
    if quant["status"] == "ACTIONABLE_CANDIDATES":
        if fast["status"] == "BLOCKED_FAST_VETO":
            return "BLOCKED_FAST_VETO"
        if fast["status"] == "BLOCKED_FAST_VETO_DATA":
            return "BLOCKED_FAST_VETO_DATA"
        return "ACTIONABLE_CANDIDATES"
    if quant["status"] == "NO_TRADE":
        return "NO_TRADE"
    if quant["status"].startswith("BLOCKED"):
        return quant["status"]
    if fast["status"].startswith("BLOCKED"):
        return fast["status"]
    if qual["status"].startswith("BLOCKED"):
        return qual["status"]
    return live["status"]


def summarize(payload: dict[str, Any]) -> str:
    quant = payload["quant"]
    fast = payload["fast"]
    qual = payload["qual"]
    live = payload["live"]
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
        lines.append(f"- order_count: {len(candidate_payload.get('orders', []))}")
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
        "## Qual",
        f"- status: {qual['status']}",
        f"- connector_exists: {qual['connector_exists']}",
        f"- opendart_api_key_present: {qual['opendart_api_key_present']}",
        f"- reasons: {qual['reasons']}",
        f"- checked_symbols: {qual.get('checked_symbols', [])}",
        f"- pending_symbols: {qual.get('pending_symbols', [])}",
        f"- review_required_symbols: {qual.get('review_required_symbols', [])}",
        f"- event_counts: {qual.get('event_counts', {})}",
        "",
        "## Live readiness",
        f"- status: {live['status']}",
        f"- ready: {live['ready']}",
        f"- default_mode: {live['default_mode']}",
        f"- dry_run_available: {live['dry_run_available']}",
        f"- missing: {live['missing']}",
        "",
        "## Notes",
        "- 정량은 엔진, 정성은 gate/veto, live는 readiness-only다.",
        "- 실주문은 수행하지 않는다.",
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
        "quant_reason": new_payload["quant"].get("reason"),
        "candidate_json": new_payload["quant"].get("candidate_json"),
    }
    tracked_old = {
        "overall_status": previous.get("overall_status"),
        "quant_status": previous.get("quant", {}).get("status"),
        "fast_status": previous.get("fast", {}).get("status"),
        "qual_status": previous.get("qual", {}).get("status"),
        "live_status": previous.get("live", {}).get("status"),
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
    fast = fast_phase(quant.get("candidate_payload") or {})
    qual = qual_phase(fast.get("effective_candidate_payload") or {})
    live = live_phase()
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "overall_status": choose_overall(quant, fast, qual, live),
        "quant": quant,
        "fast": fast,
        "qual": qual,
        "live": live,
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
        if quant.get('reason'):
            print(f"QUANT_REASON={quant['reason']}")
        candidate_payload = quant.get('candidate_payload') or {}
        if candidate_payload:
            print(f"CANDIDATE_STATUS={candidate_payload.get('status')}")
            print(f"CANDIDATE_COUNT={len(candidate_payload.get('orders', []))}")
            print(f"FAST_ALLOWED_COUNT={fast.get('allowed_count', 0)}")


if __name__ == "__main__":
    main()
