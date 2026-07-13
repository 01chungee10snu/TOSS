from __future__ import annotations

import argparse
import csv
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_POLICY = ROOT / "config" / "generated_policies" / "contextual_mon_fri_policy_seed20260607_walkforward_promoted.json"
DEFAULT_SEARCH_STATE = ROOT / "reports" / "harness" / "ml_trade_condition_5h_search_latest.json"
DEFAULT_SEARCH_AGG = ROOT / "reports" / "harness" / "ml_trade_condition_5h_search_20260707T235549_agg.csv"
DEFAULT_SEARCH_ROWS = ROOT / "reports" / "harness" / "ml_trade_condition_5h_search_20260707T235549.csv"
DEFAULT_OUT_POLICY = ROOT / "config" / "generated_policies" / "ml_trade_condition_loss_averse_promoted_20260707.json"
DEFAULT_OUT_AUDIT = ROOT / "reports" / "harness" / "ml_trade_condition_loss_averse_promotion_audit_20260707.json"
DEFAULT_OUT_MD = ROOT / "reports" / "harness" / "ml_trade_condition_loss_averse_promotion_audit_20260707.md"

BEST_CONFIG = {
    "strategy": "fusion_p0",
    "max_notional": 100_000,
    "max_positions": 3,
    "cash_fraction_per_entry": 0.15,
    "stop_loss_pct": 0.10,
    "take_profit_pct": 0.08,
    "trailing_stop_pct": 0.05,
    "max_holding_steps": 5,
    "max_equity_drawdown_stop_pct": 0.08,
    "risk_cooldown_steps": 12,
}

EXPECTED_SUMMARY = {
    "mean_return": 28.083333333333332,
    "min_return": 25.0,
    "max_mdd": -5.98,
    "mean_sharpe": 5.985600000000001,
    "total_trades": 153,
    "min_trades": 35,
    "loss_averse_score": 60.22816666666668,
}

BASELINE_SUMMARY = {
    "mean_return": 40.623333333333335,
    "min_return": 18.5,
    "max_mdd": -8.39,
    "mean_sharpe": 4.745566666666666,
    "total_trades": 205,
    "min_trades": 47,
    "loss_averse_score": 45.31083333333332,
}

ENV_MAPPING = {
    "TOSS_MAX_ORDER_KRW": "max_notional",
    "TOSS_MAX_POSITIONS": "max_positions",
    "TOSS_POSITION_STOP_LOSS_PCT": "stop_loss_pct",
    "TOSS_POSITION_TAKE_PROFIT_PCT": "take_profit_pct",
    "TOSS_POSITION_TRAILING_STOP_PCT": "trailing_stop_pct",
    "TOSS_POSITION_MAX_HOLDING_DAYS": "max_holding_steps",
    "TOSS_EQUITY_DRAWDOWN_STOP_PCT": "max_equity_drawdown_stop_pct",
    "TOSS_EQUITY_GUARD_COOLDOWN_DAYS": "risk_cooldown_steps",
}


def _same_config(row: dict[str, Any], cfg: dict[str, Any] = BEST_CONFIG) -> bool:
    for key, value in cfg.items():
        raw = row.get(key)
        if isinstance(value, str):
            if str(raw) != value:
                return False
        elif isinstance(value, int):
            if int(float(raw)) != value:
                return False
        else:
            if abs(float(raw) - float(value)) > 1e-12:
                return False
    return True


def _load_best_agg(path: Path) -> dict[str, Any]:
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    matches = [row for row in rows if _same_config(row)]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one best-config aggregate row, got {len(matches)}")
    row = dict(matches[0])
    for key in [*BEST_CONFIG.keys(), *EXPECTED_SUMMARY.keys()]:
        if key in row and key != "strategy":
            row[key] = float(row[key])
    return row


def _load_year_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    matches = [dict(row) for row in rows if _same_config(row)]
    if len(matches) != 3:
        raise RuntimeError(f"expected three year rows for best config, got {len(matches)}")
    for row in matches:
        for key, value in list(row.items()):
            if key == "strategy":
                continue
            try:
                row[key] = float(value)
            except (TypeError, ValueError):
                pass
        row["trade_year"] = int(float(row["trade_year"]))
    return sorted(matches, key=lambda r: int(r["trade_year"]))


