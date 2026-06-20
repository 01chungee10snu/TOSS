# 월→금 contextual policy fast veto 재평가

대상: 기존 월요일 시가→금요일 종가 전략 picks에 `evaluate_fast_veto()` 기본 threshold 적용

## 적용 threshold
- max_gap_pct: `0.08`
- max_intraday_range_pct: `0.15`
- min_dollar_volume_krw: `10,000,000`
- max_prev_volatility_20d: `0.10`

## 게이트 상태 분포
- READY: `29` weeks
- READY_WITH_VETO: `9` weeks
- BLOCKED_FAST_VETO: `0` weeks

## 이유 집계
- excessive_intraday_range: `8`
- excessive_gap: `3`
- excessive_prev_volatility_20d: `1`

## Baseline vs fast veto 요약

### Baseline
- weeks: `38`
- active_weeks: `38`
- total_trades: `114`
- total_return_pct: `79.43`
- max_drawdown_pct: `-22.83`
- positive_week_rate_pct: `55.26`
- avg_week_return_pct: `1.768`
- median_week_return_pct: `0.081`

### Fast veto 적용 후
- weeks: `38`
- active_weeks: `38`
- total_trades: `105`
- total_return_pct: `71.16`
- max_drawdown_pct: `-29.7`
- positive_week_rate_pct: `52.63`
- avg_week_return_pct: `1.645`
- median_week_return_pct: `0.054`

## 개선 폭이 큰 주차 TOP 10
- 2023-09-02/2023-09-08 — delta `5.146%` | baseline `-6.208%` -> veto `-1.062%` | vetoed: 비투엔
- 2025-04-05/2025-04-11 — delta `4.523%` | baseline `8.522%` -> veto `13.045%` | vetoed: 웹스
- 2022-04-30/2022-05-06 — delta `1.691%` | baseline `4.690%` -> veto `6.381%` | vetoed: 아시아종묘
- 2025-05-24/2025-05-30 — delta `0.749%` | baseline `-3.155%` -> veto `-2.405%` | vetoed: 마니커
- 2024-06-29/2024-07-05 — delta `0.000%` | baseline `32.933%` -> veto `32.933%` | vetoed: none
- 2024-04-13/2024-04-19 — delta `0.000%` | baseline `-1.927%` -> veto `-1.927%` | vetoed: none
- 2024-05-11/2024-05-17 — delta `0.000%` | baseline `6.742%` -> veto `6.742%` | vetoed: none
- 2024-06-01/2024-06-07 — delta `0.000%` | baseline `6.504%` -> veto `6.504%` | vetoed: none
- 2024-06-08/2024-06-14 — delta `0.000%` | baseline `10.307%` -> veto `10.307%` | vetoed: none
- 2024-06-15/2024-06-21 — delta `0.000%` | baseline `-5.309%` -> veto `-5.309%` | vetoed: none

## 악화된 주차 TOP 10
- 2023-06-24/2023-06-30 — delta `-13.907%` | baseline `9.148%` -> veto `-4.759%` | kept: 파라택시스코리아, 강원랜드
- 2024-06-22/2024-06-28 — delta `-0.945%` | baseline `-0.456%` -> veto `-1.401%` | kept: SK증권, 부방
- 2025-10-18/2025-10-24 — delta `-0.914%` | baseline `1.854%` -> veto `0.941%` | kept: 원텍, 안랩
- 2024-03-16/2024-03-22 — delta `-0.635%` | baseline `-0.142%` -> veto `-0.777%` | kept: KT&G, 서부T&D
- 2023-05-13/2023-05-19 — delta `-0.379%` | baseline `-4.273%` -> veto `-4.651%` | kept: 아크솔루션스, 위메이드맥스
- 2024-03-23/2024-03-29 — delta `0.000%` | baseline `0.091%` -> veto `0.091%` | kept: 코스나인, 이녹스, 하나마이크론
- 2024-04-06/2024-04-12 — delta `0.000%` | baseline `0.038%` -> veto `0.038%` | kept: 타이거일렉, 안랩, 피씨디렉트
- 2024-04-13/2024-04-19 — delta `0.000%` | baseline `-1.927%` -> veto `-1.927%` | kept: DB손해보험, 현대무벡스, 카카오뱅크
- 2024-05-11/2024-05-17 — delta `0.000%` | baseline `6.742%` -> veto `6.742%` | kept: 현대퓨처넷, 조광피혁, 메타바이오메드
- 2024-06-01/2024-06-07 — delta `0.000%` | baseline `6.504%` -> veto `6.504%` | kept: 아이센스, 쿠쿠홈시스, KT&G

