import subprocess

import pytest

from video_guestbook.config import BoothConfig
from video_guestbook.media import ffmpeg as ffmpeg_module
from video_guestbook.media import transcode as transcode_module
from video_guestbook.media.transcode import TranscodeJob, TranscodeQueue
from video_guestbook.media.validation import ValidationResult


def make_config(tmp_path, **overrides) -> BoothConfig:
    data = {
        "camera_device": "/dev/video0",
        "record_resolution": "1280x720",
        "record_fps": 30,
        "record_input_format": "mjpeg",
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
        "recording_mode": "quality",
    }
    data.update(overrides)
    return BoothConfig.from_dict(data, base_dir=tmp_path)


def _completed(returncode=0, stderr="") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout="", stderr=stderr)


def _ok_validation(**overrides) -> ValidationResult:
    base = dict(ok=True, reason="ok", size_bytes=1000, duration_seconds=5.0, has_video=True, has_audio=True)
    base.update(overrides)
    return ValidationResult(**base)


@pytest.fixture(autouse=True)
def _stub_ffmpeg(monkeypatch):
    monkeypatch.setattr(ffmpeg_module, "find_ffmpeg", lambda: "ffmpeg")


def make_job(tmp_path, session_id="20260101_000000_abcd1234") -> TranscodeJob:
    return TranscodeJob(
        session_id=session_id,
        raw_path=tmp_path / f"{session_id}.raw.mkv",
        output_path=tmp_path / f"{session_id}.mp4",
    )


def test_start_and_stop_toggle_is_running(tmp_path):
    q = TranscodeQueue(make_config(tmp_path))
    assert q.is_running is False
    q.start()
    assert q.is_running is True
    q.stop()
    assert q.is_running is False


def test_stop_without_start_is_a_noop(tmp_path):
    q = TranscodeQueue(make_config(tmp_path))
    q.stop()  # must not raise


def test_pending_count_before_start_and_after_full_drain(tmp_path, monkeypatch):
    monkeypatch.setattr(transcode_module.subprocess, "run", lambda *a, **k: _completed())
    monkeypatch.setattr(transcode_module, "validate_recording", lambda p: _ok_validation())

    q = TranscodeQueue(make_config(tmp_path))
    q.enqueue(make_job(tmp_path, "session-1"))
    q.enqueue(make_job(tmp_path, "session-2"))
    assert q.pending_count == 2  # nothing processed yet, worker not started

    q.start()
    q.stop()  # FIFO: both queued jobs drain before the stop sentinel
    assert q.pending_count == 0


def test_enqueue_processes_job_and_validates_output(tmp_path, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return _completed()

    validated_paths = []

    def fake_validate(path):
        validated_paths.append(path)
        return _ok_validation(duration_seconds=12.3, size_bytes=555)

    monkeypatch.setattr(transcode_module.subprocess, "run", fake_run)
    monkeypatch.setattr(transcode_module, "validate_recording", fake_validate)

    config = make_config(tmp_path)
    q = TranscodeQueue(config)
    q.start()
    job = make_job(tmp_path)
    q.enqueue(job)
    q.stop()  # FIFO: drains the queued job before the stop sentinel

    assert len(calls) == 1
    assert str(job.raw_path) in calls[0]
    assert str(job.output_path) in calls[0]
    assert validated_paths == [job.output_path]


def test_job_with_nonzero_exit_does_not_raise(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(
        transcode_module.subprocess, "run", lambda *a, **k: _completed(returncode=1, stderr="boom")
    )
    validate_called = []
    monkeypatch.setattr(
        transcode_module, "validate_recording", lambda p: validate_called.append(p)
    )

    q = TranscodeQueue(make_config(tmp_path))
    q.start()
    q.enqueue(make_job(tmp_path))
    q.stop()  # must not raise or hang

    # A failed ffmpeg run must never even attempt to validate the (missing) output.
    assert validate_called == []


def test_job_with_failed_validation_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(transcode_module.subprocess, "run", lambda *a, **k: _completed())
    monkeypatch.setattr(
        transcode_module,
        "validate_recording",
        lambda p: _ok_validation(ok=False, reason="no audio stream found"),
    )

    q = TranscodeQueue(make_config(tmp_path))
    q.start()
    q.enqueue(make_job(tmp_path))
    q.stop()  # must not raise or hang


def test_job_with_subprocess_oserror_does_not_raise(tmp_path, monkeypatch):
    def raise_oserror(*a, **k):
        raise OSError("no such file")

    monkeypatch.setattr(transcode_module.subprocess, "run", raise_oserror)

    q = TranscodeQueue(make_config(tmp_path))
    q.start()
    q.enqueue(make_job(tmp_path))
    q.stop()  # must not raise or hang


def test_multiple_jobs_processed_in_order(tmp_path, monkeypatch):
    processed_order = []

    def fake_run(command, **kwargs):
        # session id is embedded in the raw/output filenames within argv
        for arg in command:
            if "session-" in arg:
                processed_order.append(arg)
                break
        return _completed()

    monkeypatch.setattr(transcode_module.subprocess, "run", fake_run)
    monkeypatch.setattr(transcode_module, "validate_recording", lambda p: _ok_validation())

    q = TranscodeQueue(make_config(tmp_path))
    q.start()
    q.enqueue(make_job(tmp_path, "session-1"))
    q.enqueue(make_job(tmp_path, "session-2"))
    q.enqueue(make_job(tmp_path, "session-3"))
    q.stop()

    assert len(processed_order) == 3
    assert "session-1" in processed_order[0]
    assert "session-3" in processed_order[2]
