import struct

import pytest

from video_guestbook.media import audio_levels as audio_levels_module
from video_guestbook.media.audio_levels import (
    CLIP_PEAK_DBFS,
    LOUD_PEAK_DBFS,
    QUIET_RMS_DBFS,
    SILENCE_DBFS,
    STATUS_READY,
    STATUS_SPEAK_CLOSER,
    STATUS_TOO_LOUD,
    AudioLevelReader,
    MicrophoneError,
    classify_level,
    pcm16_to_dbfs,
    read_latest_live_level,
)


def _pcm(*samples: int) -> bytes:
    return b"".join(struct.pack("<h", s) for s in samples)


def _tone(amplitude: int, count: int = 100) -> bytes:
    return _pcm(*([amplitude] * count))


# ---------------------------------------------------------------------------
# pcm16_to_dbfs
# ---------------------------------------------------------------------------


def test_empty_chunk_is_silence():
    rms, peak = pcm16_to_dbfs(b"")
    assert rms == SILENCE_DBFS
    assert peak == SILENCE_DBFS


def test_odd_length_chunk_truncates_gracefully():
    # 3 bytes: one full sample plus a stray byte, must not raise.
    rms, peak = pcm16_to_dbfs(_pcm(1000) + b"\x00")
    assert rms != SILENCE_DBFS or peak != SILENCE_DBFS


def test_all_zero_samples_is_silence():
    rms, peak = pcm16_to_dbfs(_tone(0))
    assert rms == SILENCE_DBFS
    assert peak == SILENCE_DBFS


def test_full_scale_tone_is_near_zero_dbfs():
    rms, peak = pcm16_to_dbfs(_tone(32767))
    assert peak == pytest.approx(0.0, abs=0.01)
    assert rms == pytest.approx(0.0, abs=0.01)


def test_half_scale_tone_is_about_minus_6_dbfs():
    rms, peak = pcm16_to_dbfs(_tone(16384))
    assert peak == pytest.approx(-6.02, abs=0.1)
    assert rms == pytest.approx(-6.02, abs=0.1)


def test_quieter_tone_has_lower_dbfs_than_louder_tone():
    quiet_rms, quiet_peak = pcm16_to_dbfs(_tone(100))
    loud_rms, loud_peak = pcm16_to_dbfs(_tone(10000))
    assert quiet_rms < loud_rms
    assert quiet_peak < loud_peak


# ---------------------------------------------------------------------------
# classify_level
# ---------------------------------------------------------------------------


def test_classify_quiet_as_speak_closer():
    assert classify_level(QUIET_RMS_DBFS - 5, QUIET_RMS_DBFS - 5) == STATUS_SPEAK_CLOSER


def test_classify_normal_as_ready():
    assert classify_level(-20.0, -15.0) == STATUS_READY


def test_classify_loud_peak_as_too_loud():
    assert classify_level(-20.0, LOUD_PEAK_DBFS + 1) == STATUS_TOO_LOUD


def test_classify_clipping_peak_as_too_loud():
    assert classify_level(-10.0, CLIP_PEAK_DBFS) == STATUS_TOO_LOUD


def test_classify_boundary_values():
    # Exactly at the quiet threshold should NOT be "speak closer" (< not <=).
    assert classify_level(QUIET_RMS_DBFS, -20.0) == STATUS_READY
    # Exactly at the loud threshold should NOT be "too loud" (> not >=).
    assert classify_level(-20.0, LOUD_PEAK_DBFS) == STATUS_READY


# ---------------------------------------------------------------------------
# AudioLevelReader (subprocess mocked)
# ---------------------------------------------------------------------------


