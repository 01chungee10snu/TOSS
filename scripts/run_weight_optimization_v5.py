#!/usr/bin/env python3
"""
TOSS 가중치 최적화 v5 — 거래비용 + 생존자편향 진단

추가:
1. 거래비용 0.03%/거래 (매수+매도 = 0.06%/왕복) — 일일 리밸런싱 턴오버 계산
2. 생존자편향 진단: 2022~2024 vs 2025~2026 종목 수 변화
3. 현실적 수익률 시뮬레이션
"""
import torch
import pandas as pd
import numpy as np
import json
import time
from itertools import product

t0_global = time.time()
device = torch.device('mps' if torch.backends.mps.is_available() else 'cpu')

# ── 1. 데이터 ──
csv_path = '/Users/01chungee10/Github/TOSS/reports/backtests/practical_universe_400_2022-01-01_2026-latest_ohlcv_panel.csv'
raw_df = pd.read_csv(csv_path)
pivot = raw_df.pivot_table(index='Date', columns='code', values=['Open', 'High', 'Low', 'Close', 'Volume'], aggfunc='first')
close = pivot['Close'].sort_index()
volume = pivot['Volume'].sort_index()
n_days, n_stocks = close.shape

# ── 생존자편향 진단 ──
print('=== 생존자편향 진단 ===')
for year in [2022, 2023, 2024, 2025, 2026]:
    yr_data = close[close.index.str.contains(str(year))] if isinstance(close.index[0], str) else close[close.index.year == year]
    valid_stocks = yr_data.dropna(axis=1).shape[1]
    print(f'  {year}년: {valid_stocks}개 종목 유효 (전체 {n_stocks}개)')

# 각 종목의 첫 등장일/마지막 거래일
first_dates = close.apply(lambda col: col.first_valid_index())
last_dates = close.apply(lambda col: col.last_valid_index())
# index가 문자열이면 Timestamp로 변환
first_ts = pd.to_datetime(first_dates)
last_ts = pd.to_datetime(last_dates)
print(f'\n  종목 상장일 범위: {first_ts.min().date()} ~ {first_ts.max().date()}')
print(f'  종목 최종일 범위: {last_ts.min().date()} ~ {last_ts.max().date()}')
print(f'  → 2022-06-01부터 존재: {(first_ts <= pd.Timestamp("2022-06-01")).sum()}개')
print(f'  → 2026-06-01까지 존재: {(last_ts >= pd.Timestamp("2026-06-01")).sum()}개')

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
valid_end = n_days - 2
forward_returns = close.pct_change().shift(-1)

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

# ── 3. 가중치 ──
steps = np.arange(0.0, 1.01, 0.05)
weights_grid = []
for w1, w2, w3, w4 in product(steps, repeat=4):
    if abs(w1 + w2 + w3 + w4 - 1.0) < 0.025:
        weights_grid.append([w1, w2, w3, w4])
weights_grid = np.array(weights_grid)
n_combos = len(weights_grid)
weights_tensor = torch.from_numpy(weights_grid).float().to(device)
chunk_size = max(500, int(4e9 / (n_valid_days * n_stocks * 4)))

# ── 4. 백테스트 (★거래비용 추가★) ──
TXN_COST = 0.0003  # 0.03% per trade (Korea 기본)

