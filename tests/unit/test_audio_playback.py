import logging

from video_guestbook.media import audio_playback as audio_playback_module
from video_guestbook.media.audio_playback import play_sound_async


class _FakePopen:
    def __init__(self, command, **kwargs):
        self.command = command
        self.kwargs = kwargs


def test_missing_file_does_not_call_popen(tmp_path, monkeypatch, caplog):
    calls = []
    monkeypatch.setattr(audio_playback_module.subprocess, "Popen", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(audio_playback_module.shutil, "which", lambda name: "/usr/bin/aplay")

    with caplog.at_level(logging.WARNING):
        play_sound_async(tmp_path / "missing.wav")

    assert calls == []
    assert "not found" in caplog.text


def test_missing_aplay_does_not_raise(tmp_path, monkeypatch, caplog):
    sound = tmp_path / "pickup.wav"
    sound.write_bytes(b"fake wav data")

    calls = []
    monkeypatch.setattr(audio_playback_module.subprocess, "Popen", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(audio_playback_module.shutil, "which", lambda name: None)

    with caplog.at_level(logging.WARNING):
        play_sound_async(sound)

    assert calls == []
    assert "aplay not found" in caplog.text


def test_plays_with_default_device(tmp_path, monkeypatch):
    sound = tmp_path / "pickup.wav"
    sound.write_bytes(b"fake wav data")

    captured = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        return _FakePopen(command, **kwargs)

    monkeypatch.setattr(audio_playback_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(audio_playback_module.shutil, "which", lambda name: "/usr/bin/aplay")

    play_sound_async(sound)

    assert captured["command"] == ["/usr/bin/aplay", "-q", str(sound)]


def test_plays_with_explicit_device(tmp_path, monkeypatch):
    sound = tmp_path / "pickup.wav"
    sound.write_bytes(b"fake wav data")

    captured = {}
    monkeypatch.setattr(
        audio_playback_module.subprocess, "Popen",
        lambda command, **kwargs: captured.setdefault("command", command),
    )
    monkeypatch.setattr(audio_playback_module.shutil, "which", lambda name: "/usr/bin/aplay")

    play_sound_async(sound, device="plughw:CARD=Device,DEV=0")

    assert captured["command"] == [
        "/usr/bin/aplay", "-q", "-D", "plughw:CARD=Device,DEV=0", str(sound)
    ]


def test_popen_oserror_does_not_raise(tmp_path, monkeypatch, caplog):
    sound = tmp_path / "pickup.wav"
    sound.write_bytes(b"fake wav data")

    def raise_oserror(*a, **k):
        raise OSError("no such device")

    monkeypatch.setattr(audio_playback_module.subprocess, "Popen", raise_oserror)
    monkeypatch.setattr(audio_playback_module.shutil, "which", lambda name: "/usr/bin/aplay")

    with caplog.at_level(logging.WARNING):
        play_sound_async(sound)  # must not raise

    assert "failed to start playback" in caplog.text
