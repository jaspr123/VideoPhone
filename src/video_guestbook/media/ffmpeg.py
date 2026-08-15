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
# need the video re-encoded on the Pi's CPU somewhere -- either live
# ("fast" recording_mode) or deferred ("quality" mode, see
# build_transcode_command). These settings are a starting point, not a
# tuned result -- they have not been verified to hold 1280x720@30fps in
# real time on a Pi 4. If recordings drop frames or lag in "fast" mode, try
# a faster preset (already at the fastest) or a lower resolution/fps in
# config before anything else.
_SOFTWARE_ENCODE_PRESET = "ultrafast"
_SOFTWARE_ENCODE_BITRATE = "4M"

# Fast peak limiter for the live recording pass (config mic_limiter_enabled,
# on by default), catching a loud talker's peaks during an actual message
# without riding/hunting the overall gain the way continuous AGC would --
# PROJECT_SPEC.md section 6 explicitly warns continuous gain changes during
# a message cause pumping artifacts on this hardware. alimiter's fast
# attack/short release only engages when a peak actually crosses the
# ceiling, so normal speech (spec target: peaks below -6 dBFS, see
# media/audio_levels.py's LOUD_PEAK_DBFS) never touches it.
#   limit=0.7  -- ceiling at ~-3.1 dBFS true peak: below CLIP_PEAK_DBFS
#                 (-1 dBFS) with headroom, above the -6 dBFS "too loud"
#                 threshold so ordinary speech is unaffected.
#   attack=5   -- ms, fast enough to catch a sudden peak before it clips.
#   release=50 -- ms, short enough to stay transparent between peaks.
#   level=0    -- disable alimiter's automatic makeup-gain leveling; this
#                 must only clamp peaks, never boost quiet passages.
_MIC_LIMITER_FILTER = "alimiter=limit=0.7:attack=5:release=50:level=0"


def is_live_video_copied(config: BoothConfig) -> bool:
    """True if the live recording command copies video with no re-encode.

    Always true for cameras with real onboard H.264 (record_input_format ==
    "h264"): -c:v copy is both the fastest and highest-quality option, so
    recording_mode has no effect there. For MJPEG-only cameras, true only in
    "quality" mode -- the raw MJPEG is captured as-is (near-zero CPU, no
    audio-dropout risk) and build_transcode_command() converts it to the
    final H.264 MP4 afterward, with no live deadline. "fast" mode instead
    transcodes live (real CPU cost, immediately final) and is only worth the
    dropout risk if a busy event needs to avoid a background transcode
    backlog.
    """
    return config.record_input_format == "h264" or config.recording_mode == "quality"


def needs_deferred_transcode(config: BoothConfig) -> bool:
    """True when the live capture produces raw video that isn't the final
    playable file yet -- MJPEG-only camera in "quality" mode."""
    return config.record_input_format == "mjpeg" and config.recording_mode == "quality"


def build_record_command(config: BoothConfig, output_path: Path) -> list[str]:
    """Build the ffmpeg argv for recording camera + microphone.

    See is_live_video_copied() for when video is copied vs. software
    re-encoded live. Audio is captured as-is from ALSA and resampled/encoded
    to AAC on the output side either way. The process is expected to be
    stopped gracefully by writing 'q' to its stdin.

    When is_live_video_copied() is true because of "quality" mode (not
    because the camera has real H.264), output_path should be a raw
    container (e.g. .mkv, not .mp4 -- MJPEG doesn't map cleanly into MP4)
    since this is an intermediate file for build_transcode_command(), not
    the final deliverable. Recorder owns that path/naming decision.

    The flags here (thread_queue_size, use_wallclock_as_timestamps, the
    aresample async filter, and avoid_negative_ts) match the confirmed
    working Beta prototype (legacy/booth.py) and fix real audio/video sync
    drift observed on this hardware -- do not remove them without re-testing
    A/V sync on the Pi. The -af value is a comma-joined filter chain:
    aresample is always present, and _MIC_LIMITER_FILTER (config
    mic_limiter_enabled, on by default) is appended after it when enabled.

    config.av_sync_offset_ms applies a fixed, device-specific correction
    (PROJECT_SPEC.md section 21, known risk #7) via ffmpeg's -itsoffset:
    positive values delay the video input, negative values delay the audio
    input. It has no effect when 0 (the default).

    RECORDING's camera panel does not need a live feed from this process:
    the guest already saw themselves live on the PREVIEW screen (before
    this process ever started -- see state_machine.py), and RECORDING just
    keeps showing the last frame captured there. An earlier version of this
    function wrote a second, low-fps preview JPEG as an extra output for
    exactly that purpose; it's gone now that PREVIEW covers the need
    without a second ffmpeg output. See CHANGELOG.md.
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

    if is_live_video_copied(config):
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

    audio_filters = ["aresample=async=1:first_pts=0"]
    if config.mic_limiter_enabled:
        audio_filters.append(_MIC_LIMITER_FILTER)

    command = (
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
            ",".join(audio_filters),
            "-avoid_negative_ts",
            "make_zero",
            "-t",
            str(config.max_recording_seconds),
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )

    return command


def build_transcode_command(input_path: Path, output_path: Path, config: BoothConfig) -> list[str]:
    """Build the ffmpeg argv for the deferred "quality" mode transcode.

    Converts a raw MJPEG capture (from build_record_command when
    needs_deferred_transcode() is true) into the final playable H.264 MP4.
    Audio is copied through untouched -- it was already properly AAC-encoded
    during the live pass, no need to re-encode it twice.

    This runs after the guest has already hung up, with no real-time
    deadline, so however long it takes it can never cause an audio dropout
    the way live encoding can.
    """
    ffmpeg = find_ffmpeg()
    return [
        ffmpeg,
        "-y",
        "-i",
        str(input_path),
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
        "-c:a",
        "copy",
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
