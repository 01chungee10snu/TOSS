# TOSS Ttak Harness Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Build the first safe implementation layer of the TOSS research harness: schema, read-only connectors, goal runner, minimal backtest/report pipeline, and manual-draft guardrails.

**Architecture:** Keep Toss/OpenDart access read-only. Convert all external API responses into internal schema objects with timestamps and source metadata. Run research goals through deterministic modules before any LLM/agent narrative. The final output is a report or manual order draft marked as not a live order.

**Tech Stack:** Python 3, pytest, dataclasses or pydantic-lite style dataclasses, YAML, requests, optional pandas/pyarrow for later cache.

---

## Non-negotiable guardrails

- Do not add live order execution.
- Do not call Toss order endpoints.
- Do not ask for secrets in chat.
- Do not print full account identifiers, tokens, or balances in Telegram templates.
- All order-like objects are `OrderIntent` or `ManualDraft`, never executable orders.
- Any unknown/stale/missing data produces `BLOCK`.

## Task 1: Add internal schema models

**Objective:** Create canonical data contracts used by all later modules.

**Files:**

- Create: `src/toss_alpha/data/__init__.py`
- Create: `src/toss_alpha/data/schema.py`
- Test: `tests/test_schema.py`

**Step 1: Write failing tests**

Test these behaviors:

- `Candle` requires symbol, interval, open_time, close_time, close.
- `SignalResult` defaults to `research_only=True`.
- `RiskDecision.blocked()` returns allow=false and preserves violations.
- `OrderIntent` has `mode="manual_draft_only"` by default.

**Step 2: Run test to verify failure**

Run:

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_schema.py
```

Expected: fail because module does not exist.

**Step 3: Implement schema dataclasses**

Implement minimal dataclasses:

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

**Step 4: Verify pass**

Run:

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_schema.py
```

Expected: pass.

**Step 5: Commit**

```bash
git add src/toss_alpha/data tests/test_schema.py
git commit -m "feat: add toss alpha data schema"
```

## Task 2: Extract Toss read-only connector

**Objective:** Move API logic from `tossinvest_client.py` into a reusable read-only connector without adding order methods.

**Files:**

- Create: `src/toss_alpha/connectors/__init__.py`
- Create: `src/toss_alpha/connectors/toss_readonly.py`
- Modify: `tossinvest_client.py`
- Test: `tests/test_toss_readonly_connector.py`

**Step 1: Write failing tests**

Use monkeypatch/fake response objects.

Test:

- connector builds `Authorization` header.
- account endpoints require account seq.
- connector exposes `token`, `stocks`, `prices`, `candles`, `accounts`, `holdings`.
- connector does not expose `orders`, `place_order`, `buy`, or `sell` methods.

**Step 2: Run failure**

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_toss_readonly_connector.py
```

Expected: fail because connector missing.

**Step 3: Implement connector**

- Preserve current base URL: `https://openapi.tossinvest.com`.
- Preserve rate limit/request id header capture.
- Return structured dict with `status_code`, `headers`, `json`, `text` where applicable.
- Keep CLI backwards compatible.

**Step 4: Verify**

```bash
python3 -m py_compile tossinvest_client.py src/toss_alpha/connectors/toss_readonly.py
PYTHONPATH=src python3 -m pytest -q tests/test_toss_readonly_connector.py
```

**Step 5: Commit**

```bash
git add tossinvest_client.py src/toss_alpha/connectors tests/test_toss_readonly_connector.py
git commit -m "refactor: extract toss read-only connector"
```

## Task 3: Add research goal YAML contract

**Objective:** Add Vibe-Trading style repeatable research-goal runtime input.

**Files:**

- Create: `goals/example_momentum.yaml`
- Create: `src/toss_alpha/research/__init__.py`
- Create: `src/toss_alpha/research/goal.py`
- Test: `tests/test_research_goal.py`

**Step 1: Write failing tests**

Test:

- YAML goal loads into `ResearchGoal`.
- mode must be one of `research_only`, `backtest_only`, `paper_only`, `manual_draft_only`.
- unknown mode is rejected.
- goal has symbols, period, strategy, risk profile.

**Step 2: Run failure**

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_research_goal.py
```

**Step 3: Implement loader**

Use `yaml.safe_load`. If PyYAML is not in requirements, add it.

**Step 4: Verify**

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_research_goal.py
```

**Step 5: Commit**

```bash
git add goals src/toss_alpha/research tests/test_research_goal.py requirements.txt
git commit -m "feat: add research goal contract"
```

## Task 4: Add minimal deterministic backtest engine

**Objective:** Implement a simple backtest path for existing `simple_momentum_signal` and `volatility_penalty` using in-memory candles.

**Files:**

- Create: `src/toss_alpha/backtest/__init__.py`
- Create: `src/toss_alpha/backtest/engine.py`
- Create: `src/toss_alpha/backtest/metrics.py`
- Test: `tests/test_backtest_engine.py`

**Step 1: Write failing tests**

Test:

