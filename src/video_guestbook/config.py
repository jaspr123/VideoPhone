"""Booth configuration loading and validation.

Configuration lives in a JSON file (see config/booth.default.json) and is
validated before use, per PROJECT_SPEC.md architecture rule 4.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_RESOLUTION_RE = re.compile(r"^(\d+)x(\d+)$")
_BITRATE_RE = re.compile(r"^\d+k$")
_VALID_INPUT_FORMATS = {"h264", "mjpeg"}

REQUIRED_KEYS = (
    "camera_device",
    "record_resolution",
    "record_fps",
    "record_input_format",
    "audio_device",
    "audio_sample_rate",
    "audio_channels",
    "audio_bitrate",
    "countdown_seconds",
    "max_recording_seconds",
    "output_dir",
    "log_dir",
    "preview_resolution",
    "preview_fps",
)


class ConfigError(ValueError):
    """Raised when booth configuration is missing or invalid."""


def parse_resolution(value: str, field_name: str = "resolution") -> tuple[int, int]:
    match = _RESOLUTION_RE.match(value.strip()) if isinstance(value, str) else None
    if not match:
        raise ConfigError(
            f"{field_name} must look like WIDTHxHEIGHT (e.g. '1280x720'), got {value!r}"
        )
    width, height = int(match.group(1)), int(match.group(2))
    if width <= 0 or height <= 0:
        raise ConfigError(f"{field_name} must have positive dimensions, got {value!r}")
    return width, height


@dataclass(frozen=True)
class BoothConfig:
    camera_device: str
    record_resolution: str
    record_fps: int
    record_input_format: str
    audio_device: str
    audio_sample_rate: int
    audio_channels: int
    audio_bitrate: str
    countdown_seconds: int
    max_recording_seconds: int
    output_dir: Path
    log_dir: Path
    preview_resolution: str
    preview_fps: int

    @property
    def record_width_height(self) -> tuple[int, int]:
        return parse_resolution(self.record_resolution, "record_resolution")

    @property
    def preview_width_height(self) -> tuple[int, int]:
        return parse_resolution(self.preview_resolution, "preview_resolution")

    @classmethod
    def from_dict(cls, data: dict[str, Any], base_dir: Path | None = None) -> "BoothConfig":
        base_dir = base_dir or Path.cwd()

        missing = [key for key in REQUIRED_KEYS if key not in data]
        if missing:
            raise ConfigError(f"Missing required config keys: {', '.join(sorted(missing))}")

        camera_device = str(data["camera_device"]).strip()
        if not camera_device:
            raise ConfigError("camera_device must not be empty")

        parse_resolution(data["record_resolution"], "record_resolution")
        parse_resolution(data["preview_resolution"], "preview_resolution")

        record_fps = data["record_fps"]
        if not isinstance(record_fps, int) or not (1 <= record_fps <= 60):
            raise ConfigError(f"record_fps must be an integer between 1 and 60, got {record_fps!r}")

        preview_fps = data["preview_fps"]
        if not isinstance(preview_fps, int) or not (1 <= preview_fps <= 60):
            raise ConfigError(f"preview_fps must be an integer between 1 and 60, got {preview_fps!r}")

        record_input_format = str(data["record_input_format"]).strip().lower()
        if record_input_format not in _VALID_INPUT_FORMATS:
            raise ConfigError(
                f"record_input_format must be one of {sorted(_VALID_INPUT_FORMATS)}, "
                f"got {record_input_format!r}"
            )

        audio_device = str(data["audio_device"]).strip()
        if not audio_device:
            raise ConfigError("audio_device must not be empty")

        audio_sample_rate = data["audio_sample_rate"]
        if not isinstance(audio_sample_rate, int) or audio_sample_rate not in (
            8000,
            16000,
            22050,
            32000,
            44100,
            48000,
        ):
            raise ConfigError(
                f"audio_sample_rate must be a standard rate in Hz, got {audio_sample_rate!r}"
            )

        audio_channels = data["audio_channels"]
        if audio_channels not in (1, 2):
            raise ConfigError(f"audio_channels must be 1 (mono) or 2 (stereo), got {audio_channels!r}")

        audio_bitrate = str(data["audio_bitrate"]).strip()
        if not _BITRATE_RE.match(audio_bitrate):
            raise ConfigError(f"audio_bitrate must look like '160k', got {audio_bitrate!r}")

        countdown_seconds = data["countdown_seconds"]
        if not isinstance(countdown_seconds, int) or countdown_seconds < 0:
            raise ConfigError(f"countdown_seconds must be a non-negative integer, got {countdown_seconds!r}")

        max_recording_seconds = data["max_recording_seconds"]
        if not isinstance(max_recording_seconds, int) or max_recording_seconds <= 0:
            raise ConfigError(
                f"max_recording_seconds must be a positive integer, got {max_recording_seconds!r}"
            )

        output_dir = str(data["output_dir"]).strip()
        log_dir = str(data["log_dir"]).strip()
        if not output_dir:
            raise ConfigError("output_dir must not be empty")
        if not log_dir:
            raise ConfigError("log_dir must not be empty")

        return cls(
            camera_device=camera_device,
            record_resolution=str(data["record_resolution"]).strip(),
            record_fps=record_fps,
            record_input_format=record_input_format,
            audio_device=audio_device,
            audio_sample_rate=audio_sample_rate,
            audio_channels=audio_channels,
            audio_bitrate=audio_bitrate,
            countdown_seconds=countdown_seconds,
            max_recording_seconds=max_recording_seconds,
            output_dir=(base_dir / output_dir).resolve(),
            log_dir=(base_dir / log_dir).resolve(),
            preview_resolution=str(data["preview_resolution"]).strip(),
            preview_fps=preview_fps,
        )

    @classmethod
    def from_file(cls, path: str | Path, base_dir: Path | None = None) -> "BoothConfig":
        path = Path(path)
        try:
            raw_text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"Could not read config file {path}: {exc}") from exc

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Config file {path} is not valid JSON: {exc}") from exc

        if not isinstance(data, dict):
            raise ConfigError(f"Config file {path} must contain a JSON object")

        return cls.from_dict(data, base_dir=base_dir or Path.cwd())
