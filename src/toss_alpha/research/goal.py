"""Research goal loader."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from toss_alpha.data.schema import ResearchGoal

VALID_MODES = {"research_only", "backtest_only", "paper_only", "manual_draft_only"}


def load_goal(path: str | Path) -> ResearchGoal:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("goal YAML must be a mapping")

    mode = data.get("mode", "research_only")
    if mode not in VALID_MODES:
        raise ValueError(f"invalid mode: {mode}")

    universe = _required_mapping(data, "universe")
    period = _required_mapping(data, "period")
    strategy = _required_mapping(data, "strategy")
    symbols = universe.get("symbols")
    if not isinstance(symbols, list) or not symbols:
        raise ValueError("universe.symbols must be a non-empty list")

    return ResearchGoal(
        goal_id=str(data["goal_id"]),
        mode=mode,
        symbols=[str(s) for s in symbols],
        start=str(period["start"]),
        end=str(period["end"]),
        strategy_name=str(strategy["name"]),
        strategy_params=dict(strategy.get("params") or {}),
        risk_profile=str(data.get("risk_profile", "conservative")),
    )


def _required_mapping(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a mapping")
    return value
