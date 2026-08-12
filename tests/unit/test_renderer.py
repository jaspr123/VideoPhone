import time
from pathlib import Path

import numpy as np
import pytest

from video_guestbook.ui.renderer import Renderer, _blend_rgb, _lerp
from video_guestbook.ui.theme import Theme

PROJECT_ROOT = Path(__file__).resolve().parents[2]
THEME_DIR = PROJECT_ROOT / "themes" / "classic-walnut"


@pytest.fixture(scope="module")
def theme():
    return Theme.load(THEME_DIR)


@pytest.fixture
def renderer(theme):
    return Renderer(theme, 1024, 576)


def _assert_frame(frame, w=1024, h=576):
    assert isinstance(frame, np.ndarray)
    assert frame.shape == (h, w, 3)
    assert frame.dtype == np.uint8


def _settings_kwargs(**overrides):
    kwargs = dict(
        max_recording_seconds=90,
        countdown_seconds=3,
        recording_mode="quality",
        live_mic_meter_enabled=False,
        extended_mode=False,
        message_count=47,
        storage_free_gb=41.2,
        camera_ok=True,
        camera_device="/dev/video0",
        mic_ok=True,
        mic_device="USB Device",
        hook_ok=True,
        hook_label="On hook · GPIO 17",
        theme_name="classic-walnut",
    )
    kwargs.update(overrides)
    return kwargs


def _developer_kwargs(**overrides):
    kwargs = dict(
        camera_frame_bgr=None,
        preview_fps=20.0,
        camera_device="/dev/video0",
        dropped_frames=0,
        peak_db=-14.0,
        room_base_db=-48.0,
        clip_count=0,
        mic_device="plughw:Device",
        hook_off_hook=False,
        hook_pin_state="HIGH",
        hook_held_seconds=0.0,
        overlay_draw_ms=6.2,
        cpu_temp_c=58.4,
        throttled=False,
        cpu_load_pct=11.0,
        mem_used_mb=611.0,
        mem_total_mb=1892.0,
        av_sync_offset_ms=0,
        disk_free_gb=41.2,
        disk_free_estimate_messages=380,
        encoder_status="idle",
        uptime_seconds=11520.0,
        last_error="none",
    )
    kwargs.update(overrides)
    return kwargs


def test_blend_rgb_endpoints():
    assert _blend_rgb((0, 0, 0), (200, 100, 50), 0.0) == (0, 0, 0)
    assert _blend_rgb((0, 0, 0), (200, 100, 50), 1.0) == (200, 100, 50)


def test_blend_rgb_midpoint_matches_lerp():
    bg, fg = (10, 20, 30), (200, 150, 100)
    result = _blend_rgb(bg, fg, 0.25)
    expected = tuple(int(round(_lerp(a, b, 0.25))) for a, b in zip(bg, fg))
    assert result == expected


def test_renderer_construction_caches_background(renderer):
    assert renderer._bg.size == (1024, 576)
    assert renderer._bg_dimmed.size == (1024, 576)


def test_render_ready(renderer):
    _assert_frame(renderer.render_ready(time.monotonic()))


def test_render_ready_is_cached_across_calls(renderer):
    now = time.monotonic()
    renderer.render_ready(now)
    assert ("ready",) in renderer._static_cache


def test_render_preview_without_frame(renderer):
    _assert_frame(renderer.render_preview(time.monotonic(), None, 0.0, False))


def test_render_preview_sets_record_button_rect(renderer):
    assert renderer.record_button_rect is None
    renderer.render_preview(time.monotonic(), None, 0.0, False)
    x0, y0, x1, y1 = renderer.record_button_rect
    assert 0 <= x0 < x1 <= 1024
    assert 0 <= y0 < y1 <= 576


@pytest.mark.parametrize("shape", [(720, 1280, 3), (480, 640, 3), (1080, 1080, 3)])
def test_render_preview_with_various_frame_aspect_ratios(renderer, shape):
    frame = np.random.randint(0, 255, shape, dtype=np.uint8)
    _assert_frame(renderer.render_preview(time.monotonic(), frame, 0.6, True))


@pytest.mark.parametrize("fraction", [-1.0, 0.0, 0.5, 1.0, 2.0])
def test_render_preview_mic_fraction_out_of_range_does_not_raise(renderer, fraction):
    _assert_frame(renderer.render_preview(time.monotonic(), None, fraction, False))


def test_render_countdown_without_frame_or_mic(renderer):
    _assert_frame(renderer.render_countdown(time.monotonic(), 2.0, 3, None, None, False))


