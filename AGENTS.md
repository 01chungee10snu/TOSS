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
- live order submission 금지.
- 데이터/리스크/정성 게이트 중 하나라도 불명확하면 fail-closed.
- 상태 변화와 evidence는 `reports/harness/`에 남긴다.
