from toss_alpha.execution.state_machine import PositionLifecycleMachine


def test_position_state_machine_walks_flat_to_long_to_flat():
    machine = PositionLifecycleMachine()

    assert machine.state == "FLAT"
    assert machine.transition("submit_entry") == "ENTRY_PENDING"
    assert machine.transition("fill_entry") == "LONG"
    assert machine.transition("submit_exit") == "EXIT_PENDING"
    assert machine.transition("fill_exit") == "FLAT"


def test_position_state_machine_supports_cooldown_path():
    machine = PositionLifecycleMachine(state="LONG")

    assert machine.transition("trigger_cooldown") == "COOLDOWN"
    assert machine.transition("cooldown_complete") == "FLAT"


def test_position_state_machine_rejects_invalid_transition():
    machine = PositionLifecycleMachine(state="FLAT")

    try:
        machine.transition("fill_exit")
    except ValueError as exc:
        assert "invalid transition" in str(exc)
    else:
        raise AssertionError("expected ValueError")
