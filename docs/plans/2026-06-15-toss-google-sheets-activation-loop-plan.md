# TOSS Google Sheets Activation Loop Plan

> **For Hermes:** Use `fable-5-loop-engineering` as the top-level loop. Use `writing-plans` for this artifact. Execute one loop at a time with real evidence before advancing.

**Goal:** TOSS `daily-paper`를 실제 Google Spreadsheet를 DB처럼 읽고/쓰는 상태까지 연결하고, 실제 `sheet-id`로 end-to-end 검증한다.

**Architecture:** 이미 구현된 `GoogleSheetsClient` / `GoogleSheetsDailyPaperStore` / `daily-paper --sheet-id` / `init-daily-paper-sheet`를 활용한다. 남은 일은 코드 작성보다 **외부 API 활성화/실시트 확보/실연결 검증**이다. 따라서 이번 계획은 구현 중심이 아니라 **activation loop + verification loop**로 구성한다.

**Tech Stack:** Python, argparse CLI, Google OAuth token, Google Sheets API, Google Drive API, `src/toss_alpha/storage/google_sheets.py`, `src/toss_alpha/cli.py`, pytest.

---

## 현재 사실 기준선

### 이미 완료된 것
- `src/toss_alpha/storage/google_sheets.py`
  - `GoogleSheetsClient`
  - `GoogleSheetsDailyPaperStore`
  - `bootstrap_new_sheet(...)`
- `src/toss_alpha/cli.py`
  - `daily-paper --sheet-id <id|url>`
  - `init-daily-paper-sheet --title ...`
- 테스트 통과
  - 전체: `51 passed`
- 기존 인증 토큰 확보 및 `work` 프로필 연결 완료
- 실제 인증 live check 성공
  - `LIVE_CHECK_OK: Real API call succeeded.`

### 현재 블로커
- 기존 OAuth token 프로젝트에서 `sheets.googleapis.com` 비활성
- 기존 OAuth token 프로젝트에서 `drive.googleapis.com` 비활성
- 따라서 다음 둘 다 아직 불가
  - 기존 TOSS sheet 탐색
  - 새 TOSS sheet 생성

### 핵심 해석
이 작업의 남은 리스크는 코드 품질이 아니라 **외부 Google API activation 상태**다. 따라서 다음 루프는 구현보다 **사전조건 해소 → 재검증** 순서를 강제해야 한다.

---

## 완료 조건

다음 4개가 모두 만족되면 완료로 간주한다.

1. `setup.py --check-live` 수준이 아니라 실제 Sheets/Drive API 호출이 성공한다.
2. 기존 TOSS 시트를 찾거나, 없으면 새 시트를 생성한다.
3. 실제 시트에 `settings`, `holdings`, `orders`, `runs`, `fills`, `positions` 탭/헤더가 준비된다.
4. `PYTHONPATH=src .venv/bin/python -m toss_alpha.cli daily-paper --sheet-id <REAL_ID>`가 성공하고, writeback까지 확인된다.

---

## 루프 구조

## Loop 0 — Preflight / 상태 고정

**Objective:** 재시도 전 현재 환경과 블로커를 고정한다.

**Input**
- `src/toss_alpha/storage/google_sheets.py`
- `src/toss_alpha/cli.py`
- `~/.hermes/profiles/work/google_token.json`
- `~/.hermes/profiles/work/google_client_secret.json`

**Action**
1. 현재 토큰 파일과 client secret 파일 존재 확인
2. `setup.py --check-live` 재실행
3. `pytest -q` 재실행
4. 최근 실패 원인(403 SERVICE_DISABLED) 캡처 유지

**Verification**
- `LIVE_CHECK_OK: Real API call succeeded.`
- `pytest -q` green
- 403 오류 메시지에 `sheets.googleapis.com` / `drive.googleapis.com` 명시

**Stop condition**
- live check 또는 테스트가 깨지면 activation 진행 중단, 먼저 환경/코드 회귀 수정

**Evidence to save**
- CLI stdout/stderr 스니펫
- 최신 테스트 결과

---

## Loop 1 — API Activation Gate

**Objective:** Google 프로젝트에서 Sheets/Drive API 활성 여부를 확인하고, 비활성이면 그 사실을 사용자에게 명확히 에스컬레이션한다.

**Why first:** 이 단계가 안 풀리면 시트 탐색/생성은 모두 무의미한 재시도다.

**Action**
1. 실제 `google_api.py sheets create ...` 또는 동등 호출 재실행
2. 실제 `google_api.py drive search ...` 또는 동등 호출 재실행
3. 실패 시 오류 메시지에서 프로젝트 번호와 비활성 API 이름 추출
4. 사용자에게 필요한 최소 액션만 요청
   - `sheets.googleapis.com` 활성화
   - `drive.googleapis.com` 활성화
   - 대상 프로젝트: 현재 token이 속한 프로젝트

**Verification**
- 성공 조건: Sheets create / Drive search 둘 중 하나 이상이 403 없이 응답
- 실패 조건: 403 + `SERVICE_DISABLED` 또는 `accessNotConfigured`

**Abort / Escalation gate**
- 사용자가 Google Cloud Console 쪽 작업을 직접 해야 하는 경우 즉시 에스컬레이션
- assistant는 API enable 자체를 대신할 수 없다고 명시

**Deliverable**
- “무엇이 꺼져 있고, 사용자가 무엇을 켜야 하는지” 3줄 요약

---

## Loop 2 — Existing Sheet Discovery

**Objective:** API가 열렸다면 먼저 기존 TOSS sheet를 찾는다.

**Action**
1. Drive search로 키워드 탐색
   - `TOSS`
   - `toss_alpha`
   - `daily paper`
   - `autotrading`
   - `005930`, `000660` 등 샘플 심볼은 필요 시 보조키워드
