#!/usr/bin/env python3
"""
TOSS Walkforward 검증 - 첫 백테스트 이후 단계만 재실행
전체 데이터 로드 + 백테스트는 건너뛰고 Walkforward부터 실행
"""
import torch
import torch.nn.functional as F
import pandas as pd
import numpy as np
import json
import time
import os
from itertools import product

t0_global = time.time()
device = torch.device('mps' if torch.backends.mps.is_available() else 'cpu')
print(f'Device: {device}')

# ── 데이터 로드 + 팩터 계산 (첫 백테스트와 동일) ──
csv_path = '/Users/01chungee10/Github/TOSS/reports/backtests/practical_universe_400_2022-01-01_2026-latest_ohlcv_panel.csv'
raw_df = pd.read_csv(csv_path)

pivot = raw_df.pivot_table(index='Date', columns='code', values=['Open', 'High', 'Low', 'Close', 'Volume'], aggfunc='first')
close = pivot['Close'].sort_index()
volume = pivot['Volume'].sort_index()
n_days, n_stocks = close.shape

forward_returns = close.pct_change().shift(-1)
mom_5d = close.pct_change(5)
daily_ret = close.pct_change()
vol_20d = daily_ret.rolling(20).std()
low_vol = 1.0 / (vol_20d + 1e-8)

delta = close.diff()
gain = delta.clip(lower=0).rolling(14).mean()
loss = (-delta.clip(upper=0)).rolling(14).mean()
rs = gain / (loss + 1e-8)
rsi = 100 - (100 / (1 + rs))
rsi_reversal = (50 - rsi) / 50

vol_ma5 = volume.rolling(5).mean()
vol_ma20 = volume.rolling(20).mean()
volume_ratio = vol_ma5 / (vol_ma20 + 1e-8) - 1.0

factors = {
    'momentum': mom_5d,
    'low_vol': low_vol,
    'rsi': rsi_reversal,
    'volume_ratio': volume_ratio,
}

valid_start = 25
valid_end = n_days - 1
factor_names = list(factors.keys())
n_factors = len(factor_names)

factor_tensor = torch.zeros(valid_end - valid_start, n_stocks, n_factors, device=device)
return_tensor = torch.zeros(valid_end - valid_start, n_stocks, device=device)

for i, fname in enumerate(factor_names):
    df = factors[fname].iloc[valid_start:valid_end]
    factor_tensor[:, :, i] = torch.from_numpy(df.values).float().to(device)

ret_df = forward_returns.iloc[valid_start:valid_end]
return_tensor = torch.from_numpy(ret_df.values).float().to(device)

factor_tensor[torch.isnan(factor_tensor)] = 0
return_tensor[torch.isnan(return_tensor)] = 0

tradable_mask = torch.from_numpy((volume.iloc[valid_start:valid_end].values > 0)).to(device)
n_valid_days = valid_end - valid_start

# 가중치 그리드
steps = np.arange(0.0, 1.01, 0.01)
weights_grid = []
for w1, w2, w3, w4 in product(steps, repeat=4):
    if abs(w1 + w2 + w3 + w4 - 1.0) < 0.005:
        weights_grid.append([w1, w2, w3, w4])
weights_grid = np.array(weights_grid)
n_combos = len(weights_grid)
chunk_size = max(500, int(4e9 / (n_valid_days * n_stocks * 4)))
weights_tensor = torch.from_numpy(weights_grid).float().to(device)

print(f'데이터 로드 완료: {n_combos:,}조합, chunk_size={chunk_size}')

# ── 백테스트 함수 ──
def gpu_batch_backtest(factor_tensor, return_tensor, weights_tensor, tradable_mask,
                       top_k=3, holding_period=3, stop_loss=0.05, chunk_size=1000):
    n_days, n_stocks, n_factors = factor_tensor.shape
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
        topk_returns = ret_expanded.gather(-1, topk_indices)
        chunk_daily = topk_returns.mean(dim=-1)

        if stop_loss > 0:
            stopped = (topk_returns < -stop_loss).any(dim=-1)
            chunk_daily[stopped] = torch.clamp(chunk_daily[stopped], max=-stop_loss)

        del topk_returns, topk_indices
        all_returns.append(chunk_daily.T.clone())
        del chunk_daily

        if (chunk_idx + 1) % 20 == 0 or chunk_idx == n_chunks - 1:
            pct = (end / n_weights) * 100
            elapsed = time.time() - t0_global
            print(f'  청크 {chunk_idx+1}/{n_chunks} ({pct:.0f}%) - {elapsed:.0f}초')

    return torch.cat(all_returns, dim=0)

