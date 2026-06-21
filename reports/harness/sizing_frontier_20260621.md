# Sizing Frontier: Cash Fraction × Max Notional

Research/paper only. No live orders submitted.

## Interpretation

- Risk/exit params fixed to best surviving configs from risk_exit_frontier.
- This loop sweeps cash_fraction_per_entry (capital deployment per entry) and max_notional.
- The previous bottleneck was cash_fraction_per_entry=0.25 (25% of cash per position).
- Higher fractions deploy more capital per trade but increase concentration risk.

## Robust leaderboard (3-year avg)

- ml_rerank cash_frac=0.80 notional=300,000: mean_ret=111.95%, min_ret=52.76%, mean_daily=0.444%, mdd=-18.62%, sharpe=5.68, target_years=1/3, all_positive=True
- fusion_rerank cash_frac=0.80 notional=500,000: mean_ret=108.92%, min_ret=45.74%, mean_daily=0.432%, mdd=-14.06%, sharpe=4.95, target_years=1/3, all_positive=True
- ml_rerank cash_frac=0.60 notional=300,000: mean_ret=104.61%, min_ret=48.77%, mean_daily=0.415%, mdd=-19.67%, sharpe=5.66, target_years=1/3, all_positive=True
- fusion_rerank cash_frac=0.60 notional=300,000: mean_ret=100.97%, min_ret=42.97%, mean_daily=0.401%, mdd=-12.55%, sharpe=5.96, target_years=1/3, all_positive=True
- ml_rerank cash_frac=0.80 notional=200,000: mean_ret=100.05%, min_ret=59.91%, mean_daily=0.397%, mdd=-17.83%, sharpe=6.19, target_years=1/3, all_positive=True
- fusion_rerank cash_frac=0.50 notional=300,000: mean_ret=98.11%, min_ret=48.76%, mean_daily=0.389%, mdd=-10.62%, sharpe=5.93, target_years=1/3, all_positive=True
- ml_rerank cash_frac=0.50 notional=300,000: mean_ret=95.76%, min_ret=52.51%, mean_daily=0.380%, mdd=-19.52%, sharpe=5.44, target_years=1/3, all_positive=True
- fusion_rerank cash_frac=0.40 notional=300,000: mean_ret=95.01%, min_ret=49.45%, mean_daily=0.377%, mdd=-10.20%, sharpe=6.14, target_years=1/3, all_positive=True
- ml_rerank cash_frac=0.50 notional=200,000: mean_ret=94.90%, min_ret=38.09%, mean_daily=0.377%, mdd=-19.84%, sharpe=6.17, target_years=1/3, all_positive=True
- ml_rerank cash_frac=1.00 notional=200,000: mean_ret=93.28%, min_ret=61.76%, mean_daily=0.370%, mdd=-16.26%, sharpe=5.69, target_years=1/3, all_positive=True
- ml_rerank cash_frac=0.40 notional=300,000: mean_ret=91.10%, min_ret=48.27%, mean_daily=0.362%, mdd=-20.00%, sharpe=5.43, target_years=1/3, all_positive=True
- fusion_rerank cash_frac=0.40 notional=500,000: mean_ret=88.58%, min_ret=38.16%, mean_daily=0.352%, mdd=-11.13%, sharpe=5.48, target_years=1/3, all_positive=True
- ml_rerank cash_frac=0.40 notional=200,000: mean_ret=88.28%, min_ret=30.18%, mean_daily=0.350%, mdd=-19.16%, sharpe=6.05, target_years=1/3, all_positive=True
- ml_rerank cash_frac=0.30 notional=300,000: mean_ret=85.30%, min_ret=41.09%, mean_daily=0.338%, mdd=-19.19%, sharpe=5.61, target_years=1/3, all_positive=True
- fusion_rerank cash_frac=0.50 notional=200,000: mean_ret=84.59%, min_ret=40.94%, mean_daily=0.336%, mdd=-11.09%, sharpe=5.58, target_years=1/3, all_positive=True
- fusion_rerank cash_frac=0.60 notional=200,000: mean_ret=83.74%, min_ret=44.82%, mean_daily=0.332%, mdd=-13.99%, sharpe=5.44, target_years=1/3, all_positive=True
- ml_rerank cash_frac=0.30 notional=500,000: mean_ret=83.45%, min_ret=41.94%, mean_daily=0.331%, mdd=-19.20%, sharpe=5.45, target_years=1/3, all_positive=True
- ml_rerank cash_frac=0.30 notional=200,000: mean_ret=81.09%, min_ret=34.44%, mean_daily=0.322%, mdd=-18.43%, sharpe=5.85, target_years=1/3, all_positive=True
- fusion_rerank cash_frac=0.40 notional=200,000: mean_ret=79.36%, min_ret=33.88%, mean_daily=0.315%, mdd=-12.86%, sharpe=5.67, target_years=1/3, all_positive=True
- fusion_rerank cash_frac=0.30 notional=300,000: mean_ret=78.83%, min_ret=39.84%, mean_daily=0.313%, mdd=-11.66%, sharpe=5.68, target_years=1/3, all_positive=True
- fusion_rerank cash_frac=1.00 notional=200,000: mean_ret=78.79%, min_ret=44.06%, mean_daily=0.313%, mdd=-16.82%, sharpe=5.01, target_years=1/3, all_positive=True
- fusion_rerank cash_frac=0.80 notional=200,000: mean_ret=78.52%, min_ret=40.81%, mean_daily=0.312%, mdd=-15.31%, sharpe=5.05, target_years=1/3, all_positive=True
- fusion_rerank cash_frac=0.30 notional=500,000: mean_ret=77.27%, min_ret=35.60%, mean_daily=0.307%, mdd=-11.66%, sharpe=5.33, target_years=1/3, all_positive=True
- fusion_rerank cash_frac=0.80 notional=300,000: mean_ret=77.15%, min_ret=35.95%, mean_daily=0.306%, mdd=-14.70%, sharpe=4.62, target_years=1/3, all_positive=True
- fusion_rerank cash_frac=0.30 notional=200,000: mean_ret=73.72%, min_ret=34.97%, mean_daily=0.293%, mdd=-11.52%, sharpe=5.77, target_years=1/3, all_positive=True

