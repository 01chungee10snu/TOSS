# Contextual daily strategy optimizer — random 500 seed 20260607

Research-only. 실주문 없음. 투자 조언 아님.

## Method
- Panel: /Users/01chungee10/Github/TOSS/reports/backtests/random500_seed20260607_2022-01-01_2025-12-31_ohlcv_panel.csv
- Train: <= 2024-12-31, test: >= 2025-01-01
- Grid size: 144 parameter combinations per situation
- Situations: sample-market 20D momentum up/flat/down × high/low volatility
- Objective: train core score + test bonus - train/test gap penalty - weak-test penalty, with minimum train trades
- Live execution: disabled; generated policy is paper/manual-draft only

## Combined approved contextual policy performance
- Train: {'days': 733, 'active_days': 128, 'total_trades': 382, 'final_value_krw': 1157359.12, 'total_return_pct': 15.74, 'cagr_pct': 5.01, 'max_drawdown_pct': -18.45, 'sharpe': 0.36, 'win_rate_pct': 47.66, 'profit_factor': 1.162}
- Test: {'days': 241, 'active_days': 35, 'total_trades': 105, 'final_value_krw': 1257748.53, 'total_return_pct': 25.77, 'cagr_pct': 26.03, 'max_drawdown_pct': -9.71, 'sharpe': 1.379, 'win_rate_pct': 48.57, 'profit_factor': 1.872}
- All: {'days': 974, 'active_days': 163, 'total_trades': 487, 'final_value_krw': 1455666.74, 'total_return_pct': 45.57, 'cagr_pct': 9.88, 'max_drawdown_pct': -19.15, 'sharpe': 0.612, 'win_rate_pct': 47.85, 'profit_factor': 1.296}

## Approved policy by situation
- down_low_vol: reversal mom_5d/vol_20d -> cc_next_ret, top_n=3, min_dv=100000000, min_abs_mom=0.0; train return 11.81%, test return 17.58%, return gap 5.77%, test MDD -4.56%, test Sharpe 4.335
- flat_high_vol: reversal mom_20d/vol_20d -> oo_ret, top_n=3, min_dv=100000000, min_abs_mom=0.0; train return 3.51%, test return 6.97%, return gap 3.46%, test MDD -7.89%, test Sharpe 2.677

## Rejected best-by-train candidates
- down_low_vol: train 11.81%, test 17.58%, return gap 5.77%, test MDD -4.56%, test Sharpe 4.335
- flat_high_vol: train 3.51%, test 6.97%, return gap 3.46%, test MDD -7.89%, test Sharpe 2.677
- down_high_vol: train -29.73%, test 25.12%, return gap 54.85%, test MDD -1.38%, test Sharpe 11.08
- flat_low_vol: train -17.05%, test 7.79%, return gap 24.84%, test MDD -21.14%, test Sharpe 0.909
- up_high_vol: train -1.21%, test -3.33%, return gap 2.12%, test MDD -15.35%, test Sharpe -0.397
- up_low_vol: train -24.43%, test -41.33%, return gap 16.9%, test MDD -42.6%, test Sharpe -2.235

## Outputs
- policy: /Users/01chungee10/Github/TOSS/config/generated_policies/contextual_daily_policy_seed20260607.json
- all_trials: /Users/01chungee10/Github/TOSS/reports/backtests/random500_seed20260607_contextual_optimizer_2022-01-01_2025-12-31_all_trials.csv
- selected_by_situation: /Users/01chungee10/Github/TOSS/reports/backtests/random500_seed20260607_contextual_optimizer_2022-01-01_2025-12-31_selected_by_situation.csv
- combined_daily_curve: /Users/01chungee10/Github/TOSS/reports/backtests/random500_seed20260607_contextual_optimizer_2022-01-01_2025-12-31_combined_daily_curve.csv
- combined_picks: /Users/01chungee10/Github/TOSS/reports/backtests/random500_seed20260607_contextual_optimizer_2022-01-01_2025-12-31_combined_picks.csv
- json: /Users/01chungee10/Github/TOSS/reports/backtests/random500_seed20260607_contextual_optimizer_2022-01-01_2025-12-31.json
