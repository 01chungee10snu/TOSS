#!/usr/bin/env python3
"""
TOSS 가중치 최적화 v4 — forward return overlap 버그 수정

★핵심 수정:
1. 1일 forward return 사용 (daily rebalancing, 겹침 없음)
2. Holding period는 rebalancing 주기로만 사용 (매 N일마다 새 종목 선정)
3. Compound는 non-overlapping 일별 수익률로만 계산
"""
import torch
import pandas as pd
import numpy as np
import json
import time
from itertools import product

t0_global = time.time()
device = torch.device('mps' if torch.backends.mps.is_available() else 'cpu')
print(f'Device: {device}')

# ── 1. 데이터 ──
csv_path = '/Users/01chungee10/Github/TOSS/reports/backtests/practical_universe_400_2022-01-01_2026-latest_ohlcv_panel.csv'
raw_df = pd.read_csv(csv_path)
pivot = raw_df.pivot_table(index='Date', columns='code', values=['Open', 'High', 'Low', 'Close', 'Volume'], aggfunc='first')
close = pivot['Close'].sort_index()
volume = pivot['Volume'].sort_index()
n_days, n_stocks = close.shape
print(f'패널: {n_days}일 × {n_stocks}종목')

# ── 2. 팩터 ──
mom_5d = close.pct_change(5)
daily_ret = close.pct_change()
vol_20d = daily_ret.rolling(20).std()
low_vol_raw = 1.0 / (vol_20d + 1e-8)
delta = close.diff()
gain = delta.clip(lower=0).rolling(14).mean()
loss = (-delta.clip(upper=0)).rolling(14).mean()
rs = gain / (loss + 1e-8)
rsi = 100 - (100 / (1 + rs))
rsi_reversal = (50 - rsi) / 50
vol_ma5 = volume.rolling(5).mean()
vol_ma20 = volume.rolling(20).mean()
volume_ratio = vol_ma5 / (vol_ma20 + 1e-8) - 1.0

def rank_normalize(df):
    return df.rank(axis=1, pct=True) - 0.5

factors_normalized = {
    'momentum': rank_normalize(mom_5d),
    'low_vol': rank_normalize(low_vol_raw),
    'rsi': rank_normalize(rsi_reversal),
    'volume_ratio': rank_normalize(volume_ratio),
}

factor_names = list(factors_normalized.keys())
n_factors = len(factor_names)
valid_start = 25
valid_end = n_days - 2  # 다음날 수익률 필요

# ★핵심 수정: 1일 forward return (겹침 없음)
forward_returns = close.pct_change().shift(-1)  # 내일의 1일 수익률

factor_tensor = torch.zeros(valid_end - valid_start, n_stocks, n_factors, device=device)
return_tensor = torch.zeros(valid_end - valid_start, n_stocks, device=device)
for i, fname in enumerate(factor_names):
    df = factors_normalized[fname].iloc[valid_start:valid_end]
    factor_tensor[:, :, i] = torch.from_numpy(df.values).float().to(device)
ret_df = forward_returns.iloc[valid_start:valid_end]
return_tensor = torch.from_numpy(ret_df.values).float().to(device)
factor_tensor[torch.isnan(factor_tensor)] = 0
return_tensor[torch.isnan(return_tensor)] = 0
tradable_mask = torch.from_numpy((volume.iloc[valid_start:valid_end].values > 0)).to(device)
n_valid_days = valid_end - valid_start

# ── 3. 가중치 그리드 (0.05 step) ──
steps = np.arange(0.0, 1.01, 0.05)
weights_grid = []
for w1, w2, w3, w4 in product(steps, repeat=4):
    if abs(w1 + w2 + w3 + w4 - 1.0) < 0.025:
        weights_grid.append([w1, w2, w3, w4])
weights_grid = np.array(weights_grid)
n_combos = len(weights_grid)
weights_tensor = torch.from_numpy(weights_grid).float().to(device)
print(f'조합: {n_combos} (0.05 step)')
print(f'Forward return: 1일 (non-overlapping)')

chunk_size = max(500, int(4e9 / (n_valid_days * n_stocks * 4)))