## Year-level target rows (≥0.5%/day)

- 2025 fusion_rerank ret=212.19% daily=0.842% mdd=-13.66% sharpe=7.61 cash_frac=0.80 notional=500,000
- 2025 fusion_rerank ret=199.34% daily=0.791% mdd=-8.32% sharpe=8.32 cash_frac=1.00 notional=300,000
- 2025 fusion_rerank ret=188.42% daily=0.748% mdd=-9.37% sharpe=7.67 cash_frac=0.60 notional=500,000
- 2025 ml_rerank ret=184.78% daily=0.733% mdd=-15.69% sharpe=6.93 cash_frac=0.80 notional=300,000
- 2025 fusion_rerank ret=175.70% daily=0.697% mdd=-6.38% sharpe=8.62 cash_frac=0.60 notional=300,000
- 2025 ml_rerank ret=171.42% daily=0.680% mdd=-14.07% sharpe=6.90 cash_frac=0.60 notional=300,000
- 2025 fusion_rerank ret=170.25% daily=0.676% mdd=-8.18% sharpe=7.63 cash_frac=0.50 notional=500,000
- 2025 fusion_rerank ret=165.34% daily=0.656% mdd=-6.50% sharpe=8.29 cash_frac=0.50 notional=300,000
- 2025 fusion_rerank ret=164.80% daily=0.654% mdd=-6.23% sharpe=8.84 cash_frac=0.40 notional=300,000
- 2025 fusion_rerank ret=160.55% daily=0.637% mdd=-7.04% sharpe=8.02 cash_frac=0.40 notional=500,000
- 2025 ml_rerank ret=159.69% daily=0.634% mdd=-27.96% sharpe=5.37 cash_frac=0.60 notional=500,000
- 2025 ml_rerank ret=157.89% daily=0.627% mdd=-16.33% sharpe=7.08 cash_frac=0.80 notional=200,000
- 2025 fusion_rerank ret=156.18% daily=0.620% mdd=-8.54% sharpe=8.25 cash_frac=0.50 notional=200,000
- 2025 fusion_rerank ret=155.29% daily=0.616% mdd=-6.74% sharpe=8.62 cash_frac=0.30 notional=500,000
- 2025 fusion_rerank ret=153.33% daily=0.608% mdd=-7.39% sharpe=7.05 cash_frac=0.80 notional=300,000
- 2025 ml_rerank ret=153.03% daily=0.607% mdd=-15.34% sharpe=7.21 cash_frac=0.50 notional=200,000
- 2025 ml_rerank ret=152.71% daily=0.606% mdd=-15.02% sharpe=6.89 cash_frac=1.00 notional=200,000
- 2025 fusion_rerank ret=152.34% daily=0.605% mdd=-6.74% sharpe=9.07 cash_frac=0.30 notional=300,000
- 2025 fusion_rerank ret=150.42% daily=0.597% mdd=-5.29% sharpe=7.89 cash_frac=0.60 notional=200,000
- 2025 ml_rerank ret=149.67% daily=0.594% mdd=-15.39% sharpe=6.95 cash_frac=0.60 notional=200,000
- 2025 ml_rerank ret=149.65% daily=0.594% mdd=-16.52% sharpe=6.39 cash_frac=0.40 notional=500,000
- 2025 fusion_rerank ret=148.66% daily=0.590% mdd=-9.42% sharpe=7.46 cash_frac=0.80 notional=200,000
- 2025 ml_rerank ret=148.27% daily=0.588% mdd=-16.42% sharpe=6.59 cash_frac=0.40 notional=300,000
- 2025 fusion_rerank ret=147.06% daily=0.584% mdd=-7.74% sharpe=8.61 cash_frac=0.40 notional=200,000
- 2025 ml_rerank ret=146.08% daily=0.580% mdd=-17.99% sharpe=6.14 cash_frac=0.50 notional=300,000
- 2025 ml_rerank ret=145.73% daily=0.578% mdd=-13.98% sharpe=7.34 cash_frac=0.40 notional=200,000
- 2025 fusion_rerank ret=143.45% daily=0.569% mdd=-7.76% sharpe=7.40 cash_frac=1.00 notional=200,000
- 2025 ml_rerank ret=142.10% daily=0.564% mdd=-21.63% sharpe=5.51 cash_frac=0.50 notional=500,000
- 2025 ml_rerank ret=141.10% daily=0.560% mdd=-12.95% sharpe=7.15 cash_frac=0.30 notional=500,000
- 2025 ml_rerank ret=140.77% daily=0.559% mdd=-12.95% sharpe=7.09 cash_frac=0.30 notional=300,000
- 2025 fusion_rerank ret=140.57% daily=0.558% mdd=-6.96% sharpe=8.88 cash_frac=0.30 notional=200,000
- 2025 ml_rerank ret=140.51% daily=0.558% mdd=-12.46% sharpe=7.48 cash_frac=0.30 notional=200,000
- 2025 fusion_rerank ret=138.58% daily=0.550% mdd=-6.47% sharpe=8.69 cash_frac=0.25 notional=500,000
- 2025 ml_rerank ret=137.91% daily=0.547% mdd=-33.29% sharpe=4.47 cash_frac=1.00 notional=500,000
- 2025 fusion_rerank ret=135.87% daily=0.539% mdd=-6.47% sharpe=8.82 cash_frac=0.25 notional=300,000
- 2025 fusion_rerank ret=126.73% daily=0.503% mdd=-13.16% sharpe=5.32 cash_frac=1.00 notional=500,000

## Files

- CSV: `/Users/01chungee10/Github/TOSS/reports/harness/sizing_frontier_20260621.csv`
