#!/usr/bin/env python3
"""Build a validation-aware leaderboard across TOSS strategy research.

The tournament deliberately does *not* rank strategies by backtest return alone.
Ordering is lexicographic by promotion state, evidence grade, then a bounded
risk-adjusted performance score. This prevents a short/current-view backtest
from outranking a more executable strategy merely because its CAGR is larger.

Research-only. No broker calls and no live orders.
"""
from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
VALIDATION = REPORTS / "validation"
VALIDATION.mkdir(parents=True, exist_ok=True)

STATUS_PRIORITY = {
    "LIVE_ELIGIBLE": 5,
    "FORWARD_PAPER_PASSED": 4,
    "PAPER_CANDIDATE": 3,
    "RESEARCH_ONLY": 2,
    "REJECTED": 1,
}
GRADE_PRIORITY = {"A": 5, "B": 4, "C": 3, "D": 2, "E": 1}


def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def safe_float(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def performance_score(
    *,
    cagr_pct: float | None,
    sharpe: float | None,
    max_drawdown_pct: float | None,
) -> float:
    """Bounded 0-100 research score; not an expected-return forecast.

    Weights: Sharpe 50, CAGR/return proxy 30, drawdown 20. Inputs are clipped
    so extreme short-sample CAGR cannot dominate the tournament.
    """
    sharpe_v = safe_float(sharpe)
    cagr_v = safe_float(cagr_pct)
    mdd_v = safe_float(max_drawdown_pct)

    sharpe_points = 25.0 if sharpe_v is None else 50.0 * clamp((sharpe_v + 0.5) / 2.0)
    cagr_points = 15.0 if cagr_v is None else 30.0 * clamp((cagr_v + 5.0) / 25.0)
    mdd_points = 10.0 if mdd_v is None else 20.0 * clamp((40.0 - abs(mdd_v)) / 40.0)
    return round(sharpe_points + cagr_points + mdd_points, 2)


def candidate(
    *,
    strategy_id: str,
    family: str,
    status: str,
    evidence_grade: str,
    source: str,
    cagr_pct: float | None = None,
    total_return_pct: float | None = None,
    sharpe: float | None = None,
    max_drawdown_pct: float | None = None,
    cost_bps: float | None = None,
    positive_period_share: float | None = None,
    oos_return_pct: float | None = None,
    oos_sharpe: float | None = None,
    sample_size: int | None = None,
    live_promotion_passed: bool = False,
    paper_candidate_passed: bool = False,
    known_lookahead: bool = False,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    perf_cagr = cagr_pct if cagr_pct is not None else oos_return_pct
    perf_sharpe = sharpe if sharpe is not None else oos_sharpe
    score = performance_score(
        cagr_pct=perf_cagr,
        sharpe=perf_sharpe,
        max_drawdown_pct=max_drawdown_pct,
    )
    return {
        "strategy_id": strategy_id,
        "family": family,
        "status": status,
        "evidence_grade": evidence_grade,
        "performance_score": score,
        "cagr_pct": safe_float(cagr_pct),
        "total_return_pct": safe_float(total_return_pct),
        "sharpe": safe_float(sharpe),
        "max_drawdown_pct": safe_float(max_drawdown_pct),
        "cost_bps": safe_float(cost_bps),
        "positive_period_share": safe_float(positive_period_share),
        "oos_return_pct": safe_float(oos_return_pct),
        "oos_sharpe": safe_float(oos_sharpe),
        "sample_size": sample_size,
        "live_promotion_passed": bool(live_promotion_passed),
        "paper_candidate_passed": bool(paper_candidate_passed),
        "known_lookahead": bool(known_lookahead),
        "source": source,
        "notes": notes or [],
    }


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def latest(pattern: str) -> Path | None:
    matches = sorted(ROOT.glob(pattern))
    return matches[-1] if matches else None


def add_executable_etf(candidates: list[dict[str, Any]]) -> None:
    path = VALIDATION / "executable_etf_portfolio_latest.json"
    if not path.exists():
        return
    data = load_json(path)
    paper_path = REPORTS / "harness" / "executable_etf_paper_latest.json"
    paper = load_json(paper_path) if paper_path.exists() else {}
    paper_target = {str(k): float(v) for k, v in paper.get("target_weights", {}).items()}
    forward_passed = bool(paper.get("forward_paper_gate", {}).get("passed", False))

    for strategy_id, promotion in data.get("promotions", {}).items():
        row = promotion.get("stress_75bp", {})
        paper_passed = bool(promotion.get("paper_candidate_passed", False))
        live_passed = bool(promotion.get("live_promotion_passed", False))
        strategy_weights = {str(k): float(v) for k, v in data.get("strategies", {}).get(strategy_id, {}).items()}
        selected = bool(paper_target) and strategy_weights == paper_target
        if live_passed:
            status = "LIVE_ELIGIBLE"
        elif selected and forward_passed:
            status = "FORWARD_PAPER_PASSED"
        elif paper_passed:
            status = "PAPER_CANDIDATE"
        else:
            status = "REJECTED"
        notes = [
            "11y-class executable ETF simulation; whole shares; next-open execution",
            "75bp per traded notional stress used for tournament metrics",
        ]
        if selected:
            notes.append(f"current forward-paper target; gate_passed={forward_passed}")
        if not live_passed:
            notes.append(str(promotion.get("live_block_reason", "live promotion not passed")))

        candidates.append(candidate(
            strategy_id=strategy_id,
            family="executable_etf",
            status=status,
            evidence_grade="B",
            source=str(path.relative_to(ROOT)),
            cagr_pct=row.get("cagr_pct"),
            total_return_pct=row.get("total_return_pct"),
            sharpe=row.get("sharpe_zero_rf"),
            max_drawdown_pct=row.get("max_drawdown_pct"),
            cost_bps=row.get("per_trade_notional_cost_bps"),
            positive_period_share=promotion.get("positive_year_share"),
            sample_size=row.get("rebalances"),
            live_promotion_passed=live_passed,
            paper_candidate_passed=paper_passed,
            notes=notes,
        ))


def add_pit_walkforward(candidates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    path = latest("reports/validation/pit_validation_*.json")
    if path is None:
        return {}
    data = load_json(path)
    by_strategy: dict[str, dict[str, Any]] = {}
    for wf in data.get("walkforward_results", []):
        windows = wf.get("windows", [])
        worst_mdd = min((safe_float(w.get("max_drawdown_pct")) or 0.0 for w in windows), default=None)
        n = int(wf.get("oos_windows", 0) or 0)
        positive = int(wf.get("positive_windows", 0) or 0)
        positive_share = positive / n if n else None
        oos_return = safe_float(wf.get("avg_oos_return_pct"))
        oos_sharpe = safe_float(wf.get("avg_oos_sharpe"))
        passed = bool(
            oos_return is not None
            and oos_sharpe is not None
            and positive_share is not None
            and oos_return > 0
            and oos_sharpe > 0
            and positive_share >= 0.5
        )
        status = "RESEARCH_ONLY" if passed else "REJECTED"
        strategy_id = str(wf.get("strategy"))
        row = candidate(
            strategy_id=strategy_id,
            family="pit_walkforward_stock_selection",
            status=status,
            evidence_grade="A",
            source=str(path.relative_to(ROOT)),
            max_drawdown_pct=worst_mdd,
            cost_bps=wf.get("cost_bps"),
            positive_period_share=positive_share,
            oos_return_pct=oos_return,
            oos_sharpe=oos_sharpe,
            sample_size=wf.get("total_oos_trades"),
            notes=[
                f"independent rolling OOS windows={n}",
                "ranked on OOS result, not exploratory in-sample result",
            ],
        )
        candidates.append(row)
        by_strategy[strategy_id] = row
    return by_strategy


def add_hml_cma(candidates: list[dict[str, Any]]) -> None:
    path = latest("reports/validation/hml_cma_quarterly_v2_*.json")
    if path is None:
        return
    data = load_json(path)
    quality = data.get("data_quality", {})
    for strategy_id in ["hml_only", "cma_only", "hml_cma_intersection", "hml_cma_composite"]:
        row = data.get(strategy_id, {}).get("75bp", {})
        if not row:
            continue
        cagr = safe_float(row.get("cagr_pct"))
        sharpe = safe_float(row.get("sharpe_ratio"))
        status = "RESEARCH_ONLY" if (cagr or 0) > 0 and (sharpe or 0) > 0 else "REJECTED"
        candidates.append(candidate(
            strategy_id=strategy_id,
            family="hml_cma_factor",
            status=status,
            evidence_grade="D",
            source=str(path.relative_to(ROOT)),
            cagr_pct=cagr,
            total_return_pct=row.get("total_return_pct"),
            sharpe=sharpe,
            max_drawdown_pct=row.get("max_drawdown_pct"),
            cost_bps=row.get("cost_bps"),
            positive_period_share=(
                sum(1 for v in row.get("yearly_returns_pct", {}).values() if safe_float(v) is not None and float(v) > 0)
                / max(len(row.get("yearly_returns_pct", {})), 1)
            ),
            sample_size=row.get("rebalances"),
            notes=[
                "forecast (E) rows excluded and legacy Q4 labels repaired",
                "current-view Naver fundamentals: historical filing-snapshot PIT unresolved",
                f"survivorship_bias_resolved={quality.get('survivorship_bias_resolved')}",
                f"rebalances={row.get('rebalances')}; trading_days={row.get('trading_days')}",
            ],
        ))


def add_low_vol(candidates: list[dict[str, Any]]) -> None:
    path = REPORTS / "backtests" / "low_vol_monthly" / "low_vol_monthly_latest.json"
    if not path.exists():
        return
    data = load_json(path)
    results = data.get("results", {})
    benchmark = results.get("benchmark_equal_weight", {})
    for label, promo_key in [("current_391k", "current_391k_promotion"), ("hypo_5M", "hypo_5M_promotion")]:
        row = results.get(label, {})
        promo = results.get(promo_key, {})
        paper_passed = bool(promo.get("paper_candidate_passed", False))
        status = "PAPER_CANDIDATE" if paper_passed else "REJECTED"
        candidates.append(candidate(
            strategy_id=f"low_vol_{label}",
            family="low_vol",
            status=status,
            evidence_grade="C",
            source=str(path.relative_to(ROOT)),
            cagr_pct=row.get("cagr_pct"),
            total_return_pct=row.get("total_return_pct"),
            sharpe=row.get("sharpe_zero_rf"),
            max_drawdown_pct=row.get("max_drawdown_pct"),
            cost_bps=row.get("per_trade_cost_bps"),
            positive_period_share=promo.get("positive_year_share"),
            sample_size=row.get("n_rebalances"),
            paper_candidate_passed=paper_passed,
            notes=[
                f"benchmark_cagr={benchmark.get('cagr_pct')}%, benchmark_sharpe={benchmark.get('sharpe_zero_rf')}",
                str(data.get("survivorship_note", "")),
            ],
        ))


def add_low_vol_value(candidates: list[dict[str, Any]]) -> None:
    path = latest("reports/validation/low_vol_value_backtest_*.json")
    if path is None:
        return
    data = load_json(path)
    row = data.get("result", {})
    candidates.append(candidate(
        strategy_id="low_vol_value",
        family="low_vol_value",
        status="REJECTED",
        evidence_grade="E",
        source=str(path.relative_to(ROOT)),
        cagr_pct=row.get("cagr_pct"),
        total_return_pct=row.get("total_return_pct"),
        sharpe=row.get("sharpe_ratio"),
        max_drawdown_pct=row.get("max_drawdown_pct"),
        sample_size=row.get("rebalances"),
        known_lookahead=True,
        notes=[str(row.get("bias_warning", "known look-ahead bias"))],
    ))


def add_current_live(candidates: list[dict[str, Any]]) -> None:
    path = REPORTS / "harness" / "backtest_current_live_strategy.json"
    if not path.exists():
        return
    data = load_json(path)
    row = data.get("summary", {})
    config = data.get("config", {})
    rejected = bool(row.get("capital_exhausted")) or (safe_float(row.get("sharpe")) or 0) <= 0 or (safe_float(row.get("profit_factor")) or 0) < 1
    candidates.append(candidate(
        strategy_id="current_live_strategy",
        family="legacy_live_logic",
        status="REJECTED" if rejected else "RESEARCH_ONLY",
        evidence_grade="C",
        source=str(path.relative_to(ROOT)),
        total_return_pct=(
            safe_float(row.get("total_pnl_krw")) / safe_float(config.get("initial_capital_krw")) * 100
            if safe_float(row.get("total_pnl_krw")) is not None and safe_float(config.get("initial_capital_krw"))
            else None
        ),
        sharpe=row.get("sharpe"),
        max_drawdown_pct=row.get("max_drawdown_pct"),
        cost_bps=config.get("round_trip_bps"),
        sample_size=row.get("total_trades"),
        notes=[
            f"profit_factor={row.get('profit_factor')}",
            f"capital_exhausted={row.get('capital_exhausted')}",
            "fixed-notional backtest; rejected from new-capital allocation",
        ],
    ))


def add_v2_exploration(candidates: list[dict[str, Any]], pit_map: dict[str, dict[str, Any]]) -> None:
    path = REPORTS / "harness" / "strategy_v2_comparison.json"
    if not path.exists():
        return
    data = load_json(path)
    row = data.get("best_sharpe", {})
    linked = pit_map.get("reversal_oversold", {})
    linked_oos = linked.get("oos_return_pct")
    status = "REJECTED" if linked and linked.get("status") == "REJECTED" else "RESEARCH_ONLY"
    candidates.append(candidate(
        strategy_id="v2_reversal_best_sharpe_exploration",
        family="reversal_exploration",
        status=status,
        evidence_grade="E",
        source=str(path.relative_to(ROOT)),
        cagr_pct=row.get("annual_pct"),
        total_return_pct=None,
        sharpe=row.get("sharpe"),
        max_drawdown_pct=None,
        sample_size=row.get("trades"),
        oos_return_pct=linked_oos,
        oos_sharpe=linked.get("oos_sharpe"),
        notes=[
            f"exploratory_label={row.get('label')}",
            f"independent_reversal_oversold_oos_return={linked_oos}",
            "exploratory result is subordinate to independent PIT/OOS validation",
        ],
    ))


def add_inverse(candidates: list[dict[str, Any]]) -> None:
    path = REPORTS / "harness" / "inverse_scenario_backtest.json"
    if not path.exists():
        return
    data = load_json(path)
    ranking = data.get("ranking", [])
    best = ranking[0] if ranking else {}
    robust_pass = any(bool(x.get("robust_pass")) for x in data.get("robustness", []))
    candidates.append(candidate(
        strategy_id="inverse_sleeve_best_scenario",
        family="inverse_hedge",
        status="RESEARCH_ONLY" if best.get("passes_hedge_gate") and robust_pass else "REJECTED",
        evidence_grade="A",
        source=str(path.relative_to(ROOT)),
        total_return_pct=best.get("sleeve_total_return_pct"),
        sharpe=best.get("sleeve_sharpe"),
        max_drawdown_pct=best.get("sleeve_max_drawdown_pct"),
        cost_bps=20.0,
        positive_period_share=None,
        sample_size=best.get("trade_count"),
        notes=[
            f"scenario={best.get('scenario')}",
            f"hedge_gate={best.get('passes_hedge_gate')}; robustness_pass_any={robust_pass}",
            "DEV/OOS robustness evidence exists, but the hedge economics fail",
        ],
    ))


def sort_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(
        candidates,
        key=lambda x: (
            STATUS_PRIORITY.get(x["status"], 0),
            GRADE_PRIORITY.get(x["evidence_grade"], 0),
            x["performance_score"],
            -(abs(x["max_drawdown_pct"]) if x.get("max_drawdown_pct") is not None else 999.0),
        ),
        reverse=True,
    )
    for i, row in enumerate(ranked, 1):
        row["rank"] = i
    return ranked


def build_tournament() -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    add_executable_etf(candidates)
    pit_map = add_pit_walkforward(candidates)
    add_hml_cma(candidates)
    add_low_vol(candidates)
    add_low_vol_value(candidates)
    add_current_live(candidates)
    add_v2_exploration(candidates, pit_map)
    add_inverse(candidates)
    ranked = sort_candidates(candidates)

    live = [x for x in ranked if x["status"] == "LIVE_ELIGIBLE"]
    forward_passed = [x for x in ranked if x["status"] == "FORWARD_PAPER_PASSED"]
    paper = [x for x in ranked if x["status"] == "PAPER_CANDIDATE"]
    research = [x for x in ranked if x["status"] == "RESEARCH_ONLY"]
    rejected = [x for x in ranked if x["status"] == "REJECTED"]

    top_paper = paper[:3]
    decision = "NO_NEW_LIVE_PROMOTION"
    if live:
        decision = "LIVE_ELIGIBLE_CANDIDATE_EXISTS_REQUIRES_OPERATOR_REVIEW"

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "ranking_rule": "promotion_state -> evidence_grade -> bounded risk-adjusted performance_score",
        "performance_score_note": "0-100 research prioritization score; not an expected-return forecast",
        "evidence_grades": {
            "A": "independent OOS/robustness evidence",
            "B": "long executable/cost-stressed historical simulation",
            "C": "historical backtest with material unresolved bias/validation limits",
            "D": "current-view/short-sample factor research; PIT unresolved",
            "E": "exploratory or known look-ahead-biased evidence",
        },
        "decision": decision,
        "live_eligible_count": len(live),
        "forward_paper_passed_count": len(forward_passed),
        "paper_candidate_count": len(paper),
        "research_only_count": len(research),
        "rejected_count": len(rejected),
        "top_paper_shortlist": [
            {
                "rank": x["rank"],
                "strategy_id": x["strategy_id"],
                "performance_score": x["performance_score"],
                "cagr_pct": x["cagr_pct"],
                "sharpe": x["sharpe"],
                "max_drawdown_pct": x["max_drawdown_pct"],
            }
            for x in top_paper
        ],
        "next_actions": [
            "Continue the existing forward-paper target without strategy switching; evaluate higher-ranked ETF variants in parallel research only.",
            "Collect order-book depth and completed monthly rebalance evidence before any live promotion.",
            "Rebuild HML/CMA on filing-date historical fundamentals and a historical universe before promotion.",
            "Keep momentum/reversal/inverse/legacy-live allocation at zero until new OOS evidence turns positive.",
            "After forward evidence, compute cross-strategy return correlations before building an ensemble allocator.",
        ],
        "leaderboard": ranked,
    }


def write_outputs(report: dict[str, Any]) -> tuple[Path, Path, Path]:
    json_path = VALIDATION / "strategy_tournament_latest.json"
    csv_path = VALIDATION / "strategy_tournament_latest.csv"
    md_path = VALIDATION / "strategy_tournament_latest.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    fields = [
        "rank", "strategy_id", "family", "status", "evidence_grade", "performance_score",
        "cagr_pct", "total_return_pct", "sharpe", "max_drawdown_pct", "cost_bps",
        "positive_period_share", "oos_return_pct", "oos_sharpe", "sample_size", "source",
    ]
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report["leaderboard"])

    lines = [
        "# TOSS Strategy Tournament",
        "",
        f"- 생성: {report['generated_at_utc']}",
        f"- 최종 판단: **{report['decision']}**",
        f"- Live eligible: **{report['live_eligible_count']}** / Forward-paper passed: **{report['forward_paper_passed_count']}** / Paper candidate: **{report['paper_candidate_count']}** / Research only: **{report['research_only_count']}** / Rejected: **{report['rejected_count']}**",
        "- 정렬: **승격상태 → 증거등급 → 제한된 위험조정 성과점수**",
        "",
        "## Leaderboard",
        "",
        "| Rank | Strategy | State | Evidence | Score | CAGR/OOS% | Sharpe/OOS | MDD% |",
        "|---:|---|---|:---:|---:|---:|---:|---:|",
    ]
    for r in report["leaderboard"]:
        ret = r["cagr_pct"] if r["cagr_pct"] is not None else r["oos_return_pct"]
        shp = r["sharpe"] if r["sharpe"] is not None else r["oos_sharpe"]
        lines.append(
            f"| {r['rank']} | {r['strategy_id']} | {r['status']} | {r['evidence_grade']} | "
            f"{r['performance_score']:.2f} | {ret if ret is not None else '-'} | "
            f"{shp if shp is not None else '-'} | {r['max_drawdown_pct'] if r['max_drawdown_pct'] is not None else '-'} |"
        )

    lines.extend(["", "## 다음 액션", ""])
    for action in report["next_actions"]:
        lines.append(f"- {action}")
    md_path.write_text("\n".join(lines) + "\n")
    return json_path, csv_path, md_path


def main() -> None:
    report = build_tournament()
    json_path, csv_path, md_path = write_outputs(report)
    print("=== TOSS Strategy Tournament ===")
    print(f"Decision: {report['decision']}")
    print(
        f"Live={report['live_eligible_count']} ForwardPassed={report['forward_paper_passed_count']} "
        f"Paper={report['paper_candidate_count']} Research={report['research_only_count']} "
        f"Rejected={report['rejected_count']}"
    )
    print("\nTop 10:")
    for r in report["leaderboard"][:10]:
        ret = r["cagr_pct"] if r["cagr_pct"] is not None else r["oos_return_pct"]
        shp = r["sharpe"] if r["sharpe"] is not None else r["oos_sharpe"]
        print(
            f"{r['rank']:>2}. {r['strategy_id']:<34} {r['status']:<16} "
            f"E={r['evidence_grade']} score={r['performance_score']:>5.2f} "
            f"ret={ret} sharpe={shp} mdd={r['max_drawdown_pct']}"
        )
    print(f"\nSaved: {json_path}")
    print(f"Saved: {csv_path}")
    print(f"Saved: {md_path}")


if __name__ == "__main__":
    main()
