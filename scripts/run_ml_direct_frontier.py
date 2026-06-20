from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "reports/backtests/random500_seed20260607_2022-01-01_2026-latest_ohlcv_panel.csv"
OUT_DIR = ROOT / "reports/harness"
OUT_DIR.mkdir(parents=True, exist_ok=True)
N_JOBS = min(15, os.cpu_count() or 8)

FEATURES = [
    "ret5", "ret10", "ret20", "ret60",
    "vol10", "vol20", "vol60",
    "volume_surge5", "volume_surge20",
    "dollar_volume_log",
    "hl_range", "oc_return",
    "momentum_score", "volume_score", "volatility_score", "overextension_penalty", "base_score",
    "breadth20", "avg_ret20", "dispersion20", "risk_on", "risk_off",
    "rank_base_pct", "rank_ret20_pct", "rank_volume_surge_pct",
]

BASE_CFG = {
    "initial_cash": 1_000_000.0,
    "max_notional": 100_000.0,
    "max_positions": 4,
    "stop_loss": 0.12,
    "take_profit": 0.20,
    "max_holding_steps": 10,
    "cost_bps": 30.0,
    "step": 5,
}


@dataclass
class Pos:
    code: str
    qty: float
    entry_price: float
    entry_date: pd.Timestamp
    entry_step: int
    peak: float


def clip(s: pd.Series | float, lo=0.0, hi=100.0):
    return np.minimum(np.maximum(s, lo), hi)


def prepare_features(panel: pd.DataFrame) -> pd.DataFrame:
    cache = OUT_DIR / "ml_direct_features_20260621.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    df = panel.sort_values(["code", "Date"]).copy()
    g = df.groupby("code", group_keys=False)
    for n in [5, 10, 20, 60]:
        df[f"ret{n}"] = g["Close"].pct_change(n)
    daily_ret = g["Close"].pct_change()
    df["daily_ret"] = daily_ret
    for n in [10, 20, 60]:
        df[f"vol{n}"] = g["daily_ret"].rolling(n).std().reset_index(level=0, drop=True)
    for n in [5, 20]:
        avg_vol = g["Volume"].rolling(n).mean().reset_index(level=0, drop=True)
        df[f"volume_surge{n}"] = df["Volume"] / avg_vol.replace(0, np.nan)
    df["dollar_volume_log"] = np.log1p(df["Close"] * df["Volume"].clip(lower=0))
    df["hl_range"] = (df["High"] - df["Low"]) / df["Close"].replace(0, np.nan)
    df["oc_return"] = df["Close"] / df["Open"].replace(0, np.nan) - 1.0
    df["momentum_score"] = clip(50.0 + df["ret20"] * 250.0 + df["ret60"] * 120.0)
    df["volume_score"] = clip(50.0 + (df["volume_surge20"] - 1.0) * 25.0)
    df["volatility_score"] = clip(100.0 - df["vol20"] * 900.0)
    df["overextension_penalty"] = np.maximum(0.0, (df["ret20"] - 0.25) * 80.0)
    # date-level regime features
    by_date = df.groupby("Date")
    reg = pd.DataFrame({
        "breadth20": by_date["ret20"].apply(lambda s: float((s > 0).mean())),
        "avg_ret20": by_date["ret20"].mean(),
        "dispersion20": by_date["ret20"].quantile(0.75) - by_date["ret20"].quantile(0.25),
    }).reset_index()
    reg["risk_on"] = ((reg["breadth20"] >= 0.6) & (reg["avg_ret20"] > 0.02)).astype(float)
    reg["risk_off"] = ((reg["breadth20"] <= 0.4) & (reg["avg_ret20"] < -0.02)).astype(float)
    df = df.merge(reg, on="Date", how="left")
    regime_score = np.where(df["risk_on"] == 1, 70.0, np.where(df["risk_off"] == 1, 25.0, 50.0))
    df["base_score"] = clip(df["momentum_score"] * 0.45 + df["volume_score"] * 0.20 + df["volatility_score"] * 0.20 + regime_score * 0.15 - df["overextension_penalty"])
    for col, name in [("base_score", "rank_base_pct"), ("ret20", "rank_ret20_pct"), ("volume_surge20", "rank_volume_surge_pct")]:
        df[name] = df.groupby("Date")[col].rank(pct=True)
    df["fwd_ret20"] = g["Close"].shift(-20) / df["Close"] - 1.0
    df["fwd_ret10"] = g["Close"].shift(-10) / df["Close"] - 1.0
    keep_cols = ["Date", "code", "Open", "High", "Low", "Close", "Volume", "fwd_ret20", "fwd_ret10"] + FEATURES
    df = df[keep_cols].replace([np.inf, -np.inf], np.nan).dropna().copy()
    df["code"] = df["code"].astype(str).str.zfill(6)
    df.to_parquet(cache, index=False)
    return df


