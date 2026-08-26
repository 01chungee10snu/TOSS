"""Validate broker-executable Korean ETF portfolios with whole shares.

Rules fixed before comparison:
- exact ETF identities are verified from FinanceDataReader ETF/KR listing;
- only domestic KRX-listed ETFs with long OHLCV history are used;
- initial capital equals the live account snapshot (391,722 KRW);
- orders use integer shares only;
- month-end target weights are executed at the next trading day's open;
- transaction costs are charged on actually traded notional;
- cost stresses 31/50/75 bps are charged on each actually traded notional;
- no inverse/leveraged ETF and no fractional quantities.

This script is research-only and never submits an order.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import FinanceDataReader as fdr
import numpy as np
import pandas as pd
import yfinance as yf

BASE = Path(__file__).resolve().parents[1]
DATA_DIR = BASE / "reports" / "backtests" / "executable_etf"
REPORT_DIR = BASE / "reports" / "validation"
DATA_DIR.mkdir(parents=True, exist_ok=True)
REPORT_DIR.mkdir(parents=True, exist_ok=True)

START = "2015-01-01"
END = "2026-08-12"
INITIAL_CAPITAL = 391_722.0
MAX_POSITION_PCT = 0.50
PER_TRADE_NOTIONAL_COST_BPS = (31, 50, 75)
EXPECTED_NAMES = {
    "069500": "KODEX 200",
    "226490": "KODEX 코스피",
    "153130": "KODEX 단기채권",
    "214980": "KODEX 단기채권PLUS",
}
STRATEGIES = {
    "kodex200_100": {"069500": 1.0},
    "kodex_kospi_100": {"226490": 1.0},
    "kodex200_60_bond_40": {"069500": 0.60, "153130": 0.40},
    "kodex200_60_bondplus_40": {"069500": 0.60, "214980": 0.40},
    "kodex200_50_bond_50": {"069500": 0.50, "153130": 0.50},
    "kodex200_40_bond_60": {"069500": 0.40, "153130": 0.60},
    "kodex_kospi_60_bond_40": {"226490": 0.60, "153130": 0.40},
    "kodex_kospi_50_bond_50": {"226490": 0.50, "153130": 0.50},
    "kodex_kospi_40_bond_60": {"226490": 0.40, "153130": 0.60},
}


def allocate_integer_shares(
    *,
    equity: float,
    prices: Mapping[str, float],
    target_weights: Mapping[str, float],
    cost_bps_per_side: float,
    max_position_pct: float = 1.0,
) -> dict[str, Any]:
    """Allocate whole shares without borrowing or overspending.

    Initial floor allocation is followed by a bounded one-share improvement pass.
    The pass buys the affordable share that most reduces target-notional error.
    """
    if equity < 0:
        raise ValueError("equity must be non-negative")
    if not target_weights or sum(target_weights.values()) > 1.0000001:
        raise ValueError("target weights must be non-empty and sum to <= 1")
    side_rate = float(cost_bps_per_side) / 10_000.0
    quantities: dict[str, int] = {}
    spent = 0.0

    for code, weight in target_weights.items():
        price = float(prices.get(code, 0.0) or 0.0)
        if price <= 0 or weight <= 0:
            quantities[code] = 0
            continue
        unit_cost = price * (1.0 + side_rate)
        target_cap = min(equity * float(weight), equity * float(max_position_pct))
        qty = max(0, math.floor(target_cap / unit_cost))
        quantities[code] = int(qty)
        spent += qty * unit_cost

    # Improve allocation using remaining cash, but never buy above target notional
    # unless no asset currently has one share and it is the only feasible exposure.
    while True:
        remaining = equity - spent
        choices: list[tuple[float, str, float]] = []
        for code, weight in target_weights.items():
            price = float(prices.get(code, 0.0) or 0.0)
            unit_cost = price * (1.0 + side_rate)
            if price <= 0 or unit_cost > remaining + 1e-9 or weight <= 0:
                continue
            current_notional = quantities.get(code, 0) * price
            target_notional = equity * float(weight)
            position_cap = equity * float(max_position_pct)
            if current_notional + price > position_cap + 1e-9:
                continue
            before = abs(target_notional - current_notional)
            after = abs(target_notional - (current_notional + price))
            improvement = before - after
            if improvement > 0:
                choices.append((improvement, code, unit_cost))
        if not choices:
            break
        _, code, unit_cost = max(choices, key=lambda x: (x[0], x[1]))
        quantities[code] = quantities.get(code, 0) + 1
        spent += unit_cost

    return {
        "quantities": quantities,
        "total_spend": spent,
        "cash_after": equity - spent,
        "cost_bps_per_side": float(cost_bps_per_side),
    }


def build_rebalance_orders(*, current: Mapping[str, int], target: Mapping[str, int]) -> list[dict[str, Any]]:
    """Return whole-share sells first, then buys."""
    codes = sorted(set(current) | set(target))
    sells: list[dict[str, Any]] = []
    buys: list[dict[str, Any]] = []
    for code in codes:
        delta = int(target.get(code, 0)) - int(current.get(code, 0))
        if delta < 0:
            sells.append({"code": code, "side": "SELL", "quantity": -delta})
        elif delta > 0:
            buys.append({"code": code, "side": "BUY", "quantity": delta})
    return sells + buys


def verified_listing() -> tuple[pd.DataFrame, dict[str, dict[str, Any]]]:
    listing = fdr.StockListing("ETF/KR").copy()
    listing["Symbol"] = listing["Symbol"].astype(str).str.zfill(6)
    result: dict[str, dict[str, Any]] = {}
    for code, expected in EXPECTED_NAMES.items():
        row = listing[listing["Symbol"] == code]
        if row.empty:
            raise RuntimeError(f"ETF not found in listing: {code}")
        rec = row.iloc[0].to_dict()
        actual = str(rec.get("Name") or "")
        if actual != expected:
            raise RuntimeError(f"ETF identity mismatch {code}: expected={expected!r} actual={actual!r}")
        result[code] = {
            "code": code,
            "name": actual,
            "current_price": float(rec.get("Price") or 0),
            "current_volume": float(rec.get("Volume") or 0),
            "current_amount_million_krw": float(rec.get("Amount") or 0),
            "market_cap_100m_krw": float(rec.get("MarCap") or 0),
        }
    return listing, result


def collect_panel() -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for code, name in EXPECTED_NAMES.items():
        # Whole-share sizing requires raw historical trade prices. FDR's ETF
        # series is back-adjusted for distributions and changes affordability.
        df = yf.Ticker(f"{code}.KS").history(
            start=START, end="2026-08-13", auto_adjust=False, actions=True
        ).reset_index()
        if df.empty:
            raise RuntimeError(f"empty OHLCV: {code} {name}")
        df.columns = [str(c).lower() for c in df.columns]
        df["code"] = code
        df["name"] = name
        if "dividends" not in df.columns:
            df["dividends"] = 0.0
        if "stock splits" not in df.columns:
            df["stock splits"] = 0.0
        required = ["date", "open", "high", "low", "close", "volume", "dividends", "stock splits", "code", "name"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise RuntimeError(f"missing columns {code}: {missing}")
        df = df[required]
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        for col in ("open", "high", "low", "close", "volume", "dividends", "stock splits"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        if (df["stock splits"].fillna(0) != 0).any():
            raise RuntimeError(f"unhandled stock split: {code}")
        if (df[["open", "close"]].dropna() <= 0).any().any():
            raise RuntimeError(f"non-positive prices: {code}")
        rows.append(df)
    panel = pd.concat(rows, ignore_index=True).sort_values(["date", "code"])
    panel.to_csv(DATA_DIR / "verified_etf_unadjusted_panel_2015_2026.csv", index=False)
    return panel


@dataclass
class Result:
    strategy: str
    per_trade_notional_cost_bps: int
    equity: pd.DataFrame
    trades: list[dict[str, Any]]
    rebalances: list[dict[str, Any]]

    def summary(self) -> dict[str, Any]:
        eq = self.equity.copy()
        returns = eq["equity"].pct_change().dropna()
        total = eq["equity"].iloc[-1] / INITIAL_CAPITAL - 1.0
        years = (eq["date"].iloc[-1] - eq["date"].iloc[0]).days / 365.2425
        cagr = (eq["equity"].iloc[-1] / INITIAL_CAPITAL) ** (1 / years) - 1 if years > 0 else 0.0
        peak = eq["equity"].cummax()
        dd = eq["equity"] / peak - 1.0
        ann_vol = returns.std(ddof=1) * np.sqrt(252) if len(returns) > 1 else 0.0
        sharpe = ((returns.mean() * 252) / ann_vol) if ann_vol > 0 else 0.0
        eq["year"] = eq["date"].dt.year
        yearly: dict[str, float] = {}
        for year, group in eq.groupby("year"):
            if len(group) > 1:
                yearly[str(int(year))] = round((group.equity.iloc[-1] / group.equity.iloc[0] - 1) * 100, 2)
        monthly = eq.set_index("date")["equity"].resample("ME").last().pct_change().dropna()
        total_cost = sum(float(t["cost_krw"]) for t in self.trades)
        return {
            "strategy": self.strategy,
            "per_trade_notional_cost_bps": self.per_trade_notional_cost_bps,
            "start": eq.date.iloc[0].date().isoformat(),
            "end": eq.date.iloc[-1].date().isoformat(),
            "initial_capital_krw": INITIAL_CAPITAL,
            "final_equity_krw": round(float(eq.equity.iloc[-1]), 0),
            "total_return_pct": round(total * 100, 2),
            "cagr_pct": round(cagr * 100, 2),
            "sharpe_zero_rf": round(float(sharpe), 4),
            "annual_vol_pct": round(float(ann_vol * 100), 2),
            "max_drawdown_pct": round(float(dd.min() * 100), 2),
            "positive_months_pct": round(float((monthly > 0).mean() * 100), 1) if len(monthly) else 0.0,
            "rebalances": len(self.rebalances),
            "trade_legs": len(self.trades),
            "total_cost_krw": round(total_cost, 0),
            "yearly_returns_pct": yearly,
            "ending_quantities": self.rebalances[-1]["target_quantities"] if self.rebalances else {},
        }


def run_backtest(panel: pd.DataFrame, strategy: str, weights: Mapping[str, float], per_trade_notional_bps: int) -> Result:
    subset = panel[panel.code.isin(weights)].copy()
    opens = subset.pivot(index="date", columns="code", values="open").sort_index()
    closes = subset.pivot(index="date", columns="code", values="close").sort_index()
    dividends = subset.pivot(index="date", columns="code", values="dividends").sort_index().fillna(0.0)
    # Require synchronous dates so a portfolio is never valued using stale/missing marks.
    common = opens.dropna().index.intersection(closes.dropna().index)
    opens = opens.loc[common]
    closes = closes.loc[common]
    if len(common) < 252:
        raise RuntimeError(f"insufficient common history for {strategy}: {len(common)}")

    holdings = {code: 0 for code in weights}
    cash = INITIAL_CAPITAL
    side_bps = float(per_trade_notional_bps)
    side_rate = side_bps / 10_000.0
    trades: list[dict[str, Any]] = []
    rebalances: list[dict[str, Any]] = []
    curve: list[dict[str, Any]] = []

    months = pd.Series(common.to_period("M"), index=common)
    signal_dates = set(months.groupby(months).apply(lambda x: x.index[-1]).tolist())
    pending = True  # initial allocation on first available open

    for i, date in enumerate(common):
        open_prices = {code: float(opens.at[date, code]) for code in weights}
        close_prices = {code: float(closes.at[date, code]) for code in weights}
        # Shares held entering the ex-date receive the distribution. Credit it
        # after today's open rebalance so a same-day buyer cannot receive it.
        dividend_cash = sum(holdings[c] * float(dividends.at[date, c]) for c in weights)

        if pending:
            # Equity at executable open; existing positions are marked at that open.
            equity_open = cash + sum(holdings[c] * open_prices[c] for c in weights)
            allocation = allocate_integer_shares(
                equity=equity_open,
                prices=open_prices,
                target_weights=weights,
                cost_bps_per_side=side_bps,
                max_position_pct=MAX_POSITION_PCT,
            )
            target = allocation["quantities"]
            orders = build_rebalance_orders(current=holdings, target=target)

            # Sell first, then buy, exactly as returned by build_rebalance_orders.
            for order in orders:
                code = order["code"]
                qty = int(order["quantity"])
                price = open_prices[code]
                notional = qty * price
                cost = notional * side_rate
                if order["side"] == "SELL":
                    cash += notional - cost
                    holdings[code] -= qty
                else:
                    debit = notional + cost
                    if debit > cash + 1e-6:
                        raise RuntimeError(f"overspend {date} {code}: debit={debit} cash={cash}")
                    cash -= debit
                    holdings[code] += qty
                trades.append({
                    "date": date.date().isoformat(), "code": code, "side": order["side"],
                    "quantity": qty, "price": price, "notional_krw": notional, "cost_krw": cost,
                })
            rebalances.append({
                "date": date.date().isoformat(),
                "signal_date": None if i == 0 else common[i - 1].date().isoformat(),
                "target_quantities": dict(holdings),
                "cash_after": cash,
            })
            pending = False

        cash += dividend_cash
        equity_close = cash + sum(holdings[c] * close_prices[c] for c in weights)
        if cash < -1e-6 or any((not isinstance(q, int) or q < 0) for q in holdings.values()):
            raise RuntimeError("portfolio invariant violated")
        curve.append({
            "date": date, "equity": equity_close, "cash": cash,
            **{f"qty_{code}": holdings[code] for code in weights},
        })

        # Month-end signal is known after today's close and executes next common open.
        if date in signal_dates and i + 1 < len(common):
            pending = True

    return Result(strategy, per_trade_notional_bps, pd.DataFrame(curve), trades, rebalances)


def main() -> None:
    _, identities = verified_listing()
    panel = collect_panel()

    rows: list[dict[str, Any]] = []
    details: dict[str, Any] = {}
    for name, weights in STRATEGIES.items():
        for bps in PER_TRADE_NOTIONAL_COST_BPS:
            result = run_backtest(panel, name, weights, bps)
            summary = result.summary()
            rows.append(summary)
            details[f"{name}_{bps}bp"] = {
                "summary": summary,
                "trades": result.trades,
                "rebalances": result.rebalances,
            }

    # Promotion is intentionally strict: benchmark-positive, non-catastrophic drawdown,
    # positive performance at every cost stress, and at least 50% positive calendar years.
    table = pd.DataFrame(rows)
    promotions: dict[str, dict[str, Any]] = {}
    for name in STRATEGIES:
        group = table[table.strategy == name].sort_values("per_trade_notional_cost_bps")
        stress = group[group.per_trade_notional_cost_bps == max(PER_TRADE_NOTIONAL_COST_BPS)].iloc[0]
        years = stress["yearly_returns_pct"]
        positive_year_share = sum(v > 0 for v in years.values()) / max(1, len(years))
        paper_candidate = bool(
            (group.total_return_pct > 0).all()
            and stress.max_drawdown_pct > -35.0
            and stress.sharpe_zero_rf > 0.20
            and positive_year_share >= 0.60
        )
        promotions[name] = {
            "paper_candidate_passed": paper_candidate,
            "live_promotion_passed": False,
            "live_block_reason": "forward_paper_and_orderbook_depth_evidence_missing",
            "stress_75bp": stress.to_dict(),
            "positive_year_share": round(positive_year_share, 3),
            "required": {"mdd_gt_pct": -35.0, "sharpe_gt": 0.20, "positive_year_share_gte": 0.60},
        }

    # At the current live equity, several nominal target weights collapse to
    # the same whole-share portfolio. Record equivalence instead of counting
    # those as independent strategy confirmations.
    current_prices = {code: float(info["current_price"]) for code, info in identities.items()}
    current_allocations: dict[str, Any] = {}
    equivalence: dict[str, list[str]] = {}
    for name, weights in STRATEGIES.items():
        allocation = allocate_integer_shares(
            equity=INITIAL_CAPITAL,
            prices=current_prices,
            target_weights=weights,
            cost_bps_per_side=max(PER_TRADE_NOTIONAL_COST_BPS),
            max_position_pct=MAX_POSITION_PCT,
        )
        signature = ",".join(f"{code}:{qty}" for code, qty in sorted(allocation["quantities"].items()))
        current_allocations[name] = allocation
        equivalence.setdefault(signature, []).append(name)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "live_buy_hold_unchanged": True,
        "initial_capital_krw": INITIAL_CAPITAL,
        "max_position_pct": MAX_POSITION_PCT,
        "execution": "month-end signal -> next common trading-day open; whole shares; sell before buy",
        "cost_stress_per_traded_notional_bps": list(PER_TRADE_NOTIONAL_COST_BPS),
        "identities": identities,
        "strategies": STRATEGIES,
        "results": rows,
        "promotions": promotions,
        "current_allocations_at_75bp": current_allocations,
        "whole_share_equivalence_classes": equivalence,
        "details": details,
    }
    out = REPORT_DIR / "executable_etf_portfolio_latest.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    table.to_csv(REPORT_DIR / "executable_etf_portfolio_latest.csv", index=False)

    cols = ["strategy", "per_trade_notional_cost_bps", "total_return_pct", "cagr_pct", "sharpe_zero_rf", "max_drawdown_pct", "positive_months_pct", "final_equity_krw"]
    print(table[cols].to_string(index=False))
    print("\nPaper-candidate gates (live remains blocked):")
    for name, decision in promotions.items():
        print(f"  {name}: {'PASS' if decision['paper_candidate_passed'] else 'FAIL'} / LIVE=BLOCKED")
    print(f"\nReport: {out}")


if __name__ == "__main__":
    main()
