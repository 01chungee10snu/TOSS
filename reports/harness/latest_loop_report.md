# TOSS ttak autotrading loop report

- generated_at_utc: 2026-06-20T21:31:45.779173+00:00
- overall_status: NO_TRADE

## Quant
- status: NO_TRADE
- panel_exists: True
- policy_exists: True
- policy_json: /Users/01chungee10/Github/TOSS/config/generated_policies/contextual_mon_fri_policy_seed20260607_walkforward_promoted.json
- candidate_json: /Users/01chungee10/Github/TOSS/reports/trade_candidates/candidates_2025-12-30_contextual_mon_fri_policy_seed20260607_walkforward_promoted.json
- candidate_status: NO_TRADE
- candidate_situation: up_low_vol
- order_count: 0

## Fast veto
- status: SKIPPED_NO_CANDIDATES
- policy_json: /Users/01chungee10/Github/TOSS/config/generated_policies/contextual_mon_fri_policy_seed20260607_walkforward_promoted.json
- thresholds: {}
- reasons: ['no_candidate_symbols']
- checked_symbols: []
- vetoed_symbols: []
- allowed_count: 0 / 0
- reasons_by_symbol: {}

## Qual
- status: SKIPPED_NO_CANDIDATES
- connector_exists: True
- opendart_api_key_present: False
- reasons: ['no_candidate_symbols']
- checked_symbols: []
- pending_symbols: []
- review_required_symbols: []
- event_counts: {}

## Live readiness
- status: LIVE_BLOCKED
- ready: False
- default_mode: BLOCK_UNLESS_DOUBLE_OPT_IN
- dry_run_available: False
- missing: ['live_trading_disabled', 'env_live_trading_not_enabled', 'client_credentials', 'account_seq', 'order_endpoint_path']

## Notes
- 정량은 엔진, 정성은 gate/veto, live는 readiness-only다.
- 실주문은 수행하지 않는다.
