# TOSS Ttak Recursive Harness Design

작성일: 2026-06-06

> 목적: Toss Open API 기반 거래 시스템을 “자동매매”가 아니라 **조회·리서치·백테스트·페이퍼트레이딩·수동 주문 초안** 하네스로 설계한다.  
> 원칙: 수익 보장이 아니라 **손실 통제 하에서 검증 가능한 기대값 후보를 찾는 연구 시스템**이다.

## 1. Ttak 적용 방식

사용자 요청: “하네스 기반으로 설계해보자, ttak스킬을 너에게 재귀적으로 활용. 자문자답하면서 만들어봐.”

이번 작업에서는 Ttak을 외부 인터뷰가 아니라 내부 recursive self-QA로 적용한다.

- Interviewer: 목표·비목표·성공 기준을 질문한다.
- Planner: 하네스 모듈과 실행 순서를 설계한다.
- Critic: 금융 리스크, 데이터 계약, 컴플라이언스 위험을 반박한다.
- Harness Builder: 입력/출력/검증/승인 게이트로 고정한다.
- QA Verifier: 구현 전에 차단해야 할 조건을 정의한다.

## 2. Clarity 판단

### Q. 지금 사용자에게 다시 물어야 하나?

A. 이번 요청은 “ttak을 너에게 재귀적으로 활용해 자문자답”하라는 명시가 있으므로, 기본값은 내가 내부 질문-답변을 수행해 초안 하네스를 만드는 것이다. 다만 실거래, 주문 API 활성화, 계좌/키 입력, 리스크 한도 변경은 사람 승인 게이트로 남긴다.

### Q. 목표는 무엇인가?

A. TOSS 프로젝트를 다음 구조로 확장할 수 있게 설계한다.

1. Toss API read-only 데이터 수집
2. OpenDart 이벤트 수집
3. Research goal 기반 후보 검토
4. 백테스트
5. 페이퍼트레이딩
6. 수동 주문 초안 생성
7. Telegram 검토 알림

### Q. 비목표는 무엇인가?

A. 다음은 이번 설계의 비목표다.

- 실주문 실행
- 자동매매 daemon
- Vibe-Trading/AutoHedge live connector 직접 사용
- 브라우저 우회 스크래핑 기반 거래 데이터 수집
- 수익 보장형 표현
- 투자 자문처럼 보이는 추천 UX

## 3. 하네스 상태 머신

기본 상태는 `RESEARCH_ONLY`다. 모든 단계는 fail-closed로 동작한다.

```text
RESEARCH_ONLY
  ↓ data/schema/risk 검증 통과
BACKTEST_ONLY
  ↓ 백테스트 품질·리스크 기준 통과
PAPER_ONLY
  ↓ 페이퍼 로그·성과·리스크 리뷰 통과
MANUAL_DRAFT_ONLY
  ↓ 사용자 별도 승인 전까지 여기서 정지
LIVE_BLOCKED
```

상태 정의:

- `RESEARCH_ONLY`
  - 기본값.
  - API 조회, 캐시, 신호 계산, 리포트 생성만 허용.
  - 주문 intent는 생성하지 않는다.

- `BACKTEST_ONLY`
  - 과거 데이터에서 전략 가설을 검증한다.
  - 수수료, 세금, 슬리피지, 휴장일, 룩어헤드 방지 기준이 필요하다.

- `PAPER_ONLY`
  - 실제 주문 없이 가상 현금·보유·체결·손익을 기록한다.
  - 실계좌 API 주문 endpoint 호출 금지.

- `MANUAL_DRAFT_ONLY`
  - 주문 실행이 아니라 사용자가 직접 검토할 주문 초안만 만든다.
  - “실주문 아님 / 수동 확인 필요 / 손실 가능” 문구를 강제한다.

- `LIVE_BLOCKED`
  - 기본 차단 상태.
  - 데이터 stale, 계좌 조회 실패, 포지션 조회 실패, risk violation, 시장상태 불명, manual confirmation 없음, kill switch on이면 즉시 이 상태다.

## 4. 시스템 아키텍처

```text
Toss OpenAPI read-only
  → market/account cache
  → quant signal
  → research goal runner
  → backtest engine
  → risk gate
  → paper portfolio
  → manual order draft
  → report / Telegram alert
```

```text
OpenDart
  → disclosure event cache
  → event taxonomy
  → event check
  → evidence pack
  → report / NotebookLM QA
```

## 5. 모듈 설계

### 5.1 Connectors

경로:

