# Risk/Exit Frontier for 0.5%/day Target

Research/paper only. No live orders submitted.

## Interpretation

- This loop does not add leverage.
- It searches stop-loss, take-profit, trailing-stop, and holding-period settings under cash constraints.
- A config is promotable only if it is positive in all 2024/2025/2026 years and does not exceed the risk gate.

## Robust leaderboard

- fusion_rerank notional=150,000 maxpos=8 sl=0.06 tp=0.15 tr=0.00 hold=10: mean_ret=56.34%, min_ret=11.45%, mean_daily=0.224%, mdd=-17.97%, sharpe=4.51, target_years=1/3, all_positive=True
- fusion_rerank notional=150,000 maxpos=8 sl=0.06 tp=0.15 tr=0.00 hold=20: mean_ret=55.34%, min_ret=11.45%, mean_daily=0.220%, mdd=-17.97%, sharpe=4.47, target_years=1/3, all_positive=True
- ml_rerank notional=150,000 maxpos=8 sl=0.10 tp=0.25 tr=0.06 hold=10: mean_ret=66.20%, min_ret=26.36%, mean_daily=0.263%, mdd=-17.04%, sharpe=5.71, target_years=0/3, all_positive=True
- fusion_rerank notional=150,000 maxpos=8 sl=0.06 tp=0.25 tr=0.06 hold=20: mean_ret=66.00%, min_ret=37.81%, mean_daily=0.262%, mdd=-8.82%, sharpe=6.16, target_years=0/3, all_positive=True
- ml_rerank notional=150,000 maxpos=8 sl=0.10 tp=0.25 tr=0.06 hold=20: mean_ret=65.89%, min_ret=25.44%, mean_daily=0.261%, mdd=-17.07%, sharpe=5.68, target_years=0/3, all_positive=True
- fusion_rerank notional=150,000 maxpos=8 sl=0.06 tp=0.25 tr=0.06 hold=10: mean_ret=65.45%, min_ret=37.81%, mean_daily=0.260%, mdd=-8.82%, sharpe=6.09, target_years=0/3, all_positive=True
- ml_rerank notional=150,000 maxpos=8 sl=0.14 tp=0.25 tr=0.06 hold=20: mean_ret=63.21%, min_ret=17.89%, mean_daily=0.251%, mdd=-19.07%, sharpe=5.43, target_years=0/3, all_positive=True
- ml_rerank notional=150,000 maxpos=6 sl=0.06 tp=0.25 tr=0.06 hold=10: mean_ret=62.35%, min_ret=33.71%, mean_daily=0.247%, mdd=-17.86%, sharpe=5.80, target_years=0/3, all_positive=True
- ml_rerank notional=150,000 maxpos=6 sl=0.06 tp=0.25 tr=0.06 hold=20: mean_ret=62.35%, min_ret=33.71%, mean_daily=0.247%, mdd=-17.86%, sharpe=5.80, target_years=0/3, all_positive=True
- fusion_rerank notional=150,000 maxpos=8 sl=0.10 tp=0.25 tr=0.06 hold=20: mean_ret=61.30%, min_ret=36.25%, mean_daily=0.243%, mdd=-12.16%, sharpe=6.34, target_years=0/3, all_positive=True
- fusion_rerank notional=150,000 maxpos=8 sl=0.10 tp=0.25 tr=0.06 hold=10: mean_ret=59.36%, min_ret=35.24%, mean_daily=0.236%, mdd=-12.16%, sharpe=6.14, target_years=0/3, all_positive=True
- ml_rerank notional=150,000 maxpos=8 sl=0.06 tp=0.25 tr=0.00 hold=10: mean_ret=58.36%, min_ret=30.06%, mean_daily=0.232%, mdd=-14.30%, sharpe=5.39, target_years=0/3, all_positive=True
- ml_rerank notional=150,000 maxpos=8 sl=0.06 tp=0.25 tr=0.00 hold=20: mean_ret=58.10%, min_ret=24.08%, mean_daily=0.231%, mdd=-13.21%, sharpe=5.20, target_years=0/3, all_positive=True
- ml_rerank notional=150,000 maxpos=6 sl=0.10 tp=0.25 tr=0.06 hold=20: mean_ret=57.88%, min_ret=36.93%, mean_daily=0.230%, mdd=-12.85%, sharpe=5.49, target_years=0/3, all_positive=True
- ml_rerank notional=150,000 maxpos=6 sl=0.10 tp=0.25 tr=0.06 hold=10: mean_ret=57.52%, min_ret=35.86%, mean_daily=0.228%, mdd=-12.85%, sharpe=5.47, target_years=0/3, all_positive=True
- fusion_rerank notional=150,000 maxpos=8 sl=0.06 tp=0.15 tr=0.06 hold=10: mean_ret=56.37%, min_ret=21.34%, mean_daily=0.224%, mdd=-9.19%, sharpe=5.14, target_years=0/3, all_positive=True
- ml_rerank notional=150,000 maxpos=6 sl=0.06 tp=0.25 tr=0.00 hold=10: mean_ret=55.98%, min_ret=35.02%, mean_daily=0.222%, mdd=-9.30%, sharpe=5.41, target_years=0/3, all_positive=True
- fusion_rerank notional=150,000 maxpos=6 sl=0.06 tp=0.25 tr=0.06 hold=20: mean_ret=55.27%, min_ret=25.80%, mean_daily=0.219%, mdd=-8.61%, sharpe=5.22, target_years=0/3, all_positive=True
- fusion_rerank notional=150,000 maxpos=8 sl=0.06 tp=0.15 tr=0.06 hold=20: mean_ret=55.18%, min_ret=21.34%, mean_daily=0.219%, mdd=-9.19%, sharpe=5.09, target_years=0/3, all_positive=True
- fusion_rerank notional=150,000 maxpos=8 sl=0.06 tp=0.25 tr=0.00 hold=10: mean_ret=54.55%, min_ret=22.82%, mean_daily=0.216%, mdd=-8.25%, sharpe=5.21, target_years=0/3, all_positive=True
- ml_rerank notional=150,000 maxpos=6 sl=0.06 tp=0.25 tr=0.00 hold=20: mean_ret=54.04%, min_ret=34.19%, mean_daily=0.214%, mdd=-9.30%, sharpe=5.15, target_years=0/3, all_positive=True
- fusion_rerank notional=150,000 maxpos=6 sl=0.06 tp=0.15 tr=0.00 hold=10: mean_ret=53.95%, min_ret=18.56%, mean_daily=0.214%, mdd=-9.03%, sharpe=5.26, target_years=0/3, all_positive=True
- fusion_rerank notional=150,000 maxpos=6 sl=0.06 tp=0.25 tr=0.06 hold=10: mean_ret=53.77%, min_ret=25.80%, mean_daily=0.213%, mdd=-8.61%, sharpe=5.05, target_years=0/3, all_positive=True
- fusion_rerank notional=150,000 maxpos=6 sl=0.06 tp=0.15 tr=0.00 hold=20: mean_ret=53.55%, min_ret=18.56%, mean_daily=0.213%, mdd=-9.03%, sharpe=5.25, target_years=0/3, all_positive=True
- fusion_rerank notional=150,000 maxpos=8 sl=0.06 tp=0.25 tr=0.00 hold=20: mean_ret=53.41%, min_ret=21.45%, mean_daily=0.212%, mdd=-8.25%, sharpe=5.23, target_years=0/3, all_positive=True

## Year-level target rows

- 2025 fusion_rerank ret=138.99% daily=0.552% mdd=-4.37% sharpe=9.45 notional=150,000 maxpos=8 sl=0.06 tp=0.15 tr=0.00 hold=10
- 2025 fusion_rerank ret=135.78% daily=0.539% mdd=-4.36% sharpe=9.31 notional=150,000 maxpos=8 sl=0.06 tp=0.15 tr=0.00 hold=20

## Files

- CSV: `/Users/01chungee10/Github/TOSS/reports/harness/risk_exit_frontier_20260621.csv`
