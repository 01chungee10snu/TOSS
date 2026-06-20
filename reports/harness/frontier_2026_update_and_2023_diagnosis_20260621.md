# TOSS frontier 2026 update + 2023 diagnosis — 2026-06-21

Paper/research only. live_order_submitted: False.

## 1. 2026 latest data update

- Source: yfinance via same random500 universe mapping.
- Base panel: `reports/backtests/random500_seed20260607_2022-01-01_2025-12-31_ohlcv_panel.csv`
- Extended panel: `reports/backtests/random500_seed20260607_2022-01-01_2026-latest_ohlcv_panel.csv`
- Status JSON: `reports/backtests/random500_seed20260607_2026_update_status.json`
- Download result: 496 / 496 PASS
- Added rows: 56,048
- Combined rows: 506,152
- Combined codes: 496
- Combined date range: 2022-01-04 ~ 2026-06-19

## 2. Current frontier config

```json
{
  "rebalance_mode": "hold_until_exit",
  "step": 5,
  "score_threshold": 55,
  "stop_loss_pct": 0.12,
  "take_profit_pct": 0.20,
  "max_holding_steps": 10,
  "max_positions": 4,
  "trailing_stop_pct": 0.0,
  "sizing_mode": "flat"
}
```

## 3. Extended 2022-2026 verification

Cost stress on extended panel:

- 0bps: return 55.29%, MDD -10.14%, Sharpe 2.5896, trades 152
- 10bps: return 52.19%, MDD -10.41%, Sharpe 2.4560, trades 152
- 20bps: return 49.10%, MDD -10.67%, Sharpe 2.3216, trades 152
- 30bps: return 46.00%, MDD -10.95%, Sharpe 2.1865, trades 152

Yearly split at 30bps:

- 2022: return 12.43%, MDD -4.12%, Sharpe 3.1853, trades 27
- 2023: return -4.20%, MDD -12.20%, Sharpe -0.6598, trades 38
- 2024: return 0.97%, MDD -8.00%, Sharpe 0.3532, trades 29
- 2025: return 94.93%, MDD -4.52%, Sharpe 2.5718, trades 39
- 2026 YTD: return 1.33%, MDD -10.28%, Sharpe 0.5769, trades 20

## 4. Buy-and-hold benchmark on extended panel

- Raw unfiltered B&H: +592.24%, but dominated by `000300` abnormal/corporate-action-like outlier.
- End-volume-positive B&H: +19.96%, MDD -30.14%, Sharpe 0.3011
- Exclude `000300` only: +16.90%, MDD -29.91%, Sharpe 0.2746
- Exclude >1000% return outliers: -8.14%, MDD -35.97%, Sharpe 0.0160

Interpretation: current strategy remains better than practical/filtered B&H on return, MDD, and Sharpe.

## 5. 2023 diagnosis

Independent 2023 run at 0bps:

- return -2.05%, MDD -11.26%, Sharpe -0.2524, trades 38

Cost stress on 2023-only panel:

- 0bps: -2.05%
- 10bps: -2.77%
- 20bps: -3.48%
- 30bps: -4.20%

Exit reason decomposition, 2023-only 0bps:

- stop_loss: 13 trades, pnl -187,354.86 KRW, avg -14.41%
- regime_risk_off: 16 trades, pnl -17,375.61 KRW, avg -1.09%
- end_of_replay: 4 trades, pnl +13,915.52 KRW
- take_profit: 5 trades, pnl +165,900.95 KRW, avg +33.18%

Main failure mode: 2023 is not a broad small-loss problem. It is mostly stop-loss tail damage.

## 6. Minimal filter probe

Tested quick variants on extended panel at 30bps:

| config | full return | full Sharpe | full MDD | 2023 return | 2026 YTD return |
|---|---:|---:|---:|---:|---:|
| base sl12 t55 | 46.00% | 2.1865 | -10.95% | -4.20% | 1.33% |
| sl8 t55 | 38.63% | 1.8666 | -10.88% | -3.30% | 0.73% |
| t60 sl12 | 46.00% | 2.1865 | -10.95% | -4.20% | 1.33% |
| t60 sl8 | 38.63% | 1.8666 | -10.88% | -3.30% | 0.73% |

Interpretation:

- Tightening stop from 12% to 8% improves 2023 only slightly (-4.20% → -3.30%) but hurts full return materially (46.00% → 38.63%).
- Raising threshold 55 → 60 had no effect in this probe, implying selected candidates are already above 60 or threshold is not the binding gate.
- Simple tighter stop is not the best defense.

## 7. Partial quick-sweep follow-up

The interrupted quick sweep emitted useful partial results. Promising 2023-defense candidates were verified on the extended 2022-2026 panel at 30bps:

- base `t55_sl12_mp4`: full +46.00%, MDD -10.95%, Sharpe 2.1865, 2023 -4.20%, 2026 YTD +1.33%
- `t65_sl8_mp3`: full +32.14%, MDD -7.49%, Sharpe 1.7727, 2023 +7.58%, 2026 YTD -6.10%
- `t65_sl10_mp3`: full +25.98%, MDD -9.98%, Sharpe 1.5209, 2023 +5.84%, 2026 YTD -6.10%
- `t65_sl8_mp4`: full +37.24%, MDD -10.99%, Sharpe 1.8056, 2023 +4.60%, 2026 YTD -2.07%

Interpretation: threshold 65 + tighter stop + lower max_positions can fix 2023, but it fails 2026 YTD and reduces full-period return. It is not a better deployable policy; it exposes a robustness trade-off.

Evidence CSV: `reports/harness/frontier_promising_2023_defense_verified_minimal_20260621.csv`

## 8. Verdict

Current frontier remains the best tested candidate by full-period return and practical B&H comparison, including 2026 YTD extension.

However it is still not a promoted live policy because:

- 2023 remains negative under costs for the high-return base policy.
- 2023-defense variants become negative in 2026 YTD and reduce full-period return.
- 2026 YTD is positive but weak for the base policy and negative for the stricter defense variants.
- 2025 still contributes most of the edge.
- Simple stop/threshold/max-position retuning exposes a trade-off, not a solved robust policy.

Status: SALVAGE / NEXT, not PROMOTE.

## 8. Next required search

Do not keep retuning simple stop/threshold only. Next candidates should target tail-risk before entry:

1. Pre-entry volatility spike filter.
2. Recent drawdown / breakdown filter before buying.
3. Market breadth or regime filter to reduce 2023 stop-loss entries.
4. Liquidity quality filter beyond simple latest volume.
5. Score-gap / rank persistence filter: only enter names that remain top-ranked across multiple 5-day checks.
6. Walk-forward validation: tune on 2022-2024, evaluate 2025-2026 YTD.

## Evidence artifacts

- `reports/harness/replay_frontier_2023_loss_diagnosis_20260621.md`
- `reports/harness/replay_frontier_2023_independent_diagnosis_20260621.md`
- `reports/harness/frontier_extended_2026_verification_20260621.md`
- `reports/harness/frontier_minimal_filter_probe_20260621.csv`
- `reports/harness/frontier_2026_update_and_2023_diagnosis_20260621.md`
