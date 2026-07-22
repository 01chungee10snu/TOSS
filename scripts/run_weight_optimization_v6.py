#!/usr/bin/env python3
"""
TOSS 가중치 최적화 v6 — 현실성 검증 3종 세트
1. 슬리피지/상한가 필터: 전일 대비 +29% 이상 시 매수 불가 (KRX 상한가)
2. top-k 확대: k=3,5,10,20 비교
3. 유니버스 검증: 데이터 내 상장/상폐 이력 확인
"""
import torch
import pandas as pd
import numpy as np
import json
import time
import hashlib

t0 = time.time()
device = torch.device('mps' if torch.backends.mps.is_available() else 'cpu')
print(f'Device: {device}')

# ── 1. 데이터 ──
csv_path = '/Users/01chungee10/Github/TOSS/reports/backtests/practical_universe_400_2022-01-01_2026-latest_ohlcv_panel.csv'
raw_df = pd.read_csv(csv_path)
pivot = raw_df.pivot_table(index='Date', columns='code', values=['Open','High','Low','Close','Volume'], aggfunc='first')
close = pivot['Close'].sort_index()
open_ = pivot['Open'].sort_index()
high = pivot['High'].sort_index()
low = pivot['Low'].sort_index()
volume = pivot['Volume'].sort_index()
n_days, n_stocks = close.shape
print(f'패널: {n_days}일 × {n_stocks}종목')

# ── 2. 유니버스 검증 ──
print('\n=== 유니버스 검증 ===')
daily_ret = close.pct_change(fill_method=None)
# 각 종목의 연속 NaN 구간 = 상폐/거래중단
def max_consecutive_missing(series):
    missing = series.isna()
    groups = (~missing).cumsum()
    return int(missing.astype(int).groupby(groups).cumsum().max())

max_gap = close.apply(max_consecutive_missing, axis=0)
print(f'종목별 최대 거래중단 일수: 평균 {max_gap.mean():.0f}일, 최대 {max_gap.max()}일')
# 거래중단 60일 이상 = 사실상 상폐
long_gaps = (max_gap > 60).sum()
print(f'60일+ 거래중단 종목: {long_gaps}개')

# 2022-01-04부터 2026-07-16까지 매일 데이터가 있는 종목
full_coverage = close.notna().all().sum()
print(f'전 기간 결측 없는 종목: {full_coverage}개 / {n_stocks}개')
universe_verdict = 'INCONCLUSIVE_REQUIRES_POINT_IN_TIME_MEMBERSHIP'
print('→ 유니버스 결론: 결측 패턴만으로 생존자편향 판정 불가')
print('  시점별 구성종목 이력 또는 고정 패널 스냅샷/manifest가 필요')

with open(csv_path, 'rb') as panel_file:
    panel_sha256 = hashlib.sha256(panel_file.read()).hexdigest()
print(f'패널 SHA-256: {panel_sha256}')

# ── 3. 팩터 ──
mom_5d = close.pct_change(5, fill_method=None)
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

factors = {
    'momentum': rank_normalize(mom_5d),
    'low_vol': rank_normalize(low_vol_raw),
    'rsi': rank_normalize(rsi_reversal),
    'volume_ratio': rank_normalize(volume_ratio),
}
fnames = list(factors.keys())
nf = len(fnames)
valid_start = 25
valid_end = n_days - 2

# 1일 forward return (non-overlapping)
fwd_ret = close.pct_change(fill_method=None).shift(-1)

# ── 4. ★상한가 필터★ ──
# KRX 상한가: 전일 종가 대비 ±30% (코스닥), ±29% 등. 보수적으로 29% 적용
# 전일 종가 대비 당일 시가가 +29% 이상이면 매수 불가 (시가부터 상한가)
prev_close = close.shift(1)
open_gap = (open_ - prev_close) / (prev_close + 1e-8)
# 상한가 종목: 당일 시가가 전일 대비 +29% 이상 → 매수 불가
limit_up_mask = (open_gap >= 0.29).fillna(False)
# 당일 high가 전일 대비 +29% 이상이면 일중 상한가 도달
intraday_limit = ((high - prev_close) / (prev_close + 1e-8) >= 0.29).fillna(False)
# 매도 시에도 하한가(-29%)면 매도 불가
limit_down_mask = (open_gap <= -0.29).fillna(False)

