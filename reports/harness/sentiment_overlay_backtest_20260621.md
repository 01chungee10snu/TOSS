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
|   total_return_pct |   max_drawdown_pct |   total_trades |   winning_trades |   win_rate_pct |   sharpe_ratio |   final_equity_krw |   initial_cash_krw |   transaction_cost_bps |   total_cost_krw | candidate             |   year | overlay   |
|-------------------:|-------------------:|---------------:|-----------------:|---------------:|---------------:|-------------------:|-------------------:|-----------------------:|-----------------:|:----------------------|-------:|:----------|
|              94.93 |              -4.52 |             39 |               17 |          43.59 |         2.5718 |        1.94934e+06 |            1000000 |                     30 |         26323.4  | canonical_base        |   2025 | none      |
|              93.94 |              -5.44 |             41 |               19 |          46.34 |         2.5565 |        1.93939e+06 |            1000000 |                     30 |         27497.1  | sentiment_penalty_a5  |   2025 | penalty   |
|              89.72 |              -5.75 |             40 |               17 |          42.5  |         2.4905 |        1.89722e+06 |            1000000 |                     30 |         26768.4  | sentiment_penalty_a10 |   2025 | penalty   |
|              89.72 |              -5.75 |             40 |               17 |          42.5  |         2.4905 |        1.89722e+06 |            1000000 |                     30 |         26768.4  | sentiment_penalty_a20 |   2025 | penalty   |
|              89.72 |              -5.75 |             40 |               17 |          42.5  |         2.4905 |        1.89722e+06 |            1000000 |                     30 |         26768.4  | sentiment_penalty_a50 |   2025 | penalty   |
|              94.42 |              -4.41 |             36 |               16 |          44.44 |         2.5638 |        1.9442e+06  |            1000000 |                     30 |         24502.4  | sentiment_rerank      |   2025 | rerank    |
|               1.33 |             -10.28 |             20 |                7 |          35    |         0.5769 |        1.0133e+06  |            1000000 |                     30 |         12076.1  | canonical_base        |   2026 | none      |
|               2.24 |              -6.8  |             14 |                6 |          42.86 |         0.8881 |        1.02245e+06 |            1000000 |                     30 |          8492.83 | sentiment_penalty_a5  |   2026 | penalty   |
|               5.2  |              -3.94 |             15 |                6 |          40    |         2.0768 |        1.05202e+06 |            1000000 |                     30 |          9183.61 | sentiment_penalty_a10 |   2026 | penalty   |
|               5.2  |              -3.94 |             15 |                6 |          40    |         2.0768 |        1.05202e+06 |            1000000 |                     30 |          9183.61 | sentiment_penalty_a20 |   2026 | penalty   |
|               5.2  |              -3.94 |             15 |                6 |          40    |         2.0768 |        1.05202e+06 |            1000000 |                     30 |          9183.61 | sentiment_penalty_a50 |   2026 | penalty   |
|               9.42 |              -9.6  |             20 |               11 |          55    |         3.3364 |        1.09415e+06 |            1000000 |                     30 |         12319.4  | sentiment_rerank      |   2026 | rerank    |
