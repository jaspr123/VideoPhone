from pathlib import Path

import pytest

from video_guestbook.config import BoothConfig, ConfigError

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "booth.default.json"


def valid_config_dict(**overrides) -> dict:
    base = {
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
    base.update(overrides)
    return base


def test_valid_config_loads(tmp_path):
    config = BoothConfig.from_dict(valid_config_dict(), base_dir=tmp_path)
    assert config.camera_device == "/dev/video0"
    assert config.record_fps == 30
    assert config.audio_device == "plughw:CARD=Device,DEV=0"
    assert config.output_dir == (tmp_path / "recordings").resolve()
    assert config.log_dir == (tmp_path / "logs").resolve()
    assert config.record_width_height == (1280, 720)
    assert config.preview_width_height == (1024, 576)


def test_default_config_file_is_valid():
    config = BoothConfig.from_file(DEFAULT_CONFIG_PATH, base_dir=REPO_ROOT)
    assert config.record_resolution == "1280x720"
    assert config.record_fps == 30
    assert config.audio_device == "plughw:CARD=Device,DEV=0"
    assert config.max_recording_seconds == 90


def test_missing_key_raises():
    data = valid_config_dict()
    del data["audio_device"]
    with pytest.raises(ConfigError, match="Missing required config keys"):
        BoothConfig.from_dict(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("record_resolution", "1280"),
        ("record_resolution", "1280x"),
        ("record_resolution", "abcxdef"),
        ("preview_resolution", "0x0"),
    ],
)
def test_bad_resolution_raises(field, value):
    with pytest.raises(ConfigError):
        BoothConfig.from_dict(valid_config_dict(**{field: value}))


@pytest.mark.parametrize("fps", [0, -1, 61, 30.5, "30"])
def test_bad_record_fps_raises(fps):
    with pytest.raises(ConfigError):
        BoothConfig.from_dict(valid_config_dict(record_fps=fps))


def test_bad_input_format_raises():
    with pytest.raises(ConfigError):
        BoothConfig.from_dict(valid_config_dict(record_input_format="xyz"))


@pytest.mark.parametrize("channels", [0, 3, "1"])
def test_bad_audio_channels_raises(channels):
    with pytest.raises(ConfigError):
        BoothConfig.from_dict(valid_config_dict(audio_channels=channels))


@pytest.mark.parametrize("bitrate", ["160kbps", "160", "k160", ""])
def test_bad_audio_bitrate_raises(bitrate):
    with pytest.raises(ConfigError):
        BoothConfig.from_dict(valid_config_dict(audio_bitrate=bitrate))


@pytest.mark.parametrize("seconds", [-1, "0"])
def test_bad_countdown_seconds_raises(seconds):
    with pytest.raises(ConfigError):
        BoothConfig.from_dict(valid_config_dict(countdown_seconds=seconds))


@pytest.mark.parametrize("seconds", [0, -5, "90"])
def test_bad_max_recording_seconds_raises(seconds):
    with pytest.raises(ConfigError):
        BoothConfig.from_dict(valid_config_dict(max_recording_seconds=seconds))


def test_empty_camera_device_raises():
    with pytest.raises(ConfigError):
        BoothConfig.from_dict(valid_config_dict(camera_device="  "))


def test_missing_file_raises():
    with pytest.raises(ConfigError, match="Could not read config file"):
        BoothConfig.from_file("/nonexistent/path/booth.json")


def test_invalid_json_raises(tmp_path):
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        BoothConfig.from_file(bad_file)


def test_json_array_raises(tmp_path):
    bad_file = tmp_path / "array.json"
    bad_file.write_text("[]", encoding="utf-8")
    with pytest.raises(ConfigError, match="must contain a JSON object"):
        BoothConfig.from_file(bad_file)
