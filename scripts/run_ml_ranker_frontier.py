from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from toss_alpha.daily.decision import _classify_regime, _return_over, _return_volatility, _score_candidates, _volume_surge
from toss_alpha.daily.replay import ReplayEngine

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "reports/backtests/random500_seed20260607_2022-01-01_2026-latest_ohlcv_panel.csv"
OUT_DIR = ROOT / "reports/harness"
OUT_DIR.mkdir(parents=True, exist_ok=True)

BASE = {
    "step": 5,
    "score_threshold": 55,
    "stop_loss_pct": 0.12,
    "take_profit_pct": 0.20,
    "max_holding_steps": 10,
    "max_positions": 4,
    "trailing_stop_pct": 0.0,
    "sizing_mode": "flat",
    "rebalance_mode": "hold_until_exit",
    "min_volume": 0,
}
FEATURES = [
    "final_score",
    "momentum_score",
    "volume_score",
    "volatility_score",
    "overextension_penalty",
    "momentum_20d",
    "momentum_60d",
    "volume_surge",
    "ret5",
    "ret10",
    "ret20",
    "ret60",
    "vol10",
    "vol20",
    "vol60",
    "volume_surge_10",
    "breadth20",
    "avg_ret20",
    "dispersion20",
    "rank_final_score_pct",
]


def symbols(panel: pd.DataFrame) -> list[str]:
    return sorted(panel["code"].astype(str).str.zfill(6).unique().tolist())


def add_forward_returns(panel: pd.DataFrame, horizons=(5, 10, 20)) -> pd.DataFrame:
    panel = panel.sort_values(["code", "Date"]).copy()
    for h in horizons:
        panel[f"fwd_ret_{h}"] = panel.groupby("code")["Close"].shift(-h) / panel["Close"] - 1.0
    return panel


def build_samples(panel: pd.DataFrame, *, step: int = 5) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    panel = add_forward_returns(panel)
    all_dates = sorted(panel["Date"].unique())
    for ts in all_dates[::step]:
        dt = pd.Timestamp(ts)
        sub = panel[panel["Date"] <= dt].copy()
        cur = panel[panel["Date"] == dt].copy()
        if sub.empty or cur.empty:
            continue
        regime = _classify_regime(sub)
        candidates = _score_candidates(sub, regime=regime)
        score_by_symbol = {str(c["symbol"]).zfill(6): c for c in candidates}
        latest_features = []
        for code, g in sub.groupby("code"):
            ordered = g.sort_values("Date")
            if len(ordered) < 61:
                continue
            closes = [float(v) for v in ordered["Close"].tolist()]
            vols = [float(v) for v in ordered["Volume"].tolist()]
            latest_features.append({
                "code": str(code).zfill(6),
                "ret20": _return_over(closes, 20),
            })
        lf = pd.DataFrame(latest_features)
        dispersion20 = float(lf["ret20"].quantile(0.75) - lf["ret20"].quantile(0.25)) if not lf.empty else 0.0
        cur_by_code = {str(r["code"]).zfill(6): r for _, r in cur.iterrows()}
        base_scores = []
        for code, cand in score_by_symbol.items():
            row = cur_by_code.get(code)
            if row is None:
                continue
            base_scores.append(float(cand["final_score"]))
        score_series = pd.Series(base_scores) if base_scores else pd.Series(dtype=float)
        for code, cand in score_by_symbol.items():
            row = cur_by_code.get(code)
            if row is None:
                continue
            g = sub[sub["code"].astype(str).str.zfill(6) == code].sort_values("Date")
            if len(g) < 61:
                continue
            closes = [float(v) for v in g["Close"].tolist()]
            vols = [float(v) for v in g["Volume"].tolist()]
            comps = cand.get("components", {})
            fs = float(cand["final_score"])
            rank_pct = float((score_series <= fs).mean()) if len(score_series) else 0.0
            rows.append({
                "date": dt,
                "year": dt.year,
                "code": code,
                "final_score": fs,
                "momentum_score": float(comps.get("momentum_score", 0.0)),
                "volume_score": float(comps.get("volume_score", 0.0)),
                "volatility_score": float(comps.get("volatility_score", 0.0)),
                "overextension_penalty": float(comps.get("overextension_penalty", 0.0)),
                "momentum_20d": float(comps.get("momentum_20d", 0.0)),
                "momentum_60d": float(comps.get("momentum_60d", 0.0)),
                "volume_surge": float(comps.get("volume_surge", 0.0)),
                "ret5": _return_over(closes, 5),
                "ret10": _return_over(closes, 10),
                "ret20": _return_over(closes, 20),
                "ret60": _return_over(closes, 60),
                "vol10": _return_volatility(closes, 10),
                "vol20": _return_volatility(closes, 20),
                "vol60": _return_volatility(closes, 60),
                "volume_surge_10": _volume_surge(vols, 10),
                "breadth20": float(regime["breadth_positive_20d"]),
                "avg_ret20": float(regime["average_20d_return"]),
                "dispersion20": dispersion20,
                "rank_final_score_pct": rank_pct,
                "fwd_ret_5": float(row.get("fwd_ret_5", np.nan)),
                "fwd_ret_10": float(row.get("fwd_ret_10", np.nan)),
                "fwd_ret_20": float(row.get("fwd_ret_20", np.nan)),
            })
    df = pd.DataFrame(rows)
    return df.dropna(subset=["fwd_ret_20"]).reset_index(drop=True)


