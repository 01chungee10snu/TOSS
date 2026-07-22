# TOSS ttak autotrading loop report

- generated_at_utc: 2026-07-22T00:40:28.011623+00:00
- overall_status: NO_TRADE

## Quant
- status: NO_TRADE
- panel_exists: True
- policy_exists: True
- policy_json: /Users/01chungee10/Github/TOSS/config/generated_policies/daily_multifactor_v1_practical400.json
- candidate_json: /Users/01chungee10/Github/TOSS/reports/trade_candidates/candidates_2026-07-16_daily_multifactor_v1_practical400.json
- candidate_status: NO_TRADE
- candidate_situation: down_high_vol
- strategy_type: None
- order_count: 0
- inverse_sleeve: applied=False reason=inverse_sleeve_disabled

## Fast veto
- status: SKIPPED_NO_CANDIDATES
- policy_json: /Users/01chungee10/Github/TOSS/config/generated_policies/daily_multifactor_v1_practical400.json
- thresholds: {}
- reasons: ['no_candidate_symbols']
- checked_symbols: []
- vetoed_symbols: []
- allowed_count: 0 / 0
- reasons_by_symbol: {}

## Symbol issue authorization
- status: WATCH
- require_positive: True
- checked_symbols: []
- verdicts_by_symbol: {}
- buy/watch/review/veto: 0/0/0/0
- news/disclosure events: 0/0
- collector_errors: {}
- disclosure_errors: {}
- market_overlay: {'ordinary_buy_authorized': False, 'authorized_symbols': [], 'size_multiplier': 1.0, 'emergency_block': False, 'emergency_threshold': -0.03, 'market_day_return': 0.05395783243943031, 'market_regime': 'risk_on', 'news_severity': 'low', 'reason': 'risk_on_full_size'}

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
- dry_run: True
- submit_enabled: True
- order_count: 0
- attempted_count: 0
- submitted_count: 0
- blocked_count: 0
- violations: []
- artifact_path: /Users/01chungee10/Github/TOSS/reports/harness/live_submit_20260722T004028Z.json
- ledger_path: /Users/01chungee10/Github/TOSS/reports/harness/live_order_ledger.jsonl

## Notes
- 정량은 엔진, 정성은 gate/veto, live는 readiness, live-submit은 triple opt-in guarded executor다.
- 기본값은 실주문 미제출이며 dry-run/disabled artifact만 남긴다.
