# Contextual policy 비교: 일간 cycle vs 월요일 시가→금요일 종가 cycle

생성 시각(로컬 작업 기준): 2026-06-18

## 비교 대상
- 일간 cycle 리포트:
  - `/Users/01chungee10/Github/TOSS/reports/backtests/random500_seed20260607_contextual_optimizer_2022-01-01_2025-12-31.json`
- 일간 cycle 정책:
  - `/Users/01chungee10/Github/TOSS/config/generated_policies/contextual_daily_policy_seed20260607.json`
- 월→금 cycle 리포트:
  - `/Users/01chungee10/Github/TOSS/reports/backtests/random500_seed20260607_contextual_optimizer_mon_fri_cycle_2022-01-01_2025-12-31.json`
- 월→금 cycle 정책:
  - `/Users/01chungee10/Github/TOSS/config/generated_policies/contextual_mon_fri_policy_seed20260607.json`

## 1) 승인된 regime 비교

### 일간 cycle 승인 2개
- `down_low_vol`
  - mode: `reversal`
  - momentum_col: `mom_5d`
  - vol_col: `vol_20d`
  - return_col: `cc_next_ret`
  - top_n: `3`
  - min_dollar_volume: `100,000,000`
- `flat_high_vol`
  - mode: `reversal`
  - momentum_col: `mom_20d`
  - vol_col: `vol_20d`
  - return_col: `oo_ret`
  - top_n: `3`
  - min_dollar_volume: `100,000,000`

### 월→금 cycle 승인 1개
- `flat_low_vol`
  - mode: `reversal`
  - momentum_col: `mom_1d`
  - vol_col: `vol_20d`
  - return_col: `monfri_open_to_fri_close_ret`
  - top_n: `3`
  - min_dollar_volume: `1,000,000,000`

## 2) Combined 성능 비교

### 전체 구간 combined_all
- 일간 cycle
  - days: `974`
  - active_days: `163`
  - active_day_ratio: `16.74%`
  - total_trades: `487`
  - final_value_krw: `1,455,666.74`
  - total_return_pct: `45.57%`
  - cagr_pct: `9.88%`
  - max_drawdown_pct: `-19.15%`
  - sharpe: `0.612`
  - win_rate_pct: `47.85%`
  - profit_factor: `1.296`
  - trades_per_active_day: `2.99`

- 월→금 cycle
  - days: `187`
  - active_days: `38`
  - active_day_ratio: `20.32%`
  - total_trades: `114`
  - final_value_krw: `1,794,309.00`
  - total_return_pct: `79.43%`
  - cagr_pct: `15.88%`
  - max_drawdown_pct: `-22.83%`
  - sharpe: `0.804`
  - win_rate_pct: `55.26%`
  - profit_factor: `2.198`
  - trades_per_active_day: `3.00`

## 3) Train/Test 비교

### 일간 cycle combined_train
- total_return_pct: `15.74%`
- cagr_pct: `5.01%`
- max_drawdown_pct: `-18.45%`
- sharpe: `0.360`
- total_trades: `382`
- active_days: `128`

### 일간 cycle combined_test
- total_return_pct: `25.77%`
- cagr_pct: `26.03%`
- max_drawdown_pct: `-9.71%`
- sharpe: `1.379`
- total_trades: `105`
- active_days: `35`

### 월→금 cycle combined_train
- total_return_pct: `28.46%`
- cagr_pct: `8.80%`
- max_drawdown_pct: `-22.83%`
- sharpe: `0.480`
- total_trades: `84`
- active_days: `28`

### 월→금 cycle combined_test
- total_return_pct: `39.68%`
- cagr_pct: `40.76%`
- max_drawdown_pct: `-3.15%`
- sharpe: `2.504`
- total_trades: `30`
- active_days: `10`

## 4) 승인 regime별 비교 메모

### 일간: `down_low_vol`
- train_total_return_pct: `11.81%`
- test_total_return_pct: `17.58%`
- train_test_return_gap_pct: `5.77%`
- train_sharpe: `1.353`
- test_sharpe: `4.335`
- train_total_trades: `165`
- test_total_trades: `54`

### 일간: `flat_high_vol`
- train_total_return_pct: `3.51%`
- test_total_return_pct: `6.97%`
- train_test_return_gap_pct: `3.46%`
- train_sharpe: `0.480`
- test_sharpe: `2.677`
- train_total_trades: `217`
- test_total_trades: `51`

### 월→금: `flat_low_vol`
- train_total_return_pct: `28.46%`
- test_total_return_pct: `39.68%`
- train_test_return_gap_pct: `11.22%`
- train_sharpe: `0.996`
- test_sharpe: `6.101`
- train_total_trades: `84`
- test_total_trades: `30`

## 5) 해석

### 구조 변화
- 일간 cycle은 승인 regime가 `2개`다.
- 월→금 cycle은 승인 regime가 `1개`만 남는다.
- 즉 주간 보유로 바꾸면 살아남는 시장 상황이 더 좁아진다.

### 성과 숫자만 보면
- 월→금 cycle이 total return, CAGR, Sharpe, win rate, profit factor에서 더 좋다.
- 하지만 max drawdown은 일간보다 더 깊다 (`-22.83%` vs `-19.15%`).

### 표본/운영성 관점
- 일간 cycle은 거래 수가 훨씬 많다 (`487` trades).
- 월→금 cycle은 거래 수가 적다 (`114` trades).
- 특히 월→금 test는 `active_days 10`, `trades 30`이라 표본이 얇다.
- 따라서 월→금의 좋은 test 성과는 아직 과신하면 안 된다.

### 실무적 의미
- 일간 cycle은 더 자주 작동하는 운영 모드에 가깝다.
- 월→금 cycle은 더 선택적이고, 특정 상황(`flat_low_vol`)에만 강하게 반응하는 모드에 가깝다.
- 월→금은 수익률 효율은 좋아 보이지만, 범용 운영정책이라기보다는 얇은 specialty rule에 가깝다.

## 6) 현재 판단
- 운영 안정성/반복성 우선이면: **일간 cycle이 더 기본축**에 가깝다.
- 고른 상황 대응보다 특정 setup 집중형이면: **월→금 cycle은 보조 전략 후보**로 볼 수 있다.
- 아직은 월→금을 일간 대체재로 보기보다, **flat_low_vol 전용 서브모드**로 보는 해석이 더 보수적이다.

## 7) 바로 다음으로 좋은 검증
1. 월→금 승인 정책 `flat_low_vol`의 실제 진입 주차 목록을 `combined_picks.csv`로 뽑아 확인
2. 일간/월→금 각각에 fast veto를 동일하게 붙여 성능 변화 비교
3. 월→금 정책을 loop replay 날짜 기반으로 재현해 실제 후보 생성 일관성 검증
