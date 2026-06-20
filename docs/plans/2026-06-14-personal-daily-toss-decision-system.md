# Personal Daily Toss Decision System Implementation Plan

> **For Hermes:** Use `financial-automation-harnesses` and `writing-plans` skills when implementing this plan.

**Goal:** Build a personal-use Korean stock decision system that scans the market daily, ranks candidates, decides buy/hold/sell timing, and plugs into Toss once API credentials are issued.

**Architecture:** Keep the system fail-closed and research-first. Use local market data + backtests + daily ranking now, then connect Toss read-only/account endpoints after API issuance, and only later enable guarded manual order drafts. Reuse the existing `research`, `backtest`, `draft-order`, and `live-readiness` structure instead of inventing a new execution engine.

**Tech Stack:** Python, pandas, YAML config, local reports, existing `toss_alpha` CLI, Toss Open API when available.

---

## Product definition (personal use only)

This is **not** a public recommendation service and **not** a fully autonomous trading bot.

Daily output should answer only five questions:
1. **What kind of market is today?**
2. **Which 5–20 Korean stocks are the strongest current candidates?**
3. **Which current holdings should be held / trimmed / sold?**
4. **What exact trigger price / invalidation / stop / take-profit applies?**
5. **Should I do nothing today?**

Success criteria:
- Every run produces a reproducible report on disk.
- Every suggested action includes explanation + risk limits.
- No live order path is enabled by default.
- Toss API can be added later without redesigning the whole system.

---

## Target operating loop

### Pre-market
- Build market regime summary.
- Refresh universe and factor panel.
- Rank candidate stocks.
- Produce watchlist + trigger prices.

### Intraday
- Re-check top candidates.
- Detect entry trigger / invalidation / stop movement.
- Re-evaluate current holdings.

### After market close
- Save final action log.
- Recompute next-day candidate set.
- Append trade journal / evidence files.

---

## System modules

### 1. Market regime engine

**Purpose:** decide whether the day is trend-following, mean-reverting, defensive, or no-trade.

**Inputs:**
- KOSPI / KOSDAQ trend
- index momentum
- market breadth
- volatility regime
- trading value / liquidity concentration
- sector relative strength

**Outputs:**
- `risk_on`
- `risk_off`
- `breakout`
- `chop`
- `event_risk`

**Personal-use rule:** hardcode simple interpretable regime logic first; do not start with opaque ML.

### 2. Universe builder

**Purpose:** construct the tradable Korean equity universe.

**Filters:**
- preferred liquidity floor
- minimum median turnover
- exclude halted / untradeable names
- optional KOSPI / KOSDAQ subsets
- optional blacklist / whitelist from config

**Output:** stable daily universe snapshot saved to disk.

### 3. Candidate ranking engine

**Purpose:** score all eligible stocks every day.

**First-pass factor set:**
- short / medium momentum
- volatility-adjusted momentum
- volume surge / turnover strength
- pullback quality
- sector strength tailwind
- overextension penalty

**Output:** ranked table with factor breakdown, not just one opaque score.

### 4. Timing engine

**Purpose:** convert ranked candidates into actionable entries.

**Entry styles to support first:**
- breakout entry
- pullback entry
- gap-too-high reject
- no-trade reject

**Output fields per candidate:**
- symbol
- setup type
- trigger price
- invalidation condition
- stop rule
- initial take-profit rule
- max position size

### 5. Holdings review engine

**Purpose:** decide buy / hold / trim / sell on current positions.

**Needed states:**
- `BUY_CANDIDATE`
- `WAIT`
- `HOLD`
- `TRIM`
- `SELL`
- `COOLDOWN`

**Decision axes:**
- trend intact or broken
- market regime still supportive or not
- stop breached or not
- profit lock rule triggered or not
- portfolio concentration too high or not

### 6. Report engine

**Purpose:** produce a human-readable daily report for personal use.

**Sections:**
- market regime summary
- top candidates
- current holdings review
- blocked trades / no-trade reasons
- tomorrow watchlist

**Format:** markdown first, optionally Telegram later.

### 7. Toss integration layer

**Stage A — before API issuance**
- keep connector layer abstract
- support mock account / mock holdings / manual CSV import if needed

**Stage B — after API issuance**
- connect token
- verify account / holdings / prices / candles
- keep live trading disabled
- enrich holdings review with real account state

**Stage C — later**
- generate guarded manual order drafts only
- no direct autonomous execution by default

---

## Implementation phases

### Phase 0: Use existing repo as the spine

Current repo already has:
- `src/toss_alpha/cli.py`
- `src/toss_alpha/connectors/toss_readonly.py`
- `src/toss_alpha/execution/live_ready.py`
- `tests/test_cli_skeleton.py`

