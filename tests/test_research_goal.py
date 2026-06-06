from pathlib import Path

import pytest

from toss_alpha.research.goal import VALID_MODES, load_goal


def test_yaml_goal_loads_into_research_goal():
    goal = load_goal(Path("goals/example_momentum.yaml"))
    assert goal.goal_id == "kr_momentum_disclosure_001"
    assert goal.mode == "backtest_only"
    assert goal.symbols == ["005930", "000660"]
    assert goal.start == "2022-01-01"
    assert goal.end == "2025-12-31"
    assert goal.strategy_name == "momentum_volatility_event"
    assert goal.risk_profile == "conservative"


def test_valid_modes_are_restricted():
    assert VALID_MODES == {"research_only", "backtest_only", "paper_only", "manual_draft_only"}


def test_unknown_mode_is_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        """
goal_id: bad
mode: live
universe:
  symbols: ["005930"]
period:
  start: "2022-01-01"
  end: "2022-12-31"
strategy:
  name: momentum
risk_profile: conservative
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid mode"):
        load_goal(p)
