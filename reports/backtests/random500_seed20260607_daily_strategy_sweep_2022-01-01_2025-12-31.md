# Daily buy/sell strategy sweep — random 500 seed 20260607

Research-only. 실주문 없음. 투자 조언 아님.

## Setup
- Sample: previous random 500; period 2022-01-01 ~ 2025-12-31
- Top N: 10; min previous dollar volume: 100,000,000 KRW
- Round-trip cost: 31.0 bps
- No-lookahead: ranking features use previous close history only

## Variants ranked by total_return_pct
- open_to_next_open_bottom_5d_reversal: return -96.38%, CAGR -56.49%, MDD -96.71%, Sharpe -2.019, win 44.05%, trades 9660
- close_to_next_close_bottom_5d_reversal: return -98.03%, CAGR -62.68%, MDD -98.2%, Sharpe -2.7, win 41.47%, trades 9660
- intraday_bottom_5d_reversal: return -98.71%, CAGR -66.45%, MDD -98.72%, Sharpe -3.262, win 41.12%, trades 9670
- intraday_bottom_1d_reversal: return -99.56%, CAGR -74.35%, MDD -99.57%, Sharpe -3.937, win 38.37%, trades 9710
- close_to_next_close_top_5d_momentum: return -99.7%, CAGR -76.76%, MDD -99.76%, Sharpe -3.533, win 39.4%, trades 9650
- open_to_next_open_top_5d_momentum: return -99.98%, CAGR -88.06%, MDD -99.98%, Sharpe -4.761, win 35.57%, trades 9650
- intraday_top_5d_momentum: return -99.99%, CAGR -91.37%, MDD -99.99%, Sharpe -6.584, win 33.47%, trades 9660
- intraday_top_1d_momentum: return -100.0%, CAGR -91.87%, MDD -100.0%, Sharpe -6.787, win 32.54%, trades 9693

## Best variant
- {'name': 'open_to_next_open_bottom_5d_reversal', 'score_col': 'mom_5d_prev', 'ascending': True, 'return_col': 'open_open_ret', 'active_days': 967, 'total_trades': 9660, 'final_value_krw': 36233.62, 'total_return_pct': -96.38, 'cagr_pct': -56.49, 'max_drawdown_pct': -96.71, 'sharpe': -2.019, 'win_rate_pct': 44.05, 'profit_factor': 0.721}

## Outputs
- summary_csv: /mnt/c/Github/TOSS/reports/backtests/random500_seed20260607_daily_strategy_sweep_2022-01-01_2025-12-31_summary.csv
- best_picks_csv: /mnt/c/Github/TOSS/reports/backtests/random500_seed20260607_daily_strategy_sweep_2022-01-01_2025-12-31_best_picks.csv
- json: /mnt/c/Github/TOSS/reports/backtests/random500_seed20260607_daily_strategy_sweep_2022-01-01_2025-12-31.json
