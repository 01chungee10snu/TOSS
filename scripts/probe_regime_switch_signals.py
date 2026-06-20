from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pandas as pd

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
DEFENSIVE = dict(BASE, score_threshold=65, stop_loss_pct=0.08, max_positions=3)


def symbols(panel: pd.DataFrame) -> list[str]:
    return sorted(panel["code"].astype(str).str.zfill(6).unique().tolist())


def run_replay(panel: pd.DataFrame, cfg: dict, *, cost_bps: float = 30.0) -> dict:
    c = dict(cfg)
    step = int(c.pop("step"))
    engine = ReplayEngine(panel=panel, symbols=symbols(panel), transaction_cost_bps=cost_bps, **c)
    return engine.run(step=step)


def step_metrics(panel: pd.DataFrame, *, step: int = 5) -> pd.DataFrame:
    rows = []
    all_dates = sorted(panel["Date"].unique())
    for idx, ts in enumerate(all_dates[::step]):
        dt = pd.Timestamp(ts)
        sub = panel[panel["Date"] <= dt].copy()
        regime = _classify_regime(sub)
        candidates = _score_candidates(sub, regime=regime)
        top_scores = [float(c["final_score"]) for c in candidates]
        latest_rows = []
        for code, g in sub.groupby("code"):
            ordered = g.sort_values("Date")
            if len(ordered) < 61:
                continue
            closes = [float(v) for v in ordered["Close"].tolist()]
            vols = [float(v) for v in ordered["Volume"].tolist()]
            latest_rows.append({
                "code": str(code).zfill(6),
                "ret5": _return_over(closes, 5),
                "ret20": _return_over(closes, 20),
                "ret60": _return_over(closes, 60),
                "vol20": _return_volatility(closes, 20),
                "volume_surge": _volume_surge(vols, 20),
            })
        lf = pd.DataFrame(latest_rows)
        if lf.empty:
            continue
        rows.append({
            "date": dt.date().isoformat(),
            "year": dt.year,
            "step_idx": idx,
            "regime": regime["status"],
            "breadth20": regime["breadth_positive_20d"],
            "avg_ret20": regime["average_20d_return"],
            "median_ret20": float(lf["ret20"].median()),
            "p25_ret20": float(lf["ret20"].quantile(0.25)),
            "p75_ret20": float(lf["ret20"].quantile(0.75)),
            "dispersion_ret20": float(lf["ret20"].quantile(0.75) - lf["ret20"].quantile(0.25)),
            "mean_vol20": float(lf["vol20"].mean()),
            "p75_vol20": float(lf["vol20"].quantile(0.75)),
            "top_score": max(top_scores) if top_scores else 0.0,
            "count_score55": sum(1 for s in top_scores if s >= 55),
            "count_score65": sum(1 for s in top_scores if s >= 65),
            "score_gap_55_65": sum(1 for s in top_scores if s >= 55) - sum(1 for s in top_scores if s >= 65),
        })
    return pd.DataFrame(rows)


