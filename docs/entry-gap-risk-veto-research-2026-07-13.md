# Entry gap-risk veto research — 2026-07-13

## Verdict

**REJECT_NO_POLICY_CHANGE**. This research did not change a paper/live policy, position size, notional cap, or risk budget.

## Motivation

Recent daily paper losses were concentrated in one-day holds and stop-loss exits whose realized losses exceeded the configured stop because the next tradable price gapped through the stop. The research objective was to reduce next-session tail loss and drawdown without increasing exposure.

## Leakage correction

The previous fast-veto research used the entry session's `High`, `Low`, `Close`, and `Volume` for a Monday-open decision. Those fields are not available before the Monday open. The corrected research contract is:

- Entry-session input: opening gap versus the previous close only.
- Lagged one trading session: intraday range, dollar volume, 20-day volume surge, and 20-day volatility.
- Intersection candidates can require at least two simultaneous lagged tail-risk flags.
- Monday-open/Friday-close results are not treated as equivalent to the general daily `ReplayEngine`, which enters and exits at close and samples dates by step rather than weekday.

Regression tests confirm that changing the entry session's `High`, `Low`, or `Volume` does not alter the pre-open veto. Relevant tests passed: `17 passed` in the targeted research suite. An independent read-only review ran a broader related set: `34 passed`.

## Frozen-policy parity

The frozen contextual Monday/Friday policy was replayed on the random-500 panel.

- Existing 2022–2025 picks reproduced: 114/114.
- Maximum absolute trade-return difference: approximately `9.7e-17`.
- Frozen-policy trades in 2026: 0.

The recent 2026 daily paper losses therefore belong to a different daily strategy and cannot be used as a 2026 holdout for the frozen Monday/Friday policy.

## Walk-forward and cost stress

OOS years were 2023–2025. Extra round-trip cost stress was 0/10/20/30 bps.

| Variant | OOS return | OOS MDD | Trades | Negative years | Worst-cost return | Promotion |
|---|---:|---:|---:|---:|---:|---|
| Baseline | 81.71% | -22.83% | 108 | 1 | 63.34% | No |
| Leak-free current equivalent | 87.08% | -24.51% | 100 | 0 | 68.18% | No: MDD worsened |
| Two-factor moderate | 113.81% | -26.26% | 71 | 1 | 93.44% | No: MDD/consistency failed |
| Two-factor strict | 131.52% | -23.60% | 50 | 1 | 112.68% | No: sample/consistency failed |

No candidate simultaneously satisfied return, drawdown, stress, sample-size, consistency, and 2026-holdout requirements.

## Daily-paper reproducibility blocker

The 2026 daily paper baseline could not be reproduced from the mutable current practical-universe panel.

- Archived July 1 summary: +26.3141%, MDD -20.8032%, 253 closed trades.
- Current-panel replay: +37.3416%, MDD -23.3711%, 254 closed trades.
- First divergence: 2026-01-26.
- The archived signal and order contain code `056080` at Open 28,500 / High 33,000 / Low 25,675 / Close 31,850, but the current panel has no corresponding row.
- The Git `HEAD` panel contains that exact historical row, confirming input-universe drift rather than a trading-rule difference.

A 2026 daily veto result is invalid until the historical panel/universe snapshot is restored and baseline parity is exact.

## Additional independent-review blockers

1. `generate_contextual_daily_candidates.py` computes features before slicing to `as_of`; a full-sample volatility median can include future dates.
2. Year-based ML training cutoffs can retain final rows whose five-day forward labels cross into the next test year. Label-end-date purge/embargo is required.
3. `ReplayEngine.transaction_cost_bps` is charged on both entry and exit. A value of 30 means about 60 bps round trip, not the promoted policy's 31 bps round trip.
4. Replay trade P&L may omit entry fees, and end-of-replay forced-liquidation costs may be absent from final equity.
5. `all_dates[::step]` does not enforce Monday/Friday calendar parity and drifts around holidays.
6. If an opening gap is observed and then vetoed, a permitted fill cannot simultaneously assume the already-observed opening print without a conservative sequencing/slippage convention.

## Required work before reconsideration

- Preserve immutable panel, universe, signal, policy, and cost snapshots per run.
- Restore exact daily-paper baseline parity.
- Slice panels before `as_of` feature computation and use expanding/train-only regime thresholds.
- Purge labels by `label_next_date` or target-horizon end date.
- Separate entry fee, exit fee, slippage, and sell tax; reconcile trade P&L with final equity.
- Add explicit exchange-calendar Monday-open/Friday-close tests, including holidays.
- Re-run expanding walk-forward and cost stress without increasing position size or risk budget.

## Evidence

- `reports/harness/entry_gap_veto_research_20260713T082006Z.json`
- `reports/harness/entry_gap_veto_research_20260713T082006Z.md`
- `scripts/run_entry_gap_veto_research.py`
- `scripts/run_daily_entry_veto_replay.py`
- `src/toss_alpha/research/profit_loop.py`
- `tests/test_profit_research_loop.py`
