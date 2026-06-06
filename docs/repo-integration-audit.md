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

## 결론

1차 접목은 **Toss Open API + OpenDart + 자체 리스크 게이트** 조합으로 간다.
실거래 자동화보다 먼저 `조회 → 후보발굴 → 백테스트 → 페이퍼트레이딩 → 수동승인 주문` 순서로 단계화한다.