## Baseline 최악 주차 TOP 10
- 2024-01-27/2024-02-02 — baseline `-6.935%` | names: 미디어젠, 디와이에이, 고영
- 2023-09-02/2023-09-08 — baseline `-6.208%` | names: 비투엔, HD현대일렉트릭, 유진투자증권
- 2023-07-15/2023-07-21 — baseline `-5.834%` | names: 엠에스씨, 디젠스, 한양디지텍
- 2022-12-17/2022-12-23 — baseline `-5.677%` | names: 플래스크, LG유플러스, 유니드
- 2023-03-04/2023-03-10 — baseline `-5.408%` | names: 에스원, 링크제니시스, 부산주공
- 2024-06-15/2024-06-21 — baseline `-5.309%` | names: 마음AI, 에치에프알, 블루콤
- 2023-05-13/2023-05-19 — baseline `-4.273%` | names: 디와이피엔에프, 아크솔루션스, 위메이드맥스
- 2024-03-30/2024-04-05 — baseline `-3.172%` | names: 태광산업, 유비쿼스, KCC글라스
- 2025-05-24/2025-05-30 — baseline `-3.155%` | names: 알루코, 마니커, 일동홀딩스
- 2023-07-01/2023-07-07 — baseline `-2.955%` | names: KC코트렐, 에스앤에스텍, 위메이드맥스

## Fast veto 후 최악 주차 TOP 10
- 2024-01-27/2024-02-02 — veto `-6.935%` | kept: 미디어젠, 디와이에이, 고영 | status: READY
- 2023-07-15/2023-07-21 — veto `-5.834%` | kept: 엠에스씨, 디젠스, 한양디지텍 | status: READY
- 2022-12-17/2022-12-23 — veto `-5.677%` | kept: 플래스크, LG유플러스, 유니드 | status: READY
- 2023-03-04/2023-03-10 — veto `-5.408%` | kept: 에스원, 링크제니시스, 부산주공 | status: READY
- 2024-06-15/2024-06-21 — veto `-5.309%` | kept: 마음AI, 에치에프알, 블루콤 | status: READY
- 2023-06-24/2023-06-30 — veto `-4.759%` | kept: 파라택시스코리아, 강원랜드 | status: READY_WITH_VETO
- 2023-05-13/2023-05-19 — veto `-4.651%` | kept: 아크솔루션스, 위메이드맥스 | status: READY_WITH_VETO
- 2024-03-30/2024-04-05 — veto `-3.172%` | kept: 태광산업, 유비쿼스, KCC글라스 | status: READY
- 2023-07-01/2023-07-07 — veto `-2.955%` | kept: KC코트렐, 에스앤에스텍, 위메이드맥스 | status: READY
- 2025-05-24/2025-05-30 — veto `-2.405%` | kept: 알루코, 일동홀딩스 | status: READY_WITH_VETO

## 산출물
- weekly comparison csv: `/Users/01chungee10/Github/TOSS/reports/backtests/contextual_monfri_fast_veto_weekly_comparison_seed20260607.csv`
- analysis md: `/Users/01chungee10/Github/TOSS/reports/backtests/contextual_monfri_fast_veto_analysis_seed20260607.md`