def trade_frame(result: dict, name: str, panel: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for t in result["trades"]:
        entry_date = pd.Timestamp(t["entry_date"])
        sym = str(t["symbol"]).zfill(6)
        sub = panel[(panel["code"] == sym) & (panel["Date"] <= entry_date)].sort_values("Date")
        if len(sub) >= 61:
            closes = [float(v) for v in sub["Close"].tolist()]
            vols = [float(v) for v in sub["Volume"].tolist()]
            feat = {
                "entry_ret5": _return_over(closes, 5),
                "entry_ret20": _return_over(closes, 20),
                "entry_ret60": _return_over(closes, 60),
                "entry_vol20": _return_volatility(closes, 20),
                "entry_volume_surge": _volume_surge(vols, 20),
            }
        else:
            feat = {"entry_ret5": None, "entry_ret20": None, "entry_ret60": None, "entry_vol20": None, "entry_volume_surge": None}
        rows.append({"config": name, **t, "entry_year": entry_date.year, **feat})
    return pd.DataFrame(rows)


def summarize_steps(df: pd.DataFrame) -> pd.DataFrame:
    agg = df.groupby("year").agg(
        steps=("date", "count"),
        risk_on_share=("regime", lambda s: (s == "risk_on").mean()),
        neutral_share=("regime", lambda s: (s == "neutral").mean()),
        risk_off_share=("regime", lambda s: (s == "risk_off").mean()),
        breadth20_mean=("breadth20", "mean"),
        breadth20_min=("breadth20", "min"),
        avg_ret20_mean=("avg_ret20", "mean"),
        median_ret20_mean=("median_ret20", "mean"),
        dispersion_ret20_mean=("dispersion_ret20", "mean"),
        mean_vol20=("mean_vol20", "mean"),
        p75_vol20=("p75_vol20", "mean"),
        top_score_mean=("top_score", "mean"),
        count_score55_mean=("count_score55", "mean"),
        count_score65_mean=("count_score65", "mean"),
        score_gap_55_65_mean=("score_gap_55_65", "mean"),
    ).reset_index()
    return agg


def summarize_trades(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    rows = []
    for (config, year), g in trades.groupby(["config", "entry_year"]):
        reasons = Counter(g["exit_reason"].tolist())
        rows.append({
            "config": config,
            "entry_year": year,
            "trades": len(g),
            "avg_pnl_pct": g["pnl_pct"].mean(),
            "median_pnl_pct": g["pnl_pct"].median(),
            "win_rate_pct": (g["pnl_pct"] > 0).mean() * 100,
            "stop_loss_count": reasons.get("stop_loss", 0),
            "take_profit_count": reasons.get("take_profit", 0),
            "time_exit_count": reasons.get("time_exit", 0),
            "avg_entry_ret20": g["entry_ret20"].mean(),
            "avg_entry_vol20": g["entry_vol20"].mean(),
            "avg_entry_volume_surge": g["entry_volume_surge"].mean(),
        })
    return pd.DataFrame(rows)


def main() -> None:
    panel = pd.read_csv(PANEL, dtype={"code": str}, parse_dates=["Date"])
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    years = sorted(panel["Date"].dt.year.unique())

    sm = step_metrics(panel, step=5)
    year_regime = summarize_steps(sm)

    perf_rows = []
    trade_frames = []
    for name, cfg in [("base_t55_sl12_mp4", BASE), ("def_t65_sl8_mp3", DEFENSIVE)]:
        full = run_replay(panel, cfg, cost_bps=30)
        perf_rows.append({"config": name, "scope": "full", **full["summary"]})
        trade_frames.append(trade_frame(full, name, panel))
        for y in years:
            yp = panel[panel["Date"].dt.year == y].copy()
            r = run_replay(yp, cfg, cost_bps=30)
            perf_rows.append({"config": name, "scope": str(y), **r["summary"]})
    perf = pd.DataFrame(perf_rows)
    trades = pd.concat(trade_frames, ignore_index=True) if trade_frames else pd.DataFrame()
    trade_summary = summarize_trades(trades)

    prefix = OUT_DIR / "regime_switch_probe_20260621"
    files = {
        "step_metrics": str(prefix.with_name(prefix.name + "_step_metrics.csv")),
        "year_regime": str(prefix.with_name(prefix.name + "_year_regime.csv")),
        "perf": str(prefix.with_name(prefix.name + "_perf.csv")),
        "trade_summary": str(prefix.with_name(prefix.name + "_trade_summary.csv")),
        "trades": str(prefix.with_name(prefix.name + "_trades.csv")),
        "report": str(prefix.with_name(prefix.name + ".md")),
    }
    sm.to_csv(files["step_metrics"], index=False)
    year_regime.to_csv(files["year_regime"], index=False)
    perf.to_csv(files["perf"], index=False)
    trade_summary.to_csv(files["trade_summary"], index=False)
    trades.to_csv(files["trades"], index=False)

    y2023 = year_regime[year_regime["year"] == 2023].to_dict(orient="records")
    y2026 = year_regime[year_regime["year"] == 2026].to_dict(orient="records")
    report = [
        "# Regime switch probe — 2026-06-21",
        "",
        "Paper/research only. live_order_submitted: False.",
        "",
        "## Files",
        *[f"- {k}: `{v}`" for k, v in files.items() if k != "report"],
        "",
        "## Performance: base vs defensive",
        perf.to_markdown(index=False),
        "",
        "## Year-level pre-entry regime/features",
        year_regime.to_markdown(index=False),
        "",
        "## Trade attribution by entry year",
        trade_summary.to_markdown(index=False),
        "",
        "## First read",
        "",
        f"- 2023 regime row: `{json.dumps(y2023, ensure_ascii=False)}`",
        f"- 2026 regime row: `{json.dumps(y2026, ensure_ascii=False)}`",
        "- Candidate switch signals to inspect next: breadth20_mean/min, avg_ret20_mean, dispersion_ret20_mean, count_score65_mean, and stop_loss_count/take_profit_count split.",
        "- This probe is diagnostic only; no policy promotion.",
    ]
    Path(files["report"]).write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps({"files": files, "perf": perf.to_dict(orient="records"), "year_regime": year_regime.to_dict(orient="records"), "trade_summary": trade_summary.to_dict(orient="records")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
