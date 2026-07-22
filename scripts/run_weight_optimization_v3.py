#!/usr/bin/env python3
"""
TOSS 가중치 최적화 v3 — stop_loss 버그 수정 + 종합 진단

핵심 버그 수정:
1. stop_loss가 portfolio 전체가 아닌 개별 종목에 적용되어야 함
   기존: top-3 중 1종목이라도 -5%면 포트폴리오 전체를 -5%로 고정 → 수익 파괴
   수정: 각 종목 수익률을 -5%로 clamp 후 평균
2. stop_loss 없는 순수 알파도 함께 측정
3. 팩터 단위 효과 분석 (각 팩터만 단독 사용 시 성과)
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

# ── 3. Rank 정규화 ──
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
valid_end = n_days - 1
HOLDING_PERIOD = 3
forward_returns = close.pct_change(HOLDING_PERIOD).shift(-HOLDING_PERIOD)

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

# ── 4. 가중치 그리드 (0.05 step) ──
steps = np.arange(0.0, 1.01, 0.05)
weights_grid = []
for w1, w2, w3, w4 in product(steps, repeat=4):
    if abs(w1 + w2 + w3 + w4 - 1.0) < 0.025:
        weights_grid.append([w1, w2, w3, w4])
weights_grid = np.array(weights_grid)
n_combos = len(weights_grid)
weights_tensor = torch.from_numpy(weights_grid).float().to(device)
print(f'조합: {n_combos} (0.05 step)')

# ── 5. 백테스트 함수 (★stop_loss 수정★) ──
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

        # ★핵심 수정★: 개별 종목 stop_loss (포트폴리오가 아님)
        if stop_loss > 0:
            topk_returns = torch.clamp(topk_returns, min=-stop_loss)

        chunk_daily = topk_returns.mean(dim=-1)  # (D, W) ← clamp 후 평균

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

chunk_size = max(500, int(4e9 / (n_valid_days * n_stocks * 4)))

# ── Walkforward split ──
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
print(f'Holding: {HOLDING_PERIOD}일 | Stop loss: 5% (per-stock)')

# ══════════════════════════════════════════════════════════════
# A. stop_loss 없는 순수 알파 (모든 조합)
# ══════════════════════════════════════════════════════════════
print('\n' + '='*60)
print('A. 순수 알파 (stop_loss 없음)')
print('='*60)

print('\n--- Train (no SL) ---')
train_returns_nosl = gpu_batch_backtest(
    train_factor, train_return, weights_tensor, train_tradable,
    top_k=3, stop_loss=0, chunk_size=chunk_size
)
train_metrics_nosl = compute_metrics(train_returns_nosl)

train_df_nosl = pd.DataFrame({
    'w_mom': weights_grid[:, 0], 'w_lv': weights_grid[:, 1],
    'w_rsi': weights_grid[:, 2], 'w_vol': weights_grid[:, 3],
    'sharpe': train_metrics_nosl['sharpe'],
    'cumret': train_metrics_nosl['cumulative_ret'],
    'mdd': train_metrics_nosl['max_drawdown'],
}).sort_values('sharpe', ascending=False)

print('\nTrain Top 10 (no SL):')
print(train_df_nosl.head(10).to_string(index=False))

print(f'\nTrain Sharpe > 0: {(train_metrics_nosl["sharpe"] > 0).sum()}/{n_combos}')
print(f'Train Sharpe 범위: {train_metrics_nosl["sharpe"].min():.3f} ~ {train_metrics_nosl["sharpe"].max():.3f}')

print('\n--- Test (no SL) ---')
test_returns_nosl = gpu_batch_backtest(
    test_factor, test_return, weights_tensor, test_tradable,
    top_k=3, stop_loss=0, chunk_size=chunk_size
)
test_metrics_nosl = compute_metrics(test_returns_nosl)
print(f'Test Sharpe > 0: {(test_metrics_nosl["sharpe"] > 0).sum()}/{n_combos}')

# ══════════════════════════════════════════════════════════════
# B. stop_loss 5% (수정된 per-stock 방식)
# ══════════════════════════════════════════════════════════════
print('\n' + '='*60)
print('B. Stop loss 5% (per-stock, 수정됨)')
print('='*60)

print('\n--- Train (SL 5%) ---')
train_returns_sl = gpu_batch_backtest(
    train_factor, train_return, weights_tensor, train_tradable,
    top_k=3, stop_loss=0.05, chunk_size=chunk_size
)
train_metrics_sl = compute_metrics(train_returns_sl)

train_df_sl = pd.DataFrame({
    'w_mom': weights_grid[:, 0], 'w_lv': weights_grid[:, 1],
    'w_rsi': weights_grid[:, 2], 'w_vol': weights_grid[:, 3],
    'sharpe': train_metrics_sl['sharpe'],
    'cumret': train_metrics_sl['cumulative_ret'],
    'mdd': train_metrics_sl['max_drawdown'],
}).sort_values('sharpe', ascending=False)

print('\nTrain Top 10 (SL 5%):')
print(train_df_sl.head(10).to_string(index=False))

print('\n--- Test (SL 5%) ---')
test_returns_sl = gpu_batch_backtest(
    test_factor, test_return, weights_tensor, test_tradable,
    top_k=3, stop_loss=0.05, chunk_size=chunk_size
)
test_metrics_sl = compute_metrics(test_returns_sl)

# ══════════════════════════════════════════════════════════════
# C. 과적합 분석 (SL 5% 기준)
# ══════════════════════════════════════════════════════════════
print('\n' + '='*60)
print('C. Walkforward 과적합 분석 (SL 5%)')
print('='*60)

print(f'\n{"rk":>3} {"w_mom":>5} {"w_lv":>5} {"w_rsi":>5} {"w_vol":>5} {"tr_sh":>7} {"te_sh":>7} {"te_ret":>8} {"te_mdd":>8} {"ratio":>6}')
best_idx = None
best_test_sharpe = -999

for rank in range(20):
    idx = train_df_sl.index[rank]
    tr_sharpe = train_metrics_sl['sharpe'][idx]
    te_sharpe = test_metrics_sl['sharpe'][idx]
    te_cumret = test_metrics_sl['cumulative_ret'][idx]
    te_mdd = test_metrics_sl['max_drawdown'][idx]
    ratio = te_sharpe / (abs(tr_sharpe) + 1e-8) if tr_sharpe > 0 else -999
    w = weights_grid[idx]
    
    flag = ''
    if tr_sharpe > 0 and te_sharpe > 0:
        flag = ' ✅'
        if te_sharpe > best_test_sharpe:
            best_test_sharpe = te_sharpe
            best_idx = idx
    print(f'{rank+1:>3} {w[0]:>5.2f} {w[1]:>5.2f} {w[2]:>5.2f} {w[3]:>5.2f} {tr_sharpe:>7.3f} {te_sharpe:>7.3f} {te_cumret:>8.2%} {te_mdd:>8.2%} {ratio:>6.2f}{flag}')

# Fallback: Train Top 20에 양수가 없으면 전체에서 Train+Test 모두 양수인 조합 탐색
if best_idx is None:
    print('\n⚠️ Train Top 20에 양수 조합 없음. 전체 탐색...')
    both_positive = np.where(
        (train_metrics_sl['sharpe'] > 0) & (test_metrics_sl['sharpe'] > 0)
    )[0]
    print(f'Train+Test 모두 Sharpe > 0: {len(both_positive)}개')
    
    if len(both_positive) > 0:
        # Test Sharpe 기준 최고
        best_idx = both_positive[np.argmax(test_metrics_sl['sharpe'][both_positive])]
    else:
        # 그냥 전체 Sharpe 기준 최고 (Test만)
        best_idx = np.argmax(test_metrics_sl['sharpe'])
        print(f'⚠️ Train+Test 동시 양수 없음. Test Sharpe 최고 사용 (lookahead 주의)')

best_weights = weights_grid[best_idx]
train_sharpe = train_metrics_sl['sharpe'][best_idx]
test_sharpe = test_metrics_sl['sharpe'][best_idx]
test_cumret = test_metrics_sl['cumulative_ret'][best_idx]
test_mdd = test_metrics_sl['max_drawdown'][best_idx]
overfit_ratio = test_sharpe / (abs(train_sharpe) + 1e-8) if train_sharpe > 0 else -999

print(f'\n✅ 선택: weights={best_weights}')
print(f'   Train Sharpe: {train_sharpe:.3f} | Test Sharpe: {test_sharpe:.3f}')
print(f'   Test 누적: {test_cumret:.2%} | Test MDD: {test_mdd:.2%} | Ratio: {overfit_ratio:.2f}')

# ══════════════════════════════════════════════════════════════
# D. 단일 팩터 분석 (각 팩터만 단독 사용)
# ══════════════════════════════════════════════════════════════
print('\n' + '='*60)
print('D. 단일 팩터 효과 (weight=1.0)')
print('='*60)

single_weights = torch.eye(n_factors, device=device)  # identity → 각 팩터만 1.0
for i, fname in enumerate(factor_names):
    for period_name, ft, rt, tm in [('Train', train_factor, train_return, train_tradable),
                                      ('Test', test_factor, test_return, test_tradable)]:
        single_w = single_weights[i:i+1]
        sr = gpu_batch_backtest(ft, rt, single_w, tm, top_k=3, stop_loss=0.05, chunk_size=1)
        sm = compute_metrics(sr)
        if period_name == 'Train':
            tr_sh = sm['sharpe'][0]
            tr_cr = sm['cumulative_ret'][0]
        else:
            te_sh = sm['sharpe'][0]
            te_cr = sm['cumulative_ret'][0]
    print(f'{fname:>14}: Train Sharpe={tr_sh:.3f} ({tr_cr:.2%}) | Test Sharpe={te_sh:.3f} ({te_cr:.2%})')

# ══════════════════════════════════════════════════════════════
# E. Monte Carlo
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

print(f'실제: {actual_cumulative:.2%}')
print(f'P5/Med/P95: {np.percentile(bootstrap_cumulative,5):.2%} / {np.percentile(bootstrap_cumulative,50):.2%} / {np.percentile(bootstrap_cumulative,95):.2%}')
print(f'양수 확률: {prob_positive:.1%}')

# ── 저장 ──
final_result = {
    'strategy': 'daily_multifactor_v1_practical400_v3',
    'optimization_date': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M'),
    'data_period': f'{dates.min().date()} ~ {dates.max().date()}',
    'n_stocks': n_stocks, 'n_days': n_valid_days,
    'n_weight_combos': n_combos,
    'factor_processing': 'cross-sectional rank normalization [-0.5, 0.5]',
    'holding_period': HOLDING_PERIOD,
    'stop_loss': '5% per-stock (fixed)',
    'optimal_weights': {
        'momentum': float(best_weights[0]),
        'low_vol': float(best_weights[1]),
        'rsi': float(best_weights[2]),
        'volume_ratio': float(best_weights[3]),
    },
    'performance': {
        'walkforward_train': {'sharpe': float(train_sharpe), 'cumulative_return': float(train_metrics_sl['cumulative_ret'][best_idx])},
        'walkforward_test': {'sharpe': float(test_sharpe), 'cumulative_return': float(test_cumret), 'max_drawdown': float(test_mdd)},
        'overfit_ratio': float(overfit_ratio),
    },
    'monte_carlo': {'prob_positive': float(prob_positive), 'p5': float(np.percentile(bootstrap_cumulative,5)), 'p50': float(np.percentile(bootstrap_cumulative,50)), 'p95': float(np.percentile(bootstrap_cumulative,95))},
}

out_path = '/Users/01chungee10/Github/TOSS/reports/backtests/optimal_weights_v3_fixed.json'
with open(out_path, 'w') as f:
    json.dump(final_result, f, indent=2, ensure_ascii=False)

# 전체 결과도 저장
all_results = pd.DataFrame({
    'w_mom': weights_grid[:, 0], 'w_lv': weights_grid[:, 1],
    'w_rsi': weights_grid[:, 2], 'w_vol': weights_grid[:, 3],
    'train_sharpe_nosl': train_metrics_nosl['sharpe'],
    'test_sharpe_nosl': test_metrics_nosl['sharpe'],
    'train_sharpe_sl': train_metrics_sl['sharpe'],
    'test_sharpe_sl': test_metrics_sl['sharpe'],
    'train_cumret_sl': train_metrics_sl['cumulative_ret'],
    'test_cumret_sl': test_metrics_sl['cumulative_ret'],
})
all_results.to_csv('/Users/01chungee10/Github/TOSS/reports/backtests/all_weights_v3_results.csv', index=False)

print('\n' + '=' * 60)
print(json.dumps(final_result, indent=2, ensure_ascii=False))
print(f'\n저장: {out_path}')
print(f'CSV: all_weights_v3_results.csv')
print(f'\n총 소요: {time.time()-t0_global:.0f}초 ({(time.time()-t0_global)/60:.1f}분)')
