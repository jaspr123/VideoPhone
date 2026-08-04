from pathlib import Path

import pytest

from video_guestbook.config import BoothConfig
from video_guestbook.media import ffmpeg as ffmpeg_module


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


@pytest.fixture(autouse=True)
def _stub_ffmpeg_binary(monkeypatch):
    # These tests only check argument construction, not that ffmpeg is
    # actually installed on this machine (rule 18).
    monkeypatch.setattr(ffmpeg_module, "find_ffmpeg", lambda: "ffmpeg")


def test_record_command_includes_proven_av_sync_flags(tmp_path):
    """Regression guard for the flags confirmed working on the real hardware.

    These match legacy/booth.py, the prototype that has been proven not to
    drift audio/video sync on the actual Raspberry Pi + USB webcam + USB mic
    setup. Do not remove them without re-testing sync on real hardware.
    """
    config = make_config(tmp_path)
    output_path = tmp_path / "recordings" / "session.mp4"

    command = ffmpeg_module.build_record_command(config, output_path)

    assert command.count("-thread_queue_size") == 2
    assert command.count("-use_wallclock_as_timestamps") == 2
    assert "-af" in command
    assert command[command.index("-af") + 1] == "aresample=async=1:first_pts=0"
    assert "-avoid_negative_ts" in command
    assert command[command.index("-avoid_negative_ts") + 1] == "make_zero"
    assert "-movflags" in command
    assert command[command.index("-movflags") + 1] == "+faststart"


def test_record_command_maps_video_and_audio_streams(tmp_path):
    config = make_config(tmp_path)
    output_path = tmp_path / "recordings" / "session.mp4"

    command = ffmpeg_module.build_record_command(config, output_path)

    assert "-map" in command
    map_indices = [i for i, arg in enumerate(command) if arg == "-map"]
    mapped = {command[i + 1] for i in map_indices}
    assert mapped == {"0:v:0", "1:a:0"}


def test_record_command_copies_video_and_encodes_aac(tmp_path):
    config = make_config(tmp_path)
    output_path = tmp_path / "recordings" / "session.mp4"

    command = ffmpeg_module.build_record_command(config, output_path)

    assert command[command.index("-c:v") + 1] == "copy"
    assert command[command.index("-c:a") + 1] == "aac"
    assert command[command.index("-b:a") + 1] == config.audio_bitrate
    assert command[command.index("-ar") + 1] == str(config.audio_sample_rate)
    assert command[command.index("-ac") + 1] == str(config.audio_channels)


def test_record_command_uses_configured_devices_and_output_path(tmp_path):
    config = make_config(tmp_path)
    output_path = tmp_path / "recordings" / "session.mp4"

    command = ffmpeg_module.build_record_command(config, output_path)

    assert config.camera_device in command
    assert config.audio_device in command
    assert command[-1] == str(output_path)


def test_zero_sync_offset_adds_no_itsoffset(tmp_path):
    config = make_config(tmp_path, av_sync_offset_ms=0)
    output_path = tmp_path / "recordings" / "session.mp4"

    command = ffmpeg_module.build_record_command(config, output_path)

    assert "-itsoffset" not in command


def test_positive_sync_offset_delays_video_input(tmp_path):
    """video ahead of audio -> delay video (PROJECT_SPEC.md risk #7)."""
    config = make_config(tmp_path, av_sync_offset_ms=150)
    output_path = tmp_path / "recordings" / "session.mp4"

    command = ffmpeg_module.build_record_command(config, output_path)

    assert command.count("-itsoffset") == 1
    # -itsoffset must be the first flag of the video input block, i.e.
    # immediately after ffmpeg -y, and precede the video -i.
    assert command[2] == "-itsoffset"
    assert command[3] == "0.150"
    itsoffset_index = command.index("-itsoffset")
    camera_device_index = command.index(config.camera_device)
    audio_device_index = command.index(config.audio_device)
    assert itsoffset_index < camera_device_index < audio_device_index


def test_negative_sync_offset_delays_audio_input(tmp_path):
    """audio ahead of video -> delay audio (PROJECT_SPEC.md risk #7)."""
    config = make_config(tmp_path, av_sync_offset_ms=-200)
    output_path = tmp_path / "recordings" / "session.mp4"

    command = ffmpeg_module.build_record_command(config, output_path)

    assert command.count("-itsoffset") == 1
    camera_device_index = command.index(config.camera_device)
    audio_device_index = command.index(config.audio_device)
    itsoffset_index = command.index("-itsoffset")
    # -itsoffset must sit in the audio input block: after the video -i, and
    # before the audio -i.
    assert camera_device_index < itsoffset_index < audio_device_index
    assert command[itsoffset_index + 1] == "0.200"
