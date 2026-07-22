# Breakout Ensemble v1

- Best overall: `momentum20_baseline|h5|k10`
- Best ensemble: `ensemble_median|h5|k5`
- Verdict: `BLOCKED_RESEARCH_ONLY`

| Config | Train Sharpe | Train Cum | Train MDD | Test Sharpe | Test Cum | Test MDD |
|---|---:|---:|---:|---:|---:|---:|
| momentum20_baseline|h10|k5 | 1.484 | 633.9% | -45.6% | 1.465 | 225.0% | -47.6% |
| momentum20_baseline|h5|k5 | 1.388 | 435.7% | -41.1% | 1.700 | 305.4% | -49.7% |
| momentum20_baseline|h5|k10 | 1.002 | 132.4% | -33.3% | 2.017 | 328.8% | -45.0% |
| momentum20_baseline|h10|k10 | 0.986 | 135.1% | -33.7% | 1.918 | 299.8% | -38.7% |
| momentum20_baseline|h5|k20 | 0.600 | 42.5% | -33.4% | 1.864 | 211.9% | -37.4% |
| momentum20_baseline|h10|k20 | 0.594 | 44.3% | -30.9% | 1.908 | 211.0% | -33.3% |
| volume_breakout|h5|k5 | 0.473 | 31.2% | -25.2% | 0.288 | 0.6% | -50.0% |
| ensemble_median|h5|k5 | 0.395 | 22.5% | -29.1% | 0.610 | 31.4% | -42.8% |
| ensemble_mean|h5|k5 | 0.368 | 19.6% | -47.3% | 0.612 | 31.4% | -44.2% |
| bollinger_breakout|h5|k5 | 0.367 | 19.9% | -38.2% | 0.058 | -18.5% | -48.1% |
| donchian20|h5|k5 | 0.322 | 14.7% | -29.9% | 0.578 | 27.9% | -42.1% |
| donchian55|h10|k20 | 0.172 | 3.5% | -14.5% | 1.053 | 60.9% | -19.1% |
| ensemble_mean|h10|k5 | 0.153 | -0.0% | -26.7% | 0.752 | 43.7% | -26.1% |
| donchian55|h5|k5 | 0.095 | -5.8% | -55.2% | 0.108 | -11.7% | -46.7% |
| ensemble_consensus|h5|k5 | 0.090 | -7.3% | -58.0% | 0.178 | -8.0% | -48.1% |