def compute_metrics(daily_returns, weights_grid):
    n_weights, n_days = daily_returns.shape
    cumulative = (1 + daily_returns).prod(dim=1) - 1
    n_years = n_days / 252
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

# ── Walkforward 검증 ──
print('\n=== Walkforward 검증 ===')
dates = pd.to_datetime(close.index[valid_start:valid_end])
train_mask_np = (dates.year <= 2024).astype(bool)
test_mask_np = (dates.year >= 2025).astype(bool)

train_factor = factor_tensor[train_mask_np]
train_return = return_tensor[train_mask_np]
train_tradable = tradable_mask[train_mask_np]

test_factor = factor_tensor[test_mask_np]
test_return = return_tensor[test_mask_np]
test_tradable = tradable_mask[test_mask_np]

print(f'Train: {train_mask_np.sum()}일 ({dates[train_mask_np].min().date()} ~ {dates[train_mask_np].max().date()})')
print(f'Test:  {test_mask_np.sum()}일 ({dates[test_mask_np].min().date()} ~ {dates[test_mask_np].max().date()})')

# Train 백테스트
print('\n--- Train 백테스트 ---')
t0 = time.time()
train_returns = gpu_batch_backtest(
    train_factor, train_return, weights_tensor, train_tradable,
    top_k=3, holding_period=3, stop_loss=0.05, chunk_size=chunk_size
)
train_metrics = compute_metrics(train_returns, weights_grid)
best_train_idx = np.argmax(train_metrics['sharpe'])
best_weights = weights_grid[best_train_idx]
print(f'Train 완료 ({time.time()-t0:.0f}초)')
print(f'Train 최적: momentum={best_weights[0]:.2f}, low_vol={best_weights[1]:.2f}, rsi={best_weights[2]:.2f}, volume={best_weights[3]:.2f}')
print(f'Train Sharpe: {train_metrics["sharpe"][best_train_idx]:.3f}')
print(f'Train 누적 수익률: {train_metrics["cumulative_ret"][best_train_idx]:.2%}')

# Test 백테스트
print('\n--- Test 백테스트 ---')
t0 = time.time()
test_returns = gpu_batch_backtest(
    test_factor, test_return, weights_tensor, test_tradable,
    top_k=3, holding_period=3, stop_loss=0.05, chunk_size=chunk_size
)
test_metrics = compute_metrics(test_returns, weights_grid)
print(f'Test 완료 ({time.time()-t0:.0f}초)')

train_sharpe = train_metrics['sharpe'][best_train_idx]
test_sharpe = test_metrics['sharpe'][best_train_idx]
overfit_ratio = test_sharpe / (abs(train_sharpe) + 1e-8)

print(f'\nTest (out-of-sample):')
print(f'Test Sharpe: {test_sharpe:.3f}')
print(f'Test 누적 수익률: {test_metrics["cumulative_ret"][best_train_idx]:.2%}')
print(f'Test 최대 낙폭: {test_metrics["max_drawdown"][best_train_idx]:.2%}')
print(f'과적합 진단: overfit_ratio={overfit_ratio:.3f}')
if overfit_ratio > 0.5:
    print('✅ 일반화 양호')
elif overfit_ratio > 0:
    print('⚠️ 부분 과적합')
else:
    print('❌ 과적합')

# ── Monte Carlo Bootstrap ──
print('\n=== Monte Carlo Bootstrap (10,000회) ===')
# 최적 가중치의 전체 기간 일별 수익률 필요 → 전체 백테스트 재실행
print('전체 기간 백테스트 재실행 (최적 가중치)...')
best_w_tensor = torch.from_numpy(best_weights).float().to(device).unsqueeze(0)
full_daily = gpu_batch_backtest(
    factor_tensor, return_tensor, best_w_tensor, tradable_mask,
    top_k=3, holding_period=3, stop_loss=0.05, chunk_size=1
)
best_daily = full_daily[0].cpu().numpy()

