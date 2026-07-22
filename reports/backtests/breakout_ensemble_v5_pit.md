# Breakout Ensemble v4 Realistic (GPU/MPS)

- Device: `mps`
- Total elapsed: `623.06s`
- Best overall: `ensemble_consensus3|h10|k20`
- Best ensemble: `ensemble_consensus3|h10|k20`
- Promotion: `BLOCKED` (true PIT membership and corporate actions unresolved)
- Position cap: `5%`; dynamic ADV/spread/impact costs enabled
- Bootstrap 5000×: included

| Config | Train Sharpe | Train Cum | Train MDD | Test Sharpe | Test Cum | Test MDD | Boot>0% |
|---|---:|---:|---:|---:|---:|---:|---:|
| ensemble_consensus3|h10|k20 | -1.281 | -61.6% | -66.0% | -0.656 | -33.0% | -43.6% | 17.0% |
| ensemble_top3_uncorrelated|h10|k10 | -1.343 | -39.8% | -42.1% | -1.867 | -33.0% | -32.9% | 0.9% |
| ensemble_mean_all|h10|k5 | -1.564 | -28.2% | -27.8% | -2.727 | -46.1% | -46.5% | 0.0% |
| vol_scaled_breakout|h10|k5 | -1.605 | -34.5% | -36.7% | -0.224 | -6.1% | -21.0% | 36.4% |
| ensemble_top3_uncorrelated|h10|k5 | -1.639 | -27.7% | -28.7% | -1.829 | -17.0% | -16.1% | 1.2% |
| ensemble_top3_uncorrelated|h5|k5 | -1.648 | -26.0% | -26.3% | -2.725 | -28.2% | -29.3% | 0.1% |
| torch_mlp_mps|h10|k10 | -1.664 | -68.0% | -70.6% | -2.744 | -66.3% | -65.2% | 0.1% |
| ensemble_consensus3|h10|k10 | -1.721 | -47.5% | -50.5% | -1.034 | -27.2% | -31.2% | 8.4% |
| ensemble_top3_uncorrelated|h5|k10 | -1.750 | -44.6% | -45.8% | -2.302 | -39.8% | -41.8% | 0.2% |
| ensemble_consensus3|h10|k5 | -1.770 | -28.9% | -30.3% | -0.501 | -8.6% | -15.7% | 25.9% |
| torch_mlp_mps|h10|k5 | -1.843 | -58.9% | -59.4% | -3.819 | -61.1% | -60.1% | 0.0% |
| donchian55|h10|k5 | -1.893 | -53.0% | -51.3% | -0.981 | -22.3% | -32.4% | 11.0% |
| ensemble_top3_uncorrelated|h10|k20 | -1.906 | -73.4% | -73.3% | -2.002 | -55.0% | -55.5% | 0.7% |
| deep_mlp_mps|h10|k20 | -1.941 | -86.0% | -86.7% | -1.714 | -73.0% | -75.7% | 0.9% |
| torch_mlp_mps|h5|k5 | -2.011 | -64.0% | -65.3% | -4.172 | -63.0% | -62.8% | 0.0% |
| ensemble_consensus3|h5|k20 | -2.042 | -73.3% | -75.0% | -1.111 | -41.1% | -45.7% | 6.2% |
| deep_mlp_mps|h10|k10 | -2.089 | -67.8% | -69.0% | -1.706 | -46.1% | -47.8% | 1.3% |
| mean_reversion_20|h10|k5 | -2.114 | -61.4% | -62.8% | -3.283 | -50.5% | -50.1% | 0.0% |
| torch_mlp_mps|h10|k20 | -2.155 | -87.8% | -88.8% | -2.142 | -77.7% | -77.1% | 0.1% |
| deep_mlp_mps|h10|k5 | -2.204 | -57.3% | -58.9% | -2.358 | -31.8% | -31.8% | 0.1% |
