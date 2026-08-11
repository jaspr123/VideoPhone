import dataclasses
import logging
from pathlib import Path

import pytest

from video_guestbook import main as main_module
from video_guestbook.config import BoothConfig
from video_guestbook.main import _METER_CEILING_DBFS, _METER_FLOOR_DBFS, BoothApp, _mic_fraction
from video_guestbook.media.audio_levels import LevelReading
from video_guestbook.ui.theme import Theme

REPO_ROOT = Path(__file__).resolve().parents[2]
THEME_DIR = REPO_ROOT / "themes" / "classic-walnut"


def _reading(peak_dbfs: float) -> LevelReading:
    return LevelReading(timestamp=0.0, rms_dbfs=peak_dbfs, peak_dbfs=peak_dbfs, clipped=False, status="x")


def test_mic_fraction_none_reading_is_zero():
    assert _mic_fraction(None) == 0.0


def test_mic_fraction_at_floor_is_zero():
    assert _mic_fraction(_reading(_METER_FLOOR_DBFS)) == 0.0


def test_mic_fraction_at_ceiling_is_one():
    assert _mic_fraction(_reading(_METER_CEILING_DBFS)) == 1.0


def test_mic_fraction_clamps_below_floor():
    assert _mic_fraction(_reading(_METER_FLOOR_DBFS - 20)) == 0.0


def test_mic_fraction_clamps_above_ceiling():
    assert _mic_fraction(_reading(_METER_CEILING_DBFS + 20)) == 1.0


def test_mic_fraction_midpoint():
    midpoint = (_METER_FLOOR_DBFS + _METER_CEILING_DBFS) / 2
    assert _mic_fraction(_reading(midpoint)) == pytest.approx(0.5)


def _config(tmp_path, **overrides) -> BoothConfig:
    data = {
        "camera_device": "/dev/video0", "record_resolution": "1280x720", "record_fps": 30,
        "record_input_format": "mjpeg", "audio_device": "plughw:CARD=Device,DEV=0",
        "audio_sample_rate": 48000, "audio_channels": 1, "audio_bitrate": "160k",
        "countdown_seconds": 3, "max_recording_seconds": 90, "output_dir": "recordings",
        "log_dir": "logs", "preview_resolution": "1024x576", "preview_fps": 20,
    }
    data.update(overrides)
    return BoothConfig.from_dict(data, base_dir=tmp_path)


def _app(tmp_path, theme=None) -> BoothApp:
    theme = theme or Theme.load(THEME_DIR)
    return BoothApp(_config(tmp_path), theme, logging.getLogger("test"))


def test_play_pickup_greeting_noop_when_theme_has_no_sound(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(main_module, "play_sound_async", lambda *a, **k: calls.append((a, k)))
    app = _app(tmp_path)
    app._play_pickup_greeting()
    assert calls == []


def test_play_pickup_greeting_calls_playback_when_configured(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(main_module, "play_sound_async", lambda *a, **k: calls.append((a, k)))
    theme = dataclasses.replace(Theme.load(THEME_DIR), sounds={"pickup": tmp_path / "pickup.wav"})
    app = _app(tmp_path, theme=theme)

    app._play_pickup_greeting()

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == tmp_path / "pickup.wav"
    assert kwargs["device"] is None


def test_play_pickup_greeting_passes_configured_playback_device(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(main_module, "play_sound_async", lambda *a, **k: calls.append((a, k)))
    theme = dataclasses.replace(Theme.load(THEME_DIR), sounds={"pickup": tmp_path / "pickup.wav"})
    config = _config(tmp_path, audio_playback_device="plughw:CARD=Device,DEV=0")
    app = BoothApp(config, theme, logging.getLogger("test"))

    app._play_pickup_greeting()

    assert calls[0][1]["device"] == "plughw:CARD=Device,DEV=0"


def test_start_countdown_triggers_pickup_greeting(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(main_module, "play_sound_async", lambda *a, **k: calls.append((a, k)))
    theme = dataclasses.replace(Theme.load(THEME_DIR), sounds={"pickup": tmp_path / "pickup.wav"})
    app = _app(tmp_path, theme=theme)

    app._start_countdown()
    app._stop_mic_check()

    assert len(calls) == 1