def live_env_exports(config: dict[str, Any] = BEST_CONFIG) -> dict[str, str]:
    return {
        "TOSS_MAX_ORDER_KRW": str(int(config["max_notional"])),
        "TOSS_MAX_POSITIONS": str(int(config["max_positions"])),
        "TOSS_POSITION_STOP_LOSS_PCT": f"{float(config['stop_loss_pct']):.2f}",
        "TOSS_POSITION_TAKE_PROFIT_PCT": f"{float(config['take_profit_pct']):.2f}",
        "TOSS_POSITION_TRAILING_STOP_PCT": f"{float(config['trailing_stop_pct']):.2f}",
        "TOSS_POSITION_MAX_HOLDING_DAYS": str(int(config["max_holding_steps"])),
        "TOSS_EQUITY_DRAWDOWN_STOP_PCT": f"{float(config['max_equity_drawdown_stop_pct']):.2f}",
        "TOSS_EQUITY_GUARD_COOLDOWN_DAYS": str(int(config["risk_cooldown_steps"])),
    }


def build_promoted_policy(base_policy: dict[str, Any], *, search_state: Path, search_agg: Path, search_rows: Path, audit_path: Path) -> dict[str, Any]:
    policy = deepcopy(base_policy)
    max_notional = int(BEST_CONFIG["max_notional"])
    max_positions = int(BEST_CONFIG["max_positions"])
    policy["policy_id"] = "ml_trade_condition_loss_averse_promoted_20260707"
    policy["created_at_utc"] = datetime.now(timezone.utc).isoformat()
    policy["base_policy_source"] = str(DEFAULT_BASE_POLICY)
    policy["ml_trade_condition_promotion"] = {
        "status": "PROMOTABLE_AWAITING_FORWARD_LOOP_VERIFICATION",
        "search_state": str(search_state),
        "search_agg_csv": str(search_agg),
        "search_rows_csv": str(search_rows),
        "audit_report_json": str(audit_path),
        "best_config": BEST_CONFIG,
        "expected_summary": EXPECTED_SUMMARY,
        "baseline_summary": BASELINE_SUMMARY,
        "selection_rationale": "loss-averse score improved from 45.31 to 60.23 by reducing MDD and improving Sharpe/year stability; this is not the raw-return-max candidate.",
    }
    risk_gates = dict(policy.get("risk_gates") or {})
    risk_gates.update(
        {
            "max_positions": max_positions,
            "max_notional_krw_per_position": max_notional,
            "max_total_notional_krw": max_notional * max_positions,
            "cash_fraction_per_entry": float(BEST_CONFIG["cash_fraction_per_entry"]),
            "assumed_initial_cash_krw": 1_000_000,
            "require_manual_confirmation": True,
            "block_if_live_trading_enabled": True,
            "ml_trade_condition_source": "ml_trade_condition_loss_averse_promoted_20260707",
            "cash_fraction_note": "Backtest used min(max_notional, cash * cash_fraction_per_entry). With 1,000,000 KRW initial cash this candidate is max_notional-bound at entry (100k < 150k), but live sizing must be rechecked if account equity materially differs.",
        }
    )
    policy["risk_gates"] = risk_gates
    policy["live_env_exports_required"] = live_env_exports()
    policy["backtest_live_exit_parity"] = {
        "status": "MAPPED_REQUIRES_TESTED_WRAPPER_EXPORTS_BEFORE_LIVE_DEFAULT",
        "mapping": ENV_MAPPING,
        "notes": [
            "position_exit.py implements stop_loss, take_profit, trailing_stop, max_holding_days, risk_off exit, equity drawdown guard, and max_positions trimming.",
            "risk_cooldown_steps is mapped to wall-clock cooldown days in live mode to avoid intraday tick shrinkage.",
            "Real live submit remains guarded by readiness, qual, freshness, KRX time, duplicate ledger, and explicit confirmation gates.",
        ],
    }
    policy["promotion_verdict"] = "PROMOTED_FOR_PAPER_OR_MANUAL_DRAFT_ONLY"
    policy["disclaimer"] = "Research/paper/manual-draft policy artifact. Does not enable live orders by itself. Live wrapper must export live_env_exports_required and pass guarded submit gates."
    return policy