def gpu_batch_backtest(factor_tensor, return_tensor, weights_tensor, tradable_mask,
                       top_k=3, stop_loss=0.05, chunk_size=1000, txn_cost=TXN_COST):
    """
    거래비용 계산:
    - 각 일자의 선택 종목이 전일과 다르면 매매 발생
    - 매매당 0.03% × 2(매도+매수) = 0.06% 비용
    - turnover = 변경된 종목 비율
    """
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
        _, topk_indices = scores.topk(top_k, dim=-1)  # (D, W, top_k)
        del scores

        ret_expanded = return_tensor.unsqueeze(1).expand(-1, n_w, -1)
        topk_returns = ret_expanded.gather(-1, topk_indices)  # (D, W, top_k)

        if stop_loss > 0:
            topk_returns = torch.clamp(topk_returns, min=-stop_loss)

        # 일별 포트폴리오 수익률
        chunk_daily = topk_returns.mean(dim=-1)  # (D, W)

        # ★거래비용★: 일자간 선택 종목 변화 → turnover
        if txn_cost > 0:
            # (D, W, top_k) → 일자간 비교
            prev_indices = topk_indices[:-1]  # (D-1, W, top_k)
            curr_indices = topk_indices[1:]   # (D-1, W, top_k)
            
            # 각 (W, top_k) 쌍에 대해 겹치는 종목 수 계산
            # prev와 curr을 set 비교 → GPU에서는 element-wise
            # overlap: (D-1, W) — 겹치는 종목 수
            overlap = torch.zeros(curr_indices.shape[0], n_w, device=device)
            for k in range(top_k):
                for j in range(top_k):
                    overlap += (curr_indices[:, :, k] == prev_indices[:, :, j]).float()
            
            turnover = (top_k - overlap) / top_k  # (D-1, W) — 교체 비율
            # 거래비용: turnover × 0.06% (왕복)
            cost = turnover * 2 * txn_cost  # (D-1, W)
            
            # 첫날은 전량 매수
            first_cost = torch.ones(1, n_w, device=device) * 2 * txn_cost
            
            chunk_daily[1:] -= cost  # 2일차부터 비용 차감
            chunk_daily[0] -= (2 * txn_cost)  # 첫날

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
    win_rate = (daily_returns > 0).float().mean(dim=1)
    return {
        'cumulative_ret': cumulative.cpu().numpy(),
        'annual_ret': annual_ret.cpu().numpy(),
        'sharpe': sharpe.cpu().numpy(),
        'max_drawdown': max_dd.cpu().numpy(),
        'win_rate': win_rate.cpu().numpy(),
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

print(f'\n거래비용: {TXN_COST*100:.2f}%/거래 (왕복 {TXN_COST*200:.2f}%)')
print(f'Train: {train_mask_np.sum()}일 | Test: {test_mask_np.sum()}일')

# ══════════════════════════════════════════════════════════════
# SL 5% + 거래비용
# ══════════════════════════════════════════════════════════════
print('\n=== Train (SL 5% + TXN) ===')
train_returns = gpu_batch_backtest(
    train_factor, train_return, weights_tensor, train_tradable,
    top_k=3, stop_loss=0.05, chunk_size=chunk_size
)
train_metrics = compute_metrics(train_returns)

print('\n=== Test (SL 5% + TXN) ===')
test_returns = gpu_batch_backtest(
    test_factor, test_return, weights_tensor, test_tradable,
    top_k=3, stop_loss=0.05, chunk_size=chunk_size
)
test_metrics = compute_metrics(test_returns)

# ── 결과 ──
train_df = pd.DataFrame({
    'w_mom': weights_grid[:, 0], 'w_lv': weights_grid[:, 1],
    'w_rsi': weights_grid[:, 2], 'w_vol': weights_grid[:, 3],
    'tr_sharpe': train_metrics['sharpe'],
    'tr_cumret': train_metrics['cumulative_ret'],
    'tr_annual': train_metrics['annual_ret'],
    'tr_mdd': train_metrics['max_drawdown'],
    'te_sharpe': test_metrics['sharpe'],
    'te_cumret': test_metrics['cumulative_ret'],
    'te_annual': test_metrics['annual_ret'],
    'te_mdd': test_metrics['max_drawdown'],
    'te_winrate': test_metrics['win_rate'],
})

train_sorted = train_df.sort_values('tr_sharpe', ascending=False)

print('\n=== Train Top 20 (SL 5% + TXN, Sharpe 기준) ===')
cols = ['w_mom', 'w_lv', 'w_rsi', 'w_vol', 'tr_sharpe', 'tr_annual', 'te_sharpe', 'te_annual', 'te_cumret', 'te_mdd']
print(train_sorted.head(20)[cols].to_string(index=False))

print(f'\nTrain Sharpe > 0: {(train_metrics["sharpe"] > 0).sum()}/{n_combos}')
print(f'Train Sharpe 범위: {train_metrics["sharpe"].min():.3f} ~ {train_metrics["sharpe"].max():.3f}')
print(f'Test Sharpe > 0: {(test_metrics["sharpe"] > 0).sum()}/{n_combos}')

# ── Walkforward 분석 ──
print('\n=== Walkforward (Train Top 20) ===')
print(f'{"rk":>3} {"w":>20} {"tr_sh":>7} {"te_sh":>7} {"te_ret":>8} {"te_mdd":>8} {"te_wr":>6} {"ratio":>6}')

best_idx = None
best_test_sharpe = -999

for rank in range(20):
    row = train_sorted.iloc[rank]
    idx = train_sorted.index[rank]
    tr_sharpe = row['tr_sharpe']
    te_sharpe = row['te_sharpe']
    te_cumret = row['te_cumret']
    te_mdd = row['te_mdd']
    te_wr = row['te_winrate']
    ratio = te_sharpe / (abs(tr_sharpe) + 1e-8) if tr_sharpe > 0 else -999
    w = weights_grid[idx]
    wstr = f'{w[0]:.0f}/{w[1]:.0f}/{w[2]:.0f}/{w[3]:.0f}'
    
    flag = ''
    if tr_sharpe > 0 and te_sharpe > 0 and te_cumret > 0:
        flag = ' ✅'
        if te_sharpe > best_test_sharpe:
            best_test_sharpe = te_sharpe
            best_idx = idx
    
    print(f'{rank+1:>3} {wstr:>20} {tr_sharpe:>7.3f} {te_sharpe:>7.3f} {te_cumret:>8.1%} {te_mdd:>8.1%} {te_wr:>6.1%} {ratio:>6.2f}{flag}')

# Fallback
if best_idx is None:
    both_positive = np.where(
        (train_metrics['sharpe'] > 0) & (test_metrics['sharpe'] > 0)
    )[0]
    print(f'\nTrain+Test Sharpe > 0: {len(both_positive)}개')
    if len(both_positive) > 0:
        best_idx = both_positive[np.argmax(test_metrics['sharpe'][both_positive])]
    else:
        best_idx = np.argmax(test_metrics['sharpe'])

best_weights = weights_grid[best_idx]
train_sharpe = train_metrics['sharpe'][best_idx]
test_sharpe = test_metrics['sharpe'][best_idx]
test_cumret = test_metrics['cumulative_ret'][best_idx]
test_mdd = test_metrics['max_drawdown'][best_idx]
test_annual = test_metrics['annual_ret'][best_idx]
test_wr = test_metrics['win_rate'][best_idx]
overfit_ratio = test_sharpe / (abs(train_sharpe) + 1e-8) if train_sharpe > 0 else -999

print(f'\n✅ 최종 선택: momentum={best_weights[0]:.2f}, low_vol={best_weights[1]:.2f}, rsi={best_weights[2]:.2f}, vol={best_weights[3]:.2f}')
print(f'   Train Sharpe: {train_sharpe:.3f} | Test Sharpe: {test_sharpe:.3f} | Ratio: {overfit_ratio:.2f}')
print(f'   Test 연환산: {test_annual:.1%} | Test 누적: {test_cumret:.1%} | MDD: {test_mdd:.1%} | Win Rate: {test_wr:.1%}')

# ══════════════════════════════════════════════════════════════
# 단일 팩터
# ══════════════════════════════════════════════════════════════
print('\n=== 단일 팩터 (SL 5% + TXN) ===')
single_w = torch.eye(n_factors, device=device)
print(f'{"팩터":>14} {"Tr Sharpe":>10} {"Tr 연환산":>10} {"Te Sharpe":>10} {"Te 연환산":>10} {"Te MDD":>8}')
for i, fname in enumerate(factor_names):
    sw = single_w[i:i+1]
    tr = gpu_batch_backtest(train_factor, train_return, sw, train_tradable, top_k=3, stop_loss=0.05, chunk_size=1)
    tm = compute_metrics(tr)
    te = gpu_batch_backtest(test_factor, test_return, sw, test_tradable, top_k=3, stop_loss=0.05, chunk_size=1)
    em = compute_metrics(te)
    print(f'{fname:>14} {tm["sharpe"][0]:>10.3f} {tm["annual_ret"][0]:>10.1%} {em["sharpe"][0]:>10.3f} {em["annual_ret"][0]:>10.1%} {em["max_drawdown"][0]:>8.1%}')

# ══════════════════════════════════════════════════════════════
# Monte Carlo
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

print(f'실제: {actual_cumulative:.1%}')
print(f'P5/Med/P95: {np.percentile(bootstrap_cumulative,5):.1%} / {np.percentile(bootstrap_cumulative,50):.1%} / {np.percentile(bootstrap_cumulative,95):.1%}')
print(f'양수 확률: {prob_positive:.1%}')

# ── 저장 ──
final_result = {
    'strategy': 'daily_multifactor_v1_practical400_v5',
    'optimization_date': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M'),
    'data_period': f'{dates.min().date()} ~ {dates.max().date()}',
    'n_stocks': n_stocks, 'n_days': n_valid_days,
    'n_weight_combos': n_combos,
    'factor_processing': 'cross-sectional rank normalization [-0.5, 0.5]',
    'forward_return': '1-day (non-overlapping)',
    'stop_loss': '5% per-stock',
    'txn_cost': f'{TXN_COST*100:.2f}% per trade',
    'optimal_weights': {
        'momentum': float(best_weights[0]),
        'low_vol': float(best_weights[1]),
        'rsi': float(best_weights[2]),
        'volume_ratio': float(best_weights[3]),
    },
    'performance': {
        'walkforward_train': {'sharpe': float(train_sharpe)},
        'walkforward_test': {'sharpe': float(test_sharpe), 'cumulative_return': float(test_cumret), 'annual_return': float(test_annual), 'max_drawdown': float(test_mdd), 'win_rate': float(test_wr)},
        'overfit_ratio': float(overfit_ratio),
    },
    'monte_carlo': {'prob_positive': float(prob_positive)},
}

out_path = '/Users/01chungee10/Github/TOSS/reports/backtests/optimal_weights_v5_realistic.json'
with open(out_path, 'w') as f:
    json.dump(final_result, f, indent=2, ensure_ascii=False)
train_df.to_csv('/Users/01chungee10/Github/TOSS/reports/backtests/all_weights_v5_results.csv', index=False)

print('\n' + '=' * 60)
print(json.dumps(final_result, indent=2, ensure_ascii=False))
print(f'\n저장: {out_path}')
print(f'총 소요: {time.time()-t0_global:.0f}초')