n_bootstrap = 10000
np.random.seed(42)
bootstrap_indices = np.random.randint(0, len(best_daily), (n_bootstrap, len(best_daily)))
bootstrap_returns = best_daily[bootstrap_indices]
bootstrap_cumulative = np.prod(1 + bootstrap_returns, axis=1) - 1

actual_cumulative = np.prod(1 + best_daily) - 1
p5 = np.percentile(bootstrap_cumulative, 5)
p50 = np.percentile(bootstrap_cumulative, 50)
p95 = np.percentile(bootstrap_cumulative, 95)
prob_positive = (bootstrap_cumulative > 0).mean()

print(f'실제 누적 수익률: {actual_cumulative:.2%}')
print(f'부트스트랩 5th:  {p5:.2%}')
print(f'부트스트랩 중앙값: {p50:.2%}')
print(f'부트스트랩 95th:  {p95:.2%}')
print(f'양수 수익률 확률: {prob_positive:.1%}')

# ── 홀딩 기간 최적화 ──
print('\n=== 홀딩 기간 최적화 ===')
holding_periods = [1, 3, 5, 10]
holding_results = []

for hp in holding_periods:
    hp_forward = close.pct_change(hp).shift(-hp)
    hp_return = torch.from_numpy(hp_forward.iloc[valid_start:valid_end].values).float().to(device)
    hp_return[torch.isnan(hp_return)] = 0

    hp_daily = gpu_batch_backtest(
        factor_tensor, hp_return, best_w_tensor, tradable_mask,
        top_k=3, holding_period=hp, stop_loss=0.05, chunk_size=1
    )
    hp_metrics = compute_metrics(hp_daily, best_weights.reshape(1, -1))
    holding_results.append({
        'holding_days': hp,
        'sharpe': hp_metrics['sharpe'][0],
        'cumulative_ret': hp_metrics['cumulative_ret'][0],
        'max_drawdown': hp_metrics['max_drawdown'][0],
    })
    print(f'홀딩 {hp:2d}일: Sharpe={hp_metrics["sharpe"][0]:.3f}, 누적={hp_metrics["cumulative_ret"][0]:.2%}, MDD={hp_metrics["max_drawdown"][0]:.2%}')

holding_df = pd.DataFrame(holding_results)
best_hp = holding_df.loc[holding_df['sharpe'].idxmax()]
print(f'\n최적 홀딩 기간: {best_hp["holding_days"]:.0f}일 (Sharpe {best_hp["sharpe"]:.3f})')

# ── 최종 결과 저장 ──
final_result = {
    'strategy': 'daily_multifactor_v1_practical400',
    'optimization_date': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M'),
    'data_period': f'{dates.min().date()} ~ {dates.max().date()}',
    'n_stocks': n_stocks,
    'n_days': n_valid_days,
    'n_weight_combos': n_combos,
    'factors': factor_names,
    'optimal_weights': {
        'momentum': float(best_weights[0]),
        'low_vol': float(best_weights[1]),
        'rsi': float(best_weights[2]),
        'volume_ratio': float(best_weights[3]),
    },
    'optimal_holding_days': int(best_hp['holding_days']),
    'performance': {
        'walkforward_train': {
            'sharpe': float(train_sharpe),
            'cumulative_return': float(train_metrics['cumulative_ret'][best_train_idx]),
        },
        'walkforward_test': {
            'sharpe': float(test_sharpe),
            'cumulative_return': float(test_metrics['cumulative_ret'][best_train_idx]),
            'max_drawdown': float(test_metrics['max_drawdown'][best_train_idx]),
        },
        'overfit_ratio': float(overfit_ratio),
    },
    'monte_carlo': {
        'n_bootstrap': n_bootstrap,
        'prob_positive': float(prob_positive),
        'p5': float(p5),
        'p50': float(p50),
        'p95': float(p95),
    },
}

out_path = '/Users/01chungee10/Github/TOSS/reports/backtests/optimal_weights_001step.json'
with open(out_path, 'w') as f:
    json.dump(final_result, f, indent=2, ensure_ascii=False)

print('\n' + '=' * 60)
print('최종 최적화 결과 (0.01 step, 176,851 조합)')
print('=' * 60)
print(json.dumps(final_result, indent=2, ensure_ascii=False))
print(f'\n저장: {out_path}')

total_elapsed = time.time() - t0_global
print(f'\n총 소요 시간: {total_elapsed:.1f}초 ({total_elapsed/60:.1f}분)')