class _FakeStdout:
    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    def read(self, n: int) -> bytes:
        chunk = self._data[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk


class _FakeStderr:
    def __init__(self, data: bytes = b""):
        self._data = data

    def read(self) -> bytes:
        return self._data


class _FakeProcess:
    def __init__(self, stdout_data: bytes, stderr_data: bytes = b""):
        self.stdout = _FakeStdout(stdout_data)
        self.stderr = _FakeStderr(stderr_data)
        self.terminated = False
        self.killed = False
        self._exited = False

    def poll(self):
        return 0 if self._exited else None

    def terminate(self):
        self.terminated = True
        self._exited = True

    def kill(self):
        self.killed = True
        self._exited = True

    def wait(self, timeout=None):
        self._exited = True


@pytest.fixture(autouse=True)
def _stub_find_arecord(monkeypatch):
    monkeypatch.setattr(audio_levels_module, "find_arecord", lambda: "arecord")


def test_reader_start_failure_raises_microphone_error(monkeypatch):
    def raise_oserror(*args, **kwargs):
        raise OSError("no such device")

    monkeypatch.setattr(audio_levels_module.subprocess, "Popen", raise_oserror)

    reader = AudioLevelReader("plughw:0,0", 48000, 1, chunk_ms=100)
    with pytest.raises(MicrophoneError):
        reader.start()


def test_reader_read_returns_correct_reading(monkeypatch):
    chunk_bytes = int(48000 * 100 / 1000) * 1 * 2  # 100ms mono @ 48kHz
    fake = _FakeProcess(stdout_data=_tone(16384, count=chunk_bytes // 2))
    monkeypatch.setattr(audio_levels_module.subprocess, "Popen", lambda *a, **k: fake)

    reader = AudioLevelReader("plughw:0,0", 48000, 1, chunk_ms=100)
    reader.start()
    reading = reader.read()

    assert reading.peak_dbfs == pytest.approx(-6.02, abs=0.1)
    assert reading.clipped is False
    assert reading.status == STATUS_READY


def test_reader_read_raises_on_short_read(monkeypatch):
    fake = _FakeProcess(stdout_data=b"", stderr_data=b"device busy")
    monkeypatch.setattr(audio_levels_module.subprocess, "Popen", lambda *a, **k: fake)

    reader = AudioLevelReader("plughw:0,0", 48000, 1, chunk_ms=100)
    reader.start()
    with pytest.raises(MicrophoneError, match="device busy"):
        reader.read()


def test_reader_stop_terminates_process(monkeypatch):
    fake = _FakeProcess(stdout_data=b"")
    monkeypatch.setattr(audio_levels_module.subprocess, "Popen", lambda *a, **k: fake)

    reader = AudioLevelReader("plughw:0,0", 48000, 1, chunk_ms=100)
    reader.start()
    reader.stop()

    assert fake.terminated is True


def test_reader_context_manager_starts_and_stops(monkeypatch):
    fake = _FakeProcess(stdout_data=b"")
    monkeypatch.setattr(audio_levels_module.subprocess, "Popen", lambda *a, **k: fake)

    with AudioLevelReader("plughw:0,0", 48000, 1, chunk_ms=100) as reader:
        assert reader.is_running

    assert fake.terminated is True


# ---------------------------------------------------------------------------
# read_latest_live_level (parses ffmpeg's ametadata=print output -- see
# media/ffmpeg.py live_level_path)
# ---------------------------------------------------------------------------


def test_read_latest_live_level_missing_file_returns_none(tmp_path):
    assert read_latest_live_level(tmp_path / "does-not-exist.txt") is None


def test_read_latest_live_level_empty_file_returns_none(tmp_path):
    path = tmp_path / "levels.txt"
    path.write_text("", encoding="utf-8")
    assert read_latest_live_level(path) is None


def test_read_latest_live_level_incomplete_pair_returns_none(tmp_path):
    # Only Peak has been flushed so far -- e.g. a read caught mid-write.
    path = tmp_path / "levels.txt"
    path.write_text("frame:0    pts:0\nlavfi.astats.1.Peak_level=-18.5\n", encoding="utf-8")
    assert read_latest_live_level(path) is None


def test_read_latest_live_level_parses_single_reading(tmp_path):
    path = tmp_path / "levels.txt"
    path.write_text(
        "frame:0    pts:0       pts_time:0\n"
        "lavfi.astats.1.Peak_level=-18.063496\n"
        "lavfi.astats.1.RMS_level=-24.492307\n",
        encoding="utf-8",
    )

    reading = read_latest_live_level(path)

    assert reading is not None
    assert reading.peak_dbfs == pytest.approx(-18.063496)
    assert reading.rms_dbfs == pytest.approx(-24.492307)
    assert reading.clipped is False
    assert reading.status == classify_level(reading.rms_dbfs, reading.peak_dbfs)


def test_read_latest_live_level_uses_most_recent_pair(tmp_path):
    path = tmp_path / "levels.txt"
    path.write_text(
        "frame:0    pts:0\n"
        "lavfi.astats.1.Peak_level=-5.0\n"
        "lavfi.astats.1.RMS_level=-10.0\n"
        "frame:1    pts:4800\n"
        "lavfi.astats.1.Peak_level=-30.0\n"
        "lavfi.astats.1.RMS_level=-40.0\n",
        encoding="utf-8",
    )

    reading = read_latest_live_level(path)

    assert reading.peak_dbfs == pytest.approx(-30.0)
    assert reading.rms_dbfs == pytest.approx(-40.0)


def test_read_latest_live_level_detects_clipping(tmp_path):
    path = tmp_path / "levels.txt"
    path.write_text(
        f"lavfi.astats.1.Peak_level={CLIP_PEAK_DBFS}\nlavfi.astats.1.RMS_level=-10.0\n",
        encoding="utf-8",
    )

    reading = read_latest_live_level(path)

    assert reading.clipped is True


def test_read_latest_live_level_handles_negative_infinity(tmp_path):
    # True silence: astats reports -inf, not a numeric floor value.
    path = tmp_path / "levels.txt"
    path.write_text(
        "lavfi.astats.1.Peak_level=-inf\nlavfi.astats.1.RMS_level=-inf\n",
        encoding="utf-8",
    )

    reading = read_latest_live_level(path)

    assert reading.peak_dbfs == float("-inf")
    assert reading.rms_dbfs == float("-inf")
    assert reading.clipped is False
