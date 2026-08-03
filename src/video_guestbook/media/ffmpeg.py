"""Centralized FFmpeg command construction.

Architecture rule 3: FFmpeg command construction must be centralized. No
other module should build an ffmpeg argument list directly.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from video_guestbook.config import BoothConfig


class FFmpegNotFoundError(RuntimeError):
    """Raised when the ffmpeg binary cannot be located on PATH."""


def find_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise FFmpegNotFoundError("ffmpeg was not found on PATH")
    return path


def find_ffprobe() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise FFmpegNotFoundError("ffprobe was not found on PATH")
    return path


def build_record_command(config: BoothConfig, output_path: Path) -> list[str]:
    """Build the ffmpeg argv for recording camera + microphone to MP4.

    Video is copied straight from the webcam's H.264 stream (no re-encode,
    per PROJECT_SPEC.md section 16). Audio is encoded to AAC. The process is
    expected to be stopped gracefully by writing 'q' to its stdin.
    """
    ffmpeg = find_ffmpeg()
    width, height = config.record_width_height

    return [
        ffmpeg,
        "-y",
        "-f",
        "v4l2",
        "-input_format",
        config.record_input_format,
        "-video_size",
        f"{width}x{height}",
        "-framerate",
        str(config.record_fps),
        "-i",
        config.camera_device,
        "-f",
        "alsa",
        "-ar",
        str(config.audio_sample_rate),
        "-ac",
        str(config.audio_channels),
        "-i",
        config.audio_device,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        config.audio_bitrate,
        "-t",
        str(config.max_recording_seconds),
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def build_probe_command(media_path: Path) -> list[str]:
    ffprobe = find_ffprobe()
    return [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(media_path),
    ]
