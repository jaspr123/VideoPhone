import dataclasses
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from video_guestbook import main as main_module
from video_guestbook.config import BoothConfig
from video_guestbook.main import (
    _METER_CEILING_DBFS,
    _METER_FLOOR_DBFS,
    ADMIN_LONG_PRESS_SECONDS,
    BoothApp,
    _mic_fraction,
    _point_in_rect,
)
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


def _config_dict(**overrides) -> dict:
    data = {
        "camera_device": "/dev/video0", "record_resolution": "1280x720", "record_fps": 30,
        "record_input_format": "mjpeg", "audio_device": "plughw:CARD=Device,DEV=0",
        "audio_sample_rate": 48000, "audio_channels": 1, "audio_bitrate": "160k",
        "countdown_seconds": 3, "max_recording_seconds": 90, "output_dir": "recordings",
        "log_dir": "logs", "preview_resolution": "1024x576", "preview_fps": 20,
    }
    data.update(overrides)
    return data


def _app_with_config_file(tmp_path, theme=None, **overrides) -> BoothApp:
    """Like _app, but backed by a real config JSON file on disk, so
    _save_settings (BoothConfig.save_settings) has somewhere to write."""
    theme = theme or Theme.load(THEME_DIR)
    config_path = tmp_path / "booth.json"
    config_path.write_text(json.dumps(_config_dict(**overrides)), encoding="utf-8")
    config = BoothConfig.from_dict(_config_dict(**overrides), base_dir=tmp_path)
    return BoothApp(config, theme, logging.getLogger("test"), config_path=config_path, config_base_dir=tmp_path)


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


def test_render_countdown_state_uses_renderer(tmp_path):
    app = _app(tmp_path)
    app.state_machine.transition(BoothState.PREVIEW)
    app.state_machine.transition(BoothState.COUNTDOWN)
    app._countdown_deadline = time.monotonic() + 3

    frame = app.render(None)

    assert frame is not None


def test_render_recording_state_uses_renderer_and_sets_cancel_rect(tmp_path):
    app = _app(tmp_path)
    app.state_machine.transition(BoothState.PREVIEW)
    app.state_machine.transition(BoothState.COUNTDOWN)
    app.state_machine.transition(BoothState.RECORDING)

    frame = app.render(None)

    assert frame is not None
    assert app.renderer.cancel_button_rect is not None


def test_render_settings_state_uses_renderer(tmp_path):
    app = _app(tmp_path)
    app.state_machine.transition(BoothState.SETTINGS)

    frame = app.render(None)

    assert frame is not None
    assert "save_exit" in app.renderer.settings_rects


def test_render_developer_state_uses_renderer(tmp_path):
    app = _app(tmp_path)
    app.state_machine.transition(BoothState.SETTINGS)
    app.state_machine.transition(BoothState.DEVELOPER)

    frame = app.render(None)

    assert frame is not None
    assert "back" in app.renderer.developer_rects


# ── admin corner long-press ──────────────────────────────────────────


def _admin_corner_point(app: BoothApp) -> tuple[int, int]:
    return app.renderer.w - 5, app.renderer.h - 5


def test_is_admin_corner_true_in_bottom_right_zone(tmp_path):
    app = _app(tmp_path)
    x, y = _admin_corner_point(app)
    assert app._is_admin_corner(x, y) is True


