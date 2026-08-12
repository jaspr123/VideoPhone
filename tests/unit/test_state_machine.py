import pytest

from video_guestbook.state_machine import (
    BoothState,
    InvalidTransitionError,
    StateMachine,
)


def test_initial_state_is_ready():
    sm = StateMachine()
    assert sm.state == BoothState.READY


def test_full_happy_path_transition_sequence():
    sm = StateMachine()
    sm.transition(BoothState.PREVIEW)
    sm.transition(BoothState.COUNTDOWN)
    sm.transition(BoothState.RECORDING)
    sm.transition(BoothState.SAVING)
    sm.transition(BoothState.SAVED)
    sm.transition(BoothState.READY)
    assert sm.state == BoothState.READY


def test_error_path_from_recording():
    sm = StateMachine()
    sm.transition(BoothState.PREVIEW)
    sm.transition(BoothState.COUNTDOWN)
    sm.transition(BoothState.RECORDING)
    sm.transition(BoothState.ERROR)
    assert sm.state == BoothState.ERROR
    sm.transition(BoothState.READY)
    assert sm.state == BoothState.READY


@pytest.mark.parametrize(
    "start,target",
    [
        (BoothState.READY, BoothState.COUNTDOWN),
        (BoothState.READY, BoothState.RECORDING),
        (BoothState.READY, BoothState.SAVING),
        (BoothState.READY, BoothState.SAVED),
        (BoothState.PREVIEW, BoothState.RECORDING),
        (BoothState.COUNTDOWN, BoothState.SAVED),
        (BoothState.SAVING, BoothState.RECORDING),
        (BoothState.SAVED, BoothState.RECORDING),
        (BoothState.ERROR, BoothState.RECORDING),
        (BoothState.ERROR, BoothState.SAVED),
    ],
)
def test_invalid_transitions_raise(start, target):
    sm = StateMachine(initial_state=start)
    with pytest.raises(InvalidTransitionError):
        sm.transition(target)
    # State must not change on a rejected transition.
    assert sm.state == start


def test_can_transition_matches_transition_behavior():
    sm = StateMachine()
    assert sm.can_transition(BoothState.PREVIEW) is True
    assert sm.can_transition(BoothState.COUNTDOWN) is False
    assert sm.can_transition(BoothState.RECORDING) is False


@pytest.mark.parametrize(
    "state,expected",
    [
        (BoothState.READY, True),
        (BoothState.PREVIEW, True),
        (BoothState.COUNTDOWN, True),
        (BoothState.RECORDING, True),
        (BoothState.SAVING, False),
        (BoothState.SAVED, False),
        (BoothState.ERROR, False),
        (BoothState.SETTINGS, False),
        (BoothState.DEVELOPER, False),
    ],
)
def test_guest_input_ignored_during_saving(state, expected):
    sm = StateMachine(initial_state=state)
    assert sm.accepts_guest_input is expected


def test_admin_screens_reachable_from_ready_and_back():
    sm = StateMachine()
    sm.transition(BoothState.SETTINGS)
    assert sm.state == BoothState.SETTINGS
    sm.transition(BoothState.DEVELOPER)
    assert sm.state == BoothState.DEVELOPER
    sm.transition(BoothState.SETTINGS)
    sm.transition(BoothState.READY)
    assert sm.state == BoothState.READY


def test_recording_can_cancel_back_to_preview():
    sm = StateMachine()
    sm.transition(BoothState.PREVIEW)
    sm.transition(BoothState.COUNTDOWN)
    sm.transition(BoothState.RECORDING)
    sm.transition(BoothState.PREVIEW)
    assert sm.state == BoothState.PREVIEW


@pytest.mark.parametrize(
    "start,target",
    [
        (BoothState.SETTINGS, BoothState.PREVIEW),
        (BoothState.SETTINGS, BoothState.RECORDING),
        (BoothState.DEVELOPER, BoothState.READY),
        (BoothState.DEVELOPER, BoothState.PREVIEW),
        (BoothState.PREVIEW, BoothState.SETTINGS),
        (BoothState.COUNTDOWN, BoothState.PREVIEW),
    ],
)
def test_admin_transitions_stay_scoped(start, target):
    sm = StateMachine(initial_state=start)
    with pytest.raises(InvalidTransitionError):
        sm.transition(target)