print(f'\n상한가 필터: 시가 gap ≥ 29%')
total_limit_up = limit_up_mask.iloc[valid_start:valid_end].sum().sum()
total_cells = limit_up_mask.iloc[valid_start:valid_end].shape[0] * limit_up_mask.iloc[valid_start:valid_end].shape[1]
print(f'상한가 비율: {total_limit_up:,} / {total_cells:,} ({total_limit_up/total_cells:.2%})')

# ── 5. 텐서 변환 ──
factor_t = torch.zeros(valid_end - valid_start, n_stocks, nf, device=device)
return_t = torch.zeros(valid_end - valid_start, n_stocks, device=device)
for i, fn in enumerate(fnames):
    factor_t[:,:,i] = torch.from_numpy(factors[fn].iloc[valid_start:valid_end].fillna(0).values).float().to(device)
return_t = torch.from_numpy(fwd_ret.iloc[valid_start:valid_end].fillna(0).values).float().to(device)

# 거래 가능 마스크: volume > 0 AND 상한가 아님
vol_mask = torch.from_numpy((volume.iloc[valid_start:valid_end].fillna(0).values > 0)).to(device)
limit_up_t = torch.from_numpy(limit_up_mask.iloc[valid_start:valid_end].fillna(False).values).to(device)
tradable = vol_mask & ~limit_up_t  # 상한가 종목은 매수 불가
n_valid = valid_end - valid_start
print(f'\n매수 가능 비율: {tradable.float().mean():.2%} (상한가 제외)')

# ── 6. 가중치 ──
from itertools import product
steps = np.arange(0.0, 1.01, 0.05)
wgrid = []
for w1,w2,w3,w4 in product(steps, repeat=4):
    if abs(w1+w2+w3+w4 - 1.0) < 0.025:
        wgrid.append([w1,w2,w3,w4])
wgrid = np.array(wgrid)
n_combos = len(wgrid)
wt = torch.from_numpy(wgrid).float().to(device)
chunk = max(500, int(4e9 / (n_valid * n_stocks * 4)))

TXN = 0.0003  # 0.03% per side
SLIPPAGE = 0.001  # 0.1% slippage per trade

# ── 7. 백테스트 함수 ──
def backtest(factor_t, return_t, wt, tradable, top_k=3, stop_loss=0.05, chunk_size=1000):
    nw = wt.shape[0]
    nc = (nw + chunk_size - 1) // chunk_size
    all_ret = []

    for ci in range(nc):
        s = ci * chunk_size
        e = min((ci+1)*chunk_size, nw)
        wc = wt[s:e]
        n = e - s

        scores = torch.einsum('dsf,wf->dws', factor_t, wc)
        scores = scores.masked_fill(~tradable.unsqueeze(1), float('-inf'))
        _, idx = scores.topk(top_k, dim=-1)
        del scores

        ret_exp = return_t.unsqueeze(1).expand(-1, n, -1)
        tr = ret_exp.gather(-1, idx)  # (D, W, k)

        if stop_loss > 0:
            tr = torch.clamp(tr, min=-stop_loss)

        daily = tr.mean(dim=-1)  # (D, W)

        # 거래비용 + 슬리피지
        if TXN > 0 or SLIPPAGE > 0:
            prev = idx[:-1]
            curr = idx[1:]
            overlap = torch.zeros(curr.shape[0], n, device=device)
            for a in range(top_k):
                for b in range(top_k):
                    overlap += (curr[:,:,a] == prev[:,:,b]).float()
            turnover = (top_k - overlap) / top_k
            cost = turnover * 2 * (TXN + SLIPPAGE)
            daily[1:] -= cost
            daily[0] -= 2 * (TXN + SLIPPAGE)

        del tr, idx
        all_ret.append(daily.T.clone())
        del daily

        if (ci+1) % 5 == 0 or ci == nc-1:
            print(f'  청크 {ci+1}/{nc} ({e/nw*100:.0f}%) - {time.time()-t0:.0f}초')

    return torch.cat(all_ret, dim=0)