def train_models() -> dict[str, Any]:
    return {
        "ridge": make_pipeline(StandardScaler(), Ridge(alpha=3.0)),
        "gbr_depth2": GradientBoostingRegressor(n_estimators=80, learning_rate=0.04, max_depth=2, random_state=20260621),
        "gbr_depth3": GradientBoostingRegressor(n_estimators=100, learning_rate=0.035, max_depth=3, random_state=20260621),
        "rf_small": RandomForestRegressor(n_estimators=160, max_depth=5, min_samples_leaf=30, random_state=20260621, n_jobs=-1),
    }


def ml_picks(samples: pd.DataFrame, *, model_name: str, min_pred: float, top_n: int, train_end_year: int, test_year: int) -> pd.DataFrame:
    train = samples[samples["year"] <= train_end_year].copy()
    test = samples[samples["year"] == test_year].copy()
    if train.empty or test.empty:
        return pd.DataFrame()
    model = train_models()[model_name]
    X = train[FEATURES].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    y = train["fwd_ret_20"].clip(-0.35, 0.60)
    model.fit(X, y)
    test = test.copy()
    test["ml_pred20"] = model.predict(test[FEATURES].replace([np.inf, -np.inf], np.nan).fillna(0.0))
    test = test[test["ml_pred20"] >= min_pred]
    picked = []
    for dt, g in test.groupby("date"):
        gg = g.sort_values(["ml_pred20", "final_score"], ascending=False).head(top_n)
        picked.append(gg)
    return pd.concat(picked, ignore_index=True) if picked else pd.DataFrame()


def simulate_picks(picks: pd.DataFrame, panel: pd.DataFrame, cfg: dict, *, cost_bps: float = 30.0) -> dict[str, Any]:
    if picks.empty:
        return {
            "summary": {"total_return_pct": 0.0, "max_drawdown_pct": 0.0, "total_trades": 0, "winning_trades": 0, "win_rate_pct": 0.0, "sharpe_ratio": 0.0, "final_equity_krw": 1_000_000, "transaction_cost_bps": cost_bps, "total_cost_krw": 0.0},
            "trades": [],
            "equity_curve": [],
        }
    # Feed ReplayEngine a filtered panel where non-picked codes cannot become entries on each date.
    # To keep the production exit mechanics, run a small custom replay loop by monkey-selecting candidates is overkill here;
    # instead create per-date universe panel with picked codes plus currently held codes is not supported.
    # For this first ML frontier, approximate by using picked code universe only. This can overstate repeated availability,
    # so verdict must remain research-only until integrated into ReplayEngine.
    syms = sorted(picks["code"].astype(str).str.zfill(6).unique())
    c = dict(cfg)
    step = int(c.pop("step"))
    engine = ReplayEngine(panel=panel[panel["code"].isin(syms)].copy(), symbols=syms, transaction_cost_bps=cost_bps, **c)
    return engine.run(step=step)


def baseline(panel: pd.DataFrame) -> dict[str, Any]:
    c = dict(BASE)
    step = int(c.pop("step"))
    engine = ReplayEngine(panel=panel, symbols=symbols(panel), transaction_cost_bps=30.0, **c)
    return engine.run(step=step)


