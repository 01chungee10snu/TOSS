# Aggressive 0.5%/day Frontier

Research/paper only. No live orders submitted.

## Target

- Daily average target: 0.50% over 252 trading days
- Annual equivalent: about +251% compounded
- Metric used here: simple total_return_pct / 252, deliberately strict and comparable across years

## Robust leaderboard

- base notional=100,000 maxpos=8: mean_ret=40.98%, min_ret=31.35%, mean_daily=0.163%, mdd=-15.55%, sharpe=4.78, target_years=0/3, all_positive=True
- base notional=150,000 maxpos=8: mean_ret=34.99%, min_ret=11.25%, mean_daily=0.139%, mdd=-19.58%, sharpe=3.86, target_years=0/3, all_positive=True
- base notional=200,000 maxpos=8: mean_ret=30.98%, min_ret=10.69%, mean_daily=0.123%, mdd=-20.16%, sharpe=3.29, target_years=0/3, all_positive=True
- base notional=100,000 maxpos=6: mean_ret=28.70%, min_ret=16.42%, mean_daily=0.114%, mdd=-13.50%, sharpe=4.01, target_years=0/3, all_positive=True
- base notional=300,000 maxpos=8: mean_ret=27.88%, min_ret=5.97%, mean_daily=0.111%, mdd=-20.13%, sharpe=3.02, target_years=0/3, all_positive=True
- base notional=200,000 maxpos=6: mean_ret=27.23%, min_ret=6.72%, mean_daily=0.108%, mdd=-21.76%, sharpe=3.12, target_years=0/3, all_positive=True
- base notional=150,000 maxpos=6: mean_ret=25.28%, min_ret=6.02%, mean_daily=0.100%, mdd=-16.42%, sharpe=3.08, target_years=0/3, all_positive=True
- base notional=500,000 maxpos=8: mean_ret=24.23%, min_ret=5.97%, mean_daily=0.096%, mdd=-20.46%, sharpe=2.79, target_years=0/3, all_positive=True
- base notional=300,000 maxpos=6: mean_ret=23.59%, min_ret=0.88%, mean_daily=0.094%, mdd=-23.30%, sharpe=2.64, target_years=0/3, all_positive=True
- base notional=500,000 maxpos=6: mean_ret=23.47%, min_ret=0.88%, mean_daily=0.093%, mdd=-23.45%, sharpe=2.63, target_years=0/3, all_positive=True
- fusion_hybrid_a0p5 notional=100,000 maxpos=6: mean_ret=21.60%, min_ret=1.89%, mean_daily=0.086%, mdd=-12.22%, sharpe=3.27, target_years=0/3, all_positive=True
- base notional=150,000 maxpos=4: mean_ret=20.20%, min_ret=3.33%, mean_daily=0.080%, mdd=-14.69%, sharpe=2.81, target_years=0/3, all_positive=True
- base notional=100,000 maxpos=4: mean_ret=15.38%, min_ret=1.85%, mean_daily=0.061%, mdd=-12.94%, sharpe=3.32, target_years=0/3, all_positive=True
- ml_rerank notional=100,000 maxpos=4: mean_ret=14.90%, min_ret=1.75%, mean_daily=0.059%, mdd=-12.79%, sharpe=2.17, target_years=0/3, all_positive=True
- fusion_hybrid_a0p5 notional=100,000 maxpos=4: mean_ret=12.27%, min_ret=0.76%, mean_daily=0.049%, mdd=-10.89%, sharpe=2.69, target_years=0/3, all_positive=True
- fusion_rerank notional=150,000 maxpos=8: mean_ret=44.56%, min_ret=-15.16%, mean_daily=0.177%, mdd=-15.16%, sharpe=3.92, target_years=0/3, all_positive=False
- ml_rerank notional=150,000 maxpos=8: mean_ret=42.01%, min_ret=-8.95%, mean_daily=0.167%, mdd=-16.50%, sharpe=3.56, target_years=0/3, all_positive=False
- fusion_rerank notional=200,000 maxpos=8: mean_ret=40.63%, min_ret=-17.25%, mean_daily=0.161%, mdd=-17.25%, sharpe=2.78, target_years=0/3, all_positive=False
- fusion_hybrid_a0p5 notional=150,000 maxpos=8: mean_ret=40.04%, min_ret=-9.51%, mean_daily=0.159%, mdd=-14.60%, sharpe=3.26, target_years=0/3, all_positive=False
- ml_rerank notional=200,000 maxpos=8: mean_ret=38.46%, min_ret=-12.47%, mean_daily=0.153%, mdd=-18.75%, sharpe=3.02, target_years=0/3, all_positive=False

## Year-level best rows

- 2024 base notional=100,000 maxpos=8: ret=31.35%, daily=0.124%, mdd=-15.55%, sharpe=2.63, target=False
- 2024 base notional=100,000 maxpos=6: ret=27.42%, daily=0.109%, mdd=-13.50%, sharpe=2.37, target=False
- 2024 base notional=300,000 maxpos=8: ret=13.76%, daily=0.055%, mdd=-20.13%, sharpe=1.73, target=False
- 2024 base notional=500,000 maxpos=8: ret=13.30%, daily=0.053%, mdd=-20.46%, sharpe=1.68, target=False
- 2024 base notional=150,000 maxpos=8: ret=11.25%, daily=0.045%, mdd=-19.58%, sharpe=1.57, target=False
- 2025 fusion_rerank notional=200,000 maxpos=8: ret=120.07%, daily=0.476%, mdd=-6.26%, sharpe=8.38, target=False
- 2025 fusion_rerank notional=500,000 maxpos=6: ret=117.29%, daily=0.465%, mdd=-6.43%, sharpe=8.09, target=False
- 2025 fusion_rerank notional=500,000 maxpos=8: ret=115.87%, daily=0.460%, mdd=-7.13%, sharpe=7.75, target=False
- 2025 fusion_rerank notional=300,000 maxpos=6: ret=114.11%, daily=0.453%, mdd=-6.41%, sharpe=8.08, target=False
- 2025 fusion_rerank notional=300,000 maxpos=8: ret=113.28%, daily=0.450%, mdd=-7.13%, sharpe=7.73, target=False
- 2026 ml_rerank notional=150,000 maxpos=8: ret=47.19%, daily=0.187%, mdd=-8.29%, sharpe=6.14, target=False
- 2026 ml_rerank notional=200,000 maxpos=8: ret=45.62%, daily=0.181%, mdd=-8.61%, sharpe=5.48, target=False
- 2026 ml_rerank notional=300,000 maxpos=8: ret=42.20%, daily=0.167%, mdd=-11.58%, sharpe=4.67, target=False
- 2026 ml_rerank notional=500,000 maxpos=8: ret=41.12%, daily=0.163%, mdd=-11.59%, sharpe=4.58, target=False
- 2026 fusion_rerank notional=150,000 maxpos=8: ret=40.00%, daily=0.159%, mdd=-12.02%, sharpe=5.64, target=False

## Files

- CSV: `/Users/01chungee10/Github/TOSS/reports/harness/aggressive_05pct_daily_frontier_20260621.csv`
