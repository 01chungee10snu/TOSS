"""Cumulative replay engine — empirical validation of daily decision logic.

Loads a full OHLCV panel, steps through historical dates, reuses the same
scoring functions from ``decision.py``, and tracks cumulative paper P&L.

Safety: this is paper-only. No live orders, no broker calls.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

import pandas as pd

from toss_alpha.daily.decision import _classify_regime, _score_candidates

DEFAULT_REPLAY_DIR = Path("reports/replay")

SCORE_THRESHOLD = 70.0
STOP_LOSS_PCT = 0.05
TAKE_PROFIT_PCT = 0.08
MAX_HOLDING_STEPS = 20


@dataclass
class _Position:
    symbol: str
    quantity: float
    entry_price: float
    entry_date: str
    entry_step: int
    peak_price: float = 0.0  # for trailing stop
    ml_prediction: float | None = None


@dataclass
class _ClosedTrade:
    symbol: str
    side: str
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    quantity: float
    pnl_krw: float
    pnl_pct: float
    holding_steps: int
    exit_reason: str
    ml_prediction: float | None = None


class ReplayEngine:
    """In-memory cumulative replay of the daily decision scoring logic."""

    def __init__(
        self,
        *,
        panel: pd.DataFrame,
        symbols: list[str],
        initial_cash_krw: float = 1_000_000,
        max_notional_krw: float = 100_000,
        score_threshold: float = SCORE_THRESHOLD,
        stop_loss_pct: float = STOP_LOSS_PCT,
        take_profit_pct: float = TAKE_PROFIT_PCT,
        max_holding_steps: int = MAX_HOLDING_STEPS,
        max_positions: int = 1,
        trailing_stop_pct: float = 0.0,
        sizing_mode: str = "flat",
        min_volume: float = 0.0,
        transaction_cost_bps: float = 0.0,
        rebalance_mode: str = "hold_until_exit",
        prediction_map: dict[str, dict[str, float]] | None = None,
        prediction_min_score: float | None = None,
        prediction_overlay_mode: str | None = None,
        prediction_alpha: float = 10.0,
    ) -> None:
        self.panel = panel.copy()
        self.panel["code"] = self.panel["code"].astype(str).str.zfill(6)
        self.symbols = [str(s).zfill(6) for s in symbols]
        self.initial_cash_krw = initial_cash_krw
        self.max_notional_krw = max_notional_krw
        self.score_threshold = score_threshold
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.max_holding_steps = max_holding_steps
        self.max_positions = max_positions
        self.trailing_stop_pct = trailing_stop_pct
        self.sizing_mode = sizing_mode
        self.min_volume = min_volume
        self.transaction_cost_bps = transaction_cost_bps
        self.total_cost_krw = 0.0
        self.prediction_map = self._normalize_prediction_map(prediction_map)
        self.prediction_min_score = prediction_min_score
        self.prediction_overlay_mode = prediction_overlay_mode
        self.prediction_alpha = prediction_alpha
        if rebalance_mode not in {"hold_until_exit", "top_n_rotation", "full_liquidate_every_step"}:
            raise ValueError(f"unsupported rebalance_mode: {rebalance_mode}")
        self.rebalance_mode = rebalance_mode

        self.cash = initial_cash_krw
        self.open_positions: dict[str, _Position] = {}
        self.closed_trades: list[_ClosedTrade] = []
        self.equity_curve: list[dict[str, Any]] = []

    def run(self, *, step: int = 5) -> dict[str, Any]:
        """Step through all available dates, scoring and trading."""
        all_dates = sorted(self.panel["Date"].unique())
        replay_dates = all_dates[::step]

        for step_idx, ts in enumerate(replay_dates):
            step_date = pd.Timestamp(ts)
            date_str = step_date.date().isoformat()

            sub = self.panel[self.panel["Date"] <= step_date].copy()
            if sub.empty:
                continue

            regime = _classify_regime(sub)
            candidates = _score_candidates(sub, regime=regime)
            candidates.sort(key=lambda c: c["final_score"], reverse=True)
            candidates = self._apply_prediction_ranking(candidates, date_str)

            close_prices = self._latest_close_prices(sub, date_str)

            # --- forced rebalance modes ---
            if step_idx > 0:
                self._apply_rebalance(candidates, close_prices, date_str, step_idx)

            # --- exits first --- (also counts current open positions for entry logic)
            self._check_exits(close_prices, date_str, step_idx, regime)

            # --- entries ---
            if regime["status"] != "risk_off" and len(self.open_positions) < self.max_positions:
                volume_lookup = self._latest_volumes(sub)
                self._check_entries(candidates, close_prices, date_str, step_idx, volume_lookup)

            # --- equity mark ---
            positions_value = sum(
                pos.quantity * close_prices.get(pos.symbol, pos.entry_price)
                for pos in self.open_positions.values()
            )
            equity = self.cash + positions_value
            self.equity_curve.append({
                "date": date_str,
                "step": step_idx,
                "equity": round(equity, 2),
                "cash": round(self.cash, 2),
                "positions_value": round(positions_value, 2),
                "open_positions": len(self.open_positions),
                "regime": regime["status"],
            })

        # close remaining positions at last available prices
        if self.open_positions:
            last_sub = self.panel.copy()
            last_date = pd.Timestamp(all_dates[-1]).date().isoformat()
            last_prices = self._latest_close_prices(last_sub, last_date)
            for symbol in list(self.open_positions):
                pos = self.open_positions[symbol]
                px = last_prices.get(symbol, pos.entry_price)
                self._close_position(pos, px, last_date, len(replay_dates), "end_of_replay")

        return self._build_result(len(replay_dates))

    # -- internal --

    def _normalize_prediction_map(
        self,
        prediction_map: dict[str, dict[str, float]] | None,
    ) -> dict[str, dict[str, float]]:
        if not prediction_map:
            return {}
        normalized: dict[str, dict[str, float]] = {}
        for raw_date, by_symbol in prediction_map.items():
            date_str = pd.Timestamp(raw_date).date().isoformat()
            normalized[date_str] = {
                str(symbol).zfill(6): float(score)
                for symbol, score in by_symbol.items()
            }
        return normalized

    def _apply_prediction_ranking(
        self,
        candidates: list[dict[str, Any]],
        date_str: str,
    ) -> list[dict[str, Any]]:
        if not self.prediction_map:
            return candidates
        scores = self.prediction_map.get(date_str, {})

        mode = self.prediction_overlay_mode

        if mode is None:
            # Replacement mode (original behavior): ML replaces base ranking entirely
            ranked: list[dict[str, Any]] = []
            for candidate in candidates:
                symbol = str(candidate["symbol"]).zfill(6)
                prediction = scores.get(symbol)
                if prediction is None:
                    continue
                if self.prediction_min_score is not None and prediction < self.prediction_min_score:
                    continue
                row = dict(candidate)
                row["symbol"] = symbol
                row["ml_prediction"] = float(prediction)
                ranked.append(row)
            ranked.sort(key=lambda c: (c["ml_prediction"], c["final_score"]), reverse=True)
            return ranked

        if mode == "rerank":
            # Overlay: keep ALL base candidates (even without predictions),
            # but re-order by ml_pred first, base_score as tiebreaker.
            enriched = []
            for candidate in candidates:
                symbol = str(candidate["symbol"]).zfill(6)
                prediction = scores.get(symbol, 0.0)
                row = dict(candidate)
                row["symbol"] = symbol
                row["ml_prediction"] = float(prediction)
                enriched.append(row)
            enriched.sort(key=lambda c: (c["ml_prediction"], c["final_score"]), reverse=True)
            return enriched

        if mode == "gate":
            # Overlay: require BOTH base_score >= threshold AND ml_pred >= min.
            # Ranking stays by base final_score.
            gated = []
            for candidate in candidates:
                symbol = str(candidate["symbol"]).zfill(6)
                prediction = scores.get(symbol)
                if prediction is None:
                    continue
                if self.prediction_min_score is not None and prediction < self.prediction_min_score:
                    continue
                row = dict(candidate)
                row["symbol"] = symbol
                row["ml_prediction"] = float(prediction)
                gated.append(row)
            gated.sort(key=lambda c: c["final_score"], reverse=True)
            return gated

        if mode == "penalty":
            # Overlay: adjusted_score = base_score + alpha * ml_pred.
            # Candidates without predictions keep their base_score.
            adjusted = []
            for candidate in candidates:
                symbol = str(candidate["symbol"]).zfill(6)
                prediction = scores.get(symbol, 0.0)
                row = dict(candidate)
                row["symbol"] = symbol
                row["ml_prediction"] = float(prediction)
                row["final_score"] = row["final_score"] + self.prediction_alpha * float(prediction)
                adjusted.append(row)
            adjusted.sort(key=lambda c: c["final_score"], reverse=True)
            return adjusted

        raise ValueError(f"unsupported prediction_overlay_mode: {mode}")

    def _latest_close_prices(self, sub: pd.DataFrame, date_str: str) -> dict[str, float]:
        """Get the most recent close price for each symbol on or before date_str."""
        prices: dict[str, float] = {}
        for symbol, group in sub.groupby("code"):
            ordered = group.sort_values("Date")
            row = ordered.iloc[-1]
            prices[str(symbol)] = float(row["Close"])
        return prices

    def _latest_volumes(self, sub: pd.DataFrame) -> dict[str, float]:
        """Get the most recent volume for each symbol."""
        volumes: dict[str, float] = {}
        if "Volume" not in sub.columns:
            return volumes
        for symbol, group in sub.groupby("code"):
            ordered = group.sort_values("Date")
            row = ordered.iloc[-1]
            volumes[str(symbol)] = float(row.get("Volume", 0))
        return volumes

    def _apply_rebalance(
        self,
        candidates: list[dict[str, Any]],
        close_prices: dict[str, float],
        date_str: str,
        step_idx: int,
    ) -> None:
        """Apply forced rebalance policy before normal exits/entries."""
        if not self.open_positions or self.rebalance_mode == "hold_until_exit":
            return

        if self.rebalance_mode == "full_liquidate_every_step":
            for symbol in list(self.open_positions):
                pos = self.open_positions[symbol]
                px = close_prices.get(symbol, pos.entry_price)
                self._close_position(pos, px, date_str, step_idx, "rebalance_liquidate")
            return

        if self.rebalance_mode == "top_n_rotation":
            top_symbols = [
                c["symbol"]
                for c in candidates
                if c["final_score"] >= self.score_threshold
            ][: self.max_positions]
            top_symbols = set(top_symbols)
            for symbol in list(self.open_positions):
                if symbol not in top_symbols:
                    pos = self.open_positions[symbol]
                    px = close_prices.get(symbol, pos.entry_price)
                    self._close_position(pos, px, date_str, step_idx, "rebalance_rotation")

    def _check_exits(
        self,
        close_prices: dict[str, float],
        date_str: str,
        step_idx: int,
        regime: dict[str, Any],
    ) -> None:
        for symbol in list(self.open_positions):
            pos = self.open_positions[symbol]
            current_price = close_prices.get(symbol)
            if current_price is None:
                continue

            # update peak price for trailing stop
            if current_price > pos.peak_price:
                pos.peak_price = current_price

            pnl_pct = current_price / pos.entry_price - 1.0
            holding_steps = step_idx - pos.entry_step

            exit_reason = None
            if pnl_pct <= -self.stop_loss_pct:
                exit_reason = "stop_loss"
            elif pnl_pct >= self.take_profit_pct:
                exit_reason = "take_profit"
            elif (
                self.trailing_stop_pct > 0.0
                and pos.peak_price > pos.entry_price
                and current_price <= pos.peak_price * (1.0 - self.trailing_stop_pct)
            ):
                exit_reason = "trailing_stop"
            elif holding_steps >= self.max_holding_steps:
                exit_reason = "time_exit"
            elif regime["status"] == "risk_off":
                exit_reason = "regime_risk_off"

            if exit_reason:
                self._close_position(pos, current_price, date_str, step_idx, exit_reason)

    def _close_position(
        self,
        pos: _Position,
        exit_price: float,
        exit_date: str,
        exit_step: int,
        reason: str,
    ) -> None:
        proceeds = pos.quantity * exit_price
        exit_fee = proceeds * self.transaction_cost_bps / 10_000
        cost = pos.quantity * pos.entry_price
        pnl_krw = proceeds - exit_fee - cost
        pnl_pct = pnl_krw / cost if cost else 0.0
        self.cash += proceeds - exit_fee
        self.total_cost_krw += exit_fee
        self.closed_trades.append(_ClosedTrade(
            symbol=pos.symbol,
            side="BUY",
            entry_date=pos.entry_date,
            entry_price=pos.entry_price,
            exit_date=exit_date,
            exit_price=exit_price,
            quantity=pos.quantity,
            pnl_krw=round(pnl_krw, 2),
            pnl_pct=round(pnl_pct * 100, 2),
            holding_steps=exit_step - pos.entry_step,
            exit_reason=reason,
            ml_prediction=pos.ml_prediction,
        ))
        del self.open_positions[pos.symbol]

    def _check_entries(
        self,
        candidates: list[dict[str, Any]],
        close_prices: dict[str, float],
        date_str: str,
        step_idx: int,
        volume_lookup: dict[str, float] | None = None,
    ) -> None:
        entries_this_step = 0
        max_new_entries = max(0, self.max_positions - len(self.open_positions))
        for cand in candidates:
            if entries_this_step >= max_new_entries:
                break
            symbol = cand["symbol"]
            if symbol in self.open_positions:
                continue
            if cand["final_score"] < self.score_threshold:
                # In modes where candidates aren't sorted by final_score,
                # skip rather than break
                if self.prediction_map and self.prediction_overlay_mode in (None, "rerank"):
                    continue
                break  # sorted by score desc

            # volume filter
            if self.min_volume > 0 and volume_lookup:
                vol = volume_lookup.get(symbol, 0)
                if vol < self.min_volume:
                    continue

            price = close_prices.get(symbol)
            if price is None or price <= 0:
                continue

            # position sizing
            if self.sizing_mode == "score_weighted":
                # score 60-100 maps to 50%-100% of max_notional
                weight = max(0.5, min(1.0, (cand["final_score"] - 50) / 50))
                notional = min(self.max_notional_krw * weight, self.cash * 0.25)
            else:
                notional = min(self.max_notional_krw, self.cash * 0.25)

            if notional < price:
                continue
            quantity = notional / price
            cost = quantity * price
            entry_fee = cost * self.transaction_cost_bps / 10_000
            total_entry_cost = cost + entry_fee
            if total_entry_cost > self.cash:
                continue

            self.cash -= total_entry_cost
            self.total_cost_krw += entry_fee
            self.open_positions[symbol] = _Position(
                symbol=symbol,
                quantity=quantity,
                entry_price=price,
                entry_date=date_str,
                entry_step=step_idx,
                peak_price=price,
                ml_prediction=cand.get("ml_prediction"),
            )
            entries_this_step += 1

    def _build_result(self, total_steps: int) -> dict[str, Any]:
        equities = [row["equity"] for row in self.equity_curve]
        final_equity = equities[-1] if equities else self.initial_cash_krw
        total_return_pct = (final_equity - self.initial_cash_krw) / self.initial_cash_krw * 100.0

        # max drawdown
        peak = self.initial_cash_krw
        max_dd = 0.0
        for eq in equities:
            if eq > peak:
                peak = eq
            dd = (eq - peak) / peak * 100.0
            if dd < max_dd:
                max_dd = dd

        # sharpe (simplified — per-step returns)
        step_returns = []
        for i in range(1, len(equities)):
            if equities[i - 1] > 0:
                step_returns.append(equities[i] / equities[i - 1] - 1.0)
        sharpe = 0.0
        if len(step_returns) > 1:
            mu = mean(step_returns)
            sigma = pstdev(step_returns)
            if sigma > 0:
                sharpe = mu / sigma * (252 ** 0.5)

        wins = [t for t in self.closed_trades if t.pnl_krw > 0]
        win_rate = len(wins) / len(self.closed_trades) * 100.0 if self.closed_trades else 0.0

        return {
            "initial_cash_krw": self.initial_cash_krw,
            "total_steps": total_steps,
            "equity_curve": self.equity_curve,
            "trades": [
                {
                    "symbol": t.symbol,
                    "side": t.side,
                    "entry_date": t.entry_date,
                    "entry_price": t.entry_price,
                    "exit_date": t.exit_date,
                    "exit_price": t.exit_price,
                    "quantity": round(t.quantity, 6),
                    "pnl_krw": t.pnl_krw,
                    "pnl_pct": t.pnl_pct,
                    "holding_steps": t.holding_steps,
                    "exit_reason": t.exit_reason,
                    "ml_prediction": t.ml_prediction,
                }
                for t in self.closed_trades
            ],
            "summary": {
                "total_return_pct": round(total_return_pct, 2),
                "max_drawdown_pct": round(max_dd, 2),
                "total_trades": len(self.closed_trades),
                "winning_trades": len(wins),
                "win_rate_pct": round(win_rate, 2),
                "sharpe_ratio": round(sharpe, 4),
                "final_equity_krw": round(final_equity, 2),
                "initial_cash_krw": self.initial_cash_krw,
                "transaction_cost_bps": self.transaction_cost_bps,
                "total_cost_krw": round(self.total_cost_krw, 2),
            },
        }


def run_replay(
    *,
    panel_csv: str | Path,
    symbols: list[str],
    initial_cash_krw: float = 1_000_000,
    max_notional_krw: float = 100_000,
    step: int = 5,
    score_threshold: float = SCORE_THRESHOLD,
    out_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Run cumulative replay on a panel CSV and persist artifacts."""
    panel = pd.read_csv(panel_csv, dtype={"code": str}, parse_dates=["Date"])
    replay_dir = Path(out_dir) if out_dir else DEFAULT_REPLAY_DIR
    replay_dir.mkdir(parents=True, exist_ok=True)

    engine = ReplayEngine(
        panel=panel,
        symbols=symbols,
        initial_cash_krw=initial_cash_krw,
        max_notional_krw=max_notional_krw,
        score_threshold=score_threshold,
    )
    result = engine.run(step=step)

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    stem = f"replay_{timestamp}"
    equity_csv = replay_dir / f"{stem}_equity_curve.csv"
    summary_json = replay_dir / f"{stem}_summary.json"
    report_md = replay_dir / f"{stem}.md"

    curve_df = pd.DataFrame(result["equity_curve"])
    curve_df.to_csv(equity_csv, index=False)
    summary_json.write_text(
        json.dumps(result["summary"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report_md.write_text(_render_report(result, panel_csv=str(panel_csv)), encoding="utf-8")

    result["equity_curve_csv"] = str(equity_csv)
    result["summary_json"] = str(summary_json)
    result["report_md"] = str(report_md)
    return result


def _render_report(result: dict[str, Any], *, panel_csv: str) -> str:
    s = result["summary"]
    lines = [
        "# Cumulative Replay Report\n",
        "Paper simulation only. 실주문 아님. 투자 조언 아님.\n",
        f"## Summary\n",
        f"- initial_cash_krw: {s['initial_cash_krw']:,.0f}",
        f"- final_equity_krw: {s['final_equity_krw']:,.0f}",
        f"- total_return_pct: {s['total_return_pct']:.2f}%",
        f"- max_drawdown_pct: {s['max_drawdown_pct']:.2f}%",
        f"- sharpe_ratio: {s['sharpe_ratio']:.4f}",
        f"- total_trades: {s['total_trades']}",
        f"- winning_trades: {s['winning_trades']}",
        f"- win_rate_pct: {s['win_rate_pct']:.2f}%",
        f"- total_steps: {result['total_steps']}",
        f"- panel_csv: {panel_csv}\n",
        "## Trades\n",
    ]
    trades = result["trades"]
    if not trades:
        lines.append("- 거래 없음\n")
    else:
        for t in trades[:50]:
            lines.append(
                f"- {t['symbol']}: entry={t['entry_date']}@{t['entry_price']:.0f} "
                f"exit={t['exit_date']}@{t['exit_price']:.0f} "
                f"pnl={t['pnl_krw']:,.0f} ({t['pnl_pct']:+.2f}%) "
                f"reason={t['exit_reason']}"
            )
        if len(trades) > 50:
            lines.append(f"... 총 {len(trades)}건\n")
    return "\n".join(lines) + "\n"


# -- test helper (re-used by sweep tests) --

def _make_test_panel(start_date: str = "2024-01-01", days: int = 120, n_symbols: int = 5) -> pd.DataFrame:
    """Generate deterministic OHLCV panel for testing."""
    rows = []
    base_prices = {i: 10_000 + i * 2_000 for i in range(n_symbols)}
    symbols = [str(10_000 + i * 111).zfill(6) for i in range(n_symbols)]
    start = date.fromisoformat(start_date)
    for day_offset in range(days):
        current = start + timedelta(days=day_offset)
        if current.weekday() >= 5:
            continue
        for idx, sym in enumerate(symbols):
            base = base_prices[idx]
            if idx < 2:
                close = base * (1.0 + day_offset * 0.003)
            elif idx == 2:
                close = base * (1.0 + (day_offset % 10 - 5) * 0.001)
            else:
                close = base * (1.0 - day_offset * 0.002)
            rows.append({
                "Date": pd.Timestamp(current),
                "Open": close * 0.999,
                "High": close * 1.005,
                "Low": close * 0.995,
                "Close": close,
                "Volume": 500_000 + day_offset * 10_000,
                "code": sym,
            })
    return pd.DataFrame(rows)