2. 검색 결과에서 spreadsheet mime type만 추림
3. 후보가 여러 개면
   - 수정시각
   - 제목
   - webViewLink
   기준으로 우선순위 정렬
4. 첫 후보 1~3개에 대해 `settings!A:B`, `holdings!A:C`, `orders!A:G`를 읽어 스키마 적합성 확인

**Verification**
- 적합한 기존 시트의 판단 기준
  - 시트 읽기 성공
  - 최소 하나의 expected 탭 존재
  - TOSS daily-paper 스키마와 충돌하지 않음

**Stop condition**
- 적합 시트를 찾으면 Loop 3로 진행
- 못 찾으면 새 시트 생성 Loop 2B로 전환

**Evidence**
- spreadsheet id
- title
- webViewLink
- sample range read 결과

---

## Loop 2B — New Sheet Bootstrap

**Objective:** 기존 시트를 못 찾으면 새 TOSS 시트를 실제로 생성하고 초기화한다.

**Action**
1. `init-daily-paper-sheet --title "TOSS Daily Paper"` 실행
2. 필요 시 환경변수 고정
   - `TOSS_ALPHA_GOOGLE_API_SCRIPT=/Users/01chungee10/.hermes/profiles/work/skills/productivity/google-workspace/scripts/google_api.py`
   - `TOSS_ALPHA_GOOGLE_API_PYTHON=python`
3. 생성된 `spreadsheet_id`, `spreadsheet_url` 기록
4. 각 range 초기화 검증
   - `settings!A:B`
   - `holdings!A:C`
   - `orders!A:G`
   - `runs!A:G`
   - `fills!A:H`
   - `positions!A:F`

**Verification**
- 실제 `spreadsheet_id` 반환
- 각 탭 헤더 readback 성공
- `settings!A:B`에 `initial_cash_krw` 존재

**Stop condition**
- create는 성공했지만 header write 실패 시 bootstrap fix loop로 분기

---

## Loop 3 — Minimal Real Sheet Hydration

**Objective:** 실제 시트에 최소 실행 가능한 seed 데이터를 넣는다.

**Action**
1. `settings!A:B`
   - `initial_cash_krw`
2. `holdings!A:C`
   - 예시: `005930,5,10000`
3. `orders!A:G`
   - 예시 trim 1건 + entry 1건
4. 필요 시 JSON fixture를 별도 파일로 저장해 write source를 고정

**Verification**
- 각 탭 readback 시 값이 정확히 보임
- `load_plan(...)` equivalent path에서 파싱 가능

**Evidence**
- readback rows
- symbol/quantity/price 값

---

## Loop 4 — Real `daily-paper` End-to-End Run

**Objective:** 실제 sheet-id를 사용해 `daily-paper`를 읽고 실행하고 writeback까지 검증한다.

**Action**
1. 실행
   - `PYTHONPATH=src .venv/bin/python -m toss_alpha.cli daily-paper --sheet-id <REAL_ID>`
2. stdout 캡처
3. `runs`, `fills`, `positions` 탭 readback
4. stdout 요약과 시트 writeback 결과 교차검증

**Verification**
- stdout 조건
  - `status: OK`
  - `filled_orders:` 값 존재
  - `sheet_id: <REAL_ID>`
  - `sheet_writeback: True`
- 시트 조건
  - `runs`에 1행 추가
  - fill이 있으면 `fills`에 append
  - `positions`에 결과 포지션 반영

**Stop condition**
- stdout은 성공인데 시트에 안 써졌으면 writeback bug loop로 회귀
- 시트 writeback은 됐는데 stdout이 이상하면 CLI formatting bug loop로 회귀

---

## Loop 5 — Post-run Hardening

**Objective:** 같은 장애를 다시 안 밟게 activation/runtime guard를 보강한다.

**Candidate hardening items**
1. API disabled 오류를 사람이 읽기 쉽게 번역
2. `init-daily-paper-sheet` 실행 전 preflight check 추가
3. README or docs에 required env / API prerequisites 명시
4. 실제 API smoke test를 optional integration check로 문서화

**Verification**
- disabled API 상황에서 더 짧고 이해 가능한 오류 메시지 출력
- docs에 재현 가능한 순서 명시

---

## 실행 순서 요약

1. Loop 0 — 상태 고정
2. Loop 1 — API activation gate
3. Loop 2 — 기존 시트 탐색
4. Loop 2B — 없으면 새 시트 생성
5. Loop 3 — seed 데이터 주입
6. Loop 4 — 실제 `daily-paper` 실행 + writeback 확인
7. Loop 5 — 하드닝

---

## 현재 TODO 매핑

- `check-google-auth`
  - Loop 0
  - Loop 1
- `create-or-find-toss-sheet`
  - Loop 2
  - Loop 2B
- `connect-toss-cli`
  - Loop 3
  - Loop 4

---

## 즉시 다음 액션

다음 작업은 구현이 아니라 **Loop 1 재진입**이다.

### Next command set
1. `setup.py --check-live`
2. 실제 `drive search` 재시도
3. 실제 `sheets create` 재시도
4. 실패 시 API enable 요청으로 즉시 에스컬레이션

---

## 판단 원칙

- 같은 403 SERVICE_DISABLED를 반복 재시도하지 않는다.
- 외부 API가 막힌 상태에서는 코드 수정으로 해결 가능한지 먼저 의심하지 않는다.
- 기존 시트 탐색보다 API activation gate가 우선이다.
- 실제 시트 ID, URL, writeback row 같은 **검증 가능한 핸들** 없이는 “완료”라고 말하지 않는다.
