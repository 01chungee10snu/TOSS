# Profit research loop report

Research-only. 실주문 없음. 투자 조언 아님.

## Best branch
- branch_id: monfri_veto_higher_liquidity
- cycle: monfri
- method: fast_veto_frontier
- recommendation: promote_to_next_replay
- score: 113.1
- performance: {'periods': 37, 'total_trades': 80, 'total_return_pct': 117.0, 'max_drawdown_pct': -21.21, 'win_rate_pct': 62.16, 'sharpe_proxy': 1.731}
- thresholds: {'max_gap_pct': 0.1, 'max_intraday_range_pct': 0.2, 'min_dollar_volume_krw': 1000000000.0, 'max_prev_volatility_20d': 0.12}

## Top branches
- monfri_veto_higher_liquidity: return 117.0%, MDD -21.21%, SharpeProxy 1.731, trades 80
- monfri_veto_looser_range: return 94.52%, MDD -18.92%, SharpeProxy 1.724, trades 110
- monfri_veto_looser_all: return 94.52%, MDD -18.92%, SharpeProxy 1.724, trades 110
- monfri_contextual_baseline: return 79.43%, MDD -22.83%, SharpeProxy 0.804, trades 114
- monfri_veto_base: return 71.16%, MDD -29.7%, SharpeProxy 1.428, trades 105

## Interpretation
- Daily contextual baseline is the steadier broad-market lane.
- Monday-buy Friday-sell lane is the higher-upside tactical lane.
- Fast-veto variants test whether cutting noisy weekly entries improves return/drawdown frontier.

## Outputs
- json: /Users/01chungee10/Github/TOSS/reports/harness/profit_research_loop_20260618T103050Z.json
