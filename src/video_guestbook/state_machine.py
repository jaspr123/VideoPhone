"""Booth state machine.

States follow PROJECT_SPEC.md section 23 (Milestone 1 scope) plus PREVIEW,
added later: READY, PREVIEW, COUNTDOWN, RECORDING, SAVING, SAVED and ERROR.
SETTINGS and DEVELOPER (added for the attendant admin screens, PROJECT_SPEC.md
section 12) sit off to the side of the guest flow -- reachable only from
READY via a long-press (see main.py's _maybe_handle_admin_press), and only
returning to READY, never appearing in GUEST_INPUT_STATES or in any guest
transition.

PREVIEW sits between READY and COUNTDOWN: the guest lifts the receiver into
a live camera preview (the camera is free -- ffmpeg hasn't claimed it yet,
see main.py's module docstring) and either taps the on-screen record button
or waits out an auto-advance timer before the numeric COUNTDOWN begins. This
replaced an earlier design where RECORDING itself tried to show a live
preview via a second ffmpeg output, which caused real audio-breakup trouble
on hardware -- see CHANGELOG.md. Moving the live preview to PREVIEW (before
ffmpeg ever opens the camera) removes that conflict entirely.

RECORDING can also go directly back to PREVIEW: the guest-facing "Cancel &
restart" action on the RECORDING screen discards the in-progress take and
returns to the live preview rather than saving or erroring (see main.py's
_cancel_recording).
"""

from __future__ import annotations

import logging
from enum import Enum


class BoothState(str, Enum):
    READY = "READY"
    PREVIEW = "PREVIEW"
    COUNTDOWN = "COUNTDOWN"
    RECORDING = "RECORDING"
    SAVING = "SAVING"
    SAVED = "SAVED"
    ERROR = "ERROR"
    SETTINGS = "SETTINGS"
    DEVELOPER = "DEVELOPER"


# Architecture rule 12: guest input must be ignored during SAVING. SETTINGS
# and DEVELOPER are attendant-only (touchscreen taps on their own controls,
# handled separately in main.py) and deliberately excluded here too, so a
# guest's Spacebar/hook-switch input can never leak into the admin screens.
GUEST_INPUT_STATES = frozenset(
    {BoothState.READY, BoothState.PREVIEW, BoothState.COUNTDOWN, BoothState.RECORDING}
)

VALID_TRANSITIONS: dict[BoothState, frozenset[BoothState]] = {
    BoothState.READY: frozenset({BoothState.PREVIEW, BoothState.ERROR, BoothState.SETTINGS}),
    BoothState.PREVIEW: frozenset(
        {BoothState.COUNTDOWN, BoothState.READY, BoothState.ERROR}
    ),
    BoothState.COUNTDOWN: frozenset(
        {BoothState.RECORDING, BoothState.READY, BoothState.ERROR}
    ),
    BoothState.RECORDING: frozenset({BoothState.SAVING, BoothState.ERROR, BoothState.PREVIEW}),
    BoothState.SAVING: frozenset({BoothState.SAVED, BoothState.ERROR}),
    BoothState.SAVED: frozenset({BoothState.READY}),
    BoothState.ERROR: frozenset({BoothState.READY}),
    BoothState.SETTINGS: frozenset({BoothState.READY, BoothState.DEVELOPER}),
    BoothState.DEVELOPER: frozenset({BoothState.SETTINGS}),
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
