# TOSS ttak self-interview

작성일: 2026-06-17

## Q1. 지금 무엇을 만들어야 하는가?

A. TOSS 레포를 **계속 돌릴 수 있는 자문자답형 research harness**로 만든다. 핵심은 한 번의 답변이 아니라 반복 실행 가능한 loop, 상태 파일, evidence 산출물이다.

## Q2. 이번 loop의 1차 목표는?

A.
- 정량 입력 상태를 자동 점검
- contextual policy와 candidate draft를 재생성/확인
- 정성 데이터 파이프의 부재를 명시적 gate로 드러냄
- live readiness는 읽기 전용으로 계속 확인

## Q3. 비목표는?

A.
- 실주문 제출
- 계좌 비밀번호/토큰 취급
- 정성 데이터 미구현 상태에서 억지 자동매매 실행
- 백테스트 결과를 수익 보장처럼 해석

## Q4. 왜 quant/qual를 분리하는가?

A.
- quant는 재현성과 검증성이 높아 loop의 엔진이 된다.
- qual은 공시/뉴스/event taxonomy가 안정화되기 전까지 점수 엔진이 아니라 veto/gate가 맞다.
- 혼합을 서두르면 과최적화와 사후합리화가 커진다.

## Q5. 이번 버전의 결정은?

A.
1. quant loop는 실제 스크립트로 돌린다.
2. qual loop는 `connector 존재 여부 + API key 여부`를 게이트로 기록한다.
3. live는 `toss_alpha.cli live-readiness`만 호출한다.
4. 상태 변화가 없으면 cron은 침묵한다.

## Q6. 완료 조건은?

A.
- 하네스 문서가 생김
- 실행 loop 스크립트가 생김
- 실제 1회 실행 결과가 `reports/harness/`에 남음
- 지속 실행 경로(cron/wrapper)가 연결됨