def test_render_countdown_with_frame_and_mic(renderer):
    frame = np.random.randint(0, 255, (720, 1280, 3), dtype=np.uint8)
    _assert_frame(renderer.render_countdown(time.monotonic(), 0.0, 3, frame, 0.4, False))


def test_render_countdown_handles_zero_total_seconds(renderer):
    _assert_frame(renderer.render_countdown(time.monotonic(), 0.0, 0, None, None, False))


def test_render_countdown_go_state(renderer):
    _assert_frame(renderer.render_countdown(time.monotonic(), 0.0, 3, None, None, False))


def test_render_recording_without_frame(renderer):
    _assert_frame(renderer.render_recording(time.monotonic(), None, 85, 0.5, False))


@pytest.mark.parametrize("shape", [(720, 1280, 3), (480, 640, 3), (1080, 1080, 3)])
def test_render_recording_with_various_frame_aspect_ratios(renderer, shape):
    now = time.monotonic()
    frame = np.random.randint(0, 255, shape, dtype=np.uint8)
    _assert_frame(renderer.render_recording(now, frame, 1, 0.9, True))


@pytest.mark.parametrize("fraction", [-1.0, 0.0, 0.5, 1.0, 2.0])
def test_render_recording_mic_fraction_out_of_range_does_not_raise(renderer, fraction):
    _assert_frame(renderer.render_recording(time.monotonic(), None, 90, fraction, False))


def test_render_recording_clipped_vs_not(renderer):
    now = time.monotonic()
    _assert_frame(renderer.render_recording(now, None, 90, 0.95, True))
    _assert_frame(renderer.render_recording(now, None, 90, 0.95, False))


def test_render_recording_sets_cancel_button_rect(renderer):
    assert renderer.cancel_button_rect is None
    renderer.render_recording(time.monotonic(), None, 90, 0.0, False)
    x0, y0, x1, y1 = renderer.cancel_button_rect
    assert 0 <= x0 < x1 <= 1024
    assert 0 <= y0 < y1 <= 576


def test_render_saving(renderer):
    _assert_frame(renderer.render_saving(time.monotonic()))


def test_render_saving_is_cached_across_calls(renderer):
    renderer.render_saving(time.monotonic())
    assert ("saving",) in renderer._static_cache


def test_render_saved_progresses_checkmark(renderer):
    now = time.monotonic()
    _assert_frame(renderer.render_saved(now, 0.0))
    _assert_frame(renderer.render_saved(now, 1.0))


def test_render_error_with_and_without_reason(renderer):
    now = time.monotonic()
    _assert_frame(renderer.render_error(now, ""))
    _assert_frame(renderer.render_error(now, "camera read failed"))


def test_render_error_wraps_long_reason(renderer):
    reason = "Recording was too short to save — please lift the receiver and try again"
    _assert_frame(renderer.render_error(time.monotonic(), reason))
    assert ("error", reason) in renderer._static_cache


def test_countdown_numeral_pop_state_resets_on_new_count(renderer):
    now = time.monotonic()
    renderer.render_countdown(now, 2.6, 3, None, None, False)  # count == 3
    first_pop_state = renderer._pop_state
    renderer.render_countdown(now, 1.9, 3, None, None, False)  # count == 2
    assert renderer._pop_state != first_pop_state
    assert renderer._pop_state[0] == 2


def test_renderer_without_couple_photo(theme):
    from dataclasses import replace

    photo_off_theme = replace(theme, show_couple_photo=False, couple_photo_path=None)
    r = Renderer(photo_off_theme, 1024, 576)
    assert r._couple_photo is None
    _assert_frame(r.render_ready(time.monotonic()))


def test_render_settings(renderer):
    _assert_frame(renderer.render_settings(time.monotonic(), **_settings_kwargs()))


def test_render_settings_extended_mode(renderer):
    _assert_frame(
        renderer.render_settings(
            time.monotonic(),
            **_settings_kwargs(extended_mode=True, max_recording_seconds=300, recording_mode="fast", live_mic_meter_enabled=True),
        )
    )


def test_render_settings_populates_control_rects(renderer):
    renderer.render_settings(time.monotonic(), **_settings_kwargs())
    expected = {
        "msg_minus", "msg_plus", "cd_minus", "cd_plus", "quality", "compat",
        "mic_toggle", "guestbook", "extended", "developer", "change_event",
        "create_event", "test_recording", "save_exit",
    }
    assert expected <= set(renderer.settings_rects)
    for rect in renderer.settings_rects.values():
        x0, y0, x1, y1 = rect
        assert 0 <= x0 < x1 <= 1024
        assert 0 <= y0 < y1 <= 576


