# Current-Issue and Intraday BUY Gate Change — 2026-07-15

## Scope
- News lookback reduced from 36h to 12h.
- Undated headlines excluded from scoring.
- Publisher-normalized duplicate suppression added.
- Risk score now decays by age: 0–3h 1.0, 3–6h 0.75, 6–12h 0.5.
- `high`/`critical` news can be resolved only by fresh two-proxy risk-on confirmation; `critical` uses stricter thresholds.
- Live-submit current-issue and prior-day regime gates consume the unified override evidence.
- Inverse sleeve preserves ordinary long orders for `LONG_BUY` and replaces only for `INVERSE_BUY`.

## Dynamic momentum-entry chase policy
- Normal, stale, conflicted, or non-`LONG_BUY` conditions retain the 2% chase ceiling.
- Fresh two-proxy `LONG_BUY + risk_on` confirmation uses `min(10%, max(2%, market_day_return + 1.5%p))`.
- The absolute 10% ceiling, spread ceiling, whole-share sizing, and original-notional cap remain enforced.
- The adaptive audit records base, hard, effective cap, market return, and whether the risk-on expansion was active.

## Runtime verification
- Current issue report: `severity=low`, `risk_score=0.0`, `buy_gate=allow`.
- 2026-07-15 12:03 KST loop: `LONG_BUY`, `market_override_confirmed=true`, market return `7.85%`.
- Effective chase ceiling: `9.35%`.
- `066430`: chase `3.91%`, adapted to 2,075 KRW × 26 shares.
- `018880`: chase `3.68%`, adapted to 3,525 KRW × 15 shares.
- `308080`: chase `8.86%`, adapted to 5,650 KRW × 9 shares.
- All three passed the new chase rule without increasing original order notional.
- This replay did not submit broker orders; liquidity/fill/promoted-policy gates remain separate.

## Automated verification
- Full suite: 331 passed.
- Focused suite: 58 passed.
- Strategic live decision audit: PASS, 42 checks, 0 failures.
