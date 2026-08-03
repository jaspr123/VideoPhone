"""Recording validation.

Architecture rule 13: a recording is complete only after FFmpeg exits and
the file is validated (non-empty, contains both video and audio streams,
and meets a minimum duration).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from video_guestbook.media.ffmpeg import build_probe_command

DEFAULT_MIN_DURATION_SECONDS = 0.5


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason: str
    size_bytes: int = 0
    duration_seconds: float = 0.0
    has_video: bool = False
    has_audio: bool = False


def validate_recording(
    media_path: Path,
    min_duration_seconds: float = DEFAULT_MIN_DURATION_SECONDS,
) -> ValidationResult:
    media_path = Path(media_path)

    if not media_path.exists():
        return ValidationResult(ok=False, reason=f"file does not exist: {media_path}")

    size_bytes = media_path.stat().st_size
    if size_bytes == 0:
        return ValidationResult(ok=False, reason="file is empty", size_bytes=0)

    command = build_probe_command(media_path)
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        return ValidationResult(
            ok=False, reason=f"could not run ffprobe: {exc}", size_bytes=size_bytes
        )

    if result.returncode != 0:
        return ValidationResult(
            ok=False,
            reason=f"ffprobe failed (exit {result.returncode}): {result.stderr.strip()}",
            size_bytes=size_bytes,
        )

    try:
        probe_data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return ValidationResult(
            ok=False, reason=f"could not parse ffprobe output: {exc}", size_bytes=size_bytes
        )

    streams = probe_data.get("streams", [])
    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    duration_raw = probe_data.get("format", {}).get("duration")
    try:
        duration_seconds = float(duration_raw) if duration_raw is not None else 0.0
    except (TypeError, ValueError):
        duration_seconds = 0.0

    if not has_video:
        return ValidationResult(
            ok=False,
            reason="no video stream found",
            size_bytes=size_bytes,
            duration_seconds=duration_seconds,
            has_video=has_video,
            has_audio=has_audio,
        )
    if not has_audio:
        return ValidationResult(
            ok=False,
            reason="no audio stream found",
            size_bytes=size_bytes,
            duration_seconds=duration_seconds,
            has_video=has_video,
            has_audio=has_audio,
        )
    if duration_seconds < min_duration_seconds:
        return ValidationResult(
            ok=False,
            reason=(
                f"duration {duration_seconds:.2f}s is below minimum "
                f"{min_duration_seconds:.2f}s"
            ),
            size_bytes=size_bytes,
            duration_seconds=duration_seconds,
            has_video=has_video,
            has_audio=has_audio,
        )

    return ValidationResult(
        ok=True,
        reason="ok",
        size_bytes=size_bytes,
        duration_seconds=duration_seconds,
        has_video=has_video,
        has_audio=has_audio,
    )
