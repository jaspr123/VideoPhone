import json
import subprocess

import pytest

from video_guestbook.media import validation as validation_module
from video_guestbook.media.validation import validate_recording


@pytest.fixture(autouse=True)
def _stub_probe_command(monkeypatch):
    # Keep these tests independent of whether ffprobe is actually installed
    # on this machine (rule 18: testable without physical hardware) -- the
    # hardware test script is responsible for confirming ffprobe exists.
    monkeypatch.setattr(
        validation_module,
        "build_probe_command",
        lambda media_path: ["ffprobe", "-v", "error", str(media_path)],
    )


def _make_completed_process(stdout: str, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["ffprobe"], returncode=returncode, stdout=stdout, stderr=stderr)


def _probe_json(*, has_video=True, has_audio=True, duration=5.0) -> str:
    streams = []
    if has_video:
        streams.append({"codec_type": "video", "codec_name": "h264"})
    if has_audio:
        streams.append({"codec_type": "audio", "codec_name": "aac"})
    return json.dumps({"streams": streams, "format": {"duration": str(duration)}})


def _write_fake_recording(tmp_path, size_bytes: int = 1024):
    path = tmp_path / "recording.mp4"
    path.write_bytes(b"\x00" * size_bytes)
    return path


def test_valid_recording_passes(tmp_path, monkeypatch):
    path = _write_fake_recording(tmp_path)
    monkeypatch.setattr(
        validation_module.subprocess,
        "run",
        lambda *a, **k: _make_completed_process(_probe_json()),
    )

    result = validate_recording(path)

    assert result.ok is True
    assert result.has_video is True
    assert result.has_audio is True
    assert result.duration_seconds == pytest.approx(5.0)
    assert result.size_bytes == 1024


def test_missing_file_fails_without_calling_ffprobe(tmp_path, monkeypatch):
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        return _make_completed_process(_probe_json())

    monkeypatch.setattr(validation_module.subprocess, "run", fake_run)

    result = validate_recording(tmp_path / "does-not-exist.mp4")

    assert result.ok is False
    assert "does not exist" in result.reason
    assert called is False


def test_empty_file_fails_without_calling_ffprobe(tmp_path, monkeypatch):
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        return _make_completed_process(_probe_json())

    monkeypatch.setattr(validation_module.subprocess, "run", fake_run)

    path = _write_fake_recording(tmp_path, size_bytes=0)
    result = validate_recording(path)

    assert result.ok is False
    assert "empty" in result.reason
    assert called is False


def test_missing_video_stream_fails(tmp_path, monkeypatch):
    path = _write_fake_recording(tmp_path)
    monkeypatch.setattr(
        validation_module.subprocess,
        "run",
        lambda *a, **k: _make_completed_process(_probe_json(has_video=False)),
    )

    result = validate_recording(path)

    assert result.ok is False
    assert "video" in result.reason


def test_missing_audio_stream_fails(tmp_path, monkeypatch):
    path = _write_fake_recording(tmp_path)
    monkeypatch.setattr(
        validation_module.subprocess,
        "run",
        lambda *a, **k: _make_completed_process(_probe_json(has_audio=False)),
    )

    result = validate_recording(path)

    assert result.ok is False
    assert "audio" in result.reason


def test_duration_too_short_fails(tmp_path, monkeypatch):
    path = _write_fake_recording(tmp_path)
    monkeypatch.setattr(
        validation_module.subprocess,
        "run",
        lambda *a, **k: _make_completed_process(_probe_json(duration=0.1)),
    )

    result = validate_recording(path, min_duration_seconds=0.5)

    assert result.ok is False
    assert "duration" in result.reason


def test_ffprobe_nonzero_exit_fails(tmp_path, monkeypatch):
    path = _write_fake_recording(tmp_path)
    monkeypatch.setattr(
        validation_module.subprocess,
        "run",
        lambda *a, **k: _make_completed_process("", returncode=1, stderr="boom"),
    )

    result = validate_recording(path)

    assert result.ok is False
    assert "ffprobe failed" in result.reason


def test_invalid_json_output_fails(tmp_path, monkeypatch):
    path = _write_fake_recording(tmp_path)
    monkeypatch.setattr(
        validation_module.subprocess,
        "run",
        lambda *a, **k: _make_completed_process("not json"),
    )

    result = validate_recording(path)

    assert result.ok is False
    assert "could not parse" in result.reason
