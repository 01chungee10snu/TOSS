# Toss Auto-Trading Transition Roadmap

> **For Hermes:** Use `financial-automation-harnesses` and `writing-plans` skills when implementing this plan.

**Goal:** Evolve the current personal daily Toss decision system into a guarded, eventually autonomous Korean stock trading program.

**Architecture:** Reuse the existing `toss_alpha` research/backtest/risk/draft/live-readiness spine. Do **not** build a separate bot. Extend the current state machine from `research -> candidate selection -> holdings review -> manual draft -> guarded live execution` into `research -> paper execution -> shadow execution -> capped live execution -> autonomous execution`.

**Tech Stack:** Python, pandas, YAML config, Toss Open API, existing `toss_alpha` CLI, markdown/json reports, cron/Telegram later.

---

## Bottom line

Yes — the end goal can be a real auto-trading program.

But the repo is **not one step away** from safe autonomy.
Right now it already has the right skeleton for:
- strategy outputs,
- order intent schema,
- risk policy,
- manual draft generation,
- guarded live-readiness checks.

What it does **not** yet have is the full automation layer:
- broker state reconciliation,
- portfolio state machine,
- scheduler,
- fill/position ledger,
- paper trading loop,
- shadow-live verification,
- post-order monitoring,
- kill switch,
- recovery rules after partial failure,
- tested live order orchestration.

So the right path is **transition**, not direct flip.

---

## Current repo status: what already exists

### Already present
- `src/toss_alpha/data/schema.py`
  - `OrderIntent`
  - `RiskDecision`
- `src/toss_alpha/risk.py`
  - conservative `RiskPolicy`
  - `validate_order_intent(...)`
- `src/toss_alpha/agents/execution_draft.py`
  - manual review draft rendering only
- `src/toss_alpha/execution/live_ready.py`
  - guarded live submission path
  - explicit env/account/order-endpoint checks
  - double opt-in confirmation phrase
- `src/toss_alpha/cli.py`
  - existing CLI spine for research/backtest/live-readiness style workflows

### Important constraint discovered
Current `OrderIntent` mode is explicitly:
- `mode="manual_draft_only"`
- `not_live_order=True`

That means the current system is designed to **stop before autonomy**.
This is good. We should preserve that fail-closed posture while adding staged automation.

---

## Target end-state

The final autonomous system should do this every trading day:

1. Refresh market regime and universe.
2. Score all tradable Korean stocks.
3. Produce candidate entries and candidate exits.
4. Reconcile against real Toss holdings/cash/open orders.
5. Apply portfolio/risk constraints.
6. Decide exact actions:
   - buy
   - hold
   - trim
   - sell
   - do nothing
7. Submit only allowed orders.
8. Persist every decision, payload, broker response, and resulting position state.
9. Detect fill/reject/partial-fill mismatches.
10. Halt automatically on safety breaches.

---

## Non-negotiable safety gates

Before true autonomy, the program must support all of these:

### Gate 1: Read-only integration complete
- token fetch works
- account fetch works
- holdings fetch works
- price/candle fetch works
- all data written to local artifacts for replay

### Gate 2: Paper execution engine
- same strategy logic produces simulated orders
- fills, fees, slippage, and position changes are tracked
- daily report matches the simulated ledger

### Gate 3: Shadow-live verification
- system reads real Toss account
- system computes what it *would* have ordered
- system does **not** submit
- differences between proposed and actual holdings are logged

### Gate 4: Capped live execution
- tiny order caps
- symbol whitelist
- trading window restrictions
- one-order-at-a-time mode
- manual emergency stop

### Gate 5: Autonomous execution
- only after shadow-live consistency is stable
- only after failure-recovery paths are tested
- only after broker reconciliation is reliable

---

## The real gap between "decision system" and "auto-trader"

### Missing module A: Broker state reconciliation
The system must know at all times:
- current cash
- current holdings
- sellable quantity
- open orders
- filled quantity
- average entry price
- unrealized PnL

Without this, a BUY/SELL signal is not enough.

### Missing module B: Position state machine
Need an explicit per-symbol state machine such as:
- `FLAT`
- `ENTRY_PENDING`
- `LONG`
- `TRIM_PENDING`
- `EXIT_PENDING`
- `COOLDOWN`
- `BLOCKED`

This is the core of autonomous operation.

### Missing module C: Execution orchestrator
Need a layer that converts decisions into:
- broker-safe order payloads
- duplicate-order prevention
- idempotent client order IDs
- retry rules
- reject handling
- partial-fill handling

### Missing module D: Ledger / journal
Every action must be written to disk:
- decision timestamp
- market snapshot ID
- intended order
- broker response
- post-trade position state
- exception/failure state

### Missing module E: Kill switch
The system must halt if:
- daily loss limit breached
- repeated broker rejects
- position mismatch detected
- stale market/account data
- duplicate order risk
- connection/auth failure

### Missing module F: Scheduler + recovery
Need timed runs for:
- pre-market prep
- entry window
- intraday maintenance
- end-of-day reconciliation

And recovery behavior if a run crashes mid-session.

---

## Transition phases

### Phase 1: Strengthen the decision engine
**Goal:** make daily decisions deterministic and replayable.

Build:
- market regime engine
- ranked universe output
- candidate timing logic
- holdings review logic
- complete daily markdown/json report

Done when:
- one CLI command creates a full daily action packet
- historical replay reproduces outputs

### Phase 2: Add portfolio state + paper execution
**Goal:** turn signals into simulated trades.