def build_audit(*, state: dict[str, Any], best_agg: dict[str, Any], year_rows: list[dict[str, Any]], policy_path: Path) -> dict[str, Any]:
    checks = []
    checks.append({"name": "search_status_final", "passed": state.get("status") == "FINAL", "value": state.get("status")})
    checks.append({"name": "completed_configs_2400", "passed": int(state.get("completed_configs", 0)) == 2400, "value": state.get("completed_configs")})
    checks.append({"name": "row_count_7200", "passed": int(state.get("row_count", 0)) == 7200, "value": state.get("row_count")})
    for key, expected in EXPECTED_SUMMARY.items():
        actual = float(best_agg[key])
        tolerance = 1e-6 if abs(expected) < 100 else 1e-3
        checks.append({"name": f"best_{key}_matches_report", "passed": abs(actual - float(expected)) <= tolerance, "actual": actual, "expected": expected})
    checks.extend(
        [
            {"name": "all_years_positive", "passed": all(float(r["total_return_pct"]) > 0 for r in year_rows), "value": [r["total_return_pct"] for r in year_rows]},
            {"name": "all_years_have_trades", "passed": all(int(r["total_trades"]) > 0 for r in year_rows), "value": [r["total_trades"] for r in year_rows]},
            {"name": "mdd_gate_pass", "passed": min(float(r["max_drawdown_pct"]) for r in year_rows) >= -10.0, "value": [r["max_drawdown_pct"] for r in year_rows]},
        ]
    )
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "research_paper_manual_draft_only",
        "live_orders_enabled": False,
        "best_config": BEST_CONFIG,
        "best_aggregate": best_agg,
        "year_rows": year_rows,
        "baseline_summary": BASELINE_SUMMARY,
        "live_env_exports_required": live_env_exports(),
        "policy_json": str(policy_path),
        "checks": checks,
        "passed": all(row["passed"] for row in checks),
        "verdict": "PASS_PROMOTE_TO_PAPER_FORWARD_LOOP" if all(row["passed"] for row in checks) else "BLOCKED_AUDIT_CHECK_FAILED",
        "next_required_before_live_default": [
            "Run ttak loop with TOSS_POLICY_JSON pointing to the generated policy and dry-run/guarded submit only.",
            "Verify latest_position_exit_report.json shows stop_loss=0.10, take_profit=0.08, trailing_stop=0.05, max_holding_trading_days=5, equity guard threshold=0.08, cooldown days=12.",
            "Recheck cash_fraction parity if live account equity materially differs from the 1,000,000 KRW replay initial cash; this candidate is max_notional-bound at 1,000,000 KRW but may become cash-fraction-bound at lower equity.",
            "Treat risk_cooldown_steps=12 as an approximate wall-clock 12-day live cooldown unless a future patch implements exact KRX-trading-day cooldown expiry.",
            "Do not enable or rely on real submit unless qual/freshness/KRX/duplicate/risk gates pass and explicit live confirmation remains configured.",
        ],
        "parity_warnings": [
            {
                "field": "cash_fraction_per_entry",
                "status": "WATCH",
                "detail": "Backtest sizing uses min(max_notional, cash * cash_fraction_per_entry). The generated policy stores cash_fraction_per_entry=0.15, but current candidate generation primarily enforces fixed max_notional/max_total budgets. At 1,000,000 KRW replay cash, max_notional=100,000 is stricter than 15% cash=150,000; live parity should be rechecked for materially smaller account equity.",
            },
            {
                "field": "risk_cooldown_steps",
                "status": "WATCH",
                "detail": "Backtest cooldown is step/trading-day based. Live guard currently maps TOSS_EQUITY_GUARD_COOLDOWN_DAYS to wall-clock days. This is safe-conservative enough for dry-run/paper promotion, but exact live parity would require KRX-trading-day expiry logic.",
            },
        ],
    }


