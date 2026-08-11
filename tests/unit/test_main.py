import dataclasses
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

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


def test_start_preview_triggers_pickup_greeting(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(main_module, "play_sound_async", lambda *a, **k: calls.append((a, k)))
    theme = dataclasses.replace(Theme.load(THEME_DIR), sounds={"pickup": tmp_path / "pickup.wav"})
    app = _app(tmp_path, theme=theme)

    app._start_preview()
    app._stop_mic_check()

    assert len(calls) == 1


def test_start_countdown_does_not_replay_pickup_greeting(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(main_module, "play_sound_async", lambda *a, **k: calls.append((a, k)))
    theme = dataclasses.replace(Theme.load(THEME_DIR), sounds={"pickup": tmp_path / "pickup.wav"})
    app = _app(tmp_path, theme=theme)
    app.state_machine.transition(BoothState.PREVIEW)

    app._start_countdown()
    app._stop_mic_check()

    assert calls == []


def _fake_session(tmp_path, **overrides) -> RecordingSession:
    data = dict(
        session_id="20260101_000000_abcd1234",
        started_at=datetime.now(timezone.utc),
        live_output_path=tmp_path / "out.mp4",
        final_output_path=tmp_path / "out.mp4",
        needs_transcode=False,
    )
    data.update(overrides)
    return RecordingSession(**data)


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
    app.state_machine.transition(BoothState.PREVIEW)
    app.state_machine.transition(BoothState.COUNTDOWN)
    app.state_machine.transition(BoothState.RECORDING)
    app._start_recording_mic_meter()
    assert app._mic_check_thread is not None

    app._stop_and_save()

    assert app._mic_check_thread is None


def test_start_preview_sets_state_and_deadline(tmp_path):
    app = _app(tmp_path)
    before = time.monotonic()

    app._start_preview()
    try:
        assert app.state_machine.state == BoothState.PREVIEW
        assert app._preview_deadline is not None
        assert app._preview_deadline >= before + app.config.preview_seconds
    finally:
        app._stop_mic_check()


def test_start_countdown_from_preview_clears_preview_deadline(tmp_path):
    app = _app(tmp_path)
    app.state_machine.transition(BoothState.PREVIEW)

    app._start_countdown()

    assert app.state_machine.state == BoothState.COUNTDOWN
    assert app._preview_deadline is None
    assert app._countdown_deadline is not None


def test_cancel_to_ready_from_preview_stops_mic_check(tmp_path):
    app = _app(tmp_path)
    app._start_preview()

    app._cancel_to_ready()

    assert app.state_machine.state == BoothState.READY
    assert app._preview_deadline is None
    assert app._mic_check_thread is None


def test_cancel_to_ready_from_countdown_clears_countdown_deadline(tmp_path):
    app = _app(tmp_path)
    app.state_machine.transition(BoothState.PREVIEW)
    app.state_machine.transition(BoothState.COUNTDOWN)
    app._countdown_deadline = time.monotonic() + 100

    app._cancel_to_ready()

    assert app.state_machine.state == BoothState.READY
    assert app._countdown_deadline is None


def test_tick_auto_advances_preview_to_countdown_after_deadline(tmp_path):
    app = _app(tmp_path)
    app.state_machine.transition(BoothState.PREVIEW)
    app._preview_deadline = time.monotonic() - 0.01

    app.tick()

    assert app.state_machine.state == BoothState.COUNTDOWN


def test_tick_does_not_advance_preview_before_deadline(tmp_path):
    app = _app(tmp_path)
    app.state_machine.transition(BoothState.PREVIEW)
    app._preview_deadline = time.monotonic() + 100

    app.tick()

    assert app.state_machine.state == BoothState.PREVIEW


def test_handle_key_space_in_preview_starts_countdown(tmp_path):
    app = _app(tmp_path)
    app.state_machine.transition(BoothState.PREVIEW)

    app.handle_key(main_module.KEY_SPACE)

    assert app.state_machine.state == BoothState.COUNTDOWN


def test_on_mouse_tap_inside_button_rect_starts_countdown(tmp_path):
    app = _app(tmp_path)
    app.state_machine.transition(BoothState.PREVIEW)
    app.renderer.record_button_rect = (10, 10, 100, 60)

    app._on_mouse(main_module.cv2.EVENT_LBUTTONDOWN, 50, 30, 0, None)

    assert app.state_machine.state == BoothState.COUNTDOWN


def test_on_mouse_tap_outside_button_rect_does_nothing(tmp_path):
    app = _app(tmp_path)
    app.state_machine.transition(BoothState.PREVIEW)
    app.renderer.record_button_rect = (10, 10, 100, 60)

    app._on_mouse(main_module.cv2.EVENT_LBUTTONDOWN, 500, 500, 0, None)

    assert app.state_machine.state == BoothState.PREVIEW


def test_on_mouse_ignored_outside_preview_state(tmp_path):
    app = _app(tmp_path)  # still READY
    app.renderer.record_button_rect = (10, 10, 100, 60)

    app._on_mouse(main_module.cv2.EVENT_LBUTTONDOWN, 50, 30, 0, None)

    assert app.state_machine.state == BoothState.READY


def test_on_mouse_noop_when_button_rect_not_set_yet(tmp_path):
    app = _app(tmp_path)
    app.state_machine.transition(BoothState.PREVIEW)
    assert app.renderer.record_button_rect is None

    app._on_mouse(main_module.cv2.EVENT_LBUTTONDOWN, 50, 30, 0, None)  # must not raise

    assert app.state_machine.state == BoothState.PREVIEW


def test_on_mouse_ignores_non_click_events(tmp_path):
    app = _app(tmp_path)
    app.state_machine.transition(BoothState.PREVIEW)
    app.renderer.record_button_rect = (10, 10, 100, 60)

    app._on_mouse(main_module.cv2.EVENT_MOUSEMOVE, 50, 30, 0, None)

    assert app.state_machine.state == BoothState.PREVIEW


def test_render_preview_state_uses_renderer_and_sets_button_rect(tmp_path):
    app = _app(tmp_path)
    app.state_machine.transition(BoothState.PREVIEW)

    frame = app.render(None)

    assert frame is not None
    assert app.renderer.record_button_rect is not None
