# Contextual daily trading readiness

Research-only. Not investment advice. Live order submission remains disabled by default.

## What was optimized

The optimizer searches daily buy/sell rules by market situation:

- market direction: up / flat / down, using previous 20 trading days of the sampled equal-weight market proxy
- volatility: high / low, using previous 20-day volatility relative to the training median
- signals: previous-close momentum or reversal, volatility adjusted
- exits: open→close, open→next-open, close→next-close
- liquidity and position count filters

Training period: through 2024-12-31.  
Out-of-sample test period: 2025-01-01 onward.

## Approval rule

A situation is tradable only if its best candidate passes all gates:

- train total return > 0
- test total return > 0
- test Sharpe > 0
- test max drawdown better than -20%

Every other situation becomes **NO_TRADE**.

## Current approved situation

Only one situation passed the gates in the first optimization run:

- `flat_high_vol`
- strategy: 20-day reversal / 20-day volatility
- exit simulation: close→next-close
- top_n: 3
- minimum previous dollar volume: 100,000,000 KRW
- train return: 5.74%
- test return: 3.00%
- test MDD: -11.45%
- test Sharpe: 1.312

Combined policy, with cash in all non-approved regimes:

- all-period total return: 8.91%
- CAGR: 2.16%
- MDD: -20.21%
- Sharpe: 0.23
- active days: 90 / 974
- total trades: 268

This is **not strong enough for live trading**. It is acceptable only as a paper/manual-candidate policy.

## How to generate candidates

```bash
cd /mnt/c/Github/TOSS
. .venv/bin/activate
python scripts/generate_contextual_daily_candidates.py
```

Output is written under:

```text
reports/trade_candidates/
```

If the current situation is not approved, the script returns `NO_TRADE`.

## Live-trading boundary

The generated policy file is:

```text
config/generated_policies/contextual_daily_policy_seed20260607.json
```

It is intentionally configured as:

```json
{
  "mode": "paper_or_manual_draft_only",
  "live_trading_enabled": false
}
```

Do not convert this to real order submission until all of these are done:

1. At least 20 paper-trading candidate days are logged.
2. Paper trading remains positive after fees/taxes/slippage.
3. Broker API current-price/account/order endpoints are verified read-only first.
4. Risk policy has explicit notional, daily-loss, and kill-switch limits.
5. Exact manual confirmation is required for every real order.
6. First live run uses tiny notional only.

## Practical next improvement

The first optimizer shows that frequent trading is still weak. Next useful work:

- reduce trades further
- add stronger filters: gap, liquidity, volatility squeeze/expansion, recent drawdown, market breadth
- tune position sizing by confidence
- run a walk-forward split instead of a single train/test split
- compare against no-trade and buy-and-hold baselines per regime
