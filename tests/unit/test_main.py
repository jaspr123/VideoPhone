import pytest

from video_guestbook.main import _METER_CEILING_DBFS, _METER_FLOOR_DBFS, _mic_fraction
from video_guestbook.media.audio_levels import LevelReading


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
