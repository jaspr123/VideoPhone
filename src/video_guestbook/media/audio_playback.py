"""Non-blocking playback of short prompt/notification sounds.

Centralized here like every other subprocess call in media/ -- `aplay` is
invoked nowhere else. Playback is fire-and-forget (the caller never blocks
waiting for a greeting clip to finish) and every failure mode (missing
`aplay`, missing sound file, no such playback device) is logged and
swallowed rather than raised, per architecture rule 17: a missing/broken
speaker must never crash the guest-facing app or block recording.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path


def play_sound_async(
    path: Path,
    device: str | None = None,
    logger: logging.Logger | None = None,
) -> None:
    """Start playing `path` in the background; returns immediately."""
    log = logger or logging.getLogger(__name__)

    if not path.is_file():
        log.warning("sound file not found, skipping playback: %s", path)
        return

    aplay = shutil.which("aplay")
    if aplay is None:
        log.warning("aplay not found on PATH, skipping playback of %s", path)
        return

    command = [aplay, "-q"]
    if device:
        command += ["-D", device]
    command.append(str(path))

    try:
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        log.warning("failed to start playback of %s: %s", path, exc)