def metrics(ret):
    cum = (1+ret).prod(dim=1) - 1
    ny = ret.shape[1] / 252
    ann = (1+cum)**(1/ny) - 1
    sh = ret.mean(dim=1) / (ret.std(dim=1)+1e-8) * (252**0.5)
    c = (1+ret).cumprod(dim=1)
    dd = (c - c.cummax(dim=1)[0]) / c.cummax(dim=1)[0]
    mdd = dd.min(dim=1)[0]
    wr = (ret > 0).float().mean(dim=1)
    return {'cum':cum.cpu().numpy(), 'ann':ann.cpu().numpy(), 'sh':sh.cpu().numpy(),
            'mdd':mdd.cpu().numpy(), 'wr':wr.cpu().numpy()}

# ── Walkforward split ──
dates = pd.to_datetime(close.index[valid_start:valid_end])
tr_mask = (dates.year <= 2024).astype(bool)
te_mask = (dates.year >= 2025).astype(bool)

tr_f, te_f = factor_t[tr_mask], factor_t[te_mask]
tr_r, te_r = return_t[tr_mask], return_t[te_mask]
tr_t, te_t = tradable[tr_mask], tradable[te_mask]

print(f'\nTrain: {tr_mask.sum()}일 | Test: {te_mask.sum()}일')
print(f'거래비용: {TXN*100:.2f}%/side + 슬리피지: {SLIPPAGE*100:.2f}%/side')

# ══════════════════════════════════════════════════════════════
# top-k 비교: k=3, 5, 10, 20
# ══════════════════════════════════════════════════════════════
results_by_k = {}

for k in [3, 5, 10, 20]:
    print(f'\n{"="*60}')
    print(f'top_k = {k} (상한가 필터 + TXN + SLIPPAGE)')
    print(f'{"="*60}')

    print(f'\n--- Train (k={k}) ---')
    tr_ret = backtest(tr_f, tr_r, wt, tr_t, top_k=k, stop_loss=0.05, chunk_size=chunk)
    tr_m = metrics(tr_ret)

    print(f'\n--- Test (k={k}) ---')
    te_ret = backtest(te_f, te_r, wt, te_t, top_k=k, stop_loss=0.05, chunk_size=chunk)
    te_m = metrics(te_ret)

    # Train-only 선택 → Test는 순수 평가에만 사용 (lookahead 금지)
    best_tr_idx = np.argmax(tr_m['sh'])
    sel_idx = best_tr_idx
    sel_method = 'Train Sharpe 최고 (Test 미사용 선택)'

    sel_w = wgrid[sel_idx]
    r = {
        'k': k,
        'sel_weights': sel_w.tolist(),
        'sel_method': sel_method,
        'train_sharpe': float(tr_m['sh'][sel_idx]),
        'test_sharpe': float(te_m['sh'][sel_idx]),
        'test_cumret': float(te_m['cum'][sel_idx]),
        'test_annual': float(te_m['ann'][sel_idx]),
        'test_mdd': float(te_m['mdd'][sel_idx]),
        'test_winrate': float(te_m['wr'][sel_idx]),
        'train_sharpe_range': [float(tr_m['sh'].min()), float(tr_m['sh'].max())],
        'test_sharpe_range': [float(te_m['sh'].min()), float(te_m['sh'].max())],
        'n_positive_test': int((te_m['sh'] > 0).sum()),
    }
    results_by_k[f'k={k}'] = r

    print(f'\n✅ k={k} 선택: {sel_w} ({sel_method})')
    print(f'   Train Sharpe: {r["train_sharpe"]:.3f} | Test Sharpe: {r["test_sharpe"]:.3f}')
    print(f'   Test 연환산: {r["test_annual"]:.1%} | Test 누적: {r["test_cumret"]:.1%}')
    print(f'   Test MDD: {r["test_mdd"]:.1%} | Win Rate: {r["test_winrate"]:.1%}')
    print(f'   Test Sharpe>0: {r["n_positive_test"]}/{n_combos}')

    # Train Top 5
    tr_df = pd.DataFrame({
        'w_mom':wgrid[:,0],'w_lv':wgrid[:,1],'w_rsi':wgrid[:,2],'w_vol':wgrid[:,3],
        'tr_sh':tr_m['sh'],'te_sh':te_m['sh'],'te_ann':te_m['ann'],'te_mdd':te_m['mdd'],
    }).sort_values('tr_sh', ascending=False)
    print(f'\n   Train Top 5 (k={k}):')
    print(f'   {"w_mom":>5} {"w_lv":>5} {"w_rsi":>5} {"w_vol":>5} {"tr_sh":>7} {"te_sh":>7} {"te_ann":>8} {"te_mdd":>7}')
    for j in range(min(5, len(tr_df))):
        row = tr_df.iloc[j]
        print(f'   {row.w_mom:>5.2f} {row.w_lv:>5.2f} {row.w_rsi:>5.2f} {row.w_vol:>5.2f} {row.tr_sh:>7.3f} {row.te_sh:>7.3f} {row.te_ann:>8.1%} {row.te_mdd:>7.1%}')

