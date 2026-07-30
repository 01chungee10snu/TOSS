# TOSS ttak autotrading loop report

- generated_at_utc: 2026-07-30T04:33:55.592150+00:00
- overall_status: LIVE_SUBMIT_BLOCKED

## Quant
- status: ACTIONABLE_CANDIDATES
- panel_exists: True
- policy_exists: True
- policy_json: /Users/01chungee10/Github/TOSS/config/generated_policies/daily_multifactor_v1_practical400.json
- candidate_json: /Users/01chungee10/Github/TOSS/reports/trade_candidates/candidates_2026-07-29_daily_multifactor_v1_practical400.json
- candidate_status: CANDIDATES
- candidate_situation: down_high_vol
- strategy_type: None
- order_count: 2
- inverse_sleeve: applied=False reason=symbol_specific_ordinary_buy_authorized

## Fast veto
- status: READY
- policy_json: /Users/01chungee10/Github/TOSS/config/generated_policies/daily_multifactor_v1_practical400.json
- thresholds: {'max_gap_pct': 0.08, 'max_intraday_range_pct': 0.25, 'min_dollar_volume_krw': 10000000.0, 'max_prev_volatility_20d': 0.15}
- reasons: []
- checked_symbols: ['322000', '475150', '388210']
- vetoed_symbols: []
- allowed_count: 3 / 3
- reasons_by_symbol: {}

## Symbol issue authorization
- status: WATCH
- require_positive: False
- checked_symbols: ['322000', '388210', '475150']
- verdicts_by_symbol: {'322000': 'WATCH', '475150': 'WATCH', '388210': 'WATCH'}
- buy/watch/review/veto: 0/3/0/0
- news/disclosure events: 3/0
- collector_errors: {}
- disclosure_errors: {}
- market_overlay: {'ordinary_buy_authorized': True, 'authorized_symbols': ['475150', '388210'], 'size_multiplier': 0.35, 'emergency_block': False, 'emergency_threshold': -0.03, 'market_day_return': -0.009616997483925083, 'market_regime': 'risk_off', 'news_severity': 'high', 'reason': 'risk_off_reduced_size'}

## Position exit
- enabled: True
- status_reason: None
- positions_checked: 0
- sell_order_count: 0
- stop_loss_pct: 0.03
- take_profit_pct: 0.04
- trailing_stop_pct: 0.03
- max_holding_trading_days: 1
- max_positions_limit: None
- equity_guard: READY
- equity_guard_threshold_pct: 0.06
- equity_guard_cooldown_seconds: 691200
- equity_guard_cooldown_unit: days
- equity_guard_drawdown_pct: -0.0395879688445433
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
- checked_symbols: ['475150', '388210']
- pending_symbols: ['475150', '388210']
- blocked_symbols: []
- review_required_symbols: []
- event_counts: {'news_events': {'block': 0, 'review': 0, 'info': 0}, 'opendart': {}}
- source_statuses: {'news_events': 'READY', 'opendart': 'SKIPPED_SOURCE_UNAVAILABLE'}

## Live readiness
- status: LIVE_READY
- ready: True
- default_mode: BLOCK_UNLESS_DOUBLE_OPT_IN
- dry_run_available: True
- missing: []

## Live submit
- status: LIVE_SUBMIT_BLOCKED
- dry_run: False
- submit_enabled: True
- order_count: 2
- attempted_count: 2
- submitted_count: 0
- blocked_count: 2
- violations: []
- artifact_path: /Users/01chungee10/Github/TOSS/reports/harness/live_submit_20260730T043352Z.json
- ledger_path: /Users/01chungee10/Github/TOSS/reports/harness/live_order_ledger.jsonl

## Notes
- 정량은 엔진, 정성은 gate/veto, live는 readiness, live-submit은 triple opt-in guarded executor다.
- 기본값은 실주문 미제출이며 dry-run/disabled artifact만 남긴다.
