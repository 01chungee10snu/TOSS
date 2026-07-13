# ttak live-submit self-interview — 2026-07-02

## Interviewer

Q. 지금 만드는 것은 무엇인가?

A. `ACTIONABLE_CANDIDATES`를 KIS 주문 payload로 바꾸고, live readiness와 리스크 게이트를 통과한 경우에만 제출할 수 있는 loop 기반 절대수익추구 매매봇 실행 레이어다.

Q. 비목표는 무엇인가?

A. 기본 cron에서 무조건 실주문을 넣는 것, 정성 데이터 게이트가 막힌 상태에서 자동 제출하는 것, 시장가/신용/미수/레버리지 주문을 허용하는 것은 비목표다.

## Planner

1. 후보 생성·fast veto·qual gate·live readiness를 기존 loop에서 유지한다.
2. 새 live-submit phase를 추가한다.
3. 기본값은 `LIVE_SUBMIT_DISABLED` 또는 `DRY_RUN`이다.
4. 실제 제출은 broker/risk/submit 3중 opt-in과 확인 문구, qual gate 통과, 중복주문 ledger 통과가 모두 필요하다.

## Critic

- `LIVE_READY`는 주문 가능 조건이지 자동 주문 완료가 아니다.
- `QUAL_STATUS=BLOCKED_QUAL_DATA`는 자동 실주문 blocker로 유지한다.
- 같은 날짜·전략·종목·방향 중복 주문을 ledger로 막아야 한다.
- KIS는 지정가(`ORD_DVSN=00`)와 정수 수량만 허용해야 한다.

## Builder

- `toss_alpha.execution.live_submit` 모듈을 만든다.
- `scripts/run_ttak_autotrading_loop.py`에 submit phase와 리포트 섹션을 붙인다.
- 테스트로 disabled, dry-run, qual-blocked, duplicate-blocked를 검증한다.

## Verifier

- 실제 주문 제출 없이 pytest와 loop smoke를 실행한다.
- smoke 결과는 `reports/harness/latest_loop_report.{json,md}`와 ledger artifact로 확인한다.
