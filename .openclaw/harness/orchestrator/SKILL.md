---
name: toss-ttak-orchestrator
description: "TOSS 레포에서 ttak 자문자답 기반 quant/qual/live-readiness 루프를 운영하는 프로젝트 로컬 오케스트레이터."
---

# TOSS Ttak Orchestrator

## 목적

이 오케스트레이터는 TOSS 레포를 다음 4계층 loop로 돌린다.

1. **Quant lane**
   - 패널 준비 여부 확인
   - contextual policy 준비 여부 확인
   - candidate draft 생성
2. **Qual lane**
   - 공시/이벤트 데이터 연결성 확인
   - 정성 데이터가 없으면 `BLOCKED_QUAL_DATA`
3. **Execution gate**
   - `live-readiness` 결과 확인
   - 기본값은 live blocked
4. **Live-submit gate**
   - KIS 주문 payload를 dry-run으로 감사
   - 실제 제출은 broker/risk/submit 3중 opt-in, 정확한 확인 문구, qual gate 통과, 중복주문 ledger 통과가 모두 필요

## 운영 원칙

- 기본값은 fail-closed.
- 정량은 엔진, 정성은 veto/gate/override.
- 실주문은 기본 범위 밖이며, `live-submit`은 fail-closed guarded executor로만 다룬다.
- loop는 evidence-backed artifact를 `reports/harness/`에 남긴다.
- cron은 state change 또는 actionable candidate가 있을 때만 사용자에게 출력한다.

## ttak 내부 역할

- Interviewer: 목표/비목표/승인 게이트 점검
- Planner: 다음 실행 단위 선정
- Critic: 과최적화, 데이터 공백, 실거래 비약 차단
- Builder: 스크립트/상태파일/보고서로 고정
- Verifier: 실제 실행 결과 확인

## Loop 규약

실행 진입점:

```bash
cd /Users/01chungee10/Github/TOSS
.venv/bin/python scripts/run_ttak_autotrading_loop.py
```

옵션:

- `--build-missing-panel` : 패널 CSV가 없을 때 daily strategy sweep를 실행해 패널을 복구 시도
- `--rebuild-policy-if-missing` : 정책 JSON이 없을 때 optimizer 실행
- `--force-emit` : 상태 변화가 없어도 stdout 출력

## 성공/실패 의미

- `ACTIONABLE_CANDIDATES`: 수동 검토 가능한 후보 생성
- `NO_TRADE`: 정책상 거래 없음
- `BLOCKED_QUANT_DATA`: 정량 입력 부족
- `BLOCKED_QUAL_DATA`: 정성 입력 파이프 미구현/비활성
- `LIVE_BLOCKED`: readiness 상 실거래 차단 상태 유지
- `LIVE_SUBMIT_DRY_RUN_READY`: 주문 payload dry-run 감사 통과, 실제 제출 없음
- `LIVE_SUBMIT_DRY_RUN_BLOCKED`: 주문 payload는 만들었지만 qual/ledger/risk 등으로 제출 차단
- `LIVE_SUBMITTED`: 3중 opt-in과 확인 문구, ledger 통과 후 broker submit 완료
