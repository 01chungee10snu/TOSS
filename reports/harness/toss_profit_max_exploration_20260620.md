# TOSS profit-max exploration evidence — 2026-06-20

Paper/research only. live_order_submitted: False.

## Best by total return
- config: s5_t55_sl12_tp20_h10_mp4_tr0_flat
- total_return_pct: 49.20%
- max_drawdown_pct: -10.14%
- sharpe_ratio: 2.5876
- win_rate_pct: 45.1%
- total_trades: 142
- final_equity_krw: 1,492,045

## Top configs by return
- s5_t55_sl12_tp20_h10_mp4_tr0_flat: ret=49.20%, mdd=-10.14%, sharpe=2.5876, win=45.1%, trades=142
- s8_t55_sl12_tp20_h10_mp4_tr0_flat: ret=17.39%, mdd=-13.98%, sharpe=1.1790, win=45.3%, trades=106
- s4_t55_sl12_tp20_h10_mp4_tr0_flat: ret=11.74%, mdd=-15.39%, sharpe=0.6637, win=39.6%, trades=149
- s3_t55_sl12_tp20_h10_mp4_tr0_flat: ret=2.14%, mdd=-23.62%, sharpe=0.1811, win=37.1%, trades=186
- s6_t55_sl12_tp20_h10_mp4_tr0_flat: ret=-12.02%, mdd=-26.53%, sharpe=-0.5733, win=36.1%, trades=130

## Evidence files
- final cadence comparison: reports/sweep/sweep_20260620T074655Z_comparison.csv
- final cadence report: reports/sweep/sweep_20260620T074655Z.md
- mp4 sl/tp comparison: reports/sweep/sweep_20260620T073459Z_comparison.csv
- mp3/mp4 comparison: reports/sweep/sweep_20260620T071654Z_comparison.csv

## Notes
- Earlier +20.42% result was not promotion-grade after a max_positions cap bug was found and fixed.
- Corrected engine result improved to +49.20% by using max_positions=4, step=5, threshold=55, stop_loss=12%, take_profit=20%, holding_steps=10.
- trailing_stop did not improve the frontier; score_weighted sizing reduced return.