def test_render_settings_hardware_status_flags(renderer):
    _assert_frame(
        renderer.render_settings(
            time.monotonic(),
            **_settings_kwargs(camera_ok=False, mic_ok=False, hook_ok=False, hook_label="Off hook · GPIO 17"),
        )
    )


def test_render_developer(renderer):
    _assert_frame(renderer.render_developer(time.monotonic(), **_developer_kwargs()))


@pytest.mark.parametrize("shape", [(720, 1280, 3), (480, 640, 3)])
def test_render_developer_with_camera_frame(renderer, shape):
    frame = np.random.randint(0, 255, shape, dtype=np.uint8)
    _assert_frame(renderer.render_developer(time.monotonic(), **_developer_kwargs(camera_frame_bgr=frame)))


def test_render_developer_populates_control_rects(renderer):
    renderer.render_developer(time.monotonic(), **_developer_kwargs())
    assert set(renderer.developer_rects) == {"back", "export_logs", "restart_booth", "find_switch"}
    for rect in renderer.developer_rects.values():
        x0, y0, x1, y1 = rect
        assert 0 <= x0 < x1 <= 1024
        assert 0 <= y0 < y1 <= 576


def test_render_developer_handles_missing_readings(renderer):
    _assert_frame(
        renderer.render_developer(
            time.monotonic(),
            **_developer_kwargs(cpu_temp_c=None, throttled=None, cpu_load_pct=None, mem_used_mb=None, mem_total_mb=None, disk_free_gb=None, disk_free_estimate_messages=None),
        )
    )


def test_render_developer_off_hook_and_clipping(renderer):
    _assert_frame(
        renderer.render_developer(
            time.monotonic(),
            **_developer_kwargs(hook_off_hook=True, hook_pin_state="LOW", hook_held_seconds=4.2, peak_db=-0.5, clip_count=3, last_error="camera read failed"),
        )
    )


# ── "Find receiver switch" wizard ────────────────────────────────────

_SCAN_PINS = [17, 27, 4, 5]


def test_render_hook_scan_monitor_phase(renderer):
    scan = {
        "phase": "monitor",
        "pins": _SCAN_PINS,
        "pin_states": {17: False, 27: False, 4: True, 5: None},
        "selected_pin": None,
        "invert": None,
        "configured_pin": 17,
    }
    _assert_frame(renderer.render_hook_scan(time.monotonic(), scan))
    assert "pin_17" in renderer.hook_scan_rects
    assert "pin_4" in renderer.hook_scan_rects
    assert "pin_5" not in renderer.hook_scan_rects  # unavailable, not selectable
    assert "cancel" in renderer.hook_scan_rects


def test_render_hook_scan_confirm_phase(renderer):
    scan = {
        "phase": "confirm",
        "pins": _SCAN_PINS,
        "pin_states": {17: False, 27: False, 4: True, 5: None},
        "selected_pin": 4,
        "invert": None,
        "configured_pin": 17,
    }
    _assert_frame(renderer.render_hook_scan(time.monotonic(), scan))
    assert set(renderer.hook_scan_rects) == {"retry", "confirm"}


def test_render_hook_scan_result_phase(renderer):
    scan = {
        "phase": "result",
        "pins": _SCAN_PINS,
        "pin_states": {17: False, 27: False, 4: True, 5: None},
        "selected_pin": 4,
        "invert": True,
        "configured_pin": 17,
    }
    _assert_frame(renderer.render_hook_scan(time.monotonic(), scan))
    assert set(renderer.hook_scan_rects) == {"retry", "cancel", "apply"}


def test_render_hook_scan_error_phase(renderer):
    scan = {"phase": "error", "error": "gpiozero is not installed on this device"}
    _assert_frame(renderer.render_hook_scan(time.monotonic(), scan))
    assert set(renderer.hook_scan_rects) == {"cancel"}


def test_render_hook_scan_rects_within_bounds(renderer):
    # Matches main.py's HOOK_SCAN_CANDIDATE_PINS count (17) -- the actual
    # number the app ever passes in production.
    pins = [4, 5, 6, 12, 13, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27]
    scan = {
        "phase": "monitor",
        "pins": pins,
        "pin_states": {p: (p % 2 == 0) for p in pins},
        "selected_pin": None,
        "invert": None,
        "configured_pin": 17,
    }
    renderer.render_hook_scan(time.monotonic(), scan)
    for rect in renderer.hook_scan_rects.values():
        x0, y0, x1, y1 = rect
        assert 0 <= x0 < x1 <= 1024
        assert 0 <= y0 < y1 <= 576