Build:
- portfolio ledger
- fill simulator
- transaction cost model
- symbol state machine
- simulated holdings reconciliation

Done when:
- backtest and daily paper run share the same execution semantics
- output includes resulting simulated portfolio

### Phase 3: Toss read-only account integration
**Goal:** replace mock holdings with real account state.

Build:
- real holdings adapter
- cash/open-order snapshot adapter
- account reconciliation report

Done when:
- the system can compare desired state vs actual Toss state
- no live order is needed yet

### Phase 4: Shadow-live execution mode
**Goal:** generate real intended orders without submission.

Build:
- intended order queue
- broker payload preview
- duplicate prevention logic
- dry-run reconciliation logs

Done when:
- for several sessions, intended actions are internally consistent
- state transitions are stable under real account data

### Phase 5: Guarded micro-live mode
**Goal:** submit tiny real orders with hard caps.

Build:
- live execution mode flag separate from manual draft mode
- strict whitelist / budget caps
- one-order-per-cycle limit
- automatic halt on anomalies

Done when:
- live submission path is tested with tiny size
- post-order reconciliation succeeds reliably

### Phase 6: Autonomous mode
**Goal:** scheduled end-to-end operation.

Build:
- scheduled intraday loop
- automatic exit management
- fill polling / order status updates
- emergency stop / resume procedure

Done when:
- the system survives ordinary failures without unsafe behavior
- logs are sufficient to audit every action

---

## Recommended code evolution

### 1. Keep current fail-closed defaults
Do **not** change these defaults yet:
- `OrderIntent.mode = "manual_draft_only"`
- `OrderIntent.not_live_order = True`
- `RiskPolicy.live_trading_enabled = False`

Instead, add explicit later-stage modes only after paper/shadow/live gating exists.

### 2. Extend the existing state machine rather than replacing it
The repo already has the right conceptual spine:
- signal generation
- risk decision
- execution draft
- guarded live executor

Add missing states and execution semantics around it.

### 3. Separate three execution modes explicitly
The codebase should support:
- `manual_draft_only`
- `paper_auto`
- `live_auto_guarded`

Do not overload one mode to mean all three.

### 4. Treat SELL logic as a first-class problem
Current risk validation includes:
- `sell_requires_sellable_quantity_check`

That means autonomous SELL is not implemented yet.
Before live automation, add real holdings quantity checks and broker-state verification.

---

## Exact next implementation priorities

### Priority 1: Add execution modes to schema
**Files:**
- Modify: `src/toss_alpha/data/schema.py`
- Test: new schema tests

Add explicit execution modes that distinguish:
- manual review
- paper auto
- guarded live auto

### Priority 2: Add portfolio/position ledger
**Files:**
- Create: `src/toss_alpha/execution/ledger.py`
- Create: `tests/test_execution_ledger.py`

Need to track:
- cash
- holdings
- average cost
- open orders
- realized/unrealized PnL
- per-symbol execution state

### Priority 3: Add state machine for symbol lifecycle
**Files:**
- Create: `src/toss_alpha/execution/state_machine.py`
- Create: `tests/test_state_machine.py`

Need explicit transitions:
- flat -> entry_pending -> long
- long -> trim_pending -> long
- long -> exit_pending -> flat
- any -> blocked/cooldown

### Priority 4: Add paper execution path
**Files:**
- Create: `src/toss_alpha/execution/paper_executor.py`
- Modify: `src/toss_alpha/cli.py`
- Test: `tests/test_paper_executor.py`

This is the most important bridge to autonomy.

### Priority 5: Add Toss account reconciliation
**Files:**
- Modify: `src/toss_alpha/connectors/toss_readonly.py`
- Create: `src/toss_alpha/execution/reconcile.py`
- Test: reconciliation tests

Need to compare:
- desired positions
- actual holdings
- sellable quantities
- open orders

### Priority 6: Add shadow-live daily command
**Files:**
- Modify: `src/toss_alpha/cli.py`
- Create: tests for shadow mode

Suggested command:
- `PYTHONPATH=src python3 -m toss_alpha.cli daily shadow --use-toss-account`

### Priority 7: Add guarded micro-live mode
**Files:**
- Modify: `src/toss_alpha/execution/live_ready.py`
- Modify: `src/toss_alpha/risk.py`
- Test: live guard tests

Need:
- tiny notional caps
- whitelist
- one-order-per-cycle gate
- kill-switch integration

---

## Recommended CLI roadmap

### Near-term
- `daily run --date YYYY-MM-DD`
- `daily run --mock-holdings config/mock_holdings.yaml`
- `daily paper --date YYYY-MM-DD`

### After Toss API issuance
- `daily shadow --use-toss-account`
- `live-readiness`
- `account reconcile`

### Later
- `daily auto --use-toss-account --guarded-live`

---

## Definition of done for true auto-trading

The system is not "an auto-trader" until all of these are true:
1. it has a deterministic decision engine;
2. it has a paper execution engine with shared semantics;
3. it reconciles against real Toss account state;
4. it prevents duplicate/conflicting orders;
5. it supports reliable sellable-quantity checks;
6. it persists an auditable execution ledger;
7. it halts safely on stale data, rejects, mismatches, or risk breaches;
8. it has survived shadow-live and micro-live validation first.

---

## Recommended immediate direction

Your goal should be framed as:

> **Build a daily decision engine first, then evolve it into a guarded auto-trading program by adding a paper executor, real-account reconciliation, and capped live execution modes in order.**

That is the shortest correct path.
