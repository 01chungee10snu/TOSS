# Profit research walk-forward report

Research-only. 실주문 없음. 투자 조언 아님.

## Adaptive train-winner OOS
- performance: {'periods': 35, 'total_trades': 89, 'total_return_pct': 39.67, 'max_drawdown_pct': -35.15, 'win_rate_pct': 51.43, 'sharpe_proxy': 0.931}
- selected_variant_frequency: {'veto_base': 1, 'veto_higher_liquidity': 1, 'veto_looser_range': 1}

## Fixed variant leaderboard
- variant_id: veto_higher_liquidity
  - fixed_oos_score: 112.48
  - aggregate_oos: {'periods': 35, 'total_trades': 75, 'total_return_pct': 116.26, 'max_drawdown_pct': -21.21, 'win_rate_pct': 62.86, 'sharpe_proxy': 1.743}
  - negative_test_years: 0
  - positive_test_years: 3
  - consistency_ratio: 1.0
  - gate: {'approved': True, 'reasons': [], 'variant_id': 'veto_higher_liquidity'}
- variant_id: veto_looser_range
  - fixed_oos_score: 83.807
  - aggregate_oos: {'periods': 36, 'total_trades': 104, 'total_return_pct': 96.99, 'max_drawdown_pct': -18.92, 'win_rate_pct': 55.56, 'sharpe_proxy': 1.777}
  - negative_test_years: 1
  - positive_test_years: 2
  - consistency_ratio: 0.667
  - gate: {'approved': False, 'reasons': ['negative_test_years_exceeded', 'consistency_ratio_too_low'], 'variant_id': 'veto_looser_range'}
- variant_id: veto_looser_all
  - fixed_oos_score: 83.807
  - aggregate_oos: {'periods': 36, 'total_trades': 104, 'total_return_pct': 96.99, 'max_drawdown_pct': -18.92, 'win_rate_pct': 55.56, 'sharpe_proxy': 1.777}
  - negative_test_years: 1
  - positive_test_years: 2
  - consistency_ratio: 0.667
  - gate: {'approved': False, 'reasons': ['negative_test_years_exceeded', 'consistency_ratio_too_low'], 'variant_id': 'veto_looser_all'}
- variant_id: baseline
  - fixed_oos_score: 62.827
  - aggregate_oos: {'periods': 36, 'total_trades': 108, 'total_return_pct': 81.71, 'max_drawdown_pct': -22.83, 'win_rate_pct': 55.56, 'sharpe_proxy': 1.598}
  - negative_test_years: 1
  - positive_test_years: 2
  - consistency_ratio: 0.667
  - gate: {'approved': False, 'reasons': ['negative_test_years_exceeded', 'consistency_ratio_too_low'], 'variant_id': 'baseline'}
- variant_id: veto_base
  - fixed_oos_score: 43.237
  - aggregate_oos: {'periods': 36, 'total_trades': 100, 'total_return_pct': 70.57, 'max_drawdown_pct': -29.7, 'win_rate_pct': 52.78, 'sharpe_proxy': 1.44}
  - negative_test_years: 1
  - positive_test_years: 2
  - consistency_ratio: 0.667
  - gate: {'approved': False, 'reasons': ['negative_test_years_exceeded', 'consistency_ratio_too_low'], 'variant_id': 'veto_base'}

## Adaptive fold details
- test_year: 2023
  - train_years: [2022]
  - selected_variant_id: veto_base
  - selected_train_score: -19.76
  - train_performance: {'periods': 2, 'total_trades': 5, 'total_return_pct': 0.34, 'max_drawdown_pct': -5.68, 'win_rate_pct': 50.0, 'sharpe_proxy': 0.058}
  - test_performance: {'periods': 11, 'total_trades': 30, 'total_return_pct': -16.26, 'max_drawdown_pct': -25.46, 'win_rate_pct': 18.18, 'sharpe_proxy': -1.214}
  - test_kept_trades: 30
  - test_blocked_trades: 3
  - test_blocked_counts_by_reason: {'excessive_gap': 2, 'excessive_intraday_range': 3, 'excessive_prev_volatility_20d': 1}
- test_year: 2024
  - train_years: [2022, 2023]
  - selected_variant_id: veto_higher_liquidity
  - selected_train_score: -1.28
  - train_performance: {'periods': 13, 'total_trades': 30, 'total_return_pct': 4.71, 'max_drawdown_pct': -9.43, 'win_rate_pct': 53.85, 'sharpe_proxy': 0.344}
  - test_performance: {'periods': 14, 'total_trades': 30, 'total_return_pct': 14.63, 'max_drawdown_pct': -18.07, 'win_rate_pct': 50.0, 'sharpe_proxy': 0.504}
  - test_kept_trades: 30
  - test_blocked_trades: 15
  - test_blocked_counts_by_reason: {'low_dollar_volume': 14, 'excessive_gap': 1}
- test_year: 2025
  - train_years: [2022, 2023, 2024]
  - selected_variant_id: veto_looser_range
  - selected_train_score: 23.6
  - train_performance: {'periods': 28, 'total_trades': 81, 'total_return_pct': 33.69, 'max_drawdown_pct': -18.92, 'win_rate_pct': 42.86, 'sharpe_proxy': 0.883}
  - test_performance: {'periods': 10, 'total_trades': 29, 'total_return_pct': 45.5, 'max_drawdown_pct': -3.15, 'win_rate_pct': 90.0, 'sharpe_proxy': 2.827}
  - test_kept_trades: 29
  - test_blocked_trades: 1
  - test_blocked_counts_by_reason: {'excessive_intraday_range': 1}

## Verdict guide
- adaptive OOS가 양수여도 fixed candidate gate를 못 넘으면 단일 threshold 승격은 보류한다.
- promotable fixed candidate가 1개 이상일 때만 generated policy 고정 승격 후보로 간주한다.

## Outputs
- json: /Users/01chungee10/Github/TOSS/reports/harness/profit_research_walkforward_20260618T131246Z.json
- md: /Users/01chungee10/Github/TOSS/reports/harness/profit_research_walkforward_20260618T131246Z.md
