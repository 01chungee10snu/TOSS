# Profit research loop report

Research-only. 실주문 없음. 투자 조언 아님.

## Best branch
- branch_id: monfri_contextual_baseline
- cycle: monfri
- method: baseline
- recommendation: promote_to_next_replay
- score: 64.64
- performance: {'periods': 38, 'total_trades': 114, 'total_return_pct': 79.43, 'max_drawdown_pct': -22.83, 'win_rate_pct': 55.26, 'sharpe_proxy': 0.804}

## Top branches
- monfri_contextual_baseline: return 79.43%, MDD -22.83%, SharpeProxy 0.804, trades 114
- daily_contextual_baseline: return 45.57%, MDD -19.15%, SharpeProxy 0.612, trades 487
- monfri_veto_base: return 0.0%, MDD 0.0%, SharpeProxy 0.0, trades 0
- monfri_veto_looser_range: return 0.0%, MDD 0.0%, SharpeProxy 0.0, trades 0
- monfri_veto_higher_liquidity: return 0.0%, MDD 0.0%, SharpeProxy 0.0, trades 0

## Interpretation
- Daily contextual baseline is the steadier broad-market lane.
- Monday-buy Friday-sell lane is the higher-upside tactical lane.
- Fast-veto variants test whether cutting noisy weekly entries improves return/drawdown frontier.

## Outputs
- json: /Users/01chungee10/Github/TOSS/reports/harness/profit_research_loop_20260618T102959Z.json
