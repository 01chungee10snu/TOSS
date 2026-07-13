# Strategic Live Decision Harness

작성일: 2026-07-08

## 목적

TOSS 실거래 루프를 단일 매수 신호가 아니라 다음 순서의 전략적 의사결정 하네스로 고정한다.

```text
현재성 이슈 수집
→ 시장 전체 리스크 분류
→ 일반 BUY / 인버스 BUY / 무거래 분기
→ 장초 반등 감지 또는 인버스 진입
→ 통합 live_submit 게이트
→ 체결 후 손절익절 watchdog
→ 사후 리포트와 evidence 저장
```

## 핵심 불변조건

1. **현재성 이슈가 먼저다.** 매일 08:50 KST `current_issue_risk_report.py`가 `reports/harness/current_issues/current_issue_risk_report_YYYYMMDD.json`을 만든다. 이 리포트는 특정 속보 하나가 아니라 지정학, 유가/환율/금리, 미국 선물/뉴욕증시, 국내 지수/외국인 수급, 반도체/성장주 분위기를 함께 본다.
2. **현재성 이슈 high/critical이면 일반 주식 신규 BUY는 차단한다.** 이 차단은 `src/toss_alpha/execution/live_submit.py::current_issue_buy_violation`에서 실행되므로 모든 live-submit 경로에 공통 적용된다.
3. **SELL은 current issue로 막지 않는다.** 손절/익절/위험축소 매도는 계속 가능해야 한다.
4. **인버스 ETF BUY는 current issue high/critical에서 예외적으로 허용한다.** 기본 allowlist는 `252670,251340`이다.
5. **고정 개장가 매수는 금지한다.** 일반 반등주는 `rebound_open_detector_20260708.py`가 장초 저점 대비 반등 조건을 확인한 뒤에만 주문 후보를 만든다.
6. **시장가 주문은 금지한다.** BUY/SELL 모두 제한가만 허용한다.
7. **실주문은 반드시 `run_live_submit_phase`를 통과한다.** 개별 스크립트가 broker API를 직접 호출하지 않는다.
8. **계좌 상품코드는 KIS `01`을 강제한다.** `21`은 위탁계좌 조회 실패 이력이 있어 사용 금지다.
9. **중복주문 ledger를 통과해야 한다.** 같은 as_of/strategy/symbol/side 재제출은 ledger가 차단한다.
10. **체결 후 손절익절은 별도 watchdog이 담당한다.** 신규 매수 로직과 청산 로직은 분리하되 같은 live-submit 게이트를 쓴다.

## 전략 분기

| current issue severity | 일반 반등주 BUY | 인버스 BUY | SELL |
|---|---|---|---|
| low | 허용 | 보통 비활성 | 허용 |
| medium | 축소/주의 | 조건부 | 허용 |
| high | 차단 | 활성 | 허용 |
| critical | 차단 | 활성 | 허용 |

## 장초 반등주 BUY 조건

`rebound_open_detector_20260708.py` 기준:

- 09:03~09:25 KST
- current issue gate가 일반 BUY 허용
- 장초 저점 대비 +1% 이상 반등
- 전일 종가 대비 +3% 초과 갭상승 아님
- 전일 종가 대비 -8.5%보다 더 깊은 급락 아님
- 현재가가 제한가 이하
- 계좌/현재가 read-only 조회 성공

## 인버스 BUY 조건

`risk_off_inverse_entry_20260708.py` 기준:

- current issue severity가 high 또는 critical
- 한국 정규장 시간
- 계좌/현재가 read-only 조회 성공
- 현금 충분
- 제한가 BUY만 사용
- 주문당 상한 150,000원
- `run_live_submit_phase`의 duplicate/risk/time/liquidity/current issue allowlist 게이트 통과

## 손절익절 조건

`rebound_exit_watchdog_20260708.py` 기준:

- 체결가 대비 -3%: 전량 제한가 SELL
- 첫 5분 저점 이탈: 전량 제한가 SELL
- +5%: 절반 제한가 SELL
- +8%: 잔량 제한가 SELL
- +5% 이후 고점 대비 -2%: 잔량 제한가 SELL
- 10:30까지 수익 전환 실패: 전량 제한가 SELL
- 15:10 이후 잔여 보유: 전량 제한가 SELL

## 발견한 논리 오류와 보완

### 1. 현재성 이슈 분류의 query contamination 및 단일 속보 편향

문제: `current_issue_risk_report.py`가 검색어와 뉴스 제목을 함께 분류하면, 검색어의 “이란 공습” 때문에 무관한 기사도 critical로 분류될 수 있다. 또한 속보 하나에만 집중하면 실제 시장 분위기, 예컨대 미국 선물 반등·환율 안정·유가 진정 같은 완화 신호를 놓친다.

보완: 분류는 기사 제목만 대상으로 하고, 검색어는 source metadata로만 유지한다. 검색 쿼리도 지정학 단일 테마가 아니라 유가/환율/금리, 미국 선물/뉴욕증시, 국내 지수/외국인 수급, 반도체/성장주까지 포괄한다. 리포트에는 `category_counts`를 남겨 어떤 분위기 축이 위험도를 만든 것인지 확인한다.

### 2. 오래된 기사 재활성화

문제: Google News RSS가 오래된 관련 기사를 섞으면 오늘 리스크처럼 오인할 수 있다.

보완: pubDate 기준 36시간 lookback 필터를 적용한다. pubDate 파싱 실패 기사는 보수적으로 포함하되, 리포트에 considered/stale count를 남긴다.

### 3. current issue가 인버스까지 막는 문제

문제: high/critical일 때 모든 BUY를 막으면 방어용 인버스 진입도 불가능하다.

보완: current issue BUY allowlist를 도입해 `252670`, `251340` 같은 인버스 ETF는 허용한다. 일반 주식 BUY와 SELL은 계속 분리한다.

### 4. 고정 개장 매수의 전략 오류

문제: 지정학 속보가 있는 날 09:00 고정 매수는 하락 초입 체결 위험이 크다.

보완: 고정 매수 크론은 중지하고, 장초 저점 형성 후 반등 확인형 detector로 대체한다.

### 5. 실주문 경로 분산 위험

문제: 각 전략 스크립트가 직접 broker API를 호출하면 ledger/time/risk/current issue 게이트가 우회될 수 있다.

보완: 모든 BUY/SELL은 `run_live_submit_phase`만 사용한다.

## 루프 개선 과제

1. 현재 날짜 전용 스크립트(`*_20260708.py`)를 범용 날짜/설정 기반 스크립트로 일반화한다.
2. 인버스 보유분에도 별도 exit watchdog을 추가한다. 현재 rebound exit watchdog은 4개 반등주 중심이다.
3. current issue 분류를 RSS 키워드에서 시장 데이터(VIX, WTI, USDKRW, KOSPI 선물/ETF)와 결합한다.
4. 주문 체결 조회를 매수 직후 별도 reconcile loop로 강화한다.
5. 전략별 exposure budget을 하나의 portfolio risk allocator로 통합한다.
