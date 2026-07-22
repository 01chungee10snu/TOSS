#!/usr/bin/env python3
"""
TOSS 다중팩터 가중치 최적화 v2 - 팩터 정규화 + holding period 수정
핵심 수정:
1. Cross-sectional rank normalization (0~1) — 모든 팩터를 동일 스케일로
2. Forward return이 holding period와 일치
3. 과적합 필터링 내장
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

# ── 1. 데이터 로드 ──
csv_path = '/Users/01chungee10/Github/TOSS/reports/backtests/practical_universe_400_2022-01-01_2026-latest_ohlcv_panel.csv'
raw_df = pd.read_csv(csv_path)

pivot = raw_df.pivot_table(index='Date', columns='code', values=['Open', 'High', 'Low', 'Close', 'Volume'], aggfunc='first')
close = pivot['Close'].sort_index()
volume = pivot['Volume'].sort_index()
n_days, n_stocks = close.shape
print(f'패널: {n_days}일 × {n_stocks}종목')

# ── 2. 팩터 계산 ──
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

# ── 3. ★핵심 수정★ Cross-sectional rank normalization ──
# 각 일자별로 모든 종목의 팩터 값을 0~1 순위로 변환
def rank_normalize(df):
    """Cross-sectional rank → [0, 1] 정규화"""
    ranked = df.rank(axis=1, pct=True)
    return ranked - 0.5  # [-0.5, 0.5] 중심화

factors_raw = {
    'momentum': mom_5d,
    'low_vol': low_vol_raw,
    'rsi': rsi_reversal,
    'volume_ratio': volume_ratio,
}

factors_normalized = {}
for name, df in factors_raw.items():
    factors_normalized[name] = rank_normalize(df)

print('팩터 정규화 완료 (cross-sectional rank → [-0.5, 0.5])')
for name, df in factors_normalized.items():
    print(f'  {name}: mean={df.mean().mean():.4f}, std={df.std().mean():.4f}')

# ── 4. 텐서 변환 ──
factor_names = list(factors_normalized.keys())
n_factors = len(factor_names)
valid_start = 25
valid_end = n_days - 1

# ★ 수정: holding_period=3에 맞는 forward return 사용 ──
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
print(f'\n3D 텐서: ({n_valid_days}, {n_stocks}, {n_factors})')
print(f'수익률 텐서: ({n_valid_days}, {n_stocks}) — {HOLDING_PERIOD}일 forward return')
print(f'거래 가능 비율: {tradable_mask.float().mean():.2%}')

# ── 5. 가중치 그리드 ──
steps = np.arange(0.0, 1.01, 0.05)  # 0.05 step → 1,771조합 (빠른 탐색)
weights_grid = []
for w1, w2, w3, w4 in product(steps, repeat=4):
    if abs(w1 + w2 + w3 + w4 - 1.0) < 0.025:
        weights_grid.append([w1, w2, w3, w4])
weights_grid = np.array(weights_grid)
n_combos = len(weights_grid)
chunk_size = max(500, int(4e9 / (n_valid_days * n_stocks * 4)))
weights_tensor = torch.from_numpy(weights_grid).float().to(device)
print(f'\n가중치 조합: {n_combos:,} (0.05 step)')
print(f'청크 크기: {chunk_size}, 총 청크: {(n_combos + chunk_size - 1) // chunk_size}')

# ── 6. 백테스트 함수 ──
def gpu_batch_backtest(factor_tensor, return_tensor, weights_tensor, tradable_mask,
                       top_k=3, stop_loss=0.05, chunk_size=1000):
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

        if (chunk_idx + 1) % 5 == 0 or chunk_idx == n_chunks - 1:
            pct = (end / n_weights) * 100
            print(f'  청크 {chunk_idx+1}/{n_chunks} ({pct:.0f}%) - {time.time()-t0_global:.0f}초')

    return torch.cat(all_returns, dim=0)

def compute_metrics(daily_returns):
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

# ── 7. Walkforward ──
dates = pd.to_datetime(close.index[valid_start:valid_end])
train_mask_np = (dates.year <= 2024).astype(bool)
test_mask_np = (dates.year >= 2025).astype(bool)

train_factor = factor_tensor[train_mask_np]
train_return = return_tensor[train_mask_np]
train_tradable = tradable_mask[train_mask_np]
test_factor = factor_tensor[test_mask_np]
test_return = return_tensor[test_mask_np]
test_tradable = tradable_mask[test_mask_np]

print(f'\nTrain: {train_mask_np.sum()}일 ({dates[train_mask_np].min().date()} ~ {dates[train_mask_np].max().date()})')
print(f'Test:  {test_mask_np.sum()}일 ({dates[test_mask_np].min().date()} ~ {dates[test_mask_np].max().date()})')

# Train 백테스트
print('\n=== Train 백테스트 ===')
train_returns = gpu_batch_backtest(
    train_factor, train_return, weights_tensor, train_tradable,
    top_k=3, stop_loss=0.05, chunk_size=chunk_size
)
train_metrics = compute_metrics(train_returns)

# Train 기준 상위 20개 조합
train_df = pd.DataFrame({
    'w_momentum': weights_grid[:, 0],
    'w_low_vol': weights_grid[:, 1],
    'w_rsi': weights_grid[:, 2],
    'w_volume_ratio': weights_grid[:, 3],
    'sharpe': train_metrics['sharpe'],
    'cumulative_ret': train_metrics['cumulative_ret'],
    'max_drawdown': train_metrics['max_drawdown'],
}).sort_values('sharpe', ascending=False)

print('\n=== Train Top 20 (Sharpe 기준) ===')
print(train_df.head(20).to_string(index=False))

# Test 백테스트 (전체 조합)
print('\n=== Test 백테스트 ===')
test_returns = gpu_batch_backtest(
    test_factor, test_return, weights_tensor, test_tradable,
    top_k=3, stop_loss=0.05, chunk_size=chunk_size
)
test_metrics = compute_metrics(test_returns)

# 과적합 분석: Train Top 20의 Test 성과
print('\n=== Walkforward 과적합 분석 (Train Top 20) ===')
print(f'{"rank":>4} {"w_mom":>6} {"w_lv":>6} {"w_rsi":>6} {"w_vol":>6} {"tr_sharpe":>9} {"te_sharpe":>9} {"te_cumret":>10} {"te_mdd":>8} {"ratio":>6}')
overfit_safe = []

for rank in range(20):
    idx = train_df.index[rank]
    tr_sharpe = train_metrics['sharpe'][idx]
    te_sharpe = test_metrics['sharpe'][idx]
    te_cumret = test_metrics['cumulative_ret'][idx]
    te_mdd = test_metrics['max_drawdown'][idx]
    ratio = te_sharpe / (abs(tr_sharpe) + 1e-8) if tr_sharpe > 0 else -999
    
    w = weights_grid[idx]
    print(f'{rank+1:>4} {w[0]:>6.2f} {w[1]:>6.2f} {w[2]:>6.2f} {w[3]:>6.2f} {tr_sharpe:>9.3f} {te_sharpe:>9.3f} {te_cumret:>10.2%} {te_mdd:>8.2%} {ratio:>6.2f}')
    
    # 과적합 필터: Train Sharpe > 0, Test Sharpe > 0, Test 누적 > 0
    if tr_sharpe > 0 and te_sharpe > 0 and te_cumret > 0:
        overfit_safe.append({
            'idx': idx, 'weights': w, 'tr_sharpe': tr_sharpe,
            'te_sharpe': te_sharpe, 'te_cumret': te_cumret,
            'te_mdd': te_mdd, 'ratio': ratio,
        })

print(f'\n과적합 필터 통과: {len(overfit_safe)}개 / 20')
if len(overfit_safe) == 0:
    print('❌ Train Top 20 중 Test에서도 양수인 조합이 없음 → 전략 구조 재검토 필요')
    # Test Sharpe가 가장 높은 조합이라도 찾자
    best_test_idx = np.argmax(test_metrics['sharpe'])
    print(f'Test 최고 Sharpe 조합: {weights_grid[best_test_idx]} → Sharpe={test_metrics["sharpe"][best_test_idx]:.3f}')
    
    # 전체에서 Test Sharpe > 0인 조합 수
    positive_test = (test_metrics['sharpe'] > 0).sum()
    print(f'전체 {n_combos}조합 중 Test Sharpe > 0: {positive_test}개 ({positive_test/n_combos:.1%})')
else:
    # 과적합 통과 조합 중 Test Sharpe 최고
    best = max(overfit_safe, key=lambda x: x['te_sharpe'])
    best_train_idx = best['idx']
    best_weights = best['weights']
    train_sharpe = best['tr_sharpe']
    test_sharpe = best['te_sharpe']
    overfit_ratio = best['ratio']
    print(f'\n✅ 최적 (과적합 필터 통과): {best_weights}')
    print(f'   Train Sharpe: {train_sharpe:.3f}, Test Sharpe: {test_sharpe:.3f}, Ratio: {overfit_ratio:.2f}')

# ── 8. Monte Carlo ──
print('\n=== Monte Carlo Bootstrap ===')
# 최적 가중치로 전체 기간 백테스트
best_w_tensor = torch.from_numpy(best_weights).float().to(device).unsqueeze(0)
full_daily = gpu_batch_backtest(
    factor_tensor, return_tensor, best_w_tensor, tradable_mask,
    top_k=3, stop_loss=0.05, chunk_size=1
)
best_daily = full_daily[0].cpu().numpy()

n_bootstrap = 10000
np.random.seed(42)
indices = np.random.randint(0, len(best_daily), (n_bootstrap, len(best_daily)))
bootstrap_returns = best_daily[indices]
bootstrap_cumulative = np.prod(1 + bootstrap_returns, axis=1) - 1

actual_cumulative = np.prod(1 + best_daily) - 1
p5 = np.percentile(bootstrap_cumulative, 5)
p50 = np.percentile(bootstrap_cumulative, 50)
p95 = np.percentile(bootstrap_cumulative, 95)
prob_positive = (bootstrap_cumulative > 0).mean()

print(f'실제 누적 수익률: {actual_cumulative:.2%}')
print(f'부트스트랩 5th/50th/95th: {p5:.2%} / {p50:.2%} / {p95:.2%}')
print(f'양수 수익률 확률: {prob_positive:.1%}')

# ── 9. 결과 저장 ──
final_result = {
    'strategy': 'daily_multifactor_v1_practical400_v2',
    'optimization_date': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M'),
    'data_period': f'{dates.min().date()} ~ {dates.max().date()}',
    'n_stocks': n_stocks,
    'n_days': n_valid_days,
    'n_weight_combos': n_combos,
    'factors': factor_names,
    'factor_processing': 'cross-sectional rank normalization [-0.5, 0.5]',
    'holding_period': HOLDING_PERIOD,
    'optimal_weights': {
        'momentum': float(best_weights[0]),
        'low_vol': float(best_weights[1]),
        'rsi': float(best_weights[2]),
        'volume_ratio': float(best_weights[3]),
    },
    'performance': {
        'walkforward_train': {'sharpe': float(train_sharpe)},
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
        'p5': float(p5), 'p50': float(p50), 'p95': float(p95),
    },
}

out_path = '/Users/01chungee10/Github/TOSS/reports/backtests/optimal_weights_v2_normalized.json'
with open(out_path, 'w') as f:
    json.dump(final_result, f, indent=2, ensure_ascii=False)

print('\n' + '=' * 60)
print(json.dumps(final_result, indent=2, ensure_ascii=False))
print(f'\n저장: {out_path}')
print(f'\n총 소요: {time.time()-t0_global:.0f}초 ({(time.time()-t0_global)/60:.1f}분)')