def models() -> dict[str, Any]:
    return {
        "ridge": make_pipeline(StandardScaler(), Ridge(alpha=8.0)),
        "histgb": HistGradientBoostingRegressor(max_iter=160, learning_rate=0.045, max_leaf_nodes=31, l2_regularization=0.05, random_state=20260621),
        "extratrees": ExtraTreesRegressor(n_estimators=260, max_depth=8, min_samples_leaf=18, max_features=0.7, random_state=20260621, n_jobs=N_JOBS),
        "rf": RandomForestRegressor(n_estimators=220, max_depth=8, min_samples_leaf=20, max_features=0.7, random_state=20260621, n_jobs=N_JOBS),
        "lgbm": lgb.LGBMRegressor(n_estimators=280, learning_rate=0.035, num_leaves=31, max_depth=-1, min_child_samples=35, subsample=0.85, colsample_bytree=0.85, reg_lambda=1.0, objective="regression", random_state=20260621, n_jobs=N_JOBS, verbosity=-1),
        "xgb": xgb.XGBRegressor(n_estimators=240, learning_rate=0.035, max_depth=4, subsample=0.85, colsample_bytree=0.85, reg_lambda=2.0, objective="reg:squarederror", random_state=20260621, n_jobs=N_JOBS, tree_method="hist"),
    }


