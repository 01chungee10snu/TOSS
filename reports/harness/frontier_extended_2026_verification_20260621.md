# Frontier extended 2026 verification — 2026-06-21

Paper/research only. live_order_submitted: False.

## Panel
{
  "path": "/Users/01chungee10/Github/TOSS/reports/backtests/random500_seed20260607_2022-01-01_2026-latest_ohlcv_panel.csv",
  "rows": 506152,
  "codes": 496,
  "start": "2022-01-04",
  "end": "2026-06-19"
}

## Cost stress
|   cost_bps |   total_return_pct |   max_drawdown_pct |   sharpe_ratio |   win_rate_pct |   total_trades |   final_equity_krw |   total_cost_krw |
|-----------:|-------------------:|-------------------:|---------------:|---------------:|---------------:|-------------------:|-----------------:|
|          0 |              55.29 |             -10.14 |         2.5896 |          46.05 |            152 |        1.55289e+06 |              0   |
|         10 |              52.19 |             -10.41 |         2.456  |          46.05 |            152 |        1.52193e+06 |          30952.9 |
|         20 |              49.1  |             -10.67 |         2.3216 |          45.39 |            152 |        1.49098e+06 |          61905.8 |
|         30 |              46    |             -10.95 |         2.1865 |          45.39 |            152 |        1.46003e+06 |          92858.7 |

## Yearly split 0bps
|   year |   cost_bps |   total_return_pct |   max_drawdown_pct |   sharpe_ratio |   win_rate_pct |   total_trades |   final_equity_krw |   total_cost_krw |
|-------:|-----------:|-------------------:|-------------------:|---------------:|---------------:|---------------:|-------------------:|-----------------:|
|   2022 |          0 |              13.97 |              -3.77 |         3.5556 |          59.26 |             27 |        1.13969e+06 |                0 |
|   2023 |          0 |              -2.05 |             -11.26 |        -0.2524 |          34.21 |             38 |   979500           |                0 |
|   2024 |          0 |               2.6  |              -7.45 |         0.772  |          37.93 |             29 |        1.02603e+06 |                0 |
|   2025 |          0 |              97.45 |              -4.19 |         2.6061 |          43.59 |             39 |        1.97446e+06 |                0 |
|   2026 |          0 |               2.54 |              -9.58 |         0.9163 |          35    |             20 |        1.02538e+06 |                0 |

## Yearly split 30bps
|   year |   cost_bps |   total_return_pct |   max_drawdown_pct |   sharpe_ratio |   win_rate_pct |   total_trades |   final_equity_krw |   total_cost_krw |
|-------:|-----------:|-------------------:|-------------------:|---------------:|---------------:|---------------:|-------------------:|-----------------:|
|   2022 |         30 |              12.43 |              -4.12 |         3.1853 |          59.26 |             27 |        1.12431e+06 |          16628.8 |
|   2023 |         30 |              -4.2  |             -12.2  |        -0.6598 |          34.21 |             38 |   958017           |          22725.3 |
|   2024 |         30 |               0.97 |              -8    |         0.3532 |          37.93 |             29 |        1.00972e+06 |          17448.2 |
|   2025 |         30 |              94.93 |              -4.52 |         2.5718 |          43.59 |             39 |        1.94934e+06 |          26323.4 |
|   2026 |         30 |               1.33 |             -10.28 |         0.5769 |          35    |             20 |        1.0133e+06  |          12076.1 |

## Buy-hold benchmarks
| benchmark                   |   n |   return_pct |   final_equity_1m |   mdd_pct |   sharpe |   median_stock_return_pct |   win_rate_pct |
|:----------------------------|----:|-------------:|------------------:|----------:|---------:|--------------------------:|---------------:|
| all_start_available         | 429 |     592.239  |           6922389 |  -29.9233 |   0.7039 |                  -41.3078 |        26.8065 |
| end_volume_positive         | 402 |      19.955  |           1199550 |  -30.1427 |   0.3011 |                  -39.0656 |        27.3632 |
| exclude_000300_only         | 428 |      16.8954 |           1168954 |  -29.9096 |   0.2746 |                  -41.4153 |        26.6355 |
| exclude_return_over_1000pct | 423 |      -8.1353 |            918647 |  -35.9731 |   0.016  |                  -42.3628 |        25.7683 |

## Artifacts
- cost_csv: `/Users/01chungee10/Github/TOSS/reports/harness/cost_stress_20260620T181201Z.csv`
- yearly_0_csv: `/Users/01chungee10/Github/TOSS/reports/harness/yearly_split_20260620T181225Z.csv`
- yearly_30_csv: `/Users/01chungee10/Github/TOSS/reports/harness/yearly_split_20260620T181249Z.csv`
- trades_csv: `/Users/01chungee10/Github/TOSS/reports/harness/frontier_extended_2026_all_trades_20260621.csv`