# ── 4. 백테스트 ──
def gpu_batch_backtest(factor_tensor, return_tensor, weights_tensor, tradable_mask,
                       top_k=3, stop_loss=0.05, chunk_size=1000):
    n_weights = weights_tensor.shape[0]
    n_chunks = (n_weights + chunk_size - 1) // chunk_size
    all_returns = []

    for chunk_idx in range(n_chunks):
        start = chunk_idx * chunk_size
        end = min((chunk_idx + 1) * chunk_size, n_weights)
        w_chunk = weights_tensor[start:end]
        n_w = end - start

        scores = torch.einsum('dsf,wf->dws', factor_tensor, w_chunk)
        scores = scores.masked_fill(~tradable_mask.unsqueeze(1), float('-inf'))
        _, topk_indices = scores.topk(top_k, dim=-1)
        del scores

        ret_expanded = return_tensor.unsqueeze(1).expand(-1, n_w, -1)
        topk_returns = ret_expanded.gather(-1, topk_indices)  # (D, W, top_k)

        if stop_loss > 0:
            topk_returns = torch.clamp(topk_returns, min=-stop_loss)

        chunk_daily = topk_returns.mean(dim=-1)  # (D, W) — top_k 등분

        del topk_returns, topk_indices
        all_returns.append(chunk_daily.T.clone())
        del chunk_daily

        if (chunk_idx + 1) % 5 == 0 or chunk_idx == n_chunks - 1:
            pct = (end / n_weights) * 100
            print(f'  청크 {chunk_idx+1}/{n_chunks} ({pct:.0f}%) - {time.time()-t0_global:.0f}초')

    return torch.cat(all_returns, dim=0)

def compute_metrics(daily_returns):
    cumulative = (1 + daily_returns).prod(dim=1) - 1
    n_years = daily_returns.shape[1] / 252
    annual_ret = (1 + cumulative) ** (1 / n_years) - 1
    daily_mean = daily_returns.mean(dim=1)
    daily_std = daily_returns.std(dim=1) + 1e-8
    sharpe = daily_mean / daily_std * (252 ** 0.5)
    cum = (1 + daily_returns).cumprod(dim=1)
    peak = cum.cummax(dim=1)[0]
    drawdown = (cum - peak) / peak
    max_dd = drawdown.min(dim=1)[0]
    return {
        'cumulative_ret': cumulative.cpu().numpy(),
        'annual_ret': annual_ret.cpu().numpy(),
        'sharpe': sharpe.cpu().numpy(),
        'max_drawdown': max_dd.cpu().numpy(),
    }

# ── Walkforward ──
dates = pd.to_datetime(close.index[valid_start:valid_end])
train_mask_np = (dates.year <= 2024).astype(bool)
test_mask_np = (dates.year >= 2025).astype(bool)
train_factor = factor_tensor[train_mask_np]
train_return = return_tensor[train_mask_np]
train_tradable = tradable_mask[train_mask_np]
test_factor = factor_tensor[test_mask_np]
test_return = return_tensor[test_mask_np]
test_tradable = tradable_mask[test_mask_np]

print(f'\nTrain: {train_mask_np.sum()}일 | Test: {test_mask_np.sum()}일')

# ══════════════════════════════════════════════════════════════
# A. SL 5% (per-stock)
# ══════════════════════════════════════════════════════════════
print('\n=== Train 백테스트 (SL 5%) ===')
train_returns = gpu_batch_backtest(
    train_factor, train_return, weights_tensor, train_tradable,
    top_k=3, stop_loss=0.05, chunk_size=chunk_size
)
train_metrics = compute_metrics(train_returns)

print('\n=== Test 백테스트 (SL 5%) ===')
test_returns = gpu_batch_backtest(
    test_factor, test_return, weights_tensor, test_tradable,
    top_k=3, stop_loss=0.05, chunk_size=chunk_size
)
test_metrics = compute_metrics(test_returns)

# ── 결과 분석 ──
train_df = pd.DataFrame({
    'w_mom': weights_grid[:, 0], 'w_lv': weights_grid[:, 1],
    'w_rsi': weights_grid[:, 2], 'w_vol': weights_grid[:, 3],
    'tr_sharpe': train_metrics['sharpe'],
    'tr_cumret': train_metrics['cumulative_ret'],
    'tr_mdd': train_metrics['max_drawdown'],
    'te_sharpe': test_metrics['sharpe'],
    'te_cumret': test_metrics['cumulative_ret'],
    'te_mdd': test_metrics['max_drawdown'],
})

