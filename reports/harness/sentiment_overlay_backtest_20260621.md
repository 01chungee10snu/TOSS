# Sentiment overlay backtest — 2026-06-21

Paper/research only. live_order_submitted: False.

## Method
- News sentiment from KLUE-RoBERTa on Google News RSS titles (490 symbols).
- Forward-filled 30-day lookback sentiment_map.
- Overlay modes: penalty (base + alpha * sentiment), rerank.
- Tested on 2025 and 2026 (sentiment data coverage).

## Files
- results: `/Users/01chungee10/Github/TOSS/reports/harness/sentiment_overlay_backtest_20260621.csv`

## Results
|   total_return_pct |   max_drawdown_pct |   total_trades |   winning_trades |   win_rate_pct |   sharpe_ratio |   final_equity_krw |   initial_cash_krw |   transaction_cost_bps |   total_cost_krw | candidate              |   year | overlay   |
|-------------------:|-------------------:|---------------:|-----------------:|---------------:|---------------:|-------------------:|-------------------:|-----------------------:|-----------------:|:-----------------------|-------:|:----------|
|              94.93 |              -4.52 |             39 |               17 |          43.59 |         2.5718 |        1.94934e+06 |            1000000 |                     30 |         26323.4  | canonical_base         |   2025 | none      |
|              93.94 |              -5.44 |             41 |               19 |          46.34 |         2.5565 |        1.93939e+06 |            1000000 |                     30 |         27497.1  | sentiment_penalty_a5   |   2025 | penalty   |
|              89.72 |              -5.75 |             40 |               17 |          42.5  |         2.4905 |        1.89722e+06 |            1000000 |                     30 |         26768.4  | sentiment_penalty_a10  |   2025 | penalty   |
|              89.72 |              -5.75 |             40 |               17 |          42.5  |         2.4905 |        1.89722e+06 |            1000000 |                     30 |         26768.4  | sentiment_penalty_a20  |   2025 | penalty   |
|              89.72 |              -5.75 |             40 |               17 |          42.5  |         2.4905 |        1.89722e+06 |            1000000 |                     30 |         26768.4  | sentiment_penalty_a50  |   2025 | penalty   |
|              90.84 |              -4.5  |             37 |               17 |          45.95 |         2.5083 |        1.9084e+06  |            1000000 |                     30 |         24996.6  | sentiment_hybrid_a0p25 |   2025 | hybrid    |
|              98.09 |              -1.99 |             39 |               19 |          48.72 |         2.6205 |        1.98091e+06 |            1000000 |                     30 |         26418.4  | sentiment_hybrid_a0p5  |   2025 | hybrid    |
|              96.81 |              -2.32 |             39 |               20 |          51.28 |         2.6009 |        1.96807e+06 |            1000000 |                     30 |         26379.7  | sentiment_hybrid_a1p0  |   2025 | hybrid    |
|              89.31 |              -5.19 |             39 |               17 |          43.59 |         2.4837 |        1.89306e+06 |            1000000 |                     30 |         26154    | sentiment_hybrid_a2p0  |   2025 | hybrid    |
|              94.42 |              -4.41 |             36 |               16 |          44.44 |         2.5638 |        1.9442e+06  |            1000000 |                     30 |         24502.4  | sentiment_rerank       |   2025 | rerank    |
|               1.33 |             -10.28 |             20 |                7 |          35    |         0.5769 |        1.0133e+06  |            1000000 |                     30 |         12076.1  | canonical_base         |   2026 | none      |
|               2.24 |              -6.8  |             14 |                6 |          42.86 |         0.8881 |        1.02245e+06 |            1000000 |                     30 |          8492.83 | sentiment_penalty_a5   |   2026 | penalty   |
|               5.2  |              -3.94 |             15 |                6 |          40    |         2.0768 |        1.05202e+06 |            1000000 |                     30 |          9183.61 | sentiment_penalty_a10  |   2026 | penalty   |
|               5.2  |              -3.94 |             15 |                6 |          40    |         2.0768 |        1.05202e+06 |            1000000 |                     30 |          9183.61 | sentiment_penalty_a20  |   2026 | penalty   |
|               5.2  |              -3.94 |             15 |                6 |          40    |         2.0768 |        1.05202e+06 |            1000000 |                     30 |          9183.61 | sentiment_penalty_a50  |   2026 | penalty   |
|               7.89 |              -7.55 |             13 |                5 |          38.46 |         2.416  |        1.07892e+06 |            1000000 |                     30 |          8060.94 | sentiment_hybrid_a0p25 |   2026 | hybrid    |
|              12.9  |              -5.82 |             14 |                7 |          50    |         4.2614 |        1.12898e+06 |            1000000 |                     30 |          8813.38 | sentiment_hybrid_a0p5  |   2026 | hybrid    |
|               6.21 |              -7.57 |             14 |                6 |          42.86 |         2.1024 |        1.06214e+06 |            1000000 |                     30 |          8612.27 | sentiment_hybrid_a1p0  |   2026 | hybrid    |
|               6.93 |              -5.52 |             13 |                6 |          46.15 |         2.9277 |        1.06932e+06 |            1000000 |                     30 |          8032.07 | sentiment_hybrid_a2p0  |   2026 | hybrid    |
|               9.42 |              -9.6  |             20 |               11 |          55    |         3.3364 |        1.09415e+06 |            1000000 |                     30 |         12319.4  | sentiment_rerank       |   2026 | rerank    |
