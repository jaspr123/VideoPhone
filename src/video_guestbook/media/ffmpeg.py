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


# Cameras with no onboard H.264 encoder (record_input_format == "mjpeg")
# need the video re-encoded on the Pi's CPU instead of copied. These settings
# are a starting point, not a tuned result -- they have not been verified to
# hold 1280x720@30fps in real time on a Pi 4. If recordings drop frames or
# lag, try a faster preset (already at the fastest) or a lower resolution/fps
# in config before anything else.
_SOFTWARE_ENCODE_PRESET = "ultrafast"
_SOFTWARE_ENCODE_BITRATE = "4M"


def build_record_command(config: BoothConfig, output_path: Path) -> list[str]:
    """Build the ffmpeg argv for recording camera + microphone to MP4.

    When the camera provides its own H.264 stream (record_input_format ==
    "h264"), video is copied straight through with no re-encode, per
    PROJECT_SPEC.md section 16. Some cameras (e.g. ones with no onboard H.264
    encoder, only MJPEG) can't provide that -- for those, set
    record_input_format to "mjpeg" and video is software-encoded to H.264
    instead, at a real CPU cost. Audio is captured as-is from ALSA and
    resampled/encoded to AAC on the output side. The process is expected to
    be stopped gracefully by writing 'q' to its stdin.

    The flags here (thread_queue_size, use_wallclock_as_timestamps, the
    aresample async filter, and avoid_negative_ts) match the confirmed
    working Beta prototype (legacy/booth.py) and fix real audio/video sync
    drift observed on this hardware -- do not remove them without re-testing
    A/V sync on the Pi.

    config.av_sync_offset_ms applies a fixed, device-specific correction
    (PROJECT_SPEC.md section 21, known risk #7) via ffmpeg's -itsoffset:
    positive values delay the video input, negative values delay the audio
    input. It has no effect when 0 (the default).
    """
    ffmpeg = find_ffmpeg()
    width, height = config.record_width_height
    offset_seconds = abs(config.av_sync_offset_ms) / 1000.0

    video_input = ["-thread_queue_size", "512", "-use_wallclock_as_timestamps", "1"]
    if config.av_sync_offset_ms > 0:
        video_input = ["-itsoffset", f"{offset_seconds:.3f}"] + video_input
    video_input += [
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
    ]

    audio_input = ["-thread_queue_size", "512", "-use_wallclock_as_timestamps", "1"]
    if config.av_sync_offset_ms < 0:
        audio_input = ["-itsoffset", f"{offset_seconds:.3f}"] + audio_input
    audio_input += ["-f", "alsa", "-i", config.audio_device]

    if config.record_input_format == "h264":
        video_encode = ["-c:v", "copy"]
    else:
        video_encode = [
            "-c:v",
            "libx264",
            "-preset",
            _SOFTWARE_ENCODE_PRESET,
            "-pix_fmt",
            "yuv420p",
            "-b:v",
            _SOFTWARE_ENCODE_BITRATE,
            "-maxrate",
            _SOFTWARE_ENCODE_BITRATE,
            "-bufsize",
            "8M",
            "-r",
            str(config.record_fps),
        ]

    return (
        [ffmpeg, "-y"]
        + video_input
        + audio_input
        + [
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
        ]
        + video_encode
        + [
            "-c:a",
            "aac",
            "-b:a",
            config.audio_bitrate,
            "-ar",
            str(config.audio_sample_rate),
            "-ac",
            str(config.audio_channels),
            "-af",
            "aresample=async=1:first_pts=0",
            "-avoid_negative_ts",
            "make_zero",
            "-t",
            str(config.max_recording_seconds),
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )


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
