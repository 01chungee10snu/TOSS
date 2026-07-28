# TOSS ttak autotrading loop report

- generated_at_utc: 2026-07-28T02:35:41.988952+00:00
- overall_status: LIVE_SUBMITTED

## Quant
- status: ACTIONABLE_CANDIDATES
- panel_exists: True
- policy_exists: True
- policy_json: /Users/01chungee10/Github/TOSS/config/generated_policies/daily_multifactor_v1_practical400.json
- candidate_json: /Users/01chungee10/Github/TOSS/reports/trade_candidates/candidates_2026-07-27_inverse_sleeve_risk_off_v1.json
- candidate_status: CANDIDATES
- candidate_situation: inverse_sleeve_risk_off
- strategy_type: inverse_sleeve
- order_count: 1
- inverse_sleeve: applied=True reason=inverse_sleeve_replaced_bad_regime_long_payload
- inverse_candidate_json: /Users/01chungee10/Github/TOSS/reports/trade_candidates/candidates_2026-07-27_inverse_sleeve_risk_off_v1.json

## Fast veto
- status: READY_WITH_VETO
- policy_json: /Users/01chungee10/Github/TOSS/config/generated_policies/daily_multifactor_v1_practical400.json
- thresholds: {'max_gap_pct': 0.08, 'max_intraday_range_pct': 0.15, 'min_dollar_volume_krw': 10000000.0, 'max_prev_volatility_20d': 0.1}
- reasons: ['excessive_intraday_range']
- checked_symbols: ['319400', '161390', '108490']
- vetoed_symbols: ['319400']
- allowed_count: 2 / 3
- reasons_by_symbol: {'319400': ['excessive_intraday_range']}

## Symbol issue authorization
- status: WATCH
- require_positive: False
- checked_symbols: ['108490', '161390']
- verdicts_by_symbol: {'161390': 'WATCH', '108490': 'WATCH'}
- buy/watch/review/veto: 0/2/0/0
- news/disclosure events: 8/0
- collector_errors: {}
- disclosure_errors: {}
- market_overlay: {'ordinary_buy_authorized': False, 'authorized_symbols': [], 'size_multiplier': 0.0, 'emergency_block': True, 'emergency_threshold': -0.03, 'market_day_return': -0.10125313283208015, 'market_regime': 'risk_off', 'news_severity': 'low', 'reason': 'symbol_market_emergency_block'}

## Position exit
- enabled: True
- status_reason: None
- positions_checked: 0
- sell_order_count: 0
- stop_loss_pct: 0.05
- take_profit_pct: 0.1
- trailing_stop_pct: 0.05
- max_holding_trading_days: 3
- max_positions_limit: None
- equity_guard: READY
- equity_guard_threshold_pct: 0.06
- equity_guard_cooldown_seconds: 691200
- equity_guard_cooldown_unit: days
- equity_guard_drawdown_pct: -0.001122345385358825
- equity_guard_block_new_buys: False
- equity_guard_liquidation_required: False
- report_path: /Users/01chungee10/Github/TOSS/reports/harness/latest_position_exit_report.json

## Qual
- status: READY
- connector_exists: True
- opendart_api_key_present: False
- require_opendart: False
- news_events_path: /Users/01chungee10/Github/TOSS/reports/harness/manual_news_events.json
- news_events_count: 0
- news_events_error: None
- reasons: []
- checked_symbols: ['114800']
- pending_symbols: []
- blocked_symbols: []
- review_required_symbols: []
- event_counts: {}
- source_statuses: {'inverse_sleeve': 'READY'}

## Live readiness
- status: LIVE_READY
- ready: True
- default_mode: BLOCK_UNLESS_DOUBLE_OPT_IN
- dry_run_available: True
- missing: []

## Live submit
- status: LIVE_SUBMITTED
- dry_run: False
- submit_enabled: True
- order_count: 1
- attempted_count: 1
- submitted_count: 1
- blocked_count: 0
- violations: []
- artifact_path: /Users/01chungee10/Github/TOSS/reports/harness/live_submit_20260728T023538Z.json
- ledger_path: /Users/01chungee10/Github/TOSS/reports/harness/live_order_ledger.jsonl

## Notes
- 정량은 엔진, 정성은 gate/veto, live는 readiness, live-submit은 triple opt-in guarded executor다.
- 기본값은 실주문 미제출이며 dry-run/disabled artifact만 남긴다.
