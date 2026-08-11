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


def test_render_countdown_without_mic(renderer):
    _assert_frame(renderer.render_countdown(time.monotonic(), 2.0, 3, None, None))


def test_render_countdown_with_mic_status(renderer):
    _assert_frame(renderer.render_countdown(time.monotonic(), 0.0, 3, "MICROPHONE READY", 0.4))


def test_render_countdown_handles_zero_total_seconds(renderer):
    _assert_frame(renderer.render_countdown(time.monotonic(), 0.0, 0, None, None))


def test_render_countdown_go_state(renderer):
    _assert_frame(renderer.render_countdown(time.monotonic(), 0.0, 3, None, None))


def test_render_recording_without_frame(renderer):
    _assert_frame(renderer.render_recording(time.monotonic(), None, 5, 85, 0.5, False))


@pytest.mark.parametrize("shape", [(720, 1280, 3), (480, 640, 3), (1080, 1080, 3)])
def test_render_recording_with_various_frame_aspect_ratios(renderer, shape):
    now = time.monotonic()
    frame = np.random.randint(0, 255, shape, dtype=np.uint8)
    _assert_frame(renderer.render_recording(now, frame, 1, 1, 0.9, True))


@pytest.mark.parametrize("fraction", [-1.0, 0.0, 0.5, 1.0, 2.0])
def test_render_recording_mic_fraction_out_of_range_does_not_raise(renderer, fraction):
    _assert_frame(renderer.render_recording(time.monotonic(), None, 0, 90, fraction, False))


def test_render_recording_clipped_vs_not(renderer):
    now = time.monotonic()
    _assert_frame(renderer.render_recording(now, None, 0, 90, 0.95, True))
    _assert_frame(renderer.render_recording(now, None, 0, 90, 0.95, False))


def test_render_saving(renderer):
    _assert_frame(renderer.render_saving(time.monotonic()))


def test_render_saved_progresses_checkmark(renderer):
    now = time.monotonic()
    _assert_frame(renderer.render_saved(now, 0.0))
    _assert_frame(renderer.render_saved(now, 1.0))


def test_render_error_with_and_without_reason(renderer):
    now = time.monotonic()
    _assert_frame(renderer.render_error(now, ""))
    _assert_frame(renderer.render_error(now, "camera read failed"))


def test_countdown_numeral_pop_state_resets_on_new_count(renderer):
    now = time.monotonic()
    renderer.render_countdown(now, 2.6, 3, None, None)  # count == 3
    first_pop_state = renderer._pop_state
    renderer.render_countdown(now, 1.9, 3, None, None)  # count == 2
    assert renderer._pop_state != first_pop_state
    assert renderer._pop_state[0] == 2


def test_renderer_without_couple_photo(theme):
    from dataclasses import replace

    photo_off_theme = replace(theme, show_couple_photo=False, couple_photo_path=None)
    r = Renderer(photo_off_theme, 1024, 576)
    assert r._couple_photo is None
    _assert_frame(r.render_ready(time.monotonic()))