# ══════════════════════════════════════════════════════════════
# 최종 비교표
# ══════════════════════════════════════════════════════════════
print('\n\n' + '='*70)
print('최종 비교: top-k 확대에 따른 현실성 변화')
print('='*70)
print(f'{"k":>4} {"Train Sharpe":>12} {"Test Sharpe":>12} {"Test 연환산":>11} {"Test MDD":>9} {"Win Rate":>8} {"선택 방법":>30}')
print('-'*90)
for key, r in results_by_k.items():
    print(f'{r["k"]:>4} {r["train_sharpe"]:>12.3f} {r["test_sharpe"]:>12.3f} {r["test_annual"]:>11.1%} {r["test_mdd"]:>9.1%} {r["test_winrate"]:>8.1%} {r["sel_method"]:>30}')

# ── Monte Carlo (가장 현실적인 k=10 기준) ──
print('\n=== Monte Carlo (k=10 최적 가중치) ===')
best_k10 = results_by_k['k=10']
best_w10 = torch.from_numpy(np.array(best_k10['sel_weights'])).float().to(device).unsqueeze(0)
full_ret = backtest(factor_t, return_t, best_w10, tradable, top_k=10, stop_loss=0.05, chunk_size=1)
best_daily = full_ret[0].cpu().numpy()

np.random.seed(42)
idx = np.random.randint(0, len(best_daily), (10000, len(best_daily)))
boot = np.prod(1 + best_daily[idx], axis=1) - 1
actual = np.prod(1 + best_daily) - 1
prob_pos = (boot > 0).mean()
print(f'실제 누적: {actual:.1%}')
print(f'P5/Med/P95: {np.percentile(boot,5):.1%} / {np.percentile(boot,50):.1%} / {np.percentile(boot,95):.1%}')
print(f'양수 확률: {prob_pos:.1%}')

results_by_k['monte_carlo_k10'] = {
    'actual_cumulative': float(actual),
    'prob_positive': float(prob_pos),
    'p5': float(np.percentile(boot,5)),
    'p50': float(np.percentile(boot,50)),
    'p95': float(np.percentile(boot,95)),
}

# ── 저장 ──
final = {
    'strategy': 'daily_multifactor_v1_practical400_v6',
    'optimization_date': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M'),
    'data_period': f'{dates.min().date()} ~ {dates.max().date()}',
    'n_stocks': n_stocks,
    'n_days': n_valid,
    'n_weight_combos': n_combos,
    'factors': fnames,
    'factor_processing': 'cross-sectional rank normalization [-0.5, 0.5]',
    'forward_return': '1-day non-overlapping',
    'stop_loss': '5% per-stock',
    'txn_cost': f'{TXN*100:.2f}%/side',
    'slippage': f'{SLIPPAGE*100:.2f}%/side',
    'limit_up_filter': 'open gap >= 29% excluded',
    'universe_validation': {
        'full_coverage_stocks': int(full_coverage),
        'long_gap_stocks_60d': int(long_gaps),
        'verdict': universe_verdict,
        'panel_sha256': panel_sha256,
        'note': 'Missingness alone cannot prove absence of survivorship bias; point-in-time membership is required.',
    },
    'results_by_k': results_by_k,
}
out = '/Users/01chungee10/Github/TOSS/reports/backtests/optimal_weights_v6_realistic.json'
with open(out, 'w') as f:
    json.dump(final, f, indent=2, ensure_ascii=False)

print('\n' + '='*70)
print(json.dumps(final, indent=2, ensure_ascii=False))
print(f'\n저장: {out}')
print(f'총 소요: {time.time()-t0:.0f}초 ({(time.time()-t0)/60:.1f}분)')
