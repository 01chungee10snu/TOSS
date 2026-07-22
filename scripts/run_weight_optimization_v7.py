#!/usr/bin/env python3
"""
TOSS 가중치 최적화 v7 — 시점정합·유동성·기업행사 보수 검증
1. 당일 종가까지 신호 계산 → 다음 거래일 시가 진입 → 그다음 시가 청산
2. 시점별 60일 이력 + 전일 거래량 1만주 + 20일 중앙 거래대금 5억원
3. 진입 시가 상한가 차단, 가격제한폭 초과 양(+)수익 30% cap, 청산가 결측 평가액 유지
4. Train-only 가중치 선택, Test는 순수 평가
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
raw_df = pd.read_csv(csv_path, dtype={'code': str})
raw_df['Date'] = pd.to_datetime(raw_df['Date'])
invalid_code_rows = int((~raw_df['code'].str.fullmatch(r'\d{6}')).sum())
if invalid_code_rows:
    raise ValueError(f'6자리 종목코드 형식 위반 {invalid_code_rows}건')
duplicate_rows = int(raw_df.duplicated(['code', 'Date']).sum())
if duplicate_rows:
    raise ValueError(f'(code, Date) 중복 {duplicate_rows}건 — 피벗 전에 원자료를 정정해야 합니다')
pivot = raw_df.pivot(index='Date', columns='code', values=['Open','High','Low','Close','Volume'])
close = pivot['Close'].sort_index()
open_ = pivot['Open'].sort_index()
high = pivot['High'].sort_index()
low = pivot['Low'].sort_index()
volume = pivot['Volume'].sort_index()
if not close.index.is_monotonic_increasing or not close.index.is_unique:
    raise ValueError('패널 거래일 인덱스가 오름차순·고유 조건을 만족하지 않습니다')
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
valid_start = 60
valid_end = n_days - 2

# 시점 계약: t 종가까지 신호 → t+1 시가 진입 → t+2 시가 청산
entry_open = open_.shift(-1)
exit_open = open_.shift(-2)
raw_fwd_ret = exit_open / entry_open - 1.0
exit_missing = entry_open.notna() & exit_open.isna()

# 가격제한폭을 벗어난 양(+)수익은 기업행사/조정가격 혼용 가능성이 있어 30%로 제한한다.
# 음(-)수익과 청산가 결측은 제거하지 않아 불리한 결과를 보존한다.
extreme_positive = raw_fwd_ret > 0.30
extreme_negative = raw_fwd_ret < -0.30
fwd_ret = raw_fwd_ret.clip(lower=-1.0, upper=0.30)
fwd_ret = fwd_ret.mask(exit_missing, 0.0)

# 실행 시점 정렬 회귀검사
alignment_i = valid_start
pd.testing.assert_series_equal(entry_open.iloc[alignment_i], open_.iloc[alignment_i + 1], check_names=False)
pd.testing.assert_series_equal(exit_open.iloc[alignment_i], open_.iloc[alignment_i + 2], check_names=False)

# ── 4. 다음 거래일 시가 체결·유동성 필터 ──
# t 종가 대비 t+1 진입 시가가 +29% 이상이면 진입 불가
open_gap = (entry_open - close) / (close + 1e-8)
limit_up_mask = (open_gap >= 0.29).fillna(False)

# 후보 순위는 보수적으로 t-1까지 알려진 정보만 사용한다.
history_days = close.notna().cumsum().shift(1).fillna(0)
turnover = close * volume
median_turnover_20 = turnover.shift(1).rolling(20, min_periods=20).median()
history_ok = history_days >= 60
volume_ok = volume.shift(1).fillna(0) >= 10_000
liquidity_ok = median_turnover_20.fillna(0) >= 500_000_000
entry_price_ok = entry_open.notna() & (entry_open > 0)
factor_valid = pd.DataFrame(True, index=close.index, columns=close.columns)
for factor_df in factors.values():
    factor_valid &= factor_df.notna()

print(f'\n상한가 필터: 시가 gap ≥ 29%')
total_limit_up = limit_up_mask.iloc[valid_start:valid_end].sum().sum()
total_cells = limit_up_mask.iloc[valid_start:valid_end].shape[0] * limit_up_mask.iloc[valid_start:valid_end].shape[1]
print(f'상한가 비율: {total_limit_up:,} / {total_cells:,} ({total_limit_up/total_cells:.2%})')
print(f'진입 전 거래량 ≥ 10,000주 비율: {volume_ok.iloc[valid_start:valid_end].to_numpy().mean():.2%}')
print(f'20일 중앙 거래대금 ≥ 5억원 비율: {liquidity_ok.iloc[valid_start:valid_end].to_numpy().mean():.2%}')
print(f'open-to-open +30% 초과: {int(extreme_positive.iloc[valid_start:valid_end].sum().sum()):,}건')
print(f'open-to-open -30% 미만: {int(extreme_negative.iloc[valid_start:valid_end].sum().sum()):,}건')
print(f'진입 후 청산 시가 결측: {int(exit_missing.iloc[valid_start:valid_end].sum().sum()):,}건')

# ── 5. 텐서 변환 ──
factor_t = torch.zeros(valid_end - valid_start, n_stocks, nf, device=device)
return_t = torch.zeros(valid_end - valid_start, n_stocks, device=device)
for i, fn in enumerate(fnames):
    factor_t[:,:,i] = torch.from_numpy(factors[fn].iloc[valid_start:valid_end].fillna(0).values).float().to(device)
return_t = torch.from_numpy(fwd_ret.iloc[valid_start:valid_end].fillna(0).values).float().to(device)

# t에 순위를 고정하고, t+1 체결 불가는 현금으로 남긴다. 차순위 재선정은 금지한다.
rank_eligible_df = history_ok & volume_ok & liquidity_ok & factor_valid
execution_gate_df = entry_price_ok & ~limit_up_mask
tradable = torch.from_numpy(rank_eligible_df.iloc[valid_start:valid_end].fillna(False).values).to(device)
execution_gate_t = torch.from_numpy(execution_gate_df.iloc[valid_start:valid_end].fillna(False).values).to(device)
exit_missing_t = torch.from_numpy(exit_missing.iloc[valid_start:valid_end].fillna(False).values).to(device)
extreme_positive_t = torch.from_numpy(extreme_positive.iloc[valid_start:valid_end].fillna(False).values).to(device)
extreme_negative_t = torch.from_numpy(extreme_negative.iloc[valid_start:valid_end].fillna(False).values).to(device)
n_valid = valid_end - valid_start
print(f'\nt-1 정보 기반 후보 비율: {tradable.float().mean():.2%}')
print(f't+1 시가 체결 게이트 통과 비율: {execution_gate_t.float().mean():.2%}')

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
def backtest(factor_t, return_t, wt, tradable, execution_gate, missing_exit_t,
             top_k=3, chunk_size=1000, per_side_cost=None):
    cost_rate = TXN + SLIPPAGE if per_side_cost is None else per_side_cost
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
        selected_rank_valid = tradable.unsqueeze(1).expand(-1, n, -1).gather(-1, idx)
        selected_execution = execution_gate.unsqueeze(1).expand(-1, n, -1).gather(-1, idx)
        selected_valid = selected_rank_valid & selected_execution
        tr = torch.where(selected_valid, tr, torch.zeros_like(tr))
        selected_missing_exit = missing_exit_t.unsqueeze(1).expand(-1, n, -1).gather(-1, idx)
        tr = torch.where(selected_valid & selected_missing_exit, torch.zeros_like(tr), tr)

        daily = tr.mean(dim=-1)  # (D, W)

        # 거래비용 + 슬리피지
        if cost_rate > 0:
            prev = idx[:-1]
            curr = idx[1:]
            overlap = torch.zeros(curr.shape[0], n, device=device)
            for a in range(top_k):
                for b in range(top_k):
                    same = curr[:,:,a] == prev[:,:,b]
                    overlap += (same & selected_valid[1:,:,a] & selected_valid[:-1,:,b]).float()
            prev_count = selected_valid[:-1].float().sum(dim=-1)
            curr_count = selected_valid[1:].float().sum(dim=-1)
            traded_sides = prev_count + curr_count - 2 * overlap
            cost = traded_sides / top_k * cost_rate
            daily[1:] -= cost
            daily[0] -= selected_valid[0].float().sum(dim=-1) / top_k * cost_rate
            daily[-1] -= selected_valid[-1].float().sum(dim=-1) / top_k * cost_rate

        daily = torch.clamp(daily, min=-0.999999)

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
tr_g, te_g = execution_gate_t[tr_mask], execution_gate_t[te_mask]
tr_x, te_x = exit_missing_t[tr_mask], exit_missing_t[te_mask]
te_ep, te_en = extreme_positive_t[te_mask], extreme_negative_t[te_mask]

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
    tr_ret = backtest(tr_f, tr_r, wt, tr_t, tr_g, tr_x, top_k=k, chunk_size=chunk)
    tr_m = metrics(tr_ret)

    print(f'\n--- Test (k={k}) ---')
    te_ret = backtest(te_f, te_r, wt, te_t, te_g, te_x, top_k=k, chunk_size=chunk)
    te_m = metrics(te_ret)

    # Train-only 선택 → Test는 순수 평가에만 사용 (lookahead 금지)
    best_tr_idx = np.argmax(tr_m['sh'])
    sel_idx = best_tr_idx
    sel_method = 'Train Sharpe 최고 (Test 미사용 선택)'

    sel_w = wgrid[sel_idx]
    yearly_test = {}
    test_dates = dates[te_mask]
    for year in sorted(np.unique(test_dates.year)):
        year_mask = np.asarray(test_dates.year == year)
        year_metrics = metrics(te_ret[sel_idx:sel_idx+1, year_mask])
        yearly_test[str(int(year))] = {
            'sharpe': float(year_metrics['sh'][0]),
            'cumulative_return': float(year_metrics['cum'][0]),
            'annual_return': float(year_metrics['ann'][0]),
            'max_drawdown': float(year_metrics['mdd'][0]),
            'win_rate': float(year_metrics['wr'][0]),
            'days': int(year_mask.sum()),
        }

    selected_weight_t = wt[sel_idx:sel_idx+1]
    cost_stress = {}
    for bps in (13, 31, 50, 75):
        stressed = backtest(te_f, te_r, selected_weight_t, te_t, te_g, te_x, top_k=k, chunk_size=1,
                            per_side_cost=bps / 10000.0)
        stressed_metrics = metrics(stressed)
        cost_stress[f'{bps}bps_per_side'] = {
            'sharpe': float(stressed_metrics['sh'][0]),
            'cumulative_return': float(stressed_metrics['cum'][0]),
            'annual_return': float(stressed_metrics['ann'][0]),
            'max_drawdown': float(stressed_metrics['mdd'][0]),
        }

    with torch.no_grad():
        selected_scores = torch.einsum('dsf,f->ds', te_f, selected_weight_t[0])
        selected_scores = selected_scores.masked_fill(~te_t, float('-inf'))
        selected_idx = selected_scores.topk(k, dim=-1).indices
        selected_valid_diag = te_t.gather(-1, selected_idx) & te_g.gather(-1, selected_idx)
        selection_quality = {
            'selected_slots': int(selected_valid_diag.sum().cpu()),
            'selected_missing_exit': int((te_x.gather(-1, selected_idx) & selected_valid_diag).sum().cpu()),
            'selected_extreme_positive_capped': int((te_ep.gather(-1, selected_idx) & selected_valid_diag).sum().cpu()),
            'selected_extreme_negative_uncapped': int((te_en.gather(-1, selected_idx) & selected_valid_diag).sum().cpu()),
        }

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
        'test_years': yearly_test,
        'cost_stress_test': cost_stress,
        'test_selection_data_quality': selection_quality,
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

# 승격 게이트는 성과 선택이 아니라 사전 정의된 보수적 배포 판정이다.
promotion_checks = {}
for key, r in results_by_k.items():
    yearly_positive = all(v['cumulative_return'] > 0 for v in r['test_years'].values())
    checks = {
        'train_sharpe_positive': r['train_sharpe'] > 0,
        'test_cumulative_positive': r['test_cumret'] > 0,
        'test_mdd_at_least_minus_25pct': r['test_mdd'] >= -0.25,
        'all_test_years_positive': yearly_positive,
        '31bps_per_side_cumulative_positive': r['cost_stress_test']['31bps_per_side']['cumulative_return'] > 0,
    }
    promotion_checks[key] = {'checks': checks, 'passed': all(checks.values())}
promotion_candidates = [key for key, value in promotion_checks.items() if value['passed']]
promotion_verdict = 'READY' if promotion_candidates else 'BLOCKED'

# ── Monte Carlo (가장 현실적인 k=10 기준) ──
print('\n=== Monte Carlo (k=10 최적 가중치) ===')
best_k10 = results_by_k['k=10']
best_w10 = torch.from_numpy(np.array(best_k10['sel_weights'])).float().to(device).unsqueeze(0)
full_ret = backtest(factor_t, return_t, best_w10, tradable, execution_gate_t, exit_missing_t,
                    top_k=10, chunk_size=1)
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
    'strategy': 'daily_multifactor_v1_practical400_v7',
    'optimization_date': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M'),
    'panel_period': f'{pd.to_datetime(close.index).min().date()} ~ {pd.to_datetime(close.index).max().date()}',
    'backtest_period': f'{dates.min().date()} ~ {dates.max().date()}',
    'n_stocks': n_stocks,
    'panel_days': int(close.shape[0]),
    'backtest_days': n_valid,
    'n_weight_combos': n_combos,
    'factors': fnames,
    'factor_processing': 'cross-sectional rank normalization [-0.5, 0.5]',
    'decision_time_contract': 'eligibility uses data through t-1; factor score uses close[t]; enter open[t+1]; exit open[t+2]',
    'entry_failure_policy': 'rank fixed at t; failed t+1 open/limit-up slots remain cash; no future-informed replacement',
    'forward_return': 'next-open to following-open, 1-day non-overlapping',
    'stop_loss': 'none; overnight open-to-open path cannot guarantee a 5% fill',
    'txn_cost': f'{TXN*100:.2f}%/side',
    'slippage': f'{SLIPPAGE*100:.2f}%/side',
    'limit_up_filter': 'open gap >= 29% excluded',
    'point_in_time_eligibility': {
        'minimum_valid_history_observations_through_t_minus_1': 60,
        'minimum_previous_volume_shares': 10000,
        'minimum_20d_median_turnover_krw': 500000000,
        'rank_eligibility_rate': float(tradable.float().mean().cpu()),
        'entry_execution_gate_rate': float(execution_gate_t.float().mean().cpu()),
        'eligible_count_per_day_min': int(tradable.sum(dim=1).min().cpu()),
        'eligible_count_per_day_median': float(tradable.sum(dim=1).float().median().cpu()),
    },
    'data_quality': {
        'duplicate_code_date_rows': duplicate_rows,
        'invalid_six_digit_code_rows': invalid_code_rows,
        'extreme_positive_open_to_open_over_30pct': int(extreme_positive.iloc[valid_start:valid_end].sum().sum()),
        'extreme_negative_open_to_open_below_minus_30pct': int(extreme_negative.iloc[valid_start:valid_end].sum().sum()),
        'missing_exit_open_after_valid_entry': int(exit_missing.iloc[valid_start:valid_end].sum().sum()),
        'positive_return_cap': 0.30,
        'missing_exit_assumption': 0.0,
        'missing_exit_note': 'mark-to-last proxy; unresolved suspension/delisting remains a blocking data-quality issue',
    },
    'universe_validation': {
        'full_coverage_stocks': int(full_coverage),
        'long_gap_stocks_60d': int(long_gaps),
        'verdict': universe_verdict,
        'panel_sha256': panel_sha256,
        'note': 'Missingness alone cannot prove absence of survivorship bias; point-in-time membership is required.',
    },
    'promotion': {
        'verdict': promotion_verdict,
        'passing_candidates': promotion_candidates,
        'checks_by_k': promotion_checks,
        'blocking_data_issues': [
            'point-in-time constituent membership unavailable',
            'open-to-open returns beyond KRX price limits remain in source data',
            'missing exit opens use mark-to-last proxy rather than suspension/delisting resolution',
        ],
    },
    'results_by_k': results_by_k,
}
out = '/Users/01chungee10/Github/TOSS/reports/backtests/optimal_weights_v7_point_in_time.json'
with open(out, 'w') as f:
    json.dump(final, f, indent=2, ensure_ascii=False)

print('\n' + '='*70)
print(json.dumps(final, indent=2, ensure_ascii=False))
print(f'\n저장: {out}')
print(f'총 소요: {time.time()-t0:.0f}초 ({(time.time()-t0)/60:.1f}분)')
