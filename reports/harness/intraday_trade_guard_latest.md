# 15분 보수적 재판단 — 2026-07-30 14:17 KST

## 결론

**시장 상태와 행동 모두 `RISK_OFF / NO_TRADE`로 유지합니다.** KODEX200은 전일 대비 **-1.83%**, KODEX인버스는 **+1.98%**이고 양쪽 모두 VWAP 기준 위험회피 방향이지만, 단기 반등 뒤 재하락 확인과 시장 폭·수급 및 계좌 ETP 자격이 없어 신규 인버스도 fail-closed합니다.

## 가격 증거

- KODEX200(069500): 87,790원, 전일 대비 **-1.83%**, 시가 대비 **-1.72%**, VWAP 대비 **-2.27%**
- KODEX인버스(114800): 1,340원, 전일 대비 **+1.98%**, 시가 대비 **+1.59%**, VWAP 대비 **+2.09%**
- 시세 시각: 14:16:47~48 KST, KIS 읽기 전용 시세, 5분 이내 신선
- 직전 14:01 가드 대비: KODEX200 **+0.13%**, 인버스 **-0.15%**로 짧은 반등 흐름입니다.
- 두 프록시와 누적 VWAP은 `RISK_OFF`에 일치하지만, 09:15 이후 반등 실패·재하락 및 시장 폭·시장 전체 수급 근거가 없습니다.

## 계좌·주문

- 총자산 398,401원 / 현금 211,060원 / 보유종목 없음
- 고점 414,823원 대비 낙폭 **-3.96%**로 -4% 축소선보다 0.04%p 위입니다.
- 일반주식 후보 005290·000270은 `WATCH`이며 최신 종목별 `BUY` 승인이 없습니다.
- current-issue는 `high / require_intraday_confirmation / score=1.5`입니다. 뉴스는 위험 차단 보조 근거로만 사용했습니다.
- 보유종목이 없어 SELL 대상도 없습니다.
- 주문 제출·취소·실거래 환경 변경은 수행하지 않았습니다.

## 변화

직전 판단과 비교해 시장 상태, 행동, 위험도, 보유 상태에 유의미한 변화가 없습니다.

## 근거 파일

- `reports/harness/latest_loop_report.json`
- `reports/harness/latest_position_exit_report.json`
- `reports/harness/intraday_realtime_guard_latest.json`
- `reports/harness/current_issues/current_issue_risk_report_20260730.json`
- `reports/harness/intraday_trade_guard_latest.json`
