# Contextual Monday-buy Friday-sell optimizer — random 500 seed 20260607

Research-only. 실주문 없음. 투자 조언 아님.

## Method
- Panel: /Users/01chungee10/Github/TOSS/reports/backtests/random500_seed20260607_2022-01-01_2025-12-31_ohlcv_panel.csv
- Entry/Exit: Monday open -> same-week Friday close
- Train: <= 2024-12-31, test: >= 2025-01-01
- Grid size: 48 parameter combinations per situation
- Objective: train core score + test bonus - train/test gap penalty - weak-test penalty

## Combined approved contextual policy performance
- Train: {'days': 139, 'active_days': 28, 'total_trades': 84, 'final_value_krw': 1284570.09, 'total_return_pct': 28.46, 'cagr_pct': 8.8, 'max_drawdown_pct': -22.83, 'sharpe': 0.48, 'win_rate_pct': 42.86, 'profit_factor': 1.617}
- Test: {'days': 48, 'active_days': 10, 'total_trades': 30, 'final_value_krw': 1396816.73, 'total_return_pct': 39.68, 'cagr_pct': 40.76, 'max_drawdown_pct': -3.15, 'sharpe': 2.504, 'win_rate_pct': 90.0, 'profit_factor': 11.938}
- All: {'days': 187, 'active_days': 38, 'total_trades': 114, 'final_value_krw': 1794309.0, 'total_return_pct': 79.43, 'cagr_pct': 15.88, 'max_drawdown_pct': -22.83, 'sharpe': 0.804, 'win_rate_pct': 55.26, 'profit_factor': 2.198}

## Approved policy by situation
- flat_low_vol: reversal mom_1d/vol_20d -> monfri_open_to_fri_close_ret, top_n=3, min_dv=1000000000, min_abs_mom=0.0; train return 28.46%, test return 39.68%, return gap 11.22%, test MDD -3.15%, test Sharpe 6.101

## Rejected best-by-objective candidates
- flat_high_vol: train -10.34%, test 10.63%, return gap 20.97%, test MDD 0.0%, test Sharpe 46.787
- down_low_vol: train 40.15%, test -7.85%, return gap 48.0%, test MDD -9.37%, test Sharpe -4.318
- flat_low_vol: train 28.46%, test 39.68%, return gap 11.22%, test MDD -3.15%, test Sharpe 6.101
- up_high_vol: train 21.53%, test -5.57%, return gap 27.1%, test MDD -8.83%, test Sharpe -0.707
- down_high_vol: train 30.05%, test 7.92%, return gap 22.13%, test MDD 0.0%, test Sharpe 0.0
- up_low_vol: train -16.57%, test 11.27%, return gap 27.84%, test MDD -20.53%, test Sharpe 0.95

## Outputs
- policy: /Users/01chungee10/Github/TOSS/config/generated_policies/contextual_mon_fri_policy_seed20260607.json
- all_trials: /Users/01chungee10/Github/TOSS/reports/backtests/random500_seed20260607_contextual_optimizer_mon_fri_cycle_2022-01-01_2025-12-31_all_trials.csv
- selected_by_situation: /Users/01chungee10/Github/TOSS/reports/backtests/random500_seed20260607_contextual_optimizer_mon_fri_cycle_2022-01-01_2025-12-31_selected_by_situation.csv
- combined_daily_curve: /Users/01chungee10/Github/TOSS/reports/backtests/random500_seed20260607_contextual_optimizer_mon_fri_cycle_2022-01-01_2025-12-31_combined_daily_curve.csv
- combined_picks: /Users/01chungee10/Github/TOSS/reports/backtests/random500_seed20260607_contextual_optimizer_mon_fri_cycle_2022-01-01_2025-12-31_combined_picks.csv
- json: /Users/01chungee10/Github/TOSS/reports/backtests/random500_seed20260607_contextual_optimizer_mon_fri_cycle_2022-01-01_2025-12-31.json
