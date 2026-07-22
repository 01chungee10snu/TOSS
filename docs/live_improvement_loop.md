# TOSS guarded live improvement loop

## Objective

Keep the deployed `daily_multifactor_v1_practical400` policy trading while separating live FIFO evidence from backtest evidence. Live losses create research work; they do not directly tune or replace the live policy.

## Runtime flow

1. `toss-ttak-loop.sh` refreshes `reports/harness/live_performance_gate.json` before each intraday tick.
2. `append_position_exit_orders` reads that artifact.
3. A blocked gate removes BUY orders only. Existing and newly generated SELL exits remain eligible for the normal execution safety gates.
4. The account equity guard independently blocks BUYs and liquidates holdings at a 6% peak-to-current drawdown, followed by an 8-day cooldown.
5. `toss_market_close_settlement.sh` writes the daily settlement and then refreshes the performance gate/report.
6. Friday at 16:40 KST, Hermes cron job `TOSS weekly guarded improvement research` performs research-only review and writes candidate artifacts without changing the live policy.

## Live performance thresholds

| Rule | Action |
|---|---|
| Fewer than 20 settlement days or 30 fills | `PROBATION_CONTINUE` |
| Deployment-policy cumulative matched FIFO realized loss <= -3% of deployment baseline equity | Block new BUYs |
| Three consecutive losing fill days | Block new BUYs |
| Unmatched FIFO SELL quantity after deployment day | Block new BUYs |
| Corrupt or older-than-96-hour gate artifact | Block new BUYs |
| Deployment-day unmatched legacy inventory | Report separately; do not freeze the new policy |
| Account drawdown from recorded peak >= 6% | Block BUYs, liquidate holdings, 8-day cooldown |

## Promotion contract

A research candidate may be written as `CANDIDATE_ONLY` only after all available checks pass:

- point-in-time universe including delisted securities;
- no future-information or adjusted-price leakage;
- out-of-sample walk-forward evaluation;
- positive yearly robustness where required;
- at least 31 bps per-side cost stress;
- acceptable drawdown and sufficient trade count.

No scheduled research job may submit orders, edit the live policy file, or promote a candidate automatically. Live promotion remains an explicit audited operation.

## Operational artifacts

- `reports/harness/live_performance_gate.json`
- `reports/harness/live_performance_gate.md`
- `reports/harness/settlements/market_close_settlement_YYYYMMDD.json`
- `reports/harness/latest_position_exit_report.json`
- `reports/harness/improvement_reviews/weekly_review_YYYYMMDD.{md,json}`
- `reports/harness/improvement_reviews/promotion_candidate_YYYYMMDD.json` (only when all gates pass)

## Current deployment

- Policy: `daily_multifactor_v1_practical400`
- Deployment baseline date: `2026-07-20`
- Inverse sleeve: disabled
- Live policy auto-replacement: disabled
