import numpy as np

from toss_alpha.research.breakout_ensemble import (
    freeze_topk,
    nonoverlap_signal_indices,
    traded_sides,
)


def test_entry_failure_stays_cash_without_future_replacement():
    scores = np.array([0.9, 0.8, 0.7, 0.6])
    rank_eligible = np.array([True, True, True, True])
    execution_gate = np.array([False, True, True, True])

    idx, valid = freeze_topk(scores, rank_eligible, execution_gate, k=2)

    assert idx.tolist() == [0, 1]
    assert valid.tolist() == [False, True]
    assert 2 not in idx  # t+1 실패 후 차순위 대체선정 금지


def test_topk_shortage_leaves_cash_slots():
    scores = np.array([0.9, 0.8, 0.7])
    rank_eligible = np.array([False, True, False])
    execution_gate = np.array([True, True, True])

    idx, valid = freeze_topk(scores, rank_eligible, execution_gate, k=3)

    assert valid.sum() == 1
    assert idx[valid].tolist() == [1]


def test_nonoverlap_signal_indices_respect_holding_period():
    idx = nonoverlap_signal_indices(start=60, stop=100, hold_days=5)
    assert idx.tolist() == [60, 65, 70, 75, 80, 85, 90, 95]
    assert np.all(np.diff(idx) == 5)


def test_turnover_counts_only_valid_positions():
    prev_idx = np.array([1, 2, 0])
    prev_valid = np.array([True, True, False])
    curr_idx = np.array([2, 3, 0])
    curr_valid = np.array([True, True, False])

    # 종목 2는 유지, 종목 1 청산, 종목 3 진입 = 2 sides
    assert traded_sides(prev_idx, prev_valid, curr_idx, curr_valid) == 2
