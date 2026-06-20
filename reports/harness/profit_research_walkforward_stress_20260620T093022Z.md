# Profit research walk-forward stress report

Research-only. 실주문 없음. 투자 조언 아님.

- stress_scenarios: [{'scenario_id': 'base', 'extra_round_trip_bps': 0.0}, {'scenario_id': 'stress_plus_10bps', 'extra_round_trip_bps': 10.0}, {'scenario_id': 'stress_plus_20bps', 'extra_round_trip_bps': 20.0}, {'scenario_id': 'stress_plus_30bps', 'extra_round_trip_bps': 30.0}]
- robust_winner_count: 2
- promoted_policy_written: True

## Robust winner
- variant_id: veto_higher_liquidity_looser_range
- robust_score: 107.67
- stress_pass_count: 4/4
- worst_case_return_pct: 96.83
- worst_case_drawdown_pct: -24.74
- thresholds: {'max_gap_pct': 0.08, 'max_intraday_range_pct': 0.22, 'min_dollar_volume_krw': 1000000000.0, 'max_prev_volatility_20d': 0.1}
  - base: return 118.22%, MDD -21.21%, trades 76, gate True
  - stress_plus_10bps: return 110.85%, MDD -22.4%, trades 76, gate True
  - stress_plus_20bps: return 103.72%, MDD -23.58%, trades 76, gate True
  - stress_plus_30bps: return 96.83%, MDD -24.74%, trades 76, gate True

## Top variants
- veto_higher_liquidity_looser_range: all_scenarios_approved=True, robust_score=107.67, worst_case_return=96.83%, worst_case_drawdown=-24.74%
- veto_higher_liquidity: all_scenarios_approved=True, robust_score=105.71, worst_case_return=95.06%, worst_case_drawdown=-24.74%
- veto_looser_range: all_scenarios_approved=False, robust_score=68.91, worst_case_return=77.11%, worst_case_drawdown=-23.47%
- veto_looser_all: all_scenarios_approved=False, robust_score=68.91, worst_case_return=77.11%, worst_case_drawdown=-23.47%
- baseline: all_scenarios_approved=False, robust_score=49.62, worst_case_return=63.34%, worst_case_drawdown=-27.17%

## Outputs
- json: /Users/01chungee10/Github/TOSS/reports/harness/profit_research_walkforward_stress_20260620T093022Z.json
- md: /Users/01chungee10/Github/TOSS/reports/harness/profit_research_walkforward_stress_20260620T093022Z.md
- promoted_policy_json: /Users/01chungee10/Github/TOSS/config/generated_policies/contextual_mon_fri_policy_seed20260607_walkforward_promoted.json
