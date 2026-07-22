# Breakout Ensemble v4 Realistic (GPU/MPS)

- Device: `mps`
- Total elapsed: `185.05s`
- Best overall: `torch_mlp_mps|h10|k5`
- Best ensemble: `ensemble_top3_uncorrelated|h10|k5`
- Promotion: `BLOCKED` (true PIT membership and corporate actions unresolved)
- Position cap: `5%`; dynamic ADV/spread/impact costs enabled
- Bootstrap 5000×: included

| Config | Train Sharpe | Train Cum | Train MDD | Test Sharpe | Test Cum | Test MDD | Boot>0% |
|---|---:|---:|---:|---:|---:|---:|---:|
| torch_mlp_mps|h10|k5 | 0.150 | 2.9% | -12.4% | -0.015 | -2.2% | -19.0% | 42.3% |
| ensemble_top3_uncorrelated|h10|k5 | -0.218 | -5.6% | -10.4% | 0.022 | -0.2% | -7.3% | 48.4% |
| torch_mlp_mps|h10|k10 | -0.378 | -19.4% | -19.6% | -0.037 | -3.8% | -25.0% | 41.6% |
| torch_mlp_mps|h5|k5 | -0.449 | -15.0% | -18.5% | -0.984 | -14.2% | -18.6% | 10.1% |
| ensemble_top3_uncorrelated|h10|k10 | -0.494 | -18.1% | -20.3% | 0.478 | 8.8% | -12.8% | 68.9% |
| torch_mlp_mps|h10|k20 | -0.627 | -48.6% | -46.9% | -0.024 | -8.1% | -38.0% | 40.1% |
| ensemble_consensus3|h10|k10 | -0.662 | -23.0% | -23.9% | -0.300 | -7.5% | -15.8% | 33.3% |
| torch_mlp_mps|h5|k10 | -0.696 | -31.0% | -30.3% | -1.513 | -30.4% | -33.4% | 2.6% |
| attn_net_mps|h10|k10 | -0.766 | -31.9% | -30.4% | -0.869 | -19.9% | -24.8% | 12.3% |
| attn_net_mps|h5|k5 | -0.808 | -22.2% | -22.2% | -0.610 | -8.3% | -13.6% | 20.4% |
| attn_net_mps|h5|k10 | -0.839 | -37.8% | -36.4% | -0.664 | -17.4% | -26.2% | 17.6% |
| mean_reversion_20|h10|k10 | -0.862 | -39.8% | -39.8% | -2.017 | -41.8% | -40.9% | 0.6% |
| ensemble_consensus3|h10|k5 | -0.879 | -16.5% | -17.5% | -1.198 | -15.5% | -15.6% | 6.8% |
| attn_net_mps|h10|k5 | -0.894 | -22.2% | -21.0% | -0.819 | -10.8% | -14.2% | 14.5% |
| torch_mlp_mps|h5|k20 | -0.955 | -60.2% | -58.8% | -1.339 | -46.7% | -47.3% | 3.8% |
| ensemble_consensus3|h5|k5 | -0.957 | -19.0% | -22.5% | -1.403 | -17.4% | -17.2% | 4.3% |
| deep_mlp_mps|h10|k5 | -0.958 | -28.3% | -26.7% | -0.514 | -12.4% | -22.0% | 22.7% |
| mean_reversion_20|h10|k20 | -0.967 | -65.5% | -65.0% | -1.242 | -52.0% | -51.6% | 4.4% |
| attn_net_mps|h10|k20 | -1.018 | -61.4% | -60.6% | -0.769 | -33.4% | -38.9% | 13.4% |
| ensemble_mean_all|h10|k5 | -1.023 | -21.0% | -19.9% | 0.407 | 5.8% | -10.9% | 66.7% |
