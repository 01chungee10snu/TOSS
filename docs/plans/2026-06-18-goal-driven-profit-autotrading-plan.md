# Goal-Driven Profit Autotrading Plan

> **For Hermes:** Use `financial-automation-harnesses`, `fable-5-loop-engineering`, and the repo-local `toss-ttak-orchestrator` rules when evolving this plan.

**Goal:** Turn the repo's existing research harness into a repeatable `/goal`-driven profit-seeking system that prioritizes stable out-of-sample expectancy, bad-week suppression, and safe staged promotion over headline return.

**Architecture:** Keep the repo fail-closed. Use `/goal` YAML as the durable spec, run quant as the engine, treat external/event information as slow veto or review, and promote only through `backtest -> paper -> shadow-live -> capped live`. Do not build a separate bot.

**Tech Stack:** Python, pandas, YAML goal specs, Toss/OpenDart connectors, contextual policy reports, markdown/json artifacts.

---

## Bottom line

If the real objective is **"자동매매로 실제 돈 벌기"**, the best path in this repo is **not** to chase one magical signal.

The stronger shape is:
1. **Primary engine:** broad daily contextual strategy.
2. **Specialty add-on:** Monday->Friday mode only when regime is narrow and validated.
3. **Fast veto:** remove obvious unstable candidates, but tune separately per cycle.
4. **Slow qual:** OpenDart/event checks as veto/review, not timing alpha.
5. **Promotion gates:** only promote branches that survive costs, drawdown, and test-sample checks.

That means `/goal` should encode a **research program**, not just a single indicator.

---

## What `/goal` means in this repo right now

Verified from repo files:
- `src/toss_alpha/research/goal.py`
- `goals/example_momentum.yaml`
- `docs/ttak-recursive-harness-design.md`

Current reality:
- `/goal` is already a **YAML research spec format**.
- The loader exists.
- A fully wired general `runner.py` is **not present yet**.

So the right move now is:
- write a high-quality reusable goal spec first,
- then evolve the runner/orchestrator around it,
- instead of improvising one-off backtests forever.

---

## Recommended profit architecture

## 1. Quant engine stays first-class

Use quant as the repeatable engine because it is measurable and replayable.

### Primary lane
- daily contextual strategy
- regime-aware activation
- previous-close-only features
- liquidity floor
- top-N ranking

Why:
- better sample thickness than the narrow mon-fri branch
- easier to tune and verify across years/regimes
- better candidate flow for a daily loop

### Secondary lane
- mon-fri specialty mode
- only when regime resembles `flat_low_vol`
- never treated as the universal default

Why:
- recent analysis showed mon-fri can outperform on headline return
- but sample is thinner and outlier dependence is higher
- so it should be a controlled submode, not the full operating core

---

## 2. External information should be split by latency class

### Fast structured inputs
Use as same-run regime/risk inputs:
- market breadth
- sector relative strength
- liquidity concentration
- volatility regime

These are useful because they are structured, backtestable, and reproducible.

### Slow external inputs
Use as veto/review inputs:
- OpenDart disclosures
- earnings shocks
- capital raises
- major shareholder changes
- halt/delisting style event risk

These are useful because they reduce dumb entries, but they are **not** reliable intraday alpha timing tools.

### Research support only
- NotebookLM/doc-QA

Use this for operator understanding and documentation QA, not direct trading decisions.

---

## 3. Fast veto must be cycle-specific

Recent repo evidence matters here:
- the default `fast_veto.py` thresholds did improve some bad mon-fri weeks,
- but they also cut important winners,
- and the mon-fri replay got worse overall.

Implication:
- keep fast veto as an axis,
- but tune it separately for:
  - daily contextual mode
  - mon-fri specialty mode
- do not share one threshold set blindly.

---

## 4. Promotion criteria should optimize for practical money-making, not pretty backtests

Prefer these in order:
1. stable out-of-sample return
2. max drawdown control
3. enough trades / enough active periods
4. bad-week suppression
5. explainability

Avoid promoting a branch just because:
- CAGR is high,
- one year looks amazing,
- one outlier name carried the run,
- or train set looked pretty.

---

## 5. Best immediate `/goal` artifact

Created file:
- `goals/profit_compound_autotrading_v1.yaml`

This goal encodes:
- seed universe grounded in recent realized strategy picks
- primary daily contextual lane
- secondary mon-fri specialty lane
- fast-veto as a tunable layer
- OpenDart as slow qual veto/review
- manual-draft-only risk posture
- promotion gates favoring sample thickness and bad-week suppression

---

## Immediate next branches

### Branch A — make `/goal` executable end-to-end
- add a real runner that consumes the richer goal YAML
- map `strategy.name` to current research/backtest pipeline pieces
- emit report/policy artifacts from one command

### Branch B — build dynamic universe support
- current loader requires `universe.symbols`
- add support for liquidity-based daily universe snapshots
- keep seed-symbol fallback for reproducibility

### Branch C — tune fast veto per cycle
- daily contextual veto frontier
- mon-fri veto frontier
- compare bad-week reduction vs alpha damage

### Branch D — wire slow qual lane
- connect OpenDart event cache
- classify negative/review-required events
- fail-closed only when candidate orders exist

### Branch E — promotion ladder
- backtest
- paper ledger
- shadow-live reconciliation
- capped live

---

## Practical verdict

**Verdict: NEXT**

This repo already has the right skeleton for a profitable research harness, but the profit path should be:
- **quant first**,
- **external info as veto/review**,
- **cycle-specific gating**,
- **goal-driven repeatable execution**,
- **promotion by evidence, not excitement**.

If you want the fastest path to real money-making probability, the next best implementation step is:

**make `goals/profit_compound_autotrading_v1.yaml` actually executable through one runner command, then tune the daily engine and mon-fri submode under one shared reporting format.**