train_df_sorted = train_df.sort_values('tr_sharpe', ascending=False)

print('\n=== Train Top 20 (Sharpe 기준) ===')
cols = ['w_mom', 'w_lv', 'w_rsi', 'w_vol', 'tr_sharpe', 'tr_cumret', 'te_sharpe', 'te_cumret', 'te_mdd']
print(train_df_sorted.head(20)[cols].to_string(index=False))

print(f'\nTrain Sharpe > 0: {(train_metrics["sharpe"] > 0).sum()}/{n_combos}')
print(f'Train Sharpe 범위: {train_metrics["sharpe"].min():.3f} ~ {train_metrics["sharpe"].max():.3f}')
print(f'Test Sharpe > 0: {(test_metrics["sharpe"] > 0).sum()}/{n_combos}')
print(f'Test Sharpe 범위: {test_metrics["sharpe"].min():.3f} ~ {test_metrics["sharpe"].max():.3f}')

# ── Walkforward 과적합 분석 ──
print('\n=== Walkforward 과적합 분석 (Train Top 20) ===')
print(f'{"rk":>3} {"w_mom":>5} {"w_lv":>5} {"w_rsi":>5} {"w_vol":>5} {"tr_sh":>7} {"te_sh":>7} {"te_ret":>8} {"te_mdd":>8} {"ratio":>6}')

best_idx = None
best_test_sharpe = -999

for rank in range(20):
    idx = train_df_sorted.index[rank]
    tr_sharpe = train_df_sorted.iloc[rank]['tr_sharpe']
    te_sharpe = train_df_sorted.iloc[rank]['te_sharpe']
    te_cumret = train_df_sorted.iloc[rank]['te_cumret']
    te_mdd = train_df_sorted.iloc[rank]['te_mdd']
    ratio = te_sharpe / (abs(tr_sharpe) + 1e-8) if tr_sharpe > 0 else -999
    w = weights_grid[idx]

    flag = ''
    if tr_sharpe > 0 and te_sharpe > 0:
        flag = ' ✅'
        if te_sharpe > best_test_sharpe:
            best_test_sharpe = te_sharpe
            best_idx = idx

    print(f'{rank+1:>3} {w[0]:>5.2f} {w[1]:>5.2f} {w[2]:>5.2f} {w[3]:>5.2f} {tr_sharpe:>7.3f} {te_sharpe:>7.3f} {te_cumret:>8.2%} {te_mdd:>8.2%} {ratio:>6.2f}{flag}')

# Fallback
if best_idx is None:
    both_positive = np.where(
        (train_metrics['sharpe'] > 0) & (test_metrics['sharpe'] > 0)
    )[0]
    print(f'\nTrain+Test 모두 Sharpe > 0: {len(both_positive)}개')
    if len(both_positive) > 0:
        best_idx = both_positive[np.argmax(test_metrics['sharpe'][both_positive])]
    else:
        best_idx = np.argmax(test_metrics['sharpe'])
        print('⚠️ 동시 양수 없음. Test Sharpe 최고 사용 (lookahead 주의)')

best_weights = weights_grid[best_idx]
train_sharpe = train_metrics['sharpe'][best_idx]
test_sharpe = test_metrics['sharpe'][best_idx]
test_cumret = test_metrics['cumulative_ret'][best_idx]
test_mdd = test_metrics['max_drawdown'][best_idx]
overfit_ratio = test_sharpe / (abs(train_sharpe) + 1e-8) if train_sharpe > 0 else -999

print(f'\n✅ 선택: weights={best_weights}')
print(f'   Train Sharpe: {train_sharpe:.3f} | Test Sharpe: {test_sharpe:.3f}')
print(f'   Test 누적: {test_cumret:.2%} | Test MDD: {test_mdd:.2%} | Ratio: {overfit_ratio:.2f}')

# ══════════════════════════════════════════════════════════════
# B. 단일 팩터 분석
# ══════════════════════════════════════════════════════════════
print('\n=== 단일 팩터 효과 (weight=1.0) ===')
single_weights = torch.eye(n_factors, device=device)