def test_is_admin_corner_false_elsewhere(tmp_path):
    app = _app(tmp_path)
    assert app._is_admin_corner(app.renderer.w // 2, app.renderer.h // 2) is False


def test_long_press_in_admin_corner_opens_settings(tmp_path):
    app = _app(tmp_path)
    x, y = _admin_corner_point(app)

    app._on_mouse(main_module.cv2.EVENT_LBUTTONDOWN, x, y, 0, None)
    assert app._admin_press_started_at is not None
    app._admin_press_started_at -= ADMIN_LONG_PRESS_SECONDS + 0.1  # simulate the hold
    app._on_mouse(main_module.cv2.EVENT_LBUTTONUP, x, y, 0, None)

    assert app.state_machine.state == BoothState.SETTINGS


def test_short_press_in_admin_corner_does_not_open_settings(tmp_path):
    app = _app(tmp_path)
    x, y = _admin_corner_point(app)

    app._on_mouse(main_module.cv2.EVENT_LBUTTONDOWN, x, y, 0, None)
    app._on_mouse(main_module.cv2.EVENT_LBUTTONUP, x, y, 0, None)

    assert app.state_machine.state == BoothState.READY


def test_long_press_outside_corner_does_not_open_settings(tmp_path):
    app = _app(tmp_path)

    app._on_mouse(main_module.cv2.EVENT_LBUTTONDOWN, app.renderer.w // 2, app.renderer.h // 2, 0, None)
    app._admin_press_started_at = time.monotonic() - (ADMIN_LONG_PRESS_SECONDS + 0.1)
    app._on_mouse(main_module.cv2.EVENT_LBUTTONUP, app.renderer.w // 2, app.renderer.h // 2, 0, None)

    assert app.state_machine.state == BoothState.READY


def test_long_press_ignored_outside_ready_state(tmp_path):
    app = _app(tmp_path)
    app.state_machine.transition(BoothState.PREVIEW)
    x, y = _admin_corner_point(app)

    app._on_mouse(main_module.cv2.EVENT_LBUTTONDOWN, x, y, 0, None)
    assert app._admin_press_started_at is None


# ── SETTINGS controls ────────────────────────────────────────────────


def test_settings_message_length_stepper_persists(tmp_path):
    app = _app_with_config_file(tmp_path)
    app._apply_settings_action("msg_plus")
    assert app.config.max_recording_seconds == 105
    reloaded = json.loads(app.config_path.read_text(encoding="utf-8"))
    assert reloaded["max_recording_seconds"] == 105


def test_settings_message_length_stepper_floor(tmp_path):
    app = _app_with_config_file(tmp_path, max_recording_seconds=15)
    app._apply_settings_action("msg_minus")
    assert app.config.max_recording_seconds == 15  # clamped, not negative


def test_settings_countdown_stepper_persists(tmp_path):
    app = _app_with_config_file(tmp_path)
    app._apply_settings_action("cd_plus")
    assert app.config.countdown_seconds == 4


def test_settings_quality_compat_toggle(tmp_path):
    app = _app_with_config_file(tmp_path)
    app._apply_settings_action("compat")
    assert app.config.recording_mode == "fast"
    app._apply_settings_action("quality")
    assert app.config.recording_mode == "quality"


def test_settings_mic_toggle_flips_current_value(tmp_path):
    app = _app_with_config_file(tmp_path, live_mic_meter_enabled=False)
    app._apply_settings_action("mic_toggle")
    assert app.config.live_mic_meter_enabled is True


def test_settings_extended_mode_sets_default_seconds(tmp_path):
    app = _app_with_config_file(tmp_path)
    app._apply_settings_action("extended")
    assert app.config.extended_mode is True
    assert app.config.max_recording_seconds == main_module.EXTENDED_MAX_RECORDING_SECONDS

    app._apply_settings_action("guestbook")
    assert app.config.extended_mode is False
    assert app.config.max_recording_seconds == main_module.GUESTBOOK_MAX_RECORDING_SECONDS


def test_settings_developer_button_enters_developer_and_starts_mic_check(tmp_path):
    app = _app_with_config_file(tmp_path)
    app.state_machine.transition(BoothState.SETTINGS)

    app._apply_settings_action("developer")
    try:
        assert app.state_machine.state == BoothState.DEVELOPER
        assert app._mic_check_thread is not None
    finally:
        app._stop_mic_check()


def test_settings_save_exit_returns_to_ready(tmp_path):
    app = _app_with_config_file(tmp_path)
    app.state_machine.transition(BoothState.SETTINGS)

    app._apply_settings_action("save_exit")

    assert app.state_machine.state == BoothState.READY


@pytest.mark.parametrize("name", ["change_event", "create_event", "test_recording"])
def test_settings_placeholder_buttons_are_noop(tmp_path, name):
    app = _app_with_config_file(tmp_path)
    before = app.config

    app._apply_settings_action(name)

    assert app.config is before  # nothing persisted


def test_settings_tap_dispatches_by_rect(tmp_path):
    app = _app_with_config_file(tmp_path)
    app.state_machine.transition(BoothState.SETTINGS)
    app.render(None)  # populate renderer.settings_rects
    x0, y0, x1, y1 = app.renderer.settings_rects["msg_plus"]

    app._on_mouse(main_module.cv2.EVENT_LBUTTONDOWN, (x0 + x1) // 2, (y0 + y1) // 2, 0, None)

    assert app.config.max_recording_seconds == 105


# ── DEVELOPER controls ───────────────────────────────────────────────


def test_developer_back_returns_to_settings_and_stops_mic_check(tmp_path):
    app = _app_with_config_file(tmp_path)
    app.state_machine.transition(BoothState.SETTINGS)
    app._apply_settings_action("developer")
    assert app._mic_check_thread is not None

    app._apply_developer_action("back")

    assert app.state_machine.state == BoothState.SETTINGS
    assert app._mic_check_thread is None


@pytest.mark.parametrize("name", ["export_logs", "restart_booth"])
def test_developer_unimplemented_buttons_are_noop(tmp_path, name):
    app = _app_with_config_file(tmp_path)
    app.state_machine.transition(BoothState.SETTINGS)
    app.state_machine.transition(BoothState.DEVELOPER)

    app._apply_developer_action(name)

    assert app.state_machine.state == BoothState.DEVELOPER


# ── Cancel & restart ─────────────────────────────────────────────────


def test_cancel_recording_discards_file_and_returns_to_preview(tmp_path, monkeypatch):
    app = _app(tmp_path)
    app.state_machine.transition(BoothState.PREVIEW)
    app.state_machine.transition(BoothState.COUNTDOWN)
    app.state_machine.transition(BoothState.RECORDING)

    discarded = tmp_path / "discarded.mp4"
    discarded.write_bytes(b"fake video data")
    session = _fake_session(tmp_path, live_output_path=discarded, final_output_path=discarded)
    monkeypatch.setattr(app.recorder, "stop", lambda: session)
    monkeypatch.setattr(app, "_reopen_camera_with_retry", lambda: True)

    app._cancel_recording()

    assert app.state_machine.state == BoothState.PREVIEW
    assert not discarded.exists()
    app._stop_mic_check()


def test_cancel_recording_button_hit_test(tmp_path, monkeypatch):
    app = _app(tmp_path)
    app.state_machine.transition(BoothState.PREVIEW)
    app.state_machine.transition(BoothState.COUNTDOWN)
    app.state_machine.transition(BoothState.RECORDING)
    app.renderer.cancel_button_rect = (10, 10, 100, 60)
    monkeypatch.setattr(app, "_cancel_recording", lambda: setattr(app, "_cancel_called", True))

    app._on_mouse(main_module.cv2.EVENT_LBUTTONDOWN, 50, 30, 0, None)

    assert getattr(app, "_cancel_called", False) is True


# ── misc helpers ──────────────────────────────────────────────────────


def test_point_in_rect():
    rect = (10, 10, 100, 60)
    assert _point_in_rect(50, 30, rect) is True
    assert _point_in_rect(5, 30, rect) is False
    assert _point_in_rect(50, 500, rect) is False


def test_count_recordings_counts_only_mp4(tmp_path):
    output_dir = tmp_path / "recordings"
    output_dir.mkdir()
    (output_dir / "a.mp4").write_bytes(b"x")
    (output_dir / "b.mp4").write_bytes(b"x")
    (output_dir / "c.raw.mkv").write_bytes(b"x")
    (output_dir / "d.tmp").write_bytes(b"x")

    assert main_module._count_recordings(output_dir) == 2


def test_count_recordings_missing_dir_returns_zero(tmp_path):
    assert main_module._count_recordings(tmp_path / "does-not-exist") == 0


def test_developer_snapshot_has_expected_shape(tmp_path):
    app = _app(tmp_path)
    snapshot = app._developer_snapshot()
    expected_keys = {
        "camera_frame_bgr", "preview_fps", "camera_device", "dropped_frames",
        "peak_db", "room_base_db", "clip_count", "mic_device", "hook_off_hook",
        "hook_pin_state", "hook_held_seconds", "overlay_draw_ms", "cpu_temp_c",
        "throttled", "cpu_load_pct", "mem_used_mb", "mem_total_mb",
        "av_sync_offset_ms", "disk_free_gb", "disk_free_estimate_messages",
        "encoder_status", "uptime_seconds", "last_error",
    }
    assert set(snapshot) == expected_keys
    assert snapshot["encoder_status"] == "idle"
    assert snapshot["last_error"] == "none"


def test_developer_snapshot_reflects_last_result_reason(tmp_path):
    app = _app(tmp_path)
    app._last_result_reason = "camera read failed"
    assert app._developer_snapshot()["last_error"] == "camera read failed"


# ── "Find receiver switch" wizard ────────────────────────────────────

from video_guestbook.hardware import hook_switch as hook_switch_module  # noqa: E402


class _FakeGpioButton:
    def __init__(self, pin, pull_up=None, bounce_time=None, pin_factory=None):
        self.pin = pin
        self.is_pressed = False
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def fake_gpio(monkeypatch):
    """Patches gpiozero's Button everywhere it's referenced -- main.py's
    own `Button` name (used directly by the scan wizard) and
    hardware/hook_switch.py's (used internally by HookSwitch, which
    main.py's open_hook_switch() constructs) -- sharing one registry so
    a test can drive both through the same fake pins. A pin listed in
    `unavailable` raises on construction, simulating gpiozero refusing to
    claim it (already in use, invalid on this board, etc).
    """
    created: dict[int, _FakeGpioButton] = {}
    unavailable: set[int] = set()

    def factory(pin, **kwargs):
        if pin in unavailable:
            raise RuntimeError(f"pin {pin} busy")
        button = _FakeGpioButton(pin, **kwargs)
        created[pin] = button
        return button

    monkeypatch.setattr(main_module, "Button", factory)
    monkeypatch.setattr(hook_switch_module, "Button", factory)
    return created, unavailable


def test_start_hook_scan_opens_all_candidates_and_closes_existing_switch(tmp_path, fake_gpio):
    created, _ = fake_gpio
    app = _app(tmp_path)
    app.open_hook_switch()
    original_switch = app.hook_switch
    assert original_switch is not None

    app._start_hook_scan()

    assert app.hook_switch is None  # released so candidate pins can be opened
    assert original_switch._button.closed is True
    assert app._hook_scan["phase"] == "monitor"
    assert set(app._hook_scan["pins"]) == set(main_module.HOOK_SCAN_CANDIDATE_PINS)
    assert app._hook_scan["pins"][0] == app.config.hook_switch_gpio_pin  # configured pin first
    assert set(created) == set(main_module.HOOK_SCAN_CANDIDATE_PINS)


def test_start_hook_scan_marks_unclaimable_pins_unavailable(tmp_path, fake_gpio):
    _, unavailable = fake_gpio
    unavailable.add(22)
    app = _app(tmp_path)

    app._start_hook_scan()

    assert app._hook_scan["buttons"][22] is None
    view = app._hook_scan_view()
    assert view["pin_states"][22] is None


def test_start_hook_scan_without_gpiozero_sets_error_phase(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "Button", None)
    app = _app(tmp_path)

    app._start_hook_scan()

    assert app._hook_scan["phase"] == "error"
    assert "gpiozero" in app._hook_scan["error"]


def test_start_hook_scan_all_pins_unclaimable_sets_error_phase(tmp_path, monkeypatch):
    def always_fails(pin, **kwargs):
        raise RuntimeError("busy")

    monkeypatch.setattr(main_module, "Button", always_fails)
    app = _app(tmp_path)

    app._start_hook_scan()

    assert app._hook_scan["phase"] == "error"


def test_select_hook_scan_pin_moves_to_confirm(tmp_path, fake_gpio):
    app = _app(tmp_path)
    app._start_hook_scan()

    app._select_hook_scan_pin(22)

    assert app._hook_scan["phase"] == "confirm"
    assert app._hook_scan["selected_pin"] == 22


def test_select_hook_scan_pin_ignores_unavailable(tmp_path, fake_gpio):
    _, unavailable = fake_gpio
    unavailable.add(22)
    app = _app(tmp_path)
    app._start_hook_scan()

    app._select_hook_scan_pin(22)

    assert app._hook_scan["phase"] == "monitor"
    assert app._hook_scan["selected_pin"] is None


def test_confirm_hook_scan_polarity_normal_wiring(tmp_path, fake_gpio):
    created, _ = fake_gpio
    app = _app(tmp_path)
    app._start_hook_scan()
    app._select_hook_scan_pin(22)
    created[22].is_pressed = True  # "lifted" reads raw True -> not inverted

    app._confirm_hook_scan_polarity()

    assert app._hook_scan["phase"] == "result"
    assert app._hook_scan["invert"] is False


def test_confirm_hook_scan_polarity_inverted_wiring(tmp_path, fake_gpio):
    created, _ = fake_gpio
    app = _app(tmp_path)
    app._start_hook_scan()
    app._select_hook_scan_pin(22)
    created[22].is_pressed = False  # "lifted" reads raw False -> inverted

    app._confirm_hook_scan_polarity()

    assert app._hook_scan["phase"] == "result"
    assert app._hook_scan["invert"] is True


def test_retry_hook_scan_resets_to_monitor(tmp_path, fake_gpio):
    app = _app(tmp_path)
    app._start_hook_scan()
    app._select_hook_scan_pin(22)
    app._confirm_hook_scan_polarity()

    app._retry_hook_scan()

    assert app._hook_scan["phase"] == "monitor"
    assert app._hook_scan["selected_pin"] is None
    assert app._hook_scan["invert"] is None


def test_apply_hook_scan_result_saves_settings_and_reopens_switch(tmp_path, fake_gpio):
    created, _ = fake_gpio
    app = _app_with_config_file(tmp_path)
    app._start_hook_scan()
    app._select_hook_scan_pin(22)
    created[22].is_pressed = True
    app._confirm_hook_scan_polarity()

    app._apply_hook_scan_result()

    assert app._hook_scan is None
    assert app.config.hook_switch_gpio_pin == 22
    assert app.config.hook_switch_invert is False
    assert app.hook_switch is not None  # reopened on the new pin
    reloaded = json.loads(app.config_path.read_text(encoding="utf-8"))
    assert reloaded["hook_switch_gpio_pin"] == 22


def test_cancel_hook_scan_reopens_original_pin_without_saving(tmp_path, fake_gpio):
    app = _app_with_config_file(tmp_path)
    original_pin = app.config.hook_switch_gpio_pin
    app._start_hook_scan()
    app._select_hook_scan_pin(22)

    app._cancel_hook_scan()

    assert app._hook_scan is None
    assert app.config.hook_switch_gpio_pin == original_pin  # unchanged
    assert app.hook_switch is not None


def test_apply_hook_scan_action_dispatches_pin_selection(tmp_path, fake_gpio):
    app = _app(tmp_path)
    app._start_hook_scan()

    app._apply_hook_scan_action("pin_22")

    assert app._hook_scan["selected_pin"] == 22


@pytest.mark.parametrize(
    "action,expected_phase",
    [("cancel", None), ("confirm", "monitor")],  # confirm before selecting a pin is a no-op
)
def test_apply_hook_scan_action_cancel_and_noop_confirm(tmp_path, fake_gpio, action, expected_phase):
    app = _app(tmp_path)
    app._start_hook_scan()

    app._apply_hook_scan_action(action)

    if expected_phase is None:
        assert app._hook_scan is None
    else:
        assert app._hook_scan["phase"] == expected_phase


def test_developer_find_switch_button_starts_scan(tmp_path, fake_gpio):
    app = _app(tmp_path)
    app.state_machine.transition(BoothState.SETTINGS)
    app.state_machine.transition(BoothState.DEVELOPER)

    app._apply_developer_action("find_switch")

    assert app._hook_scan is not None
    assert app._hook_scan["phase"] == "monitor"


def test_leaving_developer_during_scan_tears_it_down(tmp_path, fake_gpio):
    app = _app(tmp_path)
    app.state_machine.transition(BoothState.SETTINGS)
    app.state_machine.transition(BoothState.DEVELOPER)
    app._start_hook_scan()

    app._leave_developer_to_settings()

    assert app._hook_scan is None
    assert app.state_machine.state == BoothState.SETTINGS
    assert app.hook_switch is not None  # restored


def test_render_dispatches_to_hook_scan_while_active(tmp_path, fake_gpio):
    app = _app(tmp_path)
    app.state_machine.transition(BoothState.SETTINGS)
    app.state_machine.transition(BoothState.DEVELOPER)
    app._start_hook_scan()

    frame = app.render(None)

    assert frame is not None
    assert "cancel" in app.renderer.hook_scan_rects


def test_mouse_down_in_developer_dispatches_to_hook_scan_rects(tmp_path, fake_gpio):
    app = _app(tmp_path)
    app.state_machine.transition(BoothState.SETTINGS)
    app.state_machine.transition(BoothState.DEVELOPER)
    app._start_hook_scan()
    app.render(None)
    x0, y0, x1, y1 = app.renderer.hook_scan_rects["cancel"]

    app._on_mouse(main_module.cv2.EVENT_LBUTTONDOWN, (x0 + x1) // 2, (y0 + y1) // 2, 0, None)

    assert app._hook_scan is None


def test_hook_scan_candidate_pins_fit_grid_capacity():
    # render_hook_scan lays candidates out in a 5-column grid; more than
    # 20 would need a layout change to avoid the button row overlapping
    # the last grid row (see ui/renderer.py's _draw_hook_pin_grid).
    assert len(main_module.HOOK_SCAN_CANDIDATE_PINS) <= 20
    assert len(set(main_module.HOOK_SCAN_CANDIDATE_PINS)) == len(main_module.HOOK_SCAN_CANDIDATE_PINS)
