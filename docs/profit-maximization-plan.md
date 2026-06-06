# Toss API 기반 이익극대화 시스템 구현 계획

> 목표는 “수익 보장”이 아니라, 손실 통제 하에서 기대값이 있는 기회를 체계적으로 찾고 검증하는 것이다.

## 원칙

- 기본값은 조회/리서치 전용. 실주문은 비활성화.
- 어떤 전략도 백테스트와 페이퍼트레이딩 전에는 실거래 금지.
- 단일 종목/일 손실/주문금액 한도를 코드로 강제.
- 주문 실행은 별도 승인 플로우가 있을 때만 추가.

## 시스템 구조

1. 데이터 수집
   - Toss: 현재가, 호가, 체결, 캔들, 계좌/보유종목
   - OpenDart: 공시, 재무제표, 지분/주요사항보고
   - 추후: KRX/뉴스/TradingView 보조 지표

2. 후보 발굴
   - 가격 모멘텀/변동성 필터
   - 공시 이벤트 필터: 실적, 배당, 자사주, 유상증자, 최대주주 변동 등
   - 유동성/거래대금 필터

3. 검증
   - 룩어헤드 방지 백테스트
   - 거래비용/슬리피지 반영
   - 기간별 성과 분해: 상승장/하락장/횡보장
   - 최대낙폭, 승률, 손익비, 샤프, 거래 빈도 확인

4. 실행
   - 1단계: 알림만
   - 2단계: 페이퍼트레이딩
   - 3단계: 수동 승인 주문
   - 4단계: 아주 작은 한도 내 조건부 자동 주문

## 초기 전략 후보

- 공시 이벤트 반응형
  - DART 주요사항보고/실적/배당/자사주 이벤트 발생 후 가격 반응 분석.
  - 장점: 기존 OpenDart 레포와 잘 맞음.

- 변동성 조정 모멘텀
  - 단순 모멘텀 점수에서 최근 변동성 패널티를 차감.
  - 장점: 구현이 단순하고 테스트가 쉬움.

- 계좌 리밸런싱/손실 제한
  - 기존 보유종목을 Toss 계좌 API로 읽고 과다집중/손실확대 감지.
  - 장점: 신규 매매보다 위험이 낮고 즉시 실용적.

## 이전 챗봇 레포 반영 방향

- `Vibe-Trading`
  - 현재 계획에 가장 직접적으로 반영한다.
  - 참고 대상: research-goal runtime, alpha/backtest 명령 구조, MCP server/tool 설계, connector read-only 점검 방식.
  - 금지: Vibe-Trading의 broker/live connector를 바로 실거래에 쓰지 않는다. Toss adapter와 자체 risk gate를 우선한다.

- `AutoHedge`
  - 참고 대상: 다중 에이전트 파이프라인 구조.
  - Toss 하네스 적용형:
    1. thesis agent: 후보/가설 생성
    2. quant check: 가격·거래대금·변동성 검증
    3. event check: OpenDart 공시/재무 이벤트 검증
    4. risk gate: 주문금액/포지션/일손실 한도 검증
    5. execution draft: 실주문이 아니라 수동 승인용 주문 초안 생성
  - 금지: wallet private key, autonomous execution, live order loop.

- `NotebookLM skill`
  - Toss OpenAPI docs, OpenDart docs, AutoHedge/Vibe-Trading/Fincept README를 업로드해 문서 기반 QA에 사용한다.
  - 목적: API 파라미터·제약·rate limit·주문 위험 문구를 출처 기반으로 재확인.

- `FinceptTerminal`
  - 당장 구현 대상은 아니다.
  - 나중에 대시보드/워크스테이션 UX, 포트폴리오/리스크 시각화, node-editor workflow를 참고한다.

- `camofox-browser`
  - 허가된 브라우저 QA와 문서 탐색 보조용.
  - 증권 웹사이트 우회/스크래핑을 핵심 데이터 파이프라인으로 삼지 않는다.

## 바로 다음 구현 태스크

1. Toss 키 발급 후 `.env` 구성.
2. `tossinvest_client.py token/prices/accounts` 실호출 검증.
3. 캔들 응답 스키마에 맞춰 `data/market/*.parquet` 캐시 구현.
4. OpenDart API 키를 `.env`에 추가하고 공시 조회 adapter 검증.
5. Vibe-Trading을 참고해 `research goal` 형태의 CLI 명령을 추가: “종목/기간/전략/리스크 기준 → 검토 리포트”.
6. 첫 백테스트: 삼성전자/SK하이닉스 대상 모멘텀+변동성 필터.
7. AutoHedge를 참고해 `thesis → quant → event → risk → execution draft` 보고서 포맷을 추가.
8. Telegram 알림: “매수/매도”가 아니라 “검토 후보 + 근거 + 리스크 + 수동 승인 필요 여부” 형식.
