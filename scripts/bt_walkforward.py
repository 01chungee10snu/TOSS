#!/usr/bin/env python3
"""Walkforward + parameter robustness test for daily multi-factor strategy."""
import pandas as pd, numpy as np, json, math, warnings
warnings.filterwarnings('ignore')

PU = '/Users/01chungee10/Github/TOSS/reports/backtests/practical_universe_400_2022-01-01_2026-latest_ohlcv_panel.csv'
BUY_C = 6.5/10000; SELL_C = 24.5/10000; CASH = 400000

df = pd.read_csv(PU, dtype={'code':str}, parse_dates=['Date'])
df['code'] = df['code'].astype(str).str.zfill(6)
df = df.sort_values(['code','Date']).reset_index(drop=True)

g = df.groupby('code', group_keys=False)
df['ret_1d'] = g['Close'].pct_change(fill_method=None)
df['mom_5d'] = g['Close'].shift(1)/g['Close'].shift(6)-1
df['vol_20d'] = g['ret_1d'].transform(lambda s: s.shift(1).rolling(20).std())
delta = g['Close'].diff()
avg_gain = delta.clip(lower=0).groupby(df['code']).transform(lambda s: s.rolling(14).mean())
avg_loss = (-delta).clip(lower=0).groupby(df['code']).transform(lambda s: s.rolling(14).mean())
df['rsi_14'] = 100 - 100/(1+avg_gain/avg_loss.replace(0,np.nan))
df['dollar_vol'] = g['Close'].shift(1)*g['Volume'].shift(1)
df['avg_dv_20d'] = g['dollar_vol'].transform(lambda s: s.shift(1).rolling(20).mean())
df['vol_ratio'] = g['Volume'].transform(lambda s: s.shift(1)/s.shift(1).rolling(20).mean().replace(0,np.nan))

dm = df.groupby('Date')['ret_1d'].mean().reset_index()
dm.columns = ['Date','mret']
dm['mvol'] = dm['mret'].rolling(20,min_periods=5).std()
vmed = dm['mvol'].median()
dm['regime'] = dm.apply(lambda r: 'flat_'+('high' if r['mvol']>vmed else 'low')+'_vol' if abs(r['mret'])<0.002
    else ('up' if r['mret']>0 else 'down')+'_'+('high' if r['mvol']>vmed else 'low')+'_vol', axis=1)
regime_map = dict(zip(dm['Date'], dm['regime']))

all_dates = sorted(df['Date'].unique())
dates_bt = [d for d in all_dates if pd.Timestamp('2025-01-01') <= d <= pd.Timestamp('2026-07-16')]

def run_bt(weights, hold_days=3, max_picks=3, sl=-0.05, date_list=None):
    dl = date_list or dates_bt
    cash = CASH; trades = []; daily_eq = []
    for i, d in enumerate(dl):
        if i + hold_days >= len(dl): break
        exit_date = dl[i + hold_days]
        regime = regime_map.get(d, 'unknown')
        if regime not in ('up_low_vol', 'flat_low_vol'):
            daily_eq.append({'date': d, 'regime': regime, 'pnl': 0, 'equity': cash})
            continue
        today = df[df['Date'] == d].copy()
        cands = today[
            (today['avg_dv_20d'] >= 5e8) & today['mom_5d'].notna() & today['vol_20d'].notna() &
            today['rsi_14'].notna() & today['vol_ratio'].notna() &
            today['mom_5d'].between(-0.15, 0.15) & today['rsi_14'].between(30, 70) &
            today['vol_ratio'].between(0.8, 3.0) & (today['vol_20d'] <= 0.08) & (today['Open'] > 1000)
        ].copy()
        if len(cands) < 5:
            daily_eq.append({'date': d, 'regime': regime, 'pnl': 0, 'equity': cash}); continue
        is_mom = regime == 'up_low_vol'
        cands['s_mom'] = cands['mom_5d'].rank(pct=True) if is_mom else (-cands['mom_5d']).rank(pct=True)
        cands['s_lv'] = (-cands['vol_20d']).rank(pct=True)
        cands['s_vn'] = (-(cands['vol_ratio']-1).abs()).rank(pct=True)
        cands['s_rm'] = (-(cands['rsi_14']-50).abs()).rank(pct=True)
        cands['comp'] = weights[0]*cands['s_mom'] + weights[1]*cands['s_lv'] + weights[2]*cands['s_vn'] + weights[3]*cands['s_rm']
        picks = cands.nlargest(max_picks, 'comp')
        dpnl = 0
        for _, p in picks.iterrows():
            code = str(p['code'])
            hd = df[(df['code']==code) & (df['Date']>=d) & (df['Date']<=exit_date)].sort_values('Date')
            if len(hd) < 2: continue
            bp = float(p['Open'])
            sp = float(hd.iloc[-1]['Close'])
            for j, (_, hr) in enumerate(hd.iterrows()):
                if j == 0: continue
                if float(hr['Low'])/bp - 1 <= sl:
                    sp = bp * (1+sl); break
            if bp <= 0 or sp <= 0: continue
            budget = min(80000, cash/max_picks)
            qty = max(1, int(budget/bp))
            cost = qty*bp*(1+BUY_C); proc = qty*sp*(1-SELL_C)
            pnl = proc - cost; cash += pnl; dpnl += pnl
            trades.append({'date': d, 'pnl': pnl})
        daily_eq.append({'date': d, 'regime': regime, 'pnl': dpnl, 'equity': cash})
    return cash, trades, daily_eq

