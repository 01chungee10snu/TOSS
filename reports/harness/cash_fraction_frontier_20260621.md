# Cash-Fraction Frontier for 0.5%/day Target

Research/paper only. No live orders submitted.

## Interpretation

- This loop varies only capital utilization after the risk/exit frontier found robust survivors.
- Promotable research configs must be positive in all 2024/2025/2026 years and MDD must stay within -20%.
- `cash_fraction_per_entry=0.25` is the legacy engine default.

## Robust leaderboard

- ml_rerank notional=300,000 cash_frac=0.75: mean_ret=112.05%, min_ret=54.15%, mean_daily=0.445%, max_mdd=-18.42%, sharpe=5.66, target_years=1/3, all_positive=True, risk_ok=True
- fusion_rerank notional=500,000 cash_frac=0.75: mean_ret=105.06%, min_ret=38.66%, mean_daily=0.417%, max_mdd=-14.69%, sharpe=4.98, target_years=1/3, all_positive=True, risk_ok=True
- ml_rerank notional=200,000 cash_frac=0.75: mean_ret=99.61%, min_ret=58.34%, mean_daily=0.395%, max_mdd=-17.90%, sharpe=6.18, target_years=1/3, all_positive=True, risk_ok=True
- fusion_rerank notional=300,000 cash_frac=0.50: mean_ret=98.11%, min_ret=48.76%, mean_daily=0.389%, max_mdd=-10.62%, sharpe=5.93, target_years=1/3, all_positive=True, risk_ok=True
- ml_rerank notional=300,000 cash_frac=0.50: mean_ret=95.76%, min_ret=52.51%, mean_daily=0.380%, max_mdd=-19.52%, sharpe=5.44, target_years=1/3, all_positive=True, risk_ok=True
- ml_rerank notional=200,000 cash_frac=0.50: mean_ret=94.90%, min_ret=38.09%, mean_daily=0.377%, max_mdd=-19.84%, sharpe=6.17, target_years=1/3, all_positive=True, risk_ok=True
- ml_rerank notional=200,000 cash_frac=1.00: mean_ret=93.28%, min_ret=61.76%, mean_daily=0.370%, max_mdd=-16.26%, sharpe=5.69, target_years=1/3, all_positive=True, risk_ok=True
- ml_rerank notional=200,000 cash_frac=0.35: mean_ret=89.36%, min_ret=36.04%, mean_daily=0.355%, max_mdd=-18.70%, sharpe=6.28, target_years=1/3, all_positive=True, risk_ok=True
- fusion_rerank notional=500,000 cash_frac=0.35: mean_ret=88.15%, min_ret=46.09%, mean_daily=0.350%, max_mdd=-10.54%, sharpe=5.76, target_years=1/3, all_positive=True, risk_ok=True
- ml_rerank notional=300,000 cash_frac=0.35: mean_ret=87.08%, min_ret=45.44%, mean_daily=0.346%, max_mdd=-19.64%, sharpe=5.46, target_years=1/3, all_positive=True, risk_ok=True
- fusion_rerank notional=300,000 cash_frac=0.35: mean_ret=86.88%, min_ret=40.21%, mean_daily=0.345%, max_mdd=-9.62%, sharpe=5.86, target_years=1/3, all_positive=True, risk_ok=True
- fusion_rerank notional=300,000 cash_frac=0.75: mean_ret=85.65%, min_ret=35.60%, mean_daily=0.340%, max_mdd=-14.80%, sharpe=4.91, target_years=1/3, all_positive=True, risk_ok=True
- fusion_rerank notional=200,000 cash_frac=0.50: mean_ret=84.59%, min_ret=40.94%, mean_daily=0.336%, max_mdd=-11.09%, sharpe=5.58, target_years=1/3, all_positive=True, risk_ok=True
- fusion_rerank notional=200,000 cash_frac=0.75: mean_ret=82.56%, min_ret=37.59%, mean_daily=0.328%, max_mdd=-14.97%, sharpe=5.11, target_years=1/3, all_positive=True, risk_ok=True
- fusion_rerank notional=200,000 cash_frac=1.00: mean_ret=78.79%, min_ret=44.06%, mean_daily=0.313%, max_mdd=-16.82%, sharpe=5.01, target_years=1/3, all_positive=True, risk_ok=True
- fusion_rerank notional=200,000 cash_frac=0.35: mean_ret=76.41%, min_ret=39.17%, mean_daily=0.303%, max_mdd=-12.28%, sharpe=5.67, target_years=1/3, all_positive=True, risk_ok=True
- fusion_rerank notional=500,000 cash_frac=0.25: mean_ret=72.37%, min_ret=37.08%, mean_daily=0.287%, max_mdd=-9.91%, sharpe=5.85, target_years=1/3, all_positive=True, risk_ok=True
- fusion_rerank notional=300,000 cash_frac=0.25: mean_ret=72.20%, min_ret=38.14%, mean_daily=0.287%, max_mdd=-9.91%, sharpe=5.97, target_years=1/3, all_positive=True, risk_ok=True
- fusion_rerank notional=500,000 cash_frac=0.50: mean_ret=96.26%, min_ret=28.26%, mean_daily=0.382%, max_mdd=-20.36%, sharpe=5.05, target_years=1/3, all_positive=True, risk_ok=False
- ml_rerank notional=500,000 cash_frac=0.50: mean_ret=96.19%, min_ret=67.99%, mean_daily=0.382%, max_mdd=-21.63%, sharpe=4.78, target_years=1/3, all_positive=True, risk_ok=False
- fusion_rerank notional=300,000 cash_frac=1.00: mean_ret=90.87%, min_ret=13.87%, mean_daily=0.361%, max_mdd=-20.98%, sharpe=4.55, target_years=1/3, all_positive=True, risk_ok=False
- ml_rerank notional=500,000 cash_frac=0.35: mean_ret=86.91%, min_ret=43.49%, mean_daily=0.345%, max_mdd=-21.78%, sharpe=5.22, target_years=1/3, all_positive=True, risk_ok=False
- ml_rerank notional=500,000 cash_frac=1.00: mean_ret=83.91%, min_ret=26.17%, mean_daily=0.333%, max_mdd=-33.29%, sharpe=3.66, target_years=1/3, all_positive=True, risk_ok=False
- fusion_rerank notional=500,000 cash_frac=1.00: mean_ret=51.90%, min_ret=-2.37%, mean_daily=0.206%, max_mdd=-35.65%, sharpe=2.60, target_years=1/3, all_positive=False, risk_ok=False
- ml_rerank notional=150,000 cash_frac=0.50: mean_ret=77.58%, min_ret=44.63%, mean_daily=0.308%, max_mdd=-15.26%, sharpe=6.14, target_years=0/3, all_positive=True, risk_ok=True

