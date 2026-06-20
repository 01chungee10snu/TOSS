from __future__ import annotations

from dataclasses import dataclass

from toss_alpha.data.schema import PositionState


_TRANSITIONS: dict[PositionState, dict[str, PositionState]] = {
    "FLAT": {
        "submit_entry": "ENTRY_PENDING",
        "block": "BLOCKED",
    },
    "ENTRY_PENDING": {
        "fill_entry": "LONG",
        "cancel_entry": "FLAT",
        "block": "BLOCKED",
    },
    "LONG": {
        "submit_trim": "TRIM_PENDING",
        "submit_exit": "EXIT_PENDING",
        "trigger_cooldown": "COOLDOWN",
        "block": "BLOCKED",
    },
    "TRIM_PENDING": {
        "fill_trim": "LONG",
        "cancel_trim": "LONG",
        "submit_exit": "EXIT_PENDING",
        "block": "BLOCKED",
    },
    "EXIT_PENDING": {
        "fill_exit": "FLAT",
        "cancel_exit": "LONG",
        "block": "BLOCKED",
    },
    "COOLDOWN": {
        "cooldown_complete": "FLAT",
        "block": "BLOCKED",
    },
    "BLOCKED": {
        "unblock": "FLAT",
    },
}


@dataclass
class PositionLifecycleMachine:
    state: PositionState = "FLAT"

    def transition(self, action: str) -> PositionState:
        next_state = _TRANSITIONS.get(self.state, {}).get(action)
        if next_state is None:
            raise ValueError(f"invalid transition: state={self.state} action={action}")
        self.state = next_state
        return self.state
