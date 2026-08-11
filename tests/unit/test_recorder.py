import pytest

from video_guestbook.config import BoothConfig
from video_guestbook.media import ffmpeg as ffmpeg_module
from video_guestbook.media import recorder as recorder_module
from video_guestbook.media.recorder import Recorder, RecorderError


def make_config(tmp_path, **overrides) -> BoothConfig:
    data = {
        "camera_device": "/dev/video0",
        "record_resolution": "1280x720",
        "record_fps": 30,
        "record_input_format": "h264",
        "audio_device": "plughw:CARD=Device,DEV=0",
        "audio_sample_rate": 48000,
        "audio_channels": 1,
        "audio_bitrate": "160k",
        "countdown_seconds": 3,
        "max_recording_seconds": 90,
        "output_dir": "recordings",
        "log_dir": "logs",
        "preview_resolution": "1024x576",
        "preview_fps": 20,
    }
    data.update(overrides)
    return BoothConfig.from_dict(data, base_dir=tmp_path)


class _FakeStdin:
    def __init__(self):
        self.written = []
        self.closed = False

    def write(self, data):
        self.written.append(data)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class _FakeStderr:
    def read(self):
        return ""


class _FakeProcess:
    def __init__(self):
        self.stdin = _FakeStdin()
        self.stderr = _FakeStderr()
        self.returncode = 0
        self._exited = False

    def poll(self):
        return 0 if self._exited else None

    def wait(self, timeout=None):
        self._exited = True

    def terminate(self):
        self._exited = True

    def kill(self):
        self._exited = True


@pytest.fixture(autouse=True)
def _stub_ffmpeg_and_popen(monkeypatch):
    monkeypatch.setattr(ffmpeg_module, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(recorder_module.subprocess, "Popen", lambda *a, **k: _FakeProcess())


def test_h264_session_uses_final_path_directly_no_transcode(tmp_path):
    config = make_config(tmp_path, record_input_format="h264")
    session = Recorder(config).start()

    assert session.needs_transcode is False
    assert session.live_output_path == session.final_output_path
    assert session.live_output_path.suffix == ".mp4"


def test_mjpeg_fast_mode_uses_final_path_directly_no_transcode(tmp_path):
    config = make_config(tmp_path, record_input_format="mjpeg", recording_mode="fast")
    session = Recorder(config).start()

    assert session.needs_transcode is False
    assert session.live_output_path == session.final_output_path
    assert session.live_output_path.suffix == ".mp4"


def test_mjpeg_quality_mode_uses_separate_raw_path_needs_transcode(tmp_path):
    config = make_config(tmp_path, record_input_format="mjpeg", recording_mode="quality")
    session = Recorder(config).start()

    assert session.needs_transcode is True
    assert session.live_output_path != session.final_output_path
    assert session.live_output_path.name.endswith(".raw.mkv")
    assert session.final_output_path.name.endswith(".mp4")
    assert session.live_output_path.name.startswith(session.session_id)
    assert session.final_output_path.name.startswith(session.session_id)


def test_live_preview_disabled_by_default_leaves_path_none(tmp_path):
    config = make_config(tmp_path)
    session = Recorder(config).start()

    assert session.live_preview_path is None


def test_live_preview_enabled_sets_path_under_log_dir(tmp_path):
    config = make_config(tmp_path, live_preview_enabled=True)
    session = Recorder(config).start()

    assert session.live_preview_path == config.log_dir / "live_preview.jpg"


def test_live_preview_start_clears_stale_file_from_prior_session(tmp_path):
    config = make_config(tmp_path, live_preview_enabled=True)
    config.log_dir.mkdir(parents=True, exist_ok=True)
    stale_preview = config.log_dir / "live_preview.jpg"
    stale_preview.write_bytes(b"stale jpeg bytes")

    Recorder(config).start()

    assert not stale_preview.exists()


def test_start_raises_if_already_running(tmp_path):
    config = make_config(tmp_path)
    recorder = Recorder(config)
    recorder.start()
    with pytest.raises(RecorderError, match="already in progress"):
        recorder.start()


def test_stop_returns_full_session_and_clears_state(tmp_path):
    config = make_config(tmp_path, record_input_format="mjpeg", recording_mode="quality")
    recorder = Recorder(config)
    started = recorder.start()

    stopped = recorder.stop()

    assert stopped.session_id == started.session_id
    assert stopped.needs_transcode is True
    assert stopped.live_output_path == started.live_output_path
    assert recorder.session is None
    assert recorder.is_running is False


def test_stop_without_start_raises(tmp_path):
    config = make_config(tmp_path)
    with pytest.raises(RecorderError, match="No recording in progress"):
        Recorder(config).stop()


def test_stop_writes_q_to_stdin(tmp_path):
    config = make_config(tmp_path)
    recorder = Recorder(config)
    recorder.start()
    fake_process = recorder._process  # inspect the fake we injected

    recorder.stop()

    assert "q\n" in fake_process.stdin.written
