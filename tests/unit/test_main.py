import dataclasses
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import pytest

from video_guestbook import main as main_module
from video_guestbook.config import BoothConfig
from video_guestbook.main import _METER_CEILING_DBFS, _METER_FLOOR_DBFS, BoothApp, _mic_fraction
from video_guestbook.media.audio_levels import LevelReading
from video_guestbook.media.recorder import RecordingSession
from video_guestbook.state_machine import BoothState
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


def test_read_live_preview_frame_none_when_no_path(tmp_path):
    app = _app(tmp_path)
    assert app._read_live_preview_frame() is None


def test_read_live_preview_frame_none_when_file_missing(tmp_path):
    app = _app(tmp_path)
    app._live_preview_path = tmp_path / "missing.jpg"
    assert app._read_live_preview_frame() is None


def test_read_live_preview_frame_decodes_real_jpeg(tmp_path):
    app = _app(tmp_path)
    path = tmp_path / "preview.jpg"
    cv2.imwrite(str(path), np.zeros((10, 10, 3), dtype=np.uint8))
    app._live_preview_path = path

    result = app._read_live_preview_frame()

    assert result is not None
    assert result.shape == (10, 10, 3)


def test_read_live_preview_frame_skips_redecode_when_mtime_unchanged(tmp_path, monkeypatch):
    app = _app(tmp_path)
    path = tmp_path / "preview.jpg"
    cv2.imwrite(str(path), np.zeros((10, 10, 3), dtype=np.uint8))
    app._live_preview_path = path

    calls = []
    real_imread = main_module.cv2.imread

    def counting_imread(p):
        calls.append(p)
        return real_imread(p)

    monkeypatch.setattr(main_module.cv2, "imread", counting_imread)

    app._read_live_preview_frame()
    app._read_live_preview_frame()

    assert len(calls) == 1


def test_read_live_preview_frame_falls_back_on_corrupt_read(tmp_path):
    app = _app(tmp_path)
    path = tmp_path / "preview.jpg"
    cv2.imwrite(str(path), np.zeros((10, 10, 3), dtype=np.uint8))
    app._live_preview_path = path

    first = app._read_live_preview_frame()
    assert first is not None

    path.write_bytes(b"not a real jpeg")
    new_mtime = path.stat().st_mtime + 5
    os.utime(path, (new_mtime, new_mtime))

    second = app._read_live_preview_frame()

    assert second is not None
    np.testing.assert_array_equal(second, first)


def _fake_session(tmp_path, **overrides) -> RecordingSession:
    data = dict(
        session_id="20260101_000000_abcd1234",
        started_at=datetime.now(timezone.utc),
        live_output_path=tmp_path / "out.mp4",
        final_output_path=tmp_path / "out.mp4",
        needs_transcode=False,
        live_preview_path=None,
    )
    data.update(overrides)
    return RecordingSession(**data)


def test_start_recording_sets_live_preview_path_from_session(tmp_path, monkeypatch):
    app = _app(tmp_path)
    fake_session = _fake_session(tmp_path, live_preview_path=tmp_path / "preview.jpg")
    monkeypatch.setattr(app.recorder, "start", lambda: fake_session)
    monkeypatch.setattr(main_module.time, "sleep", lambda seconds: None)
    app.state_machine.transition(BoothState.COUNTDOWN)

    app._start_recording()

    assert app._live_preview_path == fake_session.live_preview_path


def test_start_recording_mic_meter_noop_when_disabled(tmp_path):
    app = _app(tmp_path)  # live_mic_meter_enabled defaults to False
    app._start_recording_mic_meter()
    assert app._mic_check_thread is None


def test_start_recording_mic_meter_starts_thread_when_enabled(tmp_path):
    config = _config(tmp_path, live_mic_meter_enabled=True)
    app = BoothApp(config, Theme.load(THEME_DIR), logging.getLogger("test"))

    app._start_recording_mic_meter()
    try:
        assert app._mic_check_thread is not None
        assert app._mic_check_thread.is_alive()
    finally:
        app._stop_mic_check()


def test_start_recording_mic_meter_preserves_last_reading_until_fresh_one_arrives(tmp_path, monkeypatch):
    # If the second arecord can never open the (already busy) device, the
    # meter must keep showing the last countdown reading, not go blank.
    config = _config(tmp_path, live_mic_meter_enabled=True)
    app = BoothApp(config, Theme.load(THEME_DIR), logging.getLogger("test"))
    stale_reading = _reading(-20.0)
    app._latest_mic_reading = stale_reading

    monkeypatch.setattr(main_module, "RECORDING_MIC_METER_START_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(
        main_module.AudioLevelReader, "start",
        lambda self: (_ for _ in ()).throw(main_module.MicrophoneError("device busy")),
    )

    app._start_recording_mic_meter()
    app._mic_check_thread.join(timeout=2.0)

    assert app._latest_mic_reading is stale_reading


def test_stop_and_save_stops_recording_mic_meter(tmp_path, monkeypatch):
    config = _config(tmp_path, live_mic_meter_enabled=True)
    app = BoothApp(config, Theme.load(THEME_DIR), logging.getLogger("test"))
    fake_session = _fake_session(tmp_path)
    monkeypatch.setattr(app.recorder, "stop", lambda: fake_session)
    monkeypatch.setattr(main_module, "validate_recording", lambda path: type(
        "R", (), {"ok": True, "duration_seconds": 1.0, "size_bytes": 1, "reason": ""}
    )())
    monkeypatch.setattr(app, "_reopen_camera_with_retry", lambda: True)
    app.state_machine.transition(BoothState.COUNTDOWN)
    app.state_machine.transition(BoothState.RECORDING)
    app._start_recording_mic_meter()
    assert app._mic_check_thread is not None

    app._stop_and_save()

    assert app._mic_check_thread is None