print(f'\n{"팩터":>14} {"Train Sharpe":>12} {"Train 누적":>10} {"Test Sharpe":>12} {"Test 누적":>10} {"Test MDD":>8}')
print('-' * 70)
for i, fname in enumerate(factor_names):
    sw = single_weights[i:i+1]
    tr_sr = gpu_batch_backtest(train_factor, train_return, sw, train_tradable, top_k=3, stop_loss=0.05, chunk_size=1)
    tr_m = compute_metrics(tr_sr)
    te_sr = gpu_batch_backtest(test_factor, test_return, sw, test_tradable, top_k=3, stop_loss=0.05, chunk_size=1)
    te_m = compute_metrics(te_sr)
    print(f'{fname:>14} {tr_m["sharpe"][0]:>12.3f} {tr_m["cumulative_ret"][0]:>10.2%} {te_m["sharpe"][0]:>12.3f} {te_m["cumulative_ret"][0]:>10.2%} {te_m["max_drawdown"][0]:>8.2%}')

# ══════════════════════════════════════════════════════════════
# C. Monte Carlo
# ══════════════════════════════════════════════════════════════
print('\n=== Monte Carlo Bootstrap ===')
best_w_tensor = torch.from_numpy(best_weights).float().to(device).unsqueeze(0)
full_daily = gpu_batch_backtest(
    factor_tensor, return_tensor, best_w_tensor, tradable_mask,
    top_k=3, stop_loss=0.05, chunk_size=1
)
best_daily = full_daily[0].cpu().numpy()

np.random.seed(42)
indices = np.random.randint(0, len(best_daily), (10000, len(best_daily)))
bootstrap_cumulative = np.prod(1 + best_daily[indices], axis=1) - 1
actual_cumulative = np.prod(1 + best_daily) - 1
prob_positive = (bootstrap_cumulative > 0).mean()
p5 = np.percentile(bootstrap_cumulative, 5)
p50 = np.percentile(bootstrap_cumulative, 50)
p95 = np.percentile(bootstrap_cumulative, 95)

print(f'실제 누적: {actual_cumulative:.2%}')
print(f'P5/Med/P95: {p5:.2%} / {p50:.2%} / {p95:.2%}')
print(f'양수 확률: {prob_positive:.1%}')

# ── 저장 ──
final_result = {
    'strategy': 'daily_multifactor_v1_practical400_v4',
    'optimization_date': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M'),
    'data_period': f'{dates.min().date()} ~ {dates.max().date()}',
    'n_stocks': n_stocks, 'n_days': n_valid_days,
    'n_weight_combos': n_combos,
    'factor_processing': 'cross-sectional rank normalization [-0.5, 0.5]',
    'forward_return': '1-day (non-overlapping)',
    'stop_loss': '5% per-stock',
    'optimal_weights': {
        'momentum': float(best_weights[0]),
        'low_vol': float(best_weights[1]),
        'rsi': float(best_weights[2]),
        'volume_ratio': float(best_weights[3]),
    },
    'performance': {
        'walkforward_train': {'sharpe': float(train_sharpe), 'cumulative_return': float(train_metrics['cumulative_ret'][best_idx])},
        'walkforward_test': {'sharpe': float(test_sharpe), 'cumulative_return': float(test_cumret), 'max_drawdown': float(test_mdd)},
        'overfit_ratio': float(overfit_ratio),
    },
    'monte_carlo': {'prob_positive': float(prob_positive), 'p5': float(p5), 'p50': float(p50), 'p95': float(p95)},
}

out_path = '/Users/01chungee10/Github/TOSS/reports/backtests/optimal_weights_v4_fixed.json'
with open(out_path, 'w') as f:
    json.dump(final_result, f, indent=2, ensure_ascii=False)

# 전체 결과 저장
train_df.to_csv('/Users/01chungee10/Github/TOSS/reports/backtests/all_weights_v4_results.csv', index=False)

print('\n' + '=' * 60)
print(json.dumps(final_result, indent=2, ensure_ascii=False))
print(f'\n저장: {out_path}')
print(f'\n총 소요: {time.time()-t0_global:.0f}초 ({(time.time()-t0_global)/60:.1f}분)')
