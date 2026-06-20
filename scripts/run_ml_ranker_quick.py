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
FEATURES = ["final_score","momentum_score","volume_score","volatility_score","overextension_penalty","momentum_20d","momentum_60d","volume_surge","ret5","ret20","ret60","vol20","breadth20","avg_ret20","dispersion20","rank_final_score_pct"]


def symbols(panel: pd.DataFrame) -> list[str]:
    return sorted(panel["code"].astype(str).str.zfill(6).unique().tolist())


def build_or_load_samples(panel: pd.DataFrame) -> pd.DataFrame:
    sample_csv = OUT_DIR / "ml_ranker_quick_samples_20260621.csv"
    if sample_csv.exists():
        return pd.read_csv(sample_csv, parse_dates=["date"], dtype={"code": str})
    rows: list[dict[str, Any]] = []
    panel = panel.sort_values(["code", "Date"]).copy()
    panel["fwd_ret_20"] = panel.groupby("code")["Close"].shift(-20) / panel["Close"] - 1.0
    all_dates = sorted(panel["Date"].unique())
    # Faster probe: every 10 trading days, and keep top 80 base-score candidates per step.
    for n, ts in enumerate(all_dates[::10], 1):
        dt = pd.Timestamp(ts)
        sub = panel[panel["Date"] <= dt].copy()
        cur = panel[panel["Date"] == dt].copy()
        if len(sub) == 0 or len(cur) == 0:
            continue
        regime = _classify_regime(sub)
        candidates = _score_candidates(sub, regime=regime)[:80]
        latest_features = []
        for code, g in sub.groupby("code"):
            ordered = g.sort_values("Date")
            if len(ordered) < 61:
                continue
            closes = [float(v) for v in ordered["Close"].tolist()]
            latest_features.append({"code": str(code).zfill(6), "ret20": _return_over(closes, 20)})
        lf = pd.DataFrame(latest_features)
        dispersion20 = float(lf["ret20"].quantile(0.75) - lf["ret20"].quantile(0.25)) if not lf.empty else 0.0
        cur_by_code = {str(r["code"]).zfill(6): r for _, r in cur.iterrows()}
        scores = [float(c["final_score"]) for c in candidates]
        score_series = pd.Series(scores)
        for cand in candidates:
            code = str(cand["symbol"]).zfill(6)
            row = cur_by_code.get(code)
            if row is None or pd.isna(row.get("fwd_ret_20", np.nan)):
                continue
            g = sub[sub["code"].astype(str).str.zfill(6) == code].sort_values("Date")
            if len(g) < 61:
                continue
            closes = [float(v) for v in g["Close"].tolist()]
            vols = [float(v) for v in g["Volume"].tolist()]
            comps = cand.get("components", {})
            fs = float(cand["final_score"])
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
                "ret20": _return_over(closes, 20),
                "ret60": _return_over(closes, 60),
                "vol20": _return_volatility(closes, 20),
                "breadth20": float(regime["breadth_positive_20d"]),
                "avg_ret20": float(regime["average_20d_return"]),
                "dispersion20": dispersion20,
                "rank_final_score_pct": float((score_series <= fs).mean()) if len(score_series) else 0.0,
                "fwd_ret_20": float(row["fwd_ret_20"]),
            })
        if n % 20 == 0:
            print(f"samples step {n}/{len(all_dates[::10])} rows={len(rows)}", flush=True)
    df = pd.DataFrame(rows).dropna().reset_index(drop=True)
    df.to_csv(sample_csv, index=False)
    return df


def model_factory(name: str):
    if name == "ridge":
        return make_pipeline(StandardScaler(), Ridge(alpha=5.0))
    if name == "gbr":
        return GradientBoostingRegressor(n_estimators=60, learning_rate=0.05, max_depth=2, random_state=20260621)
    if name == "rf":
        return RandomForestRegressor(n_estimators=80, max_depth=5, min_samples_leaf=25, random_state=20260621, n_jobs=-1)
    raise ValueError(name)