def main() -> None:
    panel = pd.read_csv(PANEL, dtype={"code": str}, parse_dates=["Date"])
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    samples = build_samples(panel, step=5)
    sample_csv = OUT_DIR / "ml_ranker_samples_20260621.csv"
    samples.to_csv(sample_csv, index=False)
    base = baseline(panel)["summary"]

    rows = []
    pick_frames = []
    model_names = list(train_models())
    min_preds = [0.00, 0.02, 0.04, 0.06]
    top_ns = [4, 8, 12]
    test_years = [2023, 2024, 2025, 2026]
    for model_name in model_names:
        for min_pred in min_preds:
            for top_n in top_ns:
                fold_picks = []
                fold_rows = []
                for y in test_years:
                    train_end = y - 1
                    p = ml_picks(samples, model_name=model_name, min_pred=min_pred, top_n=top_n, train_end_year=train_end, test_year=y)
                    if not p.empty:
                        p = p.copy()
                        p["model"] = model_name
                        p["min_pred"] = min_pred
                        p["top_n"] = top_n
                        p["test_year"] = y
                        fold_picks.append(p)
                    yp = panel[panel["Date"].dt.year == y].copy()
                    # Evaluate fold on picked symbols in that year as a fast approximation.
                    r = simulate_picks(p, yp, BASE, cost_bps=30.0)["summary"] if not p.empty else {"total_return_pct": 0.0, "max_drawdown_pct": 0.0, "total_trades": 0, "sharpe_ratio": 0.0}
                    fold_rows.append({"year": y, "return": r["total_return_pct"], "mdd": r["max_drawdown_pct"], "trades": r["total_trades"], "sharpe": r["sharpe_ratio"], "pick_rows": len(p) if not p.empty else 0, "picked_symbols": p["code"].nunique() if not p.empty else 0})
                all_picks = pd.concat(fold_picks, ignore_index=True) if fold_picks else pd.DataFrame()
                if not all_picks.empty:
                    pick_frames.append(all_picks)
                full_result = simulate_picks(all_picks, panel, BASE, cost_bps=30.0)["summary"] if not all_picks.empty else {"total_return_pct": 0.0, "max_drawdown_pct": 0.0, "total_trades": 0, "winning_trades": 0, "win_rate_pct": 0.0, "sharpe_ratio": 0.0, "final_equity_krw": 1_000_000, "total_cost_krw": 0.0}
                worst_year = min(fr["return"] for fr in fold_rows)
                rows.append({
                    "model": model_name,
                    "min_pred": min_pred,
                    "top_n": top_n,
                    "full_return_30bps": full_result["total_return_pct"],
                    "full_mdd_30bps": full_result["max_drawdown_pct"],
                    "full_sharpe_30bps": full_result["sharpe_ratio"],
                    "full_trades_30bps": full_result["total_trades"],
                    "worst_fold_year_return": worst_year,
                    "objective": full_result["total_return_pct"] + 3 * worst_year,
                    **{f"y{fr['year']}_return": fr["return"] for fr in fold_rows},
                    **{f"y{fr['year']}_trades": fr["trades"] for fr in fold_rows},
                    "total_pick_rows": len(all_picks) if not all_picks.empty else 0,
                    "picked_symbols": all_picks["code"].nunique() if not all_picks.empty else 0,
                })
                print(model_name, min_pred, top_n, rows[-1]["full_return_30bps"], rows[-1]["worst_fold_year_return"], flush=True)

    results = pd.DataFrame(rows).sort_values(["full_return_30bps", "objective"], ascending=False)
    result_csv = OUT_DIR / "ml_ranker_frontier_results_20260621.csv"
    picks_csv = OUT_DIR / "ml_ranker_frontier_picks_20260621.csv"
    report_md = OUT_DIR / "ml_ranker_frontier_20260621.md"
    results.to_csv(result_csv, index=False)
    if pick_frames:
        pd.concat(pick_frames, ignore_index=True).to_csv(picks_csv, index=False)
    else:
        pd.DataFrame().to_csv(picks_csv, index=False)
    lines = [
        "# ML ranker frontier — 2026-06-21",
        "",
        "Paper/research only. live_order_submitted: False.",
        "",
        "## Baseline anchor",
        "",
        f"- base_t55_sl12_mp4 30bps full_return: {base['total_return_pct']}%",
        f"- MDD: {base['max_drawdown_pct']}%",
        f"- Sharpe: {base['sharpe_ratio']}",
        f"- trades: {base['total_trades']}",
        "",
        "## Caveat",
        "",
        "This first ML pass is a fast ranker approximation: it trains walk-forward models and evaluates picked-symbol universes through the existing ReplayEngine. A promotion-grade pass must wire ML predictions into candidate selection per replay date, not only restrict the universe after the fact.",
        "",
        "## Files",
        f"- samples: `{sample_csv}`",
        f"- results: `{result_csv}`",
        f"- picks: `{picks_csv}`",
        "",
        "## Best by full return",
        results.head(12).to_markdown(index=False),
        "",
        "## Best by objective = full + 3*worst_fold",
        results.sort_values(["objective", "full_return_30bps"], ascending=False).head(12).to_markdown(index=False),
        "",
        "## Verdict",
        "",
        "Treat as discovery only. If a candidate beats baseline here, the next step is integrating ML predictions directly into ReplayEngine entry selection and rerunning with identical exits/costs/year splits.",
    ]
    report_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({
        "baseline": base,
        "samples": str(sample_csv),
        "results": str(result_csv),
        "picks": str(picks_csv),
        "report": str(report_md),
        "best_full": results.head(8).to_dict(orient="records"),
        "best_objective": results.sort_values(["objective", "full_return_30bps"], ascending=False).head(8).to_dict(orient="records"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
