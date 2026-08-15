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
_VALID_RECORDING_MODES = {"fast", "quality"}

# Guestbook/Extended message-length presets exposed by the attendant
# SETTINGS screen's segmented switch (see ui/renderer.py's render_settings
# and main.py's _apply_settings_change). Flipping the switch sets
# max_recording_seconds to the mode's default; an attendant can still dial
# a custom value afterward with the +/- steppers.
GUESTBOOK_MAX_RECORDING_SECONDS = 90
EXTENDED_MAX_RECORDING_SECONDS = 300

# Fields the attendant SETTINGS screen is allowed to persist back to the
# config file (see BoothConfig.save_settings) -- everything else in
# booth.default.json (camera/audio devices, theme, etc.) is
# attendant-invisible and passes through a save untouched.
# hook_switch_gpio_pin/hook_switch_invert are not exposed on the SETTINGS
# screen itself -- they're written only by the DEVELOPER screen's "Find
# receiver switch" wizard (see main.py's _apply_hook_scan_result), reusing
# this same validated/atomic save path rather than a separate one.
EDITABLE_SETTINGS_KEYS = (
    "max_recording_seconds",
    "countdown_seconds",
    "recording_mode",
    "live_mic_meter_enabled",
    "extended_mode",
    "hook_switch_gpio_pin",
    "hook_switch_invert",
)

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
    # Manual A/V sync correction (PROJECT_SPEC.md section 21, known risk #7:
    # "Audio sync may need a device-specific offset"). Positive delays video
    # relative to audio (use when video is ahead / plays too early). Negative
    # delays audio relative to video (use when audio is ahead / plays too
    # early). Optional; defaults to 0 (no correction) for existing configs.
    av_sync_offset_ms: int = 0
    # Physical receiver hook switch (PROJECT_SPEC.md section 4). GPIO17 is
    # the primary signal per the spec; the diagnostic GPIO27 pin is not
    # configurable (see hardware/hook_switch.py). Optional; defaults keep
    # existing configs working. Spacebar always remains a backup input
    # regardless of this setting (architecture rule 11).
    hook_switch_enabled: bool = True
    hook_switch_gpio_pin: int = 17
    # Whether the switch's electrical logic is inverted relative to the
    # spec's assumed normally-open wiring (stable LOW = lifted under the
    # internal pull-up). A normally-closed switch wired the same
    # (GND-only, per the spec's caution) reads the opposite way -- stable
    # HIGH = lifted. False (default) assumes normally-open, matching
    # PROJECT_SPEC.md section 4. Set by the DEVELOPER screen's "Find
    # receiver switch" wizard (see main.py), not hand-edited in practice.
    hook_switch_invert: bool = False
    # Only matters when record_input_format == "mjpeg" (a camera with no
    # onboard H.264 -- see media/ffmpeg.py). "quality" (default) records the
    # camera's raw MJPEG live (near-zero CPU, no audio-dropout risk) and
    # transcodes to the final H.264 MP4 in the background after the guest
    # hangs up. "fast" transcodes live instead, so the file is immediately
    # final but competes with audio capture for CPU -- pick this only if a
    # busy event risks backing up the background transcode queue. For
    # cameras with real onboard H.264, this setting has no effect: -c:v copy
    # is already both the fastest and highest-quality option.
    recording_mode: str = "quality"
    # Directory containing theme.json plus font/image assets for the
    # guest-facing screens (PROJECT_SPEC.md section 11). Resolved relative to
    # base_dir, same as output_dir/log_dir. See src/video_guestbook/ui/theme.py.
    theme_dir: Path = Path("themes/classic-walnut")
    # ALSA *playback* device for prompt sounds (e.g. the pickup greeting),
    # via `aplay -D <device>`. Distinct from audio_device (the microphone
    # *capture* device) -- on hardware where the handset's speaker and mic
    # are the same USB audio adapter these may look similar but are not
    # interchangeable. None (default) uses aplay's system default device.
    audio_playback_device: str | None = None
    # Live mic meter on the RECORDING screen: a second AudioLevelReader
    # (same `arecord`-based mechanism as the countdown-time check, see
    # media/audio_levels.py) started shortly after ffmpeg begins recording.
    # Off by default -- whether this actually works depends on whether the
    # ALSA device/driver allows a second concurrent reader while ffmpeg
    # holds it for the recording itself; if not, arecord just fails to open
    # and the meter falls back to the last countdown reading, same as when
    # this is off. See main.py's _start_recording_mic_meter().
    live_mic_meter_enabled: bool = False
    # Fast peak limiter (ffmpeg's `alimiter` audio filter) applied to the
    # live recording pass -- see media/ffmpeg.py's build_record_command.
    # Distinct from the pre-roll gain calibration in main.py
    # (_maybe_recalibrate_mic_gain): that sets the ALSA capture level once
    # before recording starts, this catches occasional loud peaks *during*
    # the recording itself without riding/hunting the gain the way a full
    # AGC would -- PROJECT_SPEC.md section 6 warns continuous gain changes
    # during a message cause pumping artifacts on this hardware; alimiter's
    # fast attack/release only engages on peaks that actually cross the
    # threshold, so it doesn't have that failure mode. On by default.
    mic_limiter_enabled: bool = True
    # How long the PREVIEW screen (live camera, tap-to-record button) lingers
    # before auto-advancing into COUNTDOWN on its own -- a guest who never
    # taps the button (or whose tap the touchscreen missed) still isn't
    # stuck there forever (architecture rule 17). See state_machine.py's
    # module docstring for why PREVIEW exists. (RECORDING's own camera
    # preview -- config live_preview_enabled -- was removed once PREVIEW
    # took over showing a live feed; see CHANGELOG.md.)
    preview_seconds: int = 8
    # Guestbook (short) vs Extended (long) message-length preset, set from
    # the attendant SETTINGS screen's segmented switch. Purely descriptive
    # of which preset max_recording_seconds currently reflects -- nothing
    # else in the app reads this to change behavior; changing it is what
    # changes max_recording_seconds in the first place (see
    # ui/renderer.py's render_settings / main.py's _apply_settings_change).
    extended_mode: bool = False

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

        av_sync_offset_ms = data.get("av_sync_offset_ms", 0)
        if not isinstance(av_sync_offset_ms, int) or not (-5000 <= av_sync_offset_ms <= 5000):
            raise ConfigError(
                "av_sync_offset_ms must be an integer between -5000 and 5000, "
                f"got {av_sync_offset_ms!r}"
            )

        hook_switch_enabled = data.get("hook_switch_enabled", True)
        if not isinstance(hook_switch_enabled, bool):
            raise ConfigError(
                f"hook_switch_enabled must be a boolean, got {hook_switch_enabled!r}"
            )

        hook_switch_gpio_pin = data.get("hook_switch_gpio_pin", 17)
        if not isinstance(hook_switch_gpio_pin, int) or not (0 <= hook_switch_gpio_pin <= 27):
            raise ConfigError(
                "hook_switch_gpio_pin must be an integer between 0 and 27, "
                f"got {hook_switch_gpio_pin!r}"
            )

        hook_switch_invert = data.get("hook_switch_invert", False)
        if not isinstance(hook_switch_invert, bool):
            raise ConfigError(f"hook_switch_invert must be a boolean, got {hook_switch_invert!r}")

        recording_mode = str(data.get("recording_mode", "quality")).strip().lower()
        if recording_mode not in _VALID_RECORDING_MODES:
            raise ConfigError(
                f"recording_mode must be one of {sorted(_VALID_RECORDING_MODES)}, "
                f"got {recording_mode!r}"
            )

        theme_dir = str(data.get("theme_dir", "themes/classic-walnut")).strip()
        if not theme_dir:
            raise ConfigError("theme_dir must not be empty")

        audio_playback_device = data.get("audio_playback_device")
        if audio_playback_device is not None:
            audio_playback_device = str(audio_playback_device).strip()
            if not audio_playback_device:
                raise ConfigError("audio_playback_device must not be empty when set (use null to omit)")

        live_mic_meter_enabled = data.get("live_mic_meter_enabled", False)
        if not isinstance(live_mic_meter_enabled, bool):
            raise ConfigError(
                f"live_mic_meter_enabled must be a boolean, got {live_mic_meter_enabled!r}"
            )

        mic_limiter_enabled = data.get("mic_limiter_enabled", True)
        if not isinstance(mic_limiter_enabled, bool):
            raise ConfigError(
                f"mic_limiter_enabled must be a boolean, got {mic_limiter_enabled!r}"
            )

        preview_seconds = data.get("preview_seconds", 8)
        if not isinstance(preview_seconds, int) or preview_seconds <= 0:
            raise ConfigError(f"preview_seconds must be a positive integer, got {preview_seconds!r}")

        extended_mode = data.get("extended_mode", False)
        if not isinstance(extended_mode, bool):
            raise ConfigError(f"extended_mode must be a boolean, got {extended_mode!r}")

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
            av_sync_offset_ms=av_sync_offset_ms,
            hook_switch_enabled=hook_switch_enabled,
            hook_switch_gpio_pin=hook_switch_gpio_pin,
            hook_switch_invert=hook_switch_invert,
            recording_mode=recording_mode,
            theme_dir=(base_dir / theme_dir).resolve(),
            audio_playback_device=audio_playback_device,
            live_mic_meter_enabled=live_mic_meter_enabled,
            mic_limiter_enabled=mic_limiter_enabled,
            preview_seconds=preview_seconds,
            extended_mode=extended_mode,
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

    @classmethod
    def save_settings(
        cls, path: str | Path, updates: dict[str, Any], base_dir: Path | None = None
    ) -> "BoothConfig":
        """Patch a subset of EDITABLE_SETTINGS_KEYS into the config JSON
        file at `path` and return the reloaded, revalidated BoothConfig.

        Used by the attendant SETTINGS screen (main.py's
        _apply_settings_change) to persist edits made on the booth itself.
        Everything else in the file (camera/audio devices, GPIO pin,
        theme, hook switch, etc.) passes through untouched, and the merged
        result is validated via from_dict *before* anything is written, so
        a bad edit can't corrupt the file the booth needs to start up
        next time.
        """
        path = Path(path)
        unknown = set(updates) - set(EDITABLE_SETTINGS_KEYS)
        if unknown:
            raise ConfigError(f"save_settings got non-editable keys: {sorted(unknown)}")

        try:
            raw_text = path.read_text(encoding="utf-8")
            data = json.loads(raw_text)
        except OSError as exc:
            raise ConfigError(f"Could not read config file {path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Config file {path} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"Config file {path} must contain a JSON object")

        merged = {**data, **updates}
        config = cls.from_dict(merged, base_dir=base_dir or Path.cwd())  # validate before writing

        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(path)
        return config