def make_predictions(feat: pd.DataFrame, model_name: str, *, target: str = "fwd_ret20") -> pd.DataFrame:
    out = []
    # OOS years. 2023 uses 2022-only training; 2024+ use expanding training.
    for year in [2023, 2024, 2025, 2026]:
        train = feat[feat["Date"].dt.year < year]
        test = feat[feat["Date"].dt.year == year]
        if train.empty or test.empty:
            continue
        m = models()[model_name]
        X = train[FEATURES].astype(float)
        y = train[target].clip(-0.35, 0.60).astype(float)
        m.fit(X, y)
        pred_cols = list(dict.fromkeys(["Date", "code", "Close", "base_score"] + FEATURES))
        t = test[pred_cols].copy()
        t["ml_pred"] = m.predict(test[FEATURES].astype(float))
        t["model"] = model_name
        out.append(t)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def simulate(pred: pd.DataFrame, *, min_pred: float, top_n: int, score_mode: str, cfg: dict) -> dict[str, Any]:
    cash = cfg["initial_cash"]
    cost_bps = cfg["cost_bps"]
    max_positions = cfg["max_positions"]
    max_notional = cfg["max_notional"]
    positions: dict[str, Pos] = {}
    trades = []
    equity_curve = []
    pred = pred.sort_values(["Date", "code"]).copy()
    all_dates = sorted(pred["Date"].unique())
    date_frames = {d: g for d, g in pred.groupby("Date")}
    for step_idx, date in enumerate(all_dates[::cfg["step"]]):
        g = date_frames.get(date)
        if g is None or g.empty:
            continue
        prices = {str(r.code).zfill(6): float(r.Close) for r in g.itertuples()}
        # exits
        for code in list(positions):
            pos = positions[code]
            px = prices.get(code)
            if px is None:
                continue
            pos.peak = max(pos.peak, px)
            pnl = px / pos.entry_price - 1.0
            holding = step_idx - pos.entry_step
            reason = None
            if pnl <= -cfg["stop_loss"]:
                reason = "stop_loss"
            elif pnl >= cfg["take_profit"]:
                reason = "take_profit"
            elif holding >= cfg["max_holding_steps"]:
                reason = "time_exit"
            if reason:
                proceeds = pos.qty * px
                fee = proceeds * cost_bps / 10_000
                cost = pos.qty * pos.entry_price
                cash += proceeds - fee
                trades.append({"code": code, "entry_date": pos.entry_date, "exit_date": pd.Timestamp(date), "pnl_pct": (proceeds - fee - cost) / cost * 100, "pnl_krw": proceeds - fee - cost, "exit_reason": reason, "holding_steps": holding})
                del positions[code]
        # entries
        if len(positions) < max_positions:
            gg = g[g["ml_pred"] >= min_pred].copy()
            if score_mode == "ml":
                gg["entry_score"] = gg["ml_pred"]
            elif score_mode == "hybrid":
                gg["entry_score"] = gg["ml_pred"] + (gg["base_score"] / 100.0) * 0.03
            elif score_mode == "penalized_tail":
                gg["entry_score"] = gg["ml_pred"] + (gg["base_score"] / 100.0) * 0.02 - gg["volume_surge20"].clip(0, 20) * 0.001 - gg["vol20"].clip(0, 1) * 0.20
            else:
                raise ValueError(score_mode)
            gg = gg.sort_values("entry_score", ascending=False).head(top_n)
            opened = 0
            for r in gg.itertuples():
                code = str(r.code).zfill(6)
                if code in positions:
                    continue
                if opened >= max(0, max_positions - len(positions)):
                    break
                px = float(r.Close)
                notional = min(max_notional, cash * 0.25)
                if px <= 0 or notional < px:
                    continue
                qty = notional / px
                fee = notional * cost_bps / 10_000
                if notional + fee > cash:
                    continue
                cash -= notional + fee
                positions[code] = Pos(code=code, qty=qty, entry_price=px, entry_date=pd.Timestamp(date), entry_step=step_idx, peak=px)
                opened += 1
        pos_value = sum(p.qty * prices.get(c, p.entry_price) for c, p in positions.items())
        equity_curve.append({"date": pd.Timestamp(date), "equity": cash + pos_value, "cash": cash, "open_positions": len(positions)})
    # close at last available date
    if equity_curve and positions:
        last_date = equity_curve[-1]["date"]
        g = date_frames.get(last_date)
        prices = {str(r.code).zfill(6): float(r.Close) for r in g.itertuples()} if g is not None else {}
        for code in list(positions):
            pos = positions[code]
            px = prices.get(code, pos.entry_price)
            proceeds = pos.qty * px
            fee = proceeds * cost_bps / 10_000
            cost = pos.qty * pos.entry_price
            cash += proceeds - fee
            trades.append({"code": code, "entry_date": pos.entry_date, "exit_date": pd.Timestamp(last_date), "pnl_pct": (proceeds - fee - cost) / cost * 100, "pnl_krw": proceeds - fee - cost, "exit_reason": "end", "holding_steps": 0})
            del positions[code]
        equity_curve[-1]["equity"] = cash
    eq = pd.DataFrame(equity_curve)
    if eq.empty:
        return {"total_return_pct": 0.0, "mdd_pct": 0.0, "sharpe": 0.0, "trades": 0, "win_rate": 0.0, "final_equity": cfg["initial_cash"], "trade_rows": []}
    final_eq = float(eq["equity"].iloc[-1])
    total_ret = (final_eq / cfg["initial_cash"] - 1.0) * 100
    peak = eq["equity"].cummax()
    mdd = ((eq["equity"] / peak - 1.0) * 100).min()
    rets = eq["equity"].pct_change().dropna()
    sharpe = 0.0 if rets.std(ddof=0) == 0 or len(rets) < 2 else float(rets.mean() / rets.std(ddof=0) * np.sqrt(252))
    wins = sum(1 for t in trades if t["pnl_krw"] > 0)
    return {"total_return_pct": round(total_ret, 2), "mdd_pct": round(float(mdd), 2), "sharpe": round(sharpe, 4), "trades": len(trades), "win_rate": round(wins / len(trades) * 100, 2) if trades else 0.0, "final_equity": round(final_eq, 2), "trade_rows": trades, "equity_curve": equity_curve}


def run_base_like(feat: pd.DataFrame) -> dict[str, Any]:
    # Baseline using vectorized simulator with base_score as prediction; for sanity only.
    pred_cols = list(dict.fromkeys(["Date", "code", "Close", "base_score"] + FEATURES))
    pred = feat[feat["Date"].dt.year >= 2023][pred_cols].copy()
    pred["ml_pred"] = pred["base_score"].astype(float) / 100.0
    return simulate(pred, min_pred=0.55, top_n=4, score_mode="ml", cfg=BASE_CFG)