- `src/toss_alpha/connectors/toss_readonly.py`
- `src/toss_alpha/connectors/dart_events.py`

역할:

- Toss token, stocks, prices, candles, accounts, holdings 조회
- OpenDart 공시/재무 이벤트 조회
- rate limit, request id, source timestamp 기록
- 주문 endpoint는 만들지 않는다.

### 5.2 Data contracts

경로:

- `src/toss_alpha/data/schema.py`
- `src/toss_alpha/data/market_cache.py`
- `src/toss_alpha/data/event_cache.py`

필수 모델:

- `Instrument`
- `Quote`
- `Candle`
- `AccountSnapshot`
- `PositionSnapshot`
- `DisclosureEvent`
- `ResearchGoal`
- `SignalResult`
- `OrderIntent`
- `RiskDecision`
- `BacktestResult`
- `PaperTrade`

핵심 원칙:

- 모든 데이터는 `source`, `as_of`, `timezone`, `snapshot_id`를 가진다.
- Toss/OpenDart 원본 응답을 그대로 전략에 쓰지 않고 내부 schema로 변환한다.
- 공시 이벤트는 `available_at` 기준으로만 백테스트에 사용한다.

### 5.3 Research goal runtime

Vibe-Trading에서 차용할 부분이다.

경로:

- `goals/example_momentum.yaml`
- `goals/example_disclosure_event.yaml`
- `src/toss_alpha/research/goal.py`
- `src/toss_alpha/research/runner.py`

예시 goal:

```yaml
goal_id: kr_momentum_disclosure_001
mode: backtest
universe:
  symbols: ["005930", "000660"]
period:
  start: "2022-01-01"
  end: "2025-12-31"
strategy:
  name: momentum_volatility_event
  params:
    short_window: 20
    long_window: 60
    volatility_window: 20
risk_profile: conservative
outputs:
  report: true
  order_draft: false
```

### 5.4 Recursive agent pipeline

AutoHedge에서 차용하되 실거래 실행은 제거한다.

경로:

- `src/toss_alpha/agents/thesis_agent.py`
- `src/toss_alpha/agents/quant_check.py`
- `src/toss_alpha/agents/event_check.py`
- `src/toss_alpha/agents/risk_reviewer.py`
- `src/toss_alpha/agents/execution_draft.py`

파이프라인:

```text
thesis_agent
  → quant_check
  → event_check
  → risk_reviewer
  → execution_draft
```

각 단계의 실패 정책:

- 어떤 단계든 `failed`, `unknown`, `stale`, `missing_data`이면 최종 상태는 `BLOCK`.
- 다수결로 통과시키지 않는다.
- risk gate는 항상 veto 권한을 가진다.
- LLM/agent는 주문 실행 권한이 없다.

### 5.5 Backtest

경로:

- `src/toss_alpha/backtest/engine.py`
- `src/toss_alpha/backtest/metrics.py`
- `src/toss_alpha/backtest/walk_forward.py`

필수 포함:

- 거래비용
- 세금
- 슬리피지
- 시장 휴장일
- 거래정지/상하한가 가능성
- 룩어헤드 방지
- out-of-sample / walk-forward 검증

성과 지표:

- 총수익률
- MDD
- 승률
- 손익비
- Sharpe/Sortino
- turnover
- 거래 수
- 국면별 성과

### 5.6 Paper trading

경로:

- `src/toss_alpha/paper/portfolio.py`
- `src/toss_alpha/paper/broker.py`
- `src/toss_alpha/paper/journal.py`

역할:

- 가상 현금
- 가상 포지션
- 체결 로그
- mark-to-market
- risk decision 로그
- 사후 리뷰

### 5.7 Reports and alerts

경로:

- `src/toss_alpha/reports/evidence_pack.py`
- `src/toss_alpha/reports/markdown_report.py`
- `reports/research/`
- `reports/backtest/`
- `reports/paper/`
- `reports/drafts/`

Telegram 문구 원칙:

- “매수하세요”, “매도하세요” 금지
- “검토 후보”, “관찰 필요”, “가설”, “백테스트 기준”, “수동 확인 필요” 사용
- 항상 “투자 조언 아님 / 손실 가능 / 실주문 아님” 표시

## 6. Ttak recursive self-QA

### Q1. 이 시스템이 실제로 돈을 벌 수 있게 해주는가?

A. 아니다. 이 시스템은 수익을 보장하지 않는다. 목표는 검증 가능한 후보를 만들고, 위험한 실행을 차단하며, 판단 근거를 구조화하는 것이다.

### Q2. 가장 먼저 구현할 것은 전략인가, 데이터 계약인가?

