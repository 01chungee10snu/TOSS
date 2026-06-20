# TOSS Alpha Research Report

## 연구 목표
- goal_id: kr_profit_compound_autotrading_v1
- mode: backtest_only
- symbols: 101730, 267260, 004000, 005300, 014830, 033780, 053800, 060570, 203690, 214370, 284740, 344820
- period: 2022-01-01 ~ 2025-12-31
- strategy: contextual_daily_with_monfri_submode

## 데이터 기준 시점
- data_as_of: 2025-12-30T00:00:00

## 신호/이벤트 근거
- 005300: score=0.0574 / momentum=0.092060; vol_penalty=-0.034635; short_ma=130737.8305, long_ma=119716.7612; daily_return_stdev=0.034635
- 284740: score=0.0171 / momentum=0.027566; vol_penalty=-0.010465; short_ma=24251.6934, long_ma=23601.1160; daily_return_stdev=0.010465
- 053800: score=0.0114 / momentum=0.024476; vol_penalty=-0.013102; short_ma=60908.1113, long_ma=59452.9260; daily_return_stdev=0.013102
- 033780: score=0.0090 / momentum=0.024609; vol_penalty=-0.015575; short_ma=140167.6953, long_ma=136801.1374; daily_return_stdev=0.015575
- 267260: score=0.0067 / momentum=0.033057; vol_penalty=-0.026349; short_ma=808307.9531, long_ma=782443.0219; daily_return_stdev=0.026349
- 214370: score=0.0040 / momentum=0.038886; vol_penalty=-0.034899; short_ma=69769.6586, long_ma=67158.1217; daily_return_stdev=0.034899
- 344820: score=0.0003 / momentum=0.008872; vol_penalty=-0.008523; short_ma=26137.6933, long_ma=25907.8423; daily_return_stdev=0.008523
- 203690: score=0.0000 / momentum=0.000000; vol_penalty=-0.000000; short_ma=4425.0000, long_ma=4425.0000; daily_return_stdev=0.000000
- 004000: score=-0.0075 / momentum=0.007210; vol_penalty=-0.014679; short_ma=45444.7867, long_ma=45119.4876; daily_return_stdev=0.014679
- 014830: score=-0.0272 / momentum=-0.015128; vol_penalty=-0.012083; short_ma=66307.7363, long_ma=67326.2283; daily_return_stdev=0.012083
- 101730: score=-0.0329 / momentum=-0.015992; vol_penalty=-0.016882; short_ma=6502.0000, long_ma=6607.6667; daily_return_stdev=0.016882
- 060570: score=-0.0608 / momentum=-0.039595; vol_penalty=-0.021205; short_ma=1573.0000, long_ma=1637.8500; daily_return_stdev=0.021205

## 백테스트 요약
- status: PASS
- total_return: 0.200000
- max_drawdown: -0.086234
- trades: 1
- fees_krw: 0.00
- slippage_krw: 0.00

## 리스크 게이트
- status: BLOCK
- allow: False
- violations: live_trading_disabled, manual_confirmation_required, max_position_pct_exceeded

## 수동 검토 초안
## 수동 주문 검토 초안
- 상태: BLOCK
- 모드: manual_draft_only
- 안전 문구: 실주문 아님 / 수동 확인 필요
- 종목: 005300
- 방향 후보: BUY
- 금액 후보: 100000.0
- 사유: top ranked symbol from contextual_daily_with_monfri_submode
- 리스크 위반: live_trading_disabled, manual_confirmation_required, max_position_pct_exceeded
- 판단 근거: selected highest combined score among 12 tracked symbols
### Evidence
- panel_csv=reports/backtests/random500_seed20260607_2022-01-01_2025-12-31_ohlcv_panel.csv
- selected_symbol=005300
- qual_gate_status=BLOCKED_QUAL_DATA
- backtest_status=PASS


## 주의 문구
- 투자 조언 아님: 이 보고서는 연구/백테스트 보조 자료입니다.
- 손실 가능: 모든 투자는 원금 손실 가능성이 있습니다.
- 실주문 아님: 실행 전 사용자의 별도 수동 확인이 필요합니다.