- insufficient data returns blocked/insufficient result.
- deterministic candle list produces deterministic result.
- result includes fees/slippage fields even if zero/default.
- result never contains a live order.

**Step 2: Run failure**

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_backtest_engine.py
```

**Step 3: Implement minimal engine**

- Inputs: list of `Candle`, starting cash, fee bps, slippage bps.
- Output: `BacktestResult`.
- Keep strategy toy/research-only.

**Step 4: Verify**

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_backtest_engine.py tests/test_risk.py
```

**Step 5: Commit**

```bash
git add src/toss_alpha/backtest tests/test_backtest_engine.py
git commit -m "feat: add minimal research backtest engine"
```

## Task 5: Add manual draft generator with fail-closed risk gate

**Objective:** Generate manual review drafts only; no executable order methods.

**Files:**

- Create: `src/toss_alpha/agents/__init__.py`
- Create: `src/toss_alpha/agents/execution_draft.py`
- Test: `tests/test_manual_draft.py`

**Step 1: Write failing tests**

Test:

- draft includes `manual_draft_only` mode.
- if `RiskDecision.allow` is false, draft status is `BLOCK`.
- draft text includes “실주문 아님” and “수동 확인 필요”.
- module has no `execute`, `place_order`, `submit_order`, `buy`, `sell` callable.

**Step 2: Run failure**

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_manual_draft.py
```

**Step 3: Implement generator**

Input:

- `OrderIntent`
- `RiskDecision`
- rationale/evidence strings

Output:

- JSON-like dict or dataclass
- Markdown snippet for Telegram/report

**Step 4: Verify**

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_manual_draft.py
```

**Step 5: Commit**

```bash
git add src/toss_alpha/agents tests/test_manual_draft.py
git commit -m "feat: add manual order draft guardrail"
```

## Task 6: Add Markdown report renderer

**Objective:** Render research/backtest/risk/manual-draft outputs into a safe report format.

**Files:**

- Create: `src/toss_alpha/reports/__init__.py`
- Create: `src/toss_alpha/reports/markdown_report.py`
- Test: `tests/test_markdown_report.py`

**Step 1: Write failing tests**

Test:

- report includes “투자 조언 아님”.
- report includes “손실 가능”.
- report does not use direct imperative “매수하세요” or “매도하세요”.
- blocked risk decision appears as `BLOCK`.

**Step 2: Run failure**

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_markdown_report.py
```

**Step 3: Implement renderer**

Sections:

1. 연구 목표
2. 데이터 기준 시점
3. 신호/이벤트 근거
4. 백테스트 요약
5. 리스크 게이트
6. 수동 검토 초안
7. 주의 문구

**Step 4: Verify**

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_markdown_report.py
```

**Step 5: Commit**

```bash
git add src/toss_alpha/reports tests/test_markdown_report.py
git commit -m "feat: add safe markdown report renderer"
```

## Task 7: Add CLI skeleton

**Objective:** Provide Vibe-Trading-style commands without implementing live trading.

**Files:**

- Create: `src/toss_alpha/cli.py`
- Modify: `README.md`
- Test: `tests/test_cli_skeleton.py`

**Step 1: Write failing tests**

Test:

- `python -m toss_alpha.cli --help` exits 0.
- commands exist: `research run`, `backtest run`, `draft-order`.
- no command named `live`, `place-order`, `buy`, or `sell` exists.

**Step 2: Run failure**

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_cli_skeleton.py
```

**Step 3: Implement CLI skeleton**

Use argparse. Commands can initially print “not implemented yet” safely, except help.

**Step 4: Verify**

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_cli_skeleton.py
PYTHONPATH=src python3 -m toss_alpha.cli --help
```

**Step 5: Commit**

```bash
git add src/toss_alpha/cli.py README.md tests/test_cli_skeleton.py
git commit -m "feat: add research harness cli skeleton"
```

## Task 8: Full safety regression

**Objective:** Ensure the harness remains read-only/manual-draft-only.

**Files:**

- Modify: tests as needed
- Modify: docs as needed

**Step 1: Run all tests**

```bash
PYTHONPATH=src python3 -m pytest -q tests
```

Expected: all pass.

**Step 2: Search for forbidden live-order names**

```bash
grep -R "place_order\|submit_order\|live_order\|auto_trade" -n src tests || true
```

Expected: no executable live-order implementation. If strings appear in tests as forbidden checks, they must be clearly test-only.

**Step 3: Verify gitignore**

```bash
git check-ignore .env
```

Expected: `.env` is ignored.

**Step 4: Commit final docs if needed**

```bash
git add README.md docs tests src goals requirements.txt
git commit -m "docs: document ttak harness safety gates"
```

## Acceptance criteria

- All tests pass.
- No live order execution code exists.
- Research goal YAML loads and validates mode.
- Manual draft output is blocked by risk violations.
- Report includes risk disclaimers.
- CLI exposes research/backtest/draft only.
- README and docs explain this is not investment advice and not live trading.
