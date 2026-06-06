# TOSS — Toss Securities Open API Research Harness

토스증권 Open API를 활용한 **조회/리서치/백테스트/리스크 통제 중심** 프로젝트입니다.
실거래 주문은 기본적으로 비활성화되어 있습니다.

## 위치

- Windows: `C:\Github\TOSS`
- WSL: `/mnt/c/Github/TOSS`

## 준비

```bash
cd /mnt/c/Github/TOSS
cp .env.example .env
# .env에 TOSSINVEST_CLIENT_ID / TOSSINVEST_CLIENT_SECRET 입력
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

선택: DART 공시 연동을 쓰려면 `.env`에 추가:

```env
OPENDART_API_KEY=...
```

## Toss API 테스트

```bash
python tossinvest_client.py token
python tossinvest_client.py stocks 005930
python tossinvest_client.py prices 005930
python tossinvest_client.py accounts
python tossinvest_client.py holdings
```

## 프로젝트 문서

- `docs/repo-integration-audit.md` — 기존 금융 관련 레포 접목 결과
- `docs/profit-maximization-plan.md` — 단계별 리서치/백테스트/리스크 관리 계획
- `docs/ttak-recursive-harness-design.md` — Ttak recursive self-QA 기반 하네스 설계
- `docs/plans/2026-06-06-ttak-harness-implementation-plan.md` — 구현 계획
- `config/risk_policy.yaml` — 실거래 전 필수 리스크 한도
- `config/watchlist.yaml` — 초기 관심종목

## 안전 원칙

- 수익 보장 없음. 모든 전략은 손실 가능.
- 이 프로젝트의 산출물은 투자 조언 아님.
- 기본값은 조회/알림/백테스트/수동 검토 초안.
- 실주문은 기본 차단이며, API 발급 후에도 이중 opt-in과 정확한 확인 문구 없이는 제출하지 않음.
- 바로가기성 `buy`, `sell`, `place-order`, `auto-trade` 명령은 제공하지 않음.

## Research harness CLI

```bash
PYTHONPATH=src python3 -m toss_alpha.cli --help
PYTHONPATH=src python3 -m toss_alpha.cli research run goals/example_momentum.yaml
PYTHONPATH=src python3 -m toss_alpha.cli backtest run goals/example_momentum.yaml
PYTHONPATH=src python3 -m toss_alpha.cli draft-order goals/example_momentum.yaml
PYTHONPATH=src python3 -m toss_alpha.cli live-readiness
```

현재 CLI는 안전한 스켈레톤입니다. `live-readiness`는 실전매매 준비 상태만 점검하며 주문을 제출하지 않습니다.

## API 발급 후 실전매매 준비 절차

1. `.env.example`을 `.env`로 복사하고 `TOSSINVEST_CLIENT_ID`, `TOSSINVEST_CLIENT_SECRET`, `TOSSINVEST_ACCOUNT_SEQ`를 로컬에만 입력합니다.
2. 토큰/조회 확인:
   ```bash
   python tossinvest_client.py token
   python tossinvest_client.py accounts
   python tossinvest_client.py holdings
   ```
3. 공식 주문 endpoint가 확인되면 `.env`의 `TOSSINVEST_LIVE_ORDER_ENDPOINT`에 입력합니다.
4. `PYTHONPATH=src python3 -m toss_alpha.cli live-readiness`로 누락 조건을 점검합니다.
5. 실전 제출을 허용하려면 아래 두 값을 모두 켜야 합니다.
   - `config/risk_policy.yaml`: `live_trading_enabled: true`
   - `.env`: `TOSSINVEST_LIVE_TRADING_ENABLED=true`
6. 실제 제출 함수는 `GuardedLiveExecutor.submit_manual_draft(..., dry_run=False)`이며, 수동 초안·통과된 `RiskDecision`·정확한 확인 문구가 모두 필요합니다.
