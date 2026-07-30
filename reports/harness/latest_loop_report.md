# TOSS ttak autotrading loop report

- generated_at_utc: 2026-07-30T02:33:38.379846+00:00
- overall_status: BLOCKED_FAST_VETO

## Quant
- status: NO_TRADE
- panel_exists: True
- policy_exists: True
- policy_json: /Users/01chungee10/Github/TOSS/config/generated_policies/daily_multifactor_v1_practical400.json
- candidate_json: /Users/01chungee10/Github/TOSS/reports/trade_candidates/candidates_2026-07-29_daily_multifactor_v1_practical400.json
- candidate_status: NO_TRADE
- candidate_situation: down_high_vol
- strategy_type: None
- order_count: 0
- inverse_sleeve: applied=False reason=inverse_sleeve_not_needed:intraday_decision:LONG_BUY

## Fast veto
- status: BLOCKED_FAST_VETO
- policy_json: /Users/01chungee10/Github/TOSS/config/generated_policies/daily_multifactor_v1_practical400.json
- thresholds: {'max_gap_pct': 0.08, 'max_intraday_range_pct': 0.15, 'min_dollar_volume_krw': 10000000.0, 'max_prev_volatility_20d': 0.1}
- reasons: ['excessive_intraday_range', 'excessive_prev_volatility_20d']
- checked_symbols: ['322000', '475150', '388210']
- vetoed_symbols: ['322000', '475150', '388210']
- allowed_count: 0 / 3
- reasons_by_symbol: {'322000': ['excessive_intraday_range'], '475150': ['excessive_intraday_range', 'excessive_prev_volatility_20d'], '388210': ['excessive_intraday_range']}

## Symbol issue authorization
- status: WATCH
- require_positive: False
- checked_symbols: []
- verdicts_by_symbol: {}
- buy/watch/review/veto: 0/0/0/0
- news/disclosure events: 0/0
- collector_errors: {}
- disclosure_errors: {}
- market_overlay: {'ordinary_buy_authorized': False, 'authorized_symbols': [], 'size_multiplier': 1.0, 'emergency_block': False, 'emergency_threshold': -0.03, 'market_day_return': 0.011629857422420953, 'market_regime': 'risk_on', 'news_severity': 'high', 'reason': 'risk_on_full_size'}

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
- equity_guard_drawdown_pct: -0.034590656737934045
- equity_guard_block_new_buys: False
- equity_guard_liquidation_required: False
- report_path: /Users/01chungee10/Github/TOSS/reports/harness/latest_position_exit_report.json

## Qual
- status: SKIPPED_NO_CANDIDATES
- connector_exists: True
- opendart_api_key_present: False
- require_opendart: False
- news_events_path: /Users/01chungee10/Github/TOSS/reports/harness/manual_news_events.json
- news_events_count: 0
- news_events_error: None
- reasons: ['no_candidate_symbols']
- checked_symbols: []
- pending_symbols: []
- blocked_symbols: []
- review_required_symbols: []
- event_counts: {}
- source_statuses: {}

## Live readiness
- status: LIVE_READY
- ready: True
- default_mode: BLOCK_UNLESS_DOUBLE_OPT_IN
- dry_run_available: True
- missing: []

## Live submit
- status: LIVE_SUBMIT_NO_ORDERS
- dry_run: False
- submit_enabled: True
- order_count: 0
- attempted_count: 0
- submitted_count: 0
- blocked_count: 0
- violations: []
- artifact_path: /Users/01chungee10/Github/TOSS/reports/harness/live_submit_20260730T023338Z.json
- ledger_path: /Users/01chungee10/Github/TOSS/reports/harness/live_order_ledger.jsonl

## Notes
- 정량은 엔진, 정성은 gate/veto, live는 readiness, live-submit은 triple opt-in guarded executor다.
- 기본값은 실주문 미제출이며 dry-run/disabled artifact만 남긴다.