Conclusion: do **not** create a second parallel app. Extend this repo.

### Phase 1: Daily research MVP without Toss API

**Objective:** get personal daily reports working before broker integration.

Build first:
- local universe loader
- factor scoring
- regime classifier
- markdown daily report
- holdings review on mock/manual positions

Verification:
- run from CLI on historical data
- output report file on disk
- backtest can replay the same decisions historically

### Phase 2: Toss read-only integration after API issuance

**Objective:** use real account and market/account endpoints.

Add:
- token validation command
- account summary fetch
- holdings fetch
- price/candle fetch verification
- real holdings wired into holdings review report

Verification:
- `live-readiness` still says not ready for real trading by default
- read-only commands work with actual credentials
- no order endpoint required yet

### Phase 3: Personal execution assistance

**Objective:** prepare orders safely, not autopilot them.

Add:
- manual order draft from daily candidate / holdings decisions
- exact position sizing output
- stop / invalidation / risk note per draft

Verification:
- dry-run only
- blocked unless explicit double opt-in remains true

### Phase 4: Optional later automation

Only if personal trust is earned after long paper/live-shadow validation:
- scheduled daily runs
- Telegram push alerts
- guarded real submission path

---

## Exact near-term tasks

### Task 1: Define the daily report schema

**Objective:** lock the output shape before coding more engines.

**Files:**
- Create: `docs/daily-report-schema.md`
- Test: `tests/test_markdown_report.py`

**Deliverable:** markdown schema with sections for regime, candidates, holdings, blocked actions, tomorrow watchlist.

### Task 2: Define the market regime config

**Objective:** use transparent rules rather than hidden heuristics.

**Files:**
- Create: `config/market_regime.yaml`
- Modify: `src/toss_alpha/research/goal.py` or adjacent config loader if needed
- Test: new regime tests under `tests/`

**Deliverable:** thresholds for trend, breadth, volatility, risk-on/off states.

### Task 3: Build candidate score breakdown

**Objective:** score every stock with interpretable factor columns.

**Files:**
- Modify: `src/toss_alpha/signals.py`
- Test: add scoring fixture tests

**Deliverable:** ranked dataframe with component scores and final score.

### Task 4: Add holdings review state machine

**Objective:** personal holdings get buy/hold/trim/sell actions every day.

**Files:**
- Modify: `src/toss_alpha/risk.py`
- Create or modify: `src/toss_alpha/agents/execution_draft.py`
- Test: new holdings review tests

**Deliverable:** deterministic action labels plus reasons.

### Task 5: Add a personal daily CLI command

**Objective:** one command should generate the full daily decision packet.

**Files:**
- Modify: `src/toss_alpha/cli.py`
- Test: `tests/test_cli_skeleton.py` plus new command tests

**Suggested command shape:**
- `PYTHONPATH=src python3 -m toss_alpha.cli daily run --date YYYY-MM-DD`

**Deliverable:** one report command producing saved markdown/json artifacts.

### Task 6: Add mock account mode before Toss API

**Objective:** make the system useful immediately.

**Files:**
- Create: `config/mock_holdings.yaml`
- Modify: connector / report wiring as needed
- Test: mock holdings integration tests

**Deliverable:** holdings review works without broker credentials.

### Task 7: Add Toss API onboarding checklist

**Objective:** when credentials arrive, integration is mechanical.

**Files:**
- Create: `docs/toss-api-onboarding-checklist.md`
- Modify: `README.md`

**Checklist should include:**
- `.env` keys
- token test
- accounts test
- holdings test
- candle test
- confirmation that live trading remains disabled

---

## Recommended command surface

### Before Toss API
- `research run`
- `backtest run`
- new `daily run`
- optional `daily run --mock-holdings config/mock_holdings.yaml`

### After Toss API issuance
- `python tossinvest_client.py token`
- `python tossinvest_client.py accounts`
- `python tossinvest_client.py holdings`
- `python tossinvest_client.py prices <symbol>`
- `live-readiness`
- new `daily run --use-toss-account`

---

## Personal operating principles

- A valid output can be **do nothing**.
- Holdings review is as important as new entries.
- If regime is hostile, ranking engine should still allow zero trades.
- Backtest first, paper-shadow second, guarded live-assist third.
- Preserve explainability: every action needs a reason string.

---

## Definition of done

This personal system is "ready" only when:
1. one CLI command generates a complete daily report;
2. the report includes market regime, top candidates, holdings actions, and blocked-trade reasons;
3. the same logic can be replayed on historical data;
4. Toss credentials can be plugged in without changing business logic;
5. live trading is still blocked unless explicit opt-in gates are satisfied.
