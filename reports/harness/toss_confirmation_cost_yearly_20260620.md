# TOSS confirmation loop — cost stress and yearly split — 2026-06-20

Paper/research only. live_order_submitted: False.

## Candidate under verification
- config: `s5_t55_sl12_tp20_h10_mp4_tr0_flat`
- step: 5
- score_threshold: 55
- stop_loss: 12%
- take_profit: 20%
- holding_steps: 10
- max_positions: 4
- trailing_stop: disabled
- sizing: flat

## Cost stress
- 0bps: return=49.20%, MDD=-10.14%, Sharpe=2.5876, trades=142, costs=0 KRW
- 10bps: return=46.36%, MDD=-10.41%, Sharpe=2.4493, trades=142, costs=28,878 KRW
- 20bps: return=43.51%, MDD=-10.67%, Sharpe=2.3101, trades=142, costs=57,757 KRW
- 30bps: return=40.66%, MDD=-10.95%, Sharpe=2.1703, trades=142, costs=86,635 KRW


## Yearly split — 0bps
- 2022: return=13.97%, MDD=-3.77%, Sharpe=3.5556, trades=27
- 2023: return=-2.05%, MDD=-11.26%, Sharpe=-0.2524, trades=38
- 2024: return=2.60%, MDD=-7.45%, Sharpe=0.7720, trades=29
- 2025: return=97.45%, MDD=-4.19%, Sharpe=2.6061, trades=39


## Yearly split — 20bps
- 2022: return=12.94%, MDD=-4.00%, Sharpe=3.3094, trades=27, costs=11,086 KRW
- 2023: return=-3.48%, MDD=-11.88%, Sharpe=-0.5240, trades=38, costs=15,150 KRW
- 2024: return=1.52%, MDD=-7.82%, Sharpe=0.4940, trades=29, costs=11,632 KRW
- 2025: return=95.77%, MDD=-4.41%, Sharpe=2.5833, trades=39, costs=17,549 KRW


## Yearly split — 30bps
- 2022: return=12.43%, MDD=-4.12%, Sharpe=3.1853, trades=27, costs=16,629 KRW
- 2023: return=-4.20%, MDD=-12.20%, Sharpe=-0.6598, trades=38, costs=22,725 KRW
- 2024: return=0.97%, MDD=-8.00%, Sharpe=0.3532, trades=29, costs=17,448 KRW
- 2025: return=94.93%, MDD=-4.52%, Sharpe=2.5718, trades=39, costs=26,323 KRW


## Verdict
- Cost stress: PASS. Even at 30bps, full-period return remains +40.66% and Sharpe 2.17.
- Year consistency: FAIL / NEEDS FILTER. 2023 is negative under every cost assumption.
- Concentration warning: 2025 contributes most of the total edge (+94% yearly), so the candidate is not promotion-ready as a fixed policy.
- Current status: SALVAGE / NEXT, not PROMOTE.

## Next required loop
1. Diagnose 2023 bad trades by exit reason/symbol/regime.
2. Add regime/market filter or year-robustness gate.
3. Re-run cost stress after suppressing 2023 failure mode.

## Evidence files
- reports/verify/cost_stress_20260620T082928Z.csv
- reports/verify/yearly_split_20260620T082945Z.csv
- reports/verify/yearly_split_20260620T083003Z.csv
- reports/verify/yearly_split_20260620T083034Z.csv