def main() -> None:
    panel = pd.read_csv(PANEL, dtype={"code": str}, parse_dates=["Date"])
    panel["code"] = panel["code"].astype(str).str.zfill(6)
    feat = prepare_features(panel)
    print(json.dumps({"feature_rows": len(feat), "date_min": str(feat.Date.min().date()), "date_max": str(feat.Date.max().date()), "n_jobs": N_JOBS}, ensure_ascii=False), flush=True)
    base_like = run_base_like(feat)
    rows = []
    trade_rows = []
    models_to_run = ["lgbm", "xgb", "histgb", "extratrees", "rf", "ridge"]
    grids = [
        (0.00, 4, "ml"), (0.01, 4, "ml"), (0.02, 4, "ml"),
        (0.00, 4, "hybrid"), (0.01, 4, "hybrid"), (0.02, 4, "hybrid"),
        (0.00, 4, "penalized_tail"), (0.01, 4, "penalized_tail"),
        (-0.01, 8, "hybrid"), (0.00, 8, "hybrid"),
    ]
    for model_name in models_to_run:
        print(f"TRAIN {model_name}", flush=True)
        pred = make_predictions(feat, model_name)
        pred_csv = OUT_DIR / f"ml_direct_pred_{model_name}_20260621.csv"
        pred.to_csv(pred_csv, index=False)
        for min_pred, top_n, score_mode in grids:
            res = simulate(pred, min_pred=min_pred, top_n=top_n, score_mode=score_mode, cfg=BASE_CFG)
            # independent year slices using same model predictions
            yr = {}
            for y in [2023, 2024, 2025, 2026]:
                yy = pred[pred["Date"].dt.year == y].copy()
                yr_res = simulate(yy, min_pred=min_pred, top_n=top_n, score_mode=score_mode, cfg=BASE_CFG)
                yr[f"y{y}_ret"] = yr_res["total_return_pct"]
                yr[f"y{y}_trades"] = yr_res["trades"]
            worst = min(yr[f"y{y}_ret"] for y in [2023, 2024, 2025, 2026])
            row = {"model": model_name, "min_pred": min_pred, "top_n": top_n, "score_mode": score_mode, "return_30bps": res["total_return_pct"], "mdd_30bps": res["mdd_pct"], "sharpe": res["sharpe"], "trades": res["trades"], "win_rate": res["win_rate"], "final_equity": res["final_equity"], "worst_year_ret": worst, "objective": res["total_return_pct"] + 4 * worst - abs(res["mdd_pct"]), **yr, "pred_csv": str(pred_csv)}
            rows.append(row)
            for t in res["trade_rows"]:
                trade_rows.append({"model": model_name, "min_pred": min_pred, "top_n": top_n, "score_mode": score_mode, **t})
            print(json.dumps(row, ensure_ascii=False), flush=True)
    results = pd.DataFrame(rows).sort_values(["return_30bps", "objective"], ascending=False)
    result_csv = OUT_DIR / "ml_direct_frontier_results_20260621.csv"
    trades_csv = OUT_DIR / "ml_direct_frontier_trades_20260621.csv"
    report_md = OUT_DIR / "ml_direct_frontier_20260621.md"
    results.to_csv(result_csv, index=False)
    pd.DataFrame(trade_rows).to_csv(trades_csv, index=False)
    lines = [
        "# ML direct-entry frontier — 2026-06-21",
        "",
        "Paper/research only. live_order_submitted: False.",
        "",
        "## Method",
        "- Features are vectorized from the 496-code 2022-2026 OHLCV panel.",
        "- Models are trained expanding walk-forward: train prior years, predict next year.",
        "- ML predictions are injected directly into entry ranking every 5 trading days.",
        f"- n_jobs: {N_JOBS}",
        "",
        "## Sanity baseline inside this simulator",
        f"- base_score simulator return: {base_like['total_return_pct']}%",
        f"- MDD: {base_like['mdd_pct']}%",
        f"- Sharpe: {base_like['sharpe']}",
        f"- trades: {base_like['trades']}",
        "",
        "Note: canonical ReplayEngine base from prior report remains +46.00% at 30bps on 2022-2026 extended panel. This direct simulator starts OOS at 2023 due to ML training needs, so compare as research frontier, not promotion.",
        "",
        "## Files",
        f"- results: `{result_csv}`",
        f"- trades: `{trades_csv}`",
        "",
        "## Best by return",
        results.head(20).to_markdown(index=False),
        "",
        "## Best by robustness objective",
        results.sort_values(["objective", "return_30bps"], ascending=False).head(20).to_markdown(index=False),
        "",
        "## Verdict",
        "Discovery run. Promote only after comparing against canonical ReplayEngine over identical dates and adding fixed-candidate walk-forward/cost stress.",
    ]
    report_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"result_csv": str(result_csv), "trades_csv": str(trades_csv), "report_md": str(report_md), "base_like": base_like, "best_return": results.head(10).to_dict(orient="records"), "best_objective": results.sort_values(["objective", "return_30bps"], ascending=False).head(10).to_dict(orient="records")}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
