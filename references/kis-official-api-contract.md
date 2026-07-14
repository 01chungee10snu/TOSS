# KIS Open API 공식 계약 및 사용 원칙

- 확인일: 2026-07-14 KST
- 공식 저장소: [`koreainvestment/open-trading-api`](https://github.com/koreainvestment/open-trading-api)
- 확인 커밋: [`885dd4e2f5c37e4f7e23dd63c15555a9967bc7bc`](https://github.com/koreainvestment/open-trading-api/commit/885dd4e2f5c37e4f7e23dd63c15555a9967bc7bc)
- 적용 범위: TOSS 프로젝트에서 실제 사용하는 KIS 국내주식 현재가, 호가, 잔고, 현금주문, 일별체결조회, 정정취소

## 사용 원칙

1. KIS 개발자포털과 KIS 공식 GitHub 예제를 유일한 API 계약 근거로 사용한다.
2. endpoint, `tr_id`, 요청 키, 응답 컨테이너(`output`, `output1`, `output2`)를 서로 다른 API 사이에서 추정하거나 재사용하지 않는다.
3. 현재가와 최우선 호가는 서로 다른 API에서 읽는다. 현재가 응답에서 `bidp`/`askp`를 찾지 않는다.
4. 호가가 없으면 주문 가격을 임의 생성하지 않고 fail-closed한다.
5. 실전/모의 `tr_id`를 명시적으로 구분한다.
6. POST 주문 본문 키는 공식 예제대로 대문자를 사용하고 문자열 수량·가격을 전달한다.
7. KIS 응답은 HTTP 성공 외에 `rt_cd == "0"`도 확인한다.
8. 잔고·체결조회는 `tr_cont`와 `CTX_AREA_*` 연속조회 계약을 따른다.
9. 공식 저장소의 확인 커밋을 문서에 남기고, 변경 시 계약 테스트와 이 문서를 함께 갱신한다.

## 공식 API 계약표

| 용도 | endpoint | 실전 `tr_id` | 모의 `tr_id` | 핵심 요청 | 핵심 응답 |
|---|---|---:|---:|---|---|
| 주식현재가 시세 | `GET /uapi/domestic-stock/v1/quotations/inquire-price` | `FHKST01010100` | 동일 | `FID_COND_MRKT_DIV_CODE=J`, `FID_INPUT_ISCD` | `output.stck_prpr`, `output.acml_vol` |
| 호가/예상체결 | `GET /uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn` | `FHKST01010200` | 동일 | `FID_COND_MRKT_DIV_CODE=J`, `FID_INPUT_ISCD` | 호가 객체 `output1.askp1`, `output1.bidp1`; 예상체결 `output2` |
| 주식잔고조회 | `GET /uapi/domestic-stock/v1/trading/inquire-balance` | `TTTC8434R` | `VTTC8434R` | 계좌번호와 조회구분 9개, `CTX_AREA_FK100/NK100` | 보유종목 배열 `output1`, 계좌요약 `output2`, 연속키 |
| 현금주문 | `POST /uapi/domestic-stock/v1/trading/order-cash` | 매수 `TTTC0012U`, 매도 `TTTC0011U` | 매수 `VTTC0012U`, 매도 `VTTC0011U` | `CANO`, `ACNT_PRDT_CD`, `PDNO`, `ORD_DVSN`, `ORD_QTY`, `ORD_UNPR`, `EXCG_ID_DVSN_CD`; 선택 `SLL_TYPE`, `CNDT_PRIC` | `output` 주문 결과 |
| 일별 주문체결조회(3개월 이내) | `GET /uapi/domestic-stock/v1/trading/inquire-daily-ccld` | `TTTC0081R` | `VTTC0081R` | 날짜·매매·체결·조회 구분, `ODNO`, `EXCG_ID_DVSN_CD`, `CTX_AREA_FK100/NK100` | 주문 배열 `output1`, 요약 `output2`, 연속키 |
| 정정취소 | `POST /uapi/domestic-stock/v1/trading/order-rvsecncl` | `TTTC0013U` | `VTTC0013U` | 원주문 식별자, 정정취소구분, 수량·가격, `QTY_ALL_ORD_YN`, `EXCG_ID_DVSN_CD` | `output` 처리 결과 |

## 주식잔고조회 기본 요청값

프로젝트는 최초 조회 시 공식 필수 파라미터를 다음처럼 전송한다.

```json
{
  "AFHR_FLPR_YN": "N",
  "OFL_YN": "",
  "INQR_DVSN": "01",
  "UNPR_DVSN": "01",
  "FUND_STTL_ICLD_YN": "N",
  "FNCG_AMT_AUTO_RDPT_YN": "N",
  "PRCS_DVSN": "00",
  "CTX_AREA_FK100": "",
  "CTX_AREA_NK100": ""
}
```

응답 헤더 `tr_cont`가 `M` 또는 `F`이면 응답의 `ctx_area_fk100`, `ctx_area_nk100`을 다음 요청의 `CTX_AREA_FK100`, `CTX_AREA_NK100`에 넣고 요청 헤더 `tr_cont=N`으로 후속 페이지를 조회한다. 연속조회 표시는 있으나 키가 없거나 최대 페이지를 넘기면 조용히 누락시키지 않고 오류 처리한다.

## 현재가와 호가 결합 규칙

```text
inquire-price.output.stck_prpr  -> Quote.last
inquire-price.output.acml_vol   -> Quote.volume
inquire-asking-price-exp-ccn.output1.bidp1 -> Quote.bid
inquire-asking-price-exp-ccn.output1.askp1 -> Quote.ask
```

`inquire-price`에는 이 프로젝트가 필요로 하는 최우선 매수·매도호가 계약이 없다. `bidp`/`askp` 또는 임의 대체 필드를 현재가 응답에서 읽지 않는다. `bidp1`/`askp1`이 비어 있거나 0이면 adaptive 주문은 `adaptive_quote_orderbook_missing`으로 차단한다.

## 구현 매핑

| 계약 | 구현 |
|---|---|
| 현재가·호가·잔고 | `src/toss_alpha/connectors/kis_readonly.py` |
| 현금주문 설정·본문 | `src/toss_alpha/execution/live_ready.py` |
| 일별체결·정정취소 | `src/toss_alpha/execution/order_management.py` |
| 계약 회귀 | `tests/test_kis_readonly_connector.py`, `tests/test_live_execution_readiness.py`, `tests/test_live_submit.py`, `tests/test_order_management.py` |

## 공식 원문

- [주식현재가 시세 공식 예제](https://github.com/koreainvestment/open-trading-api/blob/885dd4e2f5c37e4f7e23dd63c15555a9967bc7bc/examples_llm/domestic_stock/inquire_price/inquire_price.py)
- [주식현재가 응답 필드 공식 검사 예제](https://github.com/koreainvestment/open-trading-api/blob/885dd4e2f5c37e4f7e23dd63c15555a9967bc7bc/examples_llm/domestic_stock/inquire_price/chk_inquire_price.py)
- [호가/예상체결 공식 예제](https://github.com/koreainvestment/open-trading-api/blob/885dd4e2f5c37e4f7e23dd63c15555a9967bc7bc/examples_llm/domestic_stock/inquire_asking_price_exp_ccn/inquire_asking_price_exp_ccn.py)
- [호가 응답 필드 공식 검사 예제](https://github.com/koreainvestment/open-trading-api/blob/885dd4e2f5c37e4f7e23dd63c15555a9967bc7bc/examples_llm/domestic_stock/inquire_asking_price_exp_ccn/chk_inquire_asking_price_exp_ccn.py)
- [주식잔고조회·연속조회 공식 예제](https://github.com/koreainvestment/open-trading-api/blob/885dd4e2f5c37e4f7e23dd63c15555a9967bc7bc/examples_llm/domestic_stock/inquire_balance/inquire_balance.py)
- [현금주문 공식 예제](https://github.com/koreainvestment/open-trading-api/blob/885dd4e2f5c37e4f7e23dd63c15555a9967bc7bc/examples_llm/domestic_stock/order_cash/order_cash.py)
- [일별 주문체결조회 공식 예제](https://github.com/koreainvestment/open-trading-api/blob/885dd4e2f5c37e4f7e23dd63c15555a9967bc7bc/examples_llm/domestic_stock/inquire_daily_ccld/inquire_daily_ccld.py)
- [정정취소 공식 예제](https://github.com/koreainvestment/open-trading-api/blob/885dd4e2f5c37e4f7e23dd63c15555a9967bc7bc/examples_llm/domestic_stock/order_rvsecncl/order_rvsecncl.py)