def choose_symbols(samples: pd.DataFrame, *, model_name: str, min_pred: float, top_n: int) -> pd.DataFrame:
    picks = []
    for test_year in [2024, 2025, 2026]:
        train = samples[samples["year"] < test_year]
        test = samples[samples["year"] == test_year]
        if train.empty or test.empty:
            continue
        model = model_factory(model_name)
        model.fit(train[FEATURES], train["fwd_ret_20"].clip(-0.35, 0.60))
        t = test.copy()
        t["ml_pred20"] = model.predict(t[FEATURES])
        t = t[t["ml_pred20"] >= min_pred]
        for _, g in t.groupby("date"):
            picks.append(g.sort_values(["ml_pred20", "final_score"], ascending=False).head(top_n))
    return pd.concat(picks, ignore_index=True) if picks else pd.DataFrame()


def run_engine(panel: pd.DataFrame, syms: list[str], cfg: dict) -> dict:
    c = dict(cfg)
    step = int(c.pop("step"))
    engine = ReplayEngine(panel=panel[panel["code"].isin(syms)].copy(), symbols=syms, transaction_cost_bps=30.0, **c)
    return engine.run(step=step)["summary"]


def main() -> None:
    panel = pd.read_csv(PANEL, dtype={"code": str}, parse_dates=["Date"])
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    samples = build_or_load_samples(panel)
    base = run_engine(panel, symbols(panel), BASE)
    rows = []
    all_picks = []
    for model_name in ["ridge", "gbr", "rf"]:
        for min_pred in [-0.02, 0.00, 0.02, 0.04]:
            for top_n in [4, 8, 16]:
                picks = choose_symbols(samples, model_name=model_name, min_pred=min_pred, top_n=top_n)
                if picks.empty:
                    s = {"total_return_pct": 0.0, "max_drawdown_pct": 0.0, "sharpe_ratio": 0.0, "total_trades": 0}
                    syms = []
                else:
                    picks = picks.copy(); picks["model"] = model_name; picks["min_pred"] = min_pred; picks["top_n"] = top_n
                    all_picks.append(picks)
                    syms = sorted(picks["code"].astype(str).str.zfill(6).unique())
                    s = run_engine(panel, syms, BASE)
                row = {"model": model_name, "min_pred": min_pred, "top_n": top_n, "picked_symbols": len(syms), "pick_rows": len(picks), "return_30bps": s["total_return_pct"], "mdd_30bps": s["max_drawdown_pct"], "sharpe_30bps": s["sharpe_ratio"], "trades": s["total_trades"]}
                rows.append(row)
                print(row, flush=True)
    results = pd.DataFrame(rows).sort_values(["return_30bps", "sharpe_30bps"], ascending=False)
    result_csv = OUT_DIR / "ml_ranker_quick_results_20260621.csv"
    picks_csv = OUT_DIR / "ml_ranker_quick_picks_20260621.csv"
    report = OUT_DIR / "ml_ranker_quick_20260621.md"
    results.to_csv(result_csv, index=False)
    if all_picks:
        pd.concat(all_picks, ignore_index=True).to_csv(picks_csv, index=False)
    else:
        pd.DataFrame().to_csv(picks_csv, index=False)
    lines = [
        "# ML ranker quick frontier — 2026-06-21",
        "",
        "Paper/research only. live_order_submitted: False.",
        "",
        "## Baseline",
        f"- base return_30bps: {base['total_return_pct']}%",
        f"- base mdd: {base['max_drawdown_pct']}%",
        f"- base sharpe: {base['sharpe_ratio']}",
        "",
        "## Caveat",
        "Fast approximation: ML selects a candidate symbol universe, then existing ReplayEngine trades that universe. Promotion-grade version must inject ML predictions into per-date candidate ranking.",
        "",
        "## Files",
        f"- results: `{result_csv}`",
        f"- picks: `{picks_csv}`",
        "",
        "## Best candidates",
        results.head(20).to_markdown(index=False),
    ]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"baseline": base, "results": str(result_csv), "picks": str(picks_csv), "report": str(report), "best": results.head(10).to_dict(orient="records")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
