"""Booth state machine.

States follow PROJECT_SPEC.md section 23 (Milestone 1 scope): READY,
COUNTDOWN, RECORDING, SAVING, SAVED and ERROR.
"""

from __future__ import annotations

import logging
from enum import Enum


class BoothState(str, Enum):
    READY = "READY"
    COUNTDOWN = "COUNTDOWN"
    RECORDING = "RECORDING"
    SAVING = "SAVING"
    SAVED = "SAVED"
    ERROR = "ERROR"


# Architecture rule 12: guest input must be ignored during SAVING.
GUEST_INPUT_STATES = frozenset(
    {BoothState.READY, BoothState.COUNTDOWN, BoothState.RECORDING}
)

VALID_TRANSITIONS: dict[BoothState, frozenset[BoothState]] = {
    BoothState.READY: frozenset({BoothState.COUNTDOWN, BoothState.ERROR}),
    BoothState.COUNTDOWN: frozenset(
        {BoothState.RECORDING, BoothState.READY, BoothState.ERROR}
    ),
    BoothState.RECORDING: frozenset({BoothState.SAVING, BoothState.ERROR}),
    BoothState.SAVING: frozenset({BoothState.SAVED, BoothState.ERROR}),
    BoothState.SAVED: frozenset({BoothState.READY}),
    BoothState.ERROR: frozenset({BoothState.READY}),
}


class InvalidTransitionError(RuntimeError):
    """Raised when an illegal state transition is attempted."""


class StateMachine:
    def __init__(
        self,
        initial_state: BoothState = BoothState.READY,
        logger: logging.Logger | None = None,
    ) -> None:
        self._state = initial_state
        self._logger = logger or logging.getLogger(__name__)

    @property
    def state(self) -> BoothState:
        return self._state

    @property
    def accepts_guest_input(self) -> bool:
        return self._state in GUEST_INPUT_STATES

    def can_transition(self, new_state: BoothState) -> bool:
        return new_state in VALID_TRANSITIONS.get(self._state, frozenset())

    def transition(self, new_state: BoothState) -> BoothState:
        if not self.can_transition(new_state):
            raise InvalidTransitionError(
                f"Cannot transition from {self._state.value} to {new_state.value}"
            )
        old_state = self._state
        self._state = new_state
        self._logger.info("state transition: %s -> %s", old_state.value, new_state.value)
        return old_state