## Year-level 0.5%/day target rows

- 2025 fusion_rerank ret=208.05% daily=0.826% mdd=-13.79% sharpe=8.00 notional=500,000 cash_frac=0.75
- 2025 fusion_rerank ret=199.34% daily=0.791% mdd=-8.32% sharpe=8.32 notional=300,000 cash_frac=1.00
- 2025 ml_rerank ret=187.28% daily=0.743% mdd=-15.74% sharpe=6.96 notional=300,000 cash_frac=0.75
- 2025 fusion_rerank ret=171.65% daily=0.681% mdd=-8.05% sharpe=7.61 notional=300,000 cash_frac=0.75
- 2025 fusion_rerank ret=170.25% daily=0.676% mdd=-8.18% sharpe=7.63 notional=500,000 cash_frac=0.50
- 2025 fusion_rerank ret=165.34% daily=0.656% mdd=-6.50% sharpe=8.29 notional=300,000 cash_frac=0.50
- 2025 fusion_rerank ret=161.34% daily=0.640% mdd=-6.16% sharpe=8.41 notional=500,000 cash_frac=0.35
- 2025 fusion_rerank ret=156.91% daily=0.623% mdd=-9.65% sharpe=7.72 notional=200,000 cash_frac=0.75
- 2025 fusion_rerank ret=156.31% daily=0.620% mdd=-6.41% sharpe=8.93 notional=300,000 cash_frac=0.35
- 2025 fusion_rerank ret=156.18% daily=0.620% mdd=-8.54% sharpe=8.25 notional=200,000 cash_frac=0.50
- 2025 ml_rerank ret=155.57% daily=0.617% mdd=-15.90% sharpe=6.94 notional=200,000 cash_frac=0.75
- 2025 ml_rerank ret=153.03% daily=0.607% mdd=-15.34% sharpe=7.21 notional=200,000 cash_frac=0.50
- 2025 ml_rerank ret=152.71% daily=0.606% mdd=-15.02% sharpe=6.89 notional=200,000 cash_frac=1.00
- 2025 ml_rerank ret=147.18% daily=0.584% mdd=-14.95% sharpe=6.73 notional=500,000 cash_frac=0.35
- 2025 ml_rerank ret=146.08% daily=0.580% mdd=-17.99% sharpe=6.14 notional=300,000 cash_frac=0.50
- 2025 ml_rerank ret=143.62% daily=0.570% mdd=-13.29% sharpe=7.40 notional=200,000 cash_frac=0.35
- 2025 fusion_rerank ret=143.45% daily=0.569% mdd=-7.76% sharpe=7.40 notional=200,000 cash_frac=1.00
- 2025 ml_rerank ret=142.10% daily=0.564% mdd=-21.63% sharpe=5.51 notional=500,000 cash_frac=0.50
- 2025 ml_rerank ret=141.58% daily=0.562% mdd=-15.04% sharpe=6.73 notional=300,000 cash_frac=0.35
- 2025 fusion_rerank ret=141.15% daily=0.560% mdd=-7.33% sharpe=8.64 notional=200,000 cash_frac=0.35
- 2025 fusion_rerank ret=138.58% daily=0.550% mdd=-6.47% sharpe=8.69 notional=500,000 cash_frac=0.25
- 2025 ml_rerank ret=137.91% daily=0.547% mdd=-33.29% sharpe=4.47 notional=500,000 cash_frac=1.00
- 2025 fusion_rerank ret=135.87% daily=0.539% mdd=-6.47% sharpe=8.82 notional=300,000 cash_frac=0.25
- 2025 fusion_rerank ret=126.73% daily=0.503% mdd=-13.16% sharpe=5.32 notional=500,000 cash_frac=1.00

## Files

- CSV: `/Users/01chungee10/Github/TOSS/reports/harness/cash_fraction_frontier_20260621.csv`
