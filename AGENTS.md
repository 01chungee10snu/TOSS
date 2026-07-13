# AGENTS.md

이 레포에서 **ttak 기반 자문자답형 자동매매 연구 하네스**를 다룰 때는 상세 운영 규칙을 `.openclaw/harness/`에서 먼저 읽는다.

- orchestrator: `.openclaw/harness/orchestrator/SKILL.md`
- self-QA / gate spec: `.openclaw/harness/references/ttak-self-interview.md`
- 실행 loop: `scripts/run_ttak_autotrading_loop.py`

## Trigger

다음 요청이 오면 이 하네스를 따른다.

- "ttak 스킬 기반 자문자답"
- "harness 만들어"
- "loop 계속 돌려"
- "정량/정성 자동매매 루프"

## Guardrails

- 기본 상태는 research/paper/manual-draft only.
- **[2026-07-02 활성화]** live order submission 허용. `toss-ttak-loop.sh` wrapper에서
  `TOSS_LIVE_SUBMIT_ENABLED=true`, `TOSS_LIVE_SUBMIT_DRY_RUN=false`,
  `TOSS_LIVE_SUBMIT_CONFIRMATION` + KIS live readiness env를 export한다.
  주문당 hard cap은 deep loss-averse frontier 적용 후 기본 15만원, 일일 손실 1%, 종목당 비중 5% 안전망 유지.
  live loop에는 계좌 equity drawdown guard(기본 고점 대비 -6% 트리거, 8-step cooldown)가 붙어야 하며, 트리거 시 신규 BUY 차단과 보유 SELL 우선 생성을 수행한다.
  실주문은 한국 정규장 시간 gate를 통과해야 하며 09:00 KST 전 BUY submit은 fail-closed로 차단한다.
- 데이터/리스크/정성 게이트 중 하나라도 불명확하면 fail-closed.
- 상태 변화와 evidence는 `reports/harness/`에 남긴다.