def render_md(audit: dict[str, Any]) -> str:
    b = audit["best_aggregate"]
    lines = [
        "# ML Trade-Condition Promotion Audit — 2026-07-07",
        "",
        "Research/paper/manual-draft only. No broker calls. No live orders submitted.",
        "",
        f"- verdict: `{audit['verdict']}`",
        f"- audit_passed: `{audit['passed']}`",
        f"- policy_json: `{audit['policy_json']}`",
        "",
        "## Promoted candidate",
        f"- strategy: `{audit['best_config']['strategy']}`",
        f"- notional/maxpos/cf: `{int(audit['best_config']['max_notional']):,}` / `{audit['best_config']['max_positions']}` / `{audit['best_config']['cash_fraction_per_entry']}`",
        f"- exits: SL `{audit['best_config']['stop_loss_pct']:.0%}`, TP `{audit['best_config']['take_profit_pct']:.0%}`, TR `{audit['best_config']['trailing_stop_pct']:.0%}`, hold `{audit['best_config']['max_holding_steps']}`",
        f"- equity guard: `{audit['best_config']['max_equity_drawdown_stop_pct']:.0%}` / cooldown `{audit['best_config']['risk_cooldown_steps']}` days",
        "",
        "## Aggregate metrics",
        f"- mean_ret: `{float(b['mean_return']):.2f}%`",
        f"- min_ret: `{float(b['min_return']):.2f}%`",
        f"- max_mdd: `{float(b['max_mdd']):.2f}%`",
        f"- mean_sharpe: `{float(b['mean_sharpe']):.2f}`",
        f"- total_trades: `{int(float(b['total_trades']))}`",
        f"- score: `{float(b['loss_averse_score']):.2f}`",
        "",
        "## Year rows",
    ]
    for row in audit["year_rows"]:
        lines.append(f"- {int(row['trade_year'])}: ret `{float(row['total_return_pct']):.2f}%`, MDD `{float(row['max_drawdown_pct']):.2f}%`, trades `{int(row['total_trades'])}`, Sharpe `{float(row['sharpe_ratio']):.2f}`")
    lines.extend(["", "## Live env exports required"])
    for key, value in audit["live_env_exports_required"].items():
        lines.append(f"- `{key}={value}`")
    lines.extend(["", "## Parity warnings"])
    for warning in audit.get("parity_warnings", []):
        lines.append(f"- `{warning['field']}` / `{warning['status']}`: {warning['detail']}")
    lines.extend(["", "## Checks"])
    for check in audit["checks"]:
        lines.append(f"- [{'x' if check['passed'] else ' '}] {check['name']}")
    lines.extend(["", "## Next required before live default"])
    for item in audit["next_required_before_live_default"]:
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Promote the best ML trade-condition search candidate to a paper/manual-draft policy artifact.")
    parser.add_argument("--base-policy", default=str(DEFAULT_BASE_POLICY))
    parser.add_argument("--search-state", default=str(DEFAULT_SEARCH_STATE))
    parser.add_argument("--search-agg", default=str(DEFAULT_SEARCH_AGG))
    parser.add_argument("--search-rows", default=str(DEFAULT_SEARCH_ROWS))
    parser.add_argument("--out-policy", default=str(DEFAULT_OUT_POLICY))
    parser.add_argument("--out-audit", default=str(DEFAULT_OUT_AUDIT))
    parser.add_argument("--out-md", default=str(DEFAULT_OUT_MD))
    args = parser.parse_args()

    base_policy_path = Path(args.base_policy)
    search_state_path = Path(args.search_state)
    search_agg_path = Path(args.search_agg)
    search_rows_path = Path(args.search_rows)
    out_policy = Path(args.out_policy)
    out_audit = Path(args.out_audit)
    out_md = Path(args.out_md)

    base_policy = json.loads(base_policy_path.read_text(encoding="utf-8"))
    state = json.loads(search_state_path.read_text(encoding="utf-8"))
    best_agg = _load_best_agg(search_agg_path)
    year_rows = _load_year_rows(search_rows_path)

    policy = build_promoted_policy(base_policy, search_state=search_state_path, search_agg=search_agg_path, search_rows=search_rows_path, audit_path=out_audit)
    audit = build_audit(state=state, best_agg=best_agg, year_rows=year_rows, policy_path=out_policy)

    out_policy.parent.mkdir(parents=True, exist_ok=True)
    out_audit.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_policy.write_text(json.dumps(policy, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    out_audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    out_md.write_text(render_md(audit), encoding="utf-8")

    print(f"PROMOTED_POLICY_JSON={out_policy}")
    print(f"AUDIT_JSON={out_audit}")
    print(f"AUDIT_MD={out_md}")
    print(f"VERDICT={audit['verdict']}")
    print(f"AUDIT_PASSED={audit['passed']}")


if __name__ == "__main__":
    main()
