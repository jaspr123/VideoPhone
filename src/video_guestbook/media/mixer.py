"""Best-effort ALSA capture gain discovery and one-shot adjustment.

PROJECT_SPEC.md section 6 ("Audio strategy") explicitly warns against
continuous real-time automatic gain control during a message, but does call
for locking a conservative capture gain once, before recording starts,
based on a quick countdown-time level check. This module implements that
one-shot nudge.

Every USB audio adapter names its ALSA mixer controls differently, so the
control name is discovered at runtime via `amixer scontrols` rather than
hardcoded. This is a heuristic (looks for a control with "mic" or "capture"
in its name) and has only been exercised against the devices mentioned in
PROJECT_SPEC.md -- callers must not let a failure here block recording.

Some devices' "Mic" control bundles a Playback element (a monitoring/
sidetone level, visible in alsamixer's F3 view) together with the actual
Capture element (visible in alsamixer's F4 view, what recording actually
uses) under the same name -- `amixer sget` prints Playback before Capture
for these. All percent-reading in this module is anchored specifically to
the "Capture" line for that reason; do not naively grab the first `[NN%]`
in `amixer sget` output.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass

from video_guestbook.media.audio_levels import find_arecord

_CARD_NAME_RE = re.compile(r"CARD=([^,]+)")
_CARD_INDEX_RE = re.compile(r"^[a-z]*hw:(\d+)", re.IGNORECASE)
_CONTROL_NAME_RE = re.compile(r"Simple mixer control '([^']+)'")

# Some controls (commonly named 'Mic' on USB audio adapters) bundle BOTH a
# playback/monitoring volume AND a capture volume under the same name --
# `amixer sget` prints the Playback line before the Capture line for these.
# A naive "first [NN%] in the output" match silently reads/sets the
# monitoring level instead of the actual recording gain. Always anchor to
# the line that says "Capture".
_CAPTURE_PERCENT_RE = re.compile(r"Capture[^\[\n]*\[(\d+)%\]")


class MixerError(RuntimeError):
    """Raised when the ALSA card/control can't be resolved or amixer fails."""


def find_amixer() -> str:
    path = shutil.which("amixer")
    if not path:
        raise MixerError("amixer was not found on PATH (install alsa-utils)")
    return path


def resolve_card_index(audio_device: str) -> int:
    """Resolve an ALSA card index from a 'CARD=NAME' or 'hw:N,M' device string."""
    name_match = _CARD_NAME_RE.search(audio_device)
    if name_match:
        card_name = name_match.group(1)
        arecord = find_arecord()
        try:
            result = subprocess.run(
                [arecord, "-l"], capture_output=True, text=True, check=False
            )
        except OSError as exc:
            raise MixerError(f"could not run 'arecord -l': {exc}") from exc
        pattern = re.compile(
            rf"^card (\d+): {re.escape(card_name)}\b", re.MULTILINE | re.IGNORECASE
        )
        match = pattern.search(result.stdout)
        if not match:
            raise MixerError(
                f"could not find ALSA card named {card_name!r} in 'arecord -l' output"
            )
        return int(match.group(1))

    index_match = _CARD_INDEX_RE.match(audio_device)
    if index_match:
        return int(index_match.group(1))

    raise MixerError(f"could not parse a card name or index from audio_device {audio_device!r}")


def list_capture_controls(card_index: int) -> list[str]:
    amixer = find_amixer()
    try:
        result = subprocess.run(
            [amixer, "-c", str(card_index), "scontrols"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise MixerError(f"could not run 'amixer scontrols': {exc}") from exc
    if result.returncode != 0:
        raise MixerError(
            f"'amixer scontrols' failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return _CONTROL_NAME_RE.findall(result.stdout)


def find_capture_control(card_index: int) -> str:
    """Best-effort: the first control whose name mentions mic or capture."""
    controls = list_capture_controls(card_index)
    for name in controls:
        if "mic" in name.lower():
            return name
    for name in controls:
        if "capture" in name.lower():
            return name
    raise MixerError(
        f"no Mic/Capture-like mixer control found on card {card_index} "
        f"(available controls: {controls})"
    )


def get_raw_control_info(card_index: int, control: str) -> str:
    """Return the full, unparsed `amixer sget` output for a control.

    Useful for diagnostics: shows both the Playback and Capture lines (if
    the control has both) so a human can see exactly what's configured
    rather than trusting a single parsed number.
    """
    amixer = find_amixer()
    try:
        result = subprocess.run(
            [amixer, "-c", str(card_index), "sget", control],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise MixerError(f"could not run 'amixer sget': {exc}") from exc
    if result.returncode != 0:
        raise MixerError(
            f"'amixer sget {control}' failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def get_capture_percent(card_index: int, control: str) -> int:
    stdout = get_raw_control_info(card_index, control)
    match = _CAPTURE_PERCENT_RE.search(stdout)
    if not match:
        raise MixerError(
            f"could not find a Capture level for control {control!r} "
            f"(is this actually a capture-capable control? raw output: {stdout!r})"
        )
    return int(match.group(1))


def set_capture_percent(card_index: int, control: str, percent: int) -> None:
    """Set a control's Capture volume.

    CAVEAT (unverified on real hardware): for a combined control that has
    both Playback and Capture volume elements (see get_capture_percent's
    docstring), plain `amixer sset NAME VALUE%` may set BOTH elements to
    the same value rather than Capture alone -- alsa-utils' CLI does not
    have a universally reliable "capture only" qualifier across versions.
    Callers that care whether the Playback/monitoring level was also
    changed should call get_raw_control_info() before and after and
    compare, rather than trusting this blindly.
    """
    percent = max(0, min(100, percent))
    amixer = find_amixer()
    try:
        result = subprocess.run(
            [amixer, "-c", str(card_index), "sset", control, f"{percent}%"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise MixerError(f"could not run 'amixer sset': {exc}") from exc
    if result.returncode != 0:
        raise MixerError(
            f"'amixer sset {control} {percent}%' failed "
            f"(exit {result.returncode}): {result.stderr.strip()}"
        )


@dataclass(frozen=True)
class GainNudgeResult:
    card_index: int
    control: str
    old_percent: int
    new_percent: int


def nudge_capture_gain(audio_device: str, delta_percent: int) -> GainNudgeResult:
    """Adjust the microphone's ALSA capture level by a relative amount, once.

    Raises MixerError if the card/control can't be resolved or amixer fails.
    This is a one-shot nudge, not continuous gain control (PROJECT_SPEC.md
    section 6: "avoid continuous gain changes once recording begins").
    Callers must catch MixerError and proceed without adjusting rather than
    block the guest experience.
    """
    card_index = resolve_card_index(audio_device)
    control = find_capture_control(card_index)
    old_percent = get_capture_percent(card_index, control)
    new_percent = max(0, min(100, old_percent + delta_percent))
    set_capture_percent(card_index, control, new_percent)
    return GainNudgeResult(
        card_index=card_index,
        control=control,
        old_percent=old_percent,
        new_percent=new_percent,
    )
