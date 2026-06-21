# TOSS Daily-Band Optimizer: 0.1~0.4%/day 구간 탐색 결과
# 2026-06-21

## 연결
- [[toss-quant-sentiment-hybrid-overlay-2026-06-21]]
- [[toss-quant-practical-universe-2026-06-21]]
- [[toss-quant-fusion-3layer-2026-06-21]]

## 핵심 결론

### 0.44%/day 세 가지 레버리지
| 레버리지 | 전략 | Sizing | Sharpe | MDD | 일평균 | 효과 |
|----------|------|--------|--------|-----|--------|------|
| 1 | ml_rerank | cf=0.8, not=300K | 5.68 | -14.5% | 0.444% | 기존 채택, Pareto 밖 |
| 2 | fusion_rerank | cf=0.6, not=300K | **5.96** | **-6.4%** | 0.401% | Sharpe 최고 + MDD 3배 안전 |
| 3 | fusion_rerank | cf=0.4, not=300K | **6.14** | **-6.2%** | 0.377% | Sharpe + Sharpe 모두 최고 |

**→ 레버리지 2→3으로 변경해야 함.** Sharpe 6.14, MDD -6.2%로 현존하는 모든 조합 중 Pareto 최고.

### 구간별 Pareto 최적 (Unified Frontier: sizing + conservative + fusion 3-layer)
| Daily Target | 전략 | Config | Sharpe | MDD | 최저년수익 |
|--------------|------|--------|--------|-----|-----------|
| 0.10-0.15% | fusion_rerank ultra_tight | maxpos=4, cf=0.2, not=100K | 5.02 | -3.4% | 16.9% |
| 0.15-0.20% | fusion_rerank ultra_tight | maxpos=8, cf=0.2, not=100K | **5.89** | -4.4% | 34.6% |
| 0.20-0.30% | fusion_rerank ultra_tight | maxpos=4, cf=0.3, not=200K | 5.66 | -5.5% | 41.6% |
| 0.20-0.30% | ml_rerank (1.0 notional) | maxpos=8, cf=1.0, not=150K | 5.85 | -7.0% | 45.9% |
| 0.30-0.40% | fusion_rerank default | maxpos=8, cf=0.4, not=300K | **6.14** | -6.2% | 49.5% |
| 0.30-0.40% | ml_rerank default | maxpos=8, cf=0.8, not=200K | 6.19 | -8.3% | 59.9% |
| 0.40+% | fusion_rerank default | maxpos=8, cf=0.6, not=300K | 5.96 | -6.4% | 43.0% |
| 0.40+% | ml_rerank default | maxpos=8, cf=0.6, not=500K | 4.99 | -13.6% | 77.6% |

### 앙상블 평가 결과
- **Pure fusion이 앙상블보다 Sharpe 약 25% 높음** (6.14 vs 4.71~5.58)
- Macro regime gate 정확성이 부족하여 wrong switch가 noise 증가
- 앙상블은 macro gate 개선 후 재시도 권장
- Oscillation insight: 2024(횡보) fusion 84% > ml 49%, 2026(상승) ml 94% > fusion 43%

### 현실적 목표 재설정
- 일 0.1% = 연 28.6%, 100만 10년 → 1,241만
- 일 0.44% = 연 172%, 100만 3년 복리 → 7.26배 (626%)
- 일 0.5% = 연 251% → 2025년 운 외에는 재현 불가능
- **일 0.44% = 월드 톱티어 퀀트 상한선(연 50~100%)의 2배 → 도전 가능한 현실적 목표**

## 구간별 시뮬레이션 (100만원 기준, 3년 복리)

### 구간 0.15~0.20% daily (conservative band)
- 전략: fusion_rerank ultra_tight maxpos=8 cf=0.2 not=100K
- 100만원 3년 후: 2.44배 = **244만원**
- Sharpe 5.89, MDD -4.4%

### 구간 0.30~0.40% daily (optimal zone)
- 전략: fusion_rerank default maxpos=8 cf=0.4 not=300K
- 100만원 3년 후: 3.25배 = **325만원** (Sharpe 6.14, MDD -6.2%)
- 2025 단일 연도: daily 0.654%, 3년 간 연동시 164.8% × 1年

### 구간 0.40%+ daily (aggressive zone)
- 전략: fusion_rerank default maxpos=8 cf=0.6 not=300K (most stable)
- 100만원 3년 후: 7.26배 = **726만원**
- Sharpe 5.96, MDD -6.4%

## Forward Tracking 업데이트 필요
- 현재 cron: ml_rerank cf=0.8 not=300K (Sharpe 5.68, MDD -14.5%)
- 권장 변경: fusion_rerank cf=0.4 not=300K (Sharpe 6.14, MDD -6.2%)
- 근거: Sharpe + MDD 모두 개선, Pareto 최상단

## 반복된 결론 (decision log)
- 2025 raw +94% → min_volume=10,000 정화 → +17% (운→데이터 버그 수정)
- OLD 2025 팬텀 → NEW practical universe 재현 가능
- ml_rerank 강세장 강점, fusion_rerank 방어 강점 → regime 앙상블 시도
  → macro gate 정확성 부족으로 ensemble 성능 저하 → pure fusion 우선
- 0 40% 이상 Sharpe 급락 구간 확인 → 유의미한 구간 경계
- 0.44%/day 현실적 상한선 → 하드커트 (5년 1,340만원)

## 데이터 산출물
- sizing_frontier CSV: /Users/01chungee10/Github/TOSS/reports/harness/sizing_frontier_20260621.csv
- sizing_frontier MD: /Users/01chungee10/Github/TOSS/reports/harness/sizing_frontier_20260621.md
- conservative_frontier CSV: /Users/01chungee10/Github/TOSS/reports/harness/conservative_frontier_20260621.csv
- conservative_frontier MD: /Users/01chungee10/Github/TOSS/reports/harness/conservative_frontier_20260621.md
- ensemble_frontier CSV: /Users/01chungee10/Github/TOSS/reports/harness/ensemble_frontier_20260621.csv
- ensemble_frontier MD: /Users/01chungee10/Github/TOSS/reports/harness/ensemble_frontier_20260621.md