A. 데이터 계약이다. Toss/OpenDart 응답이 어떤 schema로 저장되는지 고정하지 않으면 백테스트와 리스크 판단이 모두 흔들린다.

### Q3. 왜 Vibe-Trading을 그대로 붙이지 않는가?

A. Toss API와 한국/미국 주식 계좌 맥락이 다르고, live connector를 직접 붙이면 통제권이 흐려진다. Vibe-Trading에서는 research goal, CLI, backtest/MCP 구조만 차용한다.

### Q4. 왜 AutoHedge를 그대로 쓰지 않는가?

A. AutoHedge는 Solana/Jupiter autonomous trading 성격이 강하다. TOSS에서는 execution agent를 실주문이 아니라 manual draft generator로 바꿔야 한다.

### Q5. NotebookLM은 어디에 쓰는가?

A. Toss/OpenDart 문서, 전략 설명, 백테스트 리포트, risk policy를 evidence pack으로 묶고 출처 기반 QA에 사용한다. 코드 실행 계층이 아니다.

### Q6. 백테스트를 통과하면 자동으로 페이퍼로 넘어가나?

A. 아니다. 승격은 자동이 아니다. 데이터 품질, 표본 수, 거래비용 반영, out-of-sample, MDD, 리스크 위반 여부를 확인해야 한다.

### Q7. 페이퍼를 통과하면 실거래로 넘어가나?

A. 아니다. 이번 하네스의 종착점은 manual draft다. 실거래는 별도 RFC, 별도 승인, 별도 브랜치, 별도 테스트 없이는 설계하지 않는다.

### Q8. SELL은 어떻게 다룰 것인가?

A. 매도 가능 수량, 보유 수량, 미체결 주문, 거래정지 여부가 확인되기 전까지 차단한다. 공매도는 기본 금지다.

### Q9. LLM이 좋은 후보를 말하면 믿어도 되나?

A. 아니다. LLM 출력은 thesis일 뿐이다. quant/event/risk/data-quality gate를 통과하지 못하면 보고서에서도 `BLOCK`으로 표시한다.

### Q10. Telegram 알림은 어떻게 써야 하나?

A. 매매 지시가 아니라 검토 리포트로 보낸다. 예: “검토 후보: 삼성전자 / 근거: 20일 모멘텀 양수 / 리스크: 데이터 1개 누락 / 상태: 실주문 아님, 수동 확인 필요”.

## 7. 승인 게이트

### 구현 전 승인 없이 가능한 것

- read-only connector 리팩터링
- schema 정의
- cache 구현
- backtest/paper engine 구현
- report 생성
- risk gate 테스트 확장
- NotebookLM용 evidence pack 생성

### 사용자 확인이 필요한 것

- 실제 Toss API key `.env` 입력 여부
- 계좌 조회 실행
- holdings 조회 실행
- Telegram 알림 cron 등록
- 리스크 한도 변경
- live order 관련 어떤 코드든 추가

### 명시적으로 금지된 것

- 사용자 승인 없는 실주문
- 자동 주문 loop
- leverage/options/shorts
- 계좌/토큰/잔고 전체를 Telegram에 노출
- 증권 웹사이트 anti-bot 우회 자동화

## 8. 성공 기준

1. Toss/OpenDart 데이터가 내부 schema로 저장된다.
2. goal YAML 하나로 같은 리서치를 반복 실행할 수 있다.
3. 백테스트 결과가 수수료/슬리피지를 반영해 Markdown으로 나온다.
4. risk gate 위반 시 draft/order/report가 `BLOCK`을 표시한다.
5. paper portfolio가 실제 주문 없이 체결/손익 로그를 남긴다.
6. Telegram용 문구가 투자 권유가 아니라 검토 후보 형식이다.
7. 테스트가 live order 부재와 fail-closed 동작을 증명한다.

## 9. 다음 구현 순서

1. `src/toss_alpha/data/schema.py` 추가
2. `src/toss_alpha/connectors/toss_readonly.py` 추가
3. 기존 `tossinvest_client.py`를 connector 기반 CLI로 얇게 유지
4. `goals/example_momentum.yaml` 추가
5. `src/toss_alpha/research/goal.py` 추가
6. `src/toss_alpha/backtest/engine.py` 최소 구현
7. `src/toss_alpha/reports/markdown_report.py` 추가
8. `src/toss_alpha/agents/execution_draft.py` 추가하되 실주문 함수 없음 보장
9. risk gate 테스트 확장
10. 전체 smoke test와 문서 업데이트