def summarize(cash, trades, daily_eq):
    pnl = cash - CASH; ret = pnl/CASH*100
    eq = [d['equity'] for d in daily_eq] or [cash]
    peak = eq[0]; mdd = 0
    for e in eq:
        peak = max(peak, e); mdd = min(mdd, (e/peak-1)*100)
    deq = pd.DataFrame(daily_eq); deq['r'] = deq['equity'].pct_change()
    sharpe = deq['r'].mean()/deq['r'].std()*math.sqrt(252) if deq['r'].std() > 0 else 0
    tdf = pd.DataFrame(trades); n = len(tdf)
    wins = int((tdf['pnl']>0).sum()) if n else 0
    wr = wins/n*100 if n else 0
    gp = tdf[tdf['pnl']>0]['pnl'].sum() if n else 0
    gl = abs(tdf[tdf['pnl']<0]['pnl'].sum()) if n else 0
    pf = gp/gl if gl > 0 else float('inf')
    return {'pnl': pnl, 'ret': ret, 'mdd': mdd, 'sharpe': sharpe, 'n': n, 'wr': wr, 'pf': pf}

W = [0.40, 0.25, 0.15, 0.20]
dates_train = [d for d in dates_bt if d <= pd.Timestamp('2025-12-31')]
dates_test  = [d for d in dates_bt if d >= pd.Timestamp('2026-01-01')]

print('='*70)
print('  1. Walkforward: train(2025) vs test(2026H1)')
print('='*70)
r_all = summarize(*run_bt(W))
r_tr  = summarize(*run_bt(W, date_list=dates_train))
r_te  = summarize(*run_bt(W, date_list=dates_test))
print(f'\n  {"":15} {"ret":>8} {"Sharpe":>8} {"MDD":>8} {"PF":>6} {"N":>6}')
print(f'  {"-"*55}')
for label, r in [('Full (1.5Y)', r_all), ('Train (2025)', r_tr), ('Test (2026H1)', r_te)]:
    print(f'  {label:15} {r["ret"]:>+7.1f}% {r["sharpe"]:>8.2f} {r["mdd"]:>+7.1f}% {r["pf"]:>6.2f} {r["n"]:>6}')

print(f'\n{"="*70}')
print(f'  2. Weight sensitivity (full period)')
print('='*70)
print(f'\n  {"Weights":42} {"ret":>8} {"Sharpe":>8} {"PF":>6}')
print(f'  {"-"*67}')
for label, w in [
    ('Original (0.40/0.25/0.15/0.20)', W),
    ('Equal (0.25 each)', [0.25,0.25,0.25,0.25]),
    ('Momentum-heavy (0.60/0.15/0.10/0.15)', [0.60,0.15,0.10,0.15]),
    ('Low-vol-heavy (0.20/0.50/0.15/0.15)', [0.20,0.50,0.15,0.15]),
    ('RSI-heavy (0.20/0.20/0.15/0.45)', [0.20,0.20,0.15,0.45]),
    ('Volume-heavy (0.20/0.20/0.45/0.15)', [0.20,0.20,0.45,0.15]),
]:
    r = summarize(*run_bt(w))
    print(f'  {label:42} {r["ret"]:>+7.1f}% {r["sharpe"]:>8.2f} {r["pf"]:>6.2f}')

print(f'\n{"="*70}')
print(f'  3. Hold period sensitivity')
print('='*70)
print(f'\n  {"Hold":>6} {"ret":>8} {"Sharpe":>8} {"MDD":>8} {"PF":>6} {"N":>6}')
print(f'  {"-"*50}')
for hd in [1, 2, 3, 5, 10]:
    r = summarize(*run_bt(W, hold_days=hd))
    print(f'  {hd:>4}d {r["ret"]:>+7.1f}% {r["sharpe"]:>8.2f} {r["mdd"]:>+7.1f}% {r["pf"]:>6.2f} {r["n"]:>6}')

print(f'\n{"="*70}')
print(f'  4. Stop loss sensitivity')
print('='*70)
print(f'\n  {"SL":>8} {"ret":>8} {"Sharpe":>8} {"MDD":>8} {"PF":>6} {"N":>6}')
print(f'  {"-"*50}')
for sl in [-0.03, -0.05, -0.08, -0.10, -0.99]:
    r = summarize(*run_bt(W, sl=sl))
    label = f'{sl*100:.0f}%' if sl > -0.9 else 'None'
    print(f'  {label:>8} {r["ret"]:>+7.1f}% {r["sharpe"]:>8.2f} {r["mdd"]:>+7.1f}% {r["pf"]:>6.2f} {r["n"]:>6}')
