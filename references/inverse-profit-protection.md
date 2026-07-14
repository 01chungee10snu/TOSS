# 인버스 ETF 이익 보호 청산 계약

- 적용일: 2026-07-14
- 범위: `114800`, `251340`, `252670` 인버스 헤지 포지션
- 구현: `src/toss_alpha/execution/position_exit.py`, `src/toss_alpha/execution/live_submit.py`

## 기본 상태 정책

| 조건 | 동작 | 기본값 |
|---|---|---:|
| 진입 후 하락 | 전량 손절 | -2.5% |
| 고점 +1.5% 도달 | 이익 잠금 활성화 | +1.5% |
| 보호 하한 | 평균단가 + 비용 버퍼 | +0.2% |
| 수익 +2.5% | 1차 부분익절 | 최초 수량 33% |
| 수익 +4.0% | 2차 부분익절 | 최초 수량 33% |
| 잔여 물량 | 고점 추적 | -1.5% |
| 시장 위험 해소 | 전량청산 | 기존 신선한 장중 판정 필요 |

보호가격은 다음처럼 계산한다.

```text
max(평균단가 × 1.002, 진입 후 고점 × 0.985)
```

고점이 +1.5%에 도달한 뒤 현재가가 보호가격 이하가 되면 잔량 전부를 SELL 대상으로 만든다. 호가 공백·급락·미체결 때문에 실제 체결가격이 보호가격을 보장하지는 않는다.

## 상태·중복주문 안전성

- `live_position_tracker.json`에 lifecycle ID, 최초 수량, 평균단가, 최초 관찰일, 고점을 원자적으로 저장한다.
- 프로세스 재시작 후 기존 상태가 있으면 KIS 공식 현재가 응답의 당일 고가 `stck_hgpr`로 중단 구간 고점을 보완한다. 새 포지션에는 진입 전 당일 고가를 적용하지 않는다.
- 부분익절 완료는 주문 생성/제출이 아니라 다음 KIS 잔고의 실제 수량 감소로만 확인한다.
- 부분익절 키는 `inverse_profit_1/2 + lifecycle_id`로 분리하되 가격·수량은 중복키에 넣지 않는다.
- 같은 종목의 활성 SELL이 있으면 다른 단계 SELL을 차단해 과매도를 막는다.
- 미체결 SELL은 30초 임계값 이후 다음 1분 watchdog tick에서 취소를 요청한다. 브로커 종결 확인 전에는 재주문하지 않고, 확인 후 잔량만 최신 bid로 재호가한다.

## 환경변수

- `TOSS_INVERSE_STOP_LOSS_PCT=0.025`
- `TOSS_INVERSE_PROFIT_LOCK_ACTIVATION_PCT=0.015`
- `TOSS_INVERSE_PROFIT_FLOOR_PCT=0.002`
- `TOSS_INVERSE_PARTIAL_1_PCT=0.025`
- `TOSS_INVERSE_PARTIAL_2_PCT=0.04`
- `TOSS_INVERSE_PARTIAL_FRACTION=0.33`
- `TOSS_INVERSE_TRAILING_STOP_PCT=0.015`
- `TOSS_CANCEL_STALE_UNFILLED_ENABLED=true`
- `TOSS_UNFILLED_CANCEL_AFTER_MINUTES=0.5`

## 검증

- 표적 회귀: 86 passed
- 전체 회귀: 309 passed
- 오늘 경로 재생: 평균 1,120.4403원, 고점 +4%에서 116/352주 1차 익절; 이후 보호가격 1,147.779원에서 잔여 236주 전량청산 신호
- `git diff --check`, watchdog shell syntax 검증 통과
