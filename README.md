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
- 기본값은 조회/알림/백테스트.
- 실주문은 별도 승인, 한도, 수동확인 없이는 구현/실행하지 않음.
