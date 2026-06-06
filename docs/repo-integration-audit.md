# 기존 금융 관련 레포 접목 감사

## 발견한 후보

- `C:/Github/OpenDart`
  - 성격: 금융감독원 Open DART API 래퍼. 공시 목록, 사업보고서, 재무제표, 지분공시, 주요사항보고서 조회 가능.
  - 접목 가치: 토스증권 가격/주문 API와 결합해 **공시 이벤트 + 재무지표 + 가격 반응** 리서치 파이프라인 구성.
  - 적용 방향: `src/toss_alpha/dart_adapter.py`에서 optional adapter로 연결. `OPENDART_API_KEY`는 TOSS 프로젝트 `.env`에 별도 저장.

- `C:/Github/Bithumb`
  - 성격: 현재 유효 파일이 `nul`뿐이라 재사용 가능한 코드 없음.
  - 접목 가치: 낮음. 암호화폐까지 확장할 때 새로 구현/외부 공식 API 검토 필요.

- `C:/Github/PracticalStatisticsForDataScientists`, `C:/Github/R`
  - 성격: 통계/분석 학습 자료.
  - 접목 가치: 백테스트 통계, 가설검정, 리스크 지표 구현 시 참고 가능.

- `C:/Github/browser-harness` 내 TradingView scraping skill
  - 성격: 브라우저 스크래핑 노하우.
  - 접목 가치: 공식 API로 부족한 차트/지표 탐색 시 참고 가능하나, 거래 시스템의 핵심 데이터원으로는 부적합.

## 이전 챗봇 세션에서 붙여넣은 GitHub 후보

출처: Hermes `try` profile 세션 `20260602_063745_cf932af9`, 사용자 메시지 `207` 및 이후 생성된 skill 내용.

- `https://github.com/The-Swarm-Corporation/AutoHedge`
  - 성격: Solana/Jupiter 중심 autonomous trading agent. `Director Agent → Quant Agent → Risk Manager → Execution Agent` 구조.
  - 접목 가치: **아키텍처 참고용**. Toss 한국/미국 주식 API와 직접 호환되는 브로커 어댑터는 아니므로 바로 붙이지 않는다.
  - 적용 방향: 주문 실행 로직은 재사용하지 말고, `thesis → quant check → risk gate → execution draft` 식의 에이전트 단계 구조만 차용한다.

- `https://github.com/HKUDS/Vibe-Trading`
  - 성격: CLI/TUI, FastAPI web UI, MCP server, research-goal runtime, alpha/backtest, broker connector profiles, market-data fallback을 가진 trading-agent 프레임워크.
  - 접목 가치: **가장 높음**. Toss 하네스에 `research goal`, `alpha/backtest`, `MCP tool` 개념을 붙이는 데 참고 가능.
  - 적용 방향: 초기에는 `vibe-trading` 자체 live/broker connector를 쓰지 않고, read-only/backtest 패턴과 MCP 인터페이스 설계만 참고한다. Toss client는 독립 adapter로 유지한다.

- `https://github.com/Fincept-Corporation/FinceptTerminal`
  - 성격: C++20/Qt6 기반 금융 터미널. embedded Python analytics, portfolio/risk/equity research, node editor workflow.
  - 접목 가치: 중간. 백엔드 전략 엔진보다는 **대시보드/워크스테이션 참고용**.
  - 적용 방향: 나중에 결과 시각화, 포트폴리오/리스크 화면, 노드형 분석 흐름이 필요할 때 참고한다. 초기 Toss API 검증에는 제외.

- `https://github.com/jo-inc/camofox-browser`
  - 성격: Camoufox 기반 anti-detection browser server / REST OpenAPI / agent browser automation.
  - 접목 가치: 낮음~보조. 공식 API 문서 확인, 로그인/웹 QA, NotebookLM/문서 작업 보조에는 유용하나 거래 데이터 핵심원으로 쓰지 않는다.
  - 적용 방향: 증권/거래 사이트 스크래핑 회피 용도로 쓰지 않는다. 허가된 문서 탐색·브라우저 QA에만 사용한다.

- `https://github.com/PleasePrompto/notebooklm-skill`
  - 성격: NotebookLM 노트북을 브라우저 자동화로 질의하고 출처 기반 답변을 얻는 skill.
  - 접목 가치: 중간~높음. Toss OpenAPI docs, OpenDart docs, repo README를 NotebookLM에 넣어 **문서 기반 QA/요약** 워크플로우로 활용 가능.
  - 적용 방향: 코드 실행 계층이 아니라 리서치/문서 검증 계층으로 둔다.

## 결론

1차 접목은 **Toss Open API + OpenDart + 자체 리스크 게이트** 조합으로 간다.

이전 챗봇 세션 후보까지 반영하면 우선순위는 다음과 같다.

1. `Vibe-Trading`: research/backtest/MCP 설계 참고.
2. `AutoHedge`: multi-agent trading pipeline 구조 참고. live execution은 금지.
3. `NotebookLM skill`: Toss/OpenDart/레포 문서 QA 보조.
4. `FinceptTerminal`: 나중에 대시보드/워크스테이션 UX 참고.
5. `camofox-browser`: 허가된 브라우저 QA/문서 탐색 보조. 거래 데이터 핵심원으로는 제외.

실거래 자동화보다 먼저 `조회 → 후보발굴 → 백테스트 → 페이퍼트레이딩 → 수동승인 주문` 순서로 단계화한다.
