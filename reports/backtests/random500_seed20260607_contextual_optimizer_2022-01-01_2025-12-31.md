# Contextual daily strategy optimizer — random 500 seed 20260607

Research-only. 실주문 없음. 투자 조언 아님.

## Method
- Panel: /mnt/c/Github/TOSS/reports/backtests/random500_seed20260607_2022-01-01_2025-12-31_ohlcv_panel.csv
- Train: <= 2024-12-31, test: >= 2025-01-01
- Grid size: 144 parameter combinations per situation
- Situations: sample-market 20D momentum up/flat/down × high/low volatility
- Objective: train Sharpe + CAGR penalty/bonus + drawdown penalty, with minimum train trades
- Live execution: disabled; generated policy is paper/manual-draft only

## Combined approved contextual policy performance
- Train: {'days': 733, 'active_days': 73, 'total_trades': 217, 'final_value_krw': 1057393.41, 'total_return_pct': 5.74, 'cagr_pct': 1.89, 'max_drawdown_pct': -20.21, 'sharpe': 0.204, 'win_rate_pct': 46.58, 'profit_factor': 1.118}
- Test: {'days': 241, 'active_days': 17, 'total_trades': 51, 'final_value_krw': 1030002.43, 'total_return_pct': 3.0, 'cagr_pct': 3.03, 'max_drawdown_pct': -11.45, 'sharpe': 0.357, 'win_rate_pct': 47.06, 'profit_factor': 1.245}
- All: {'days': 974, 'active_days': 90, 'total_trades': 268, 'final_value_krw': 1089117.77, 'total_return_pct': 8.91, 'cagr_pct': 2.16, 'max_drawdown_pct': -20.21, 'sharpe': 0.23, 'win_rate_pct': 46.67, 'profit_factor': 1.139}

## Approved policy by situation
- flat_high_vol: reversal mom_20d/vol_20d -> cc_next_ret, top_n=3, min_dv=100000000, min_abs_mom=0.0; train return 5.74%, test return 3.0%, test MDD -11.45%, test Sharpe 1.312

## Rejected best-by-train candidates
- down_low_vol: train 17.2%, test -11.91%, test MDD -14.69%, test Sharpe -6.121
- up_high_vol: train 9.39%, test -32.44%, test MDD -40.57%, test Sharpe -3.299
- flat_high_vol: train 5.74%, test 3.0%, test MDD -11.45%, test Sharpe 1.312
- flat_low_vol: train 4.94%, test -14.17%, test MDD -26.11%, test Sharpe -1.104
- down_high_vol: train -8.26%, test -4.46%, test MDD -4.37%, test Sharpe -4.543
- up_low_vol: train -22.78%, test -42.0%, test MDD -49.04%, test Sharpe -2.22

## Outputs
- policy: /mnt/c/Github/TOSS/config/generated_policies/contextual_daily_policy_seed20260607.json
- all_trials: /mnt/c/Github/TOSS/reports/backtests/random500_seed20260607_contextual_optimizer_2022-01-01_2025-12-31_all_trials.csv
- selected_by_situation: /mnt/c/Github/TOSS/reports/backtests/random500_seed20260607_contextual_optimizer_2022-01-01_2025-12-31_selected_by_situation.csv
- combined_daily_curve: /mnt/c/Github/TOSS/reports/backtests/random500_seed20260607_contextual_optimizer_2022-01-01_2025-12-31_combined_daily_curve.csv
- combined_picks: /mnt/c/Github/TOSS/reports/backtests/random500_seed20260607_contextual_optimizer_2022-01-01_2025-12-31_combined_picks.csv
- json: /mnt/c/Github/TOSS/reports/backtests/random500_seed20260607_contextual_optimizer_2022-01-01_2025-12-31.json
