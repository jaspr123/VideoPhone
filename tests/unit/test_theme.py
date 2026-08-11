import json
from pathlib import Path

import pytest

from video_guestbook.ui.theme import (
    REQUIRED_COLOR_KEYS,
    REQUIRED_FONT_KEYS,
    REQUIRED_TEXT_KEYS,
    Theme,
    ThemeError,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REAL_THEME_DIR = PROJECT_ROOT / "themes" / "classic-walnut"


def _valid_theme_data() -> dict:
    return {
        "name": "Test Theme",
        "couple_names": "A & B",
        "event_date": "1 January 2030",
        "show_couple_photo": True,
        "colors": {k: "#112233" for k in REQUIRED_COLOR_KEYS},
        "fonts": {k: f"fonts/{k}.ttf" for k in REQUIRED_FONT_KEYS},
        "assets": {"frame": "assets/frame.png", "couple_photo": "assets/photo.jpg"},
        "text": {k: f"text for {k}" for k in REQUIRED_TEXT_KEYS},
    }


def _write_theme(tmp_path: Path, data: dict) -> Path:
    (tmp_path / "theme.json").write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / "fonts").mkdir(exist_ok=True)
    for key in REQUIRED_FONT_KEYS:
        (tmp_path / "fonts" / f"{key}.ttf").touch()
    (tmp_path / "assets").mkdir(exist_ok=True)
    (tmp_path / "assets" / "frame.png").touch()
    (tmp_path / "assets" / "photo.jpg").touch()
    return tmp_path


def test_load_valid_theme(tmp_path):
    _write_theme(tmp_path, _valid_theme_data())
    theme = Theme.load(tmp_path)
    assert theme.name == "Test Theme"
    assert theme.colors["gold"] == (0x11, 0x22, 0x33)
    assert theme.couple_photo_path is not None
    assert theme.couple_photo_path.is_file()


def test_load_missing_theme_json_raises(tmp_path):
    with pytest.raises(ThemeError, match="Could not read"):
        Theme.load(tmp_path)


def test_load_invalid_json_raises(tmp_path):
    (tmp_path / "theme.json").write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ThemeError, match="not valid JSON"):
        Theme.load(tmp_path)


def test_load_non_object_json_raises(tmp_path):
    (tmp_path / "theme.json").write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ThemeError, match="JSON object"):
        Theme.load(tmp_path)


def test_missing_name_raises(tmp_path):
    data = _valid_theme_data()
    data["name"] = ""
    _write_theme(tmp_path, data)
    with pytest.raises(ThemeError, match="name"):
        Theme.load(tmp_path)


def test_missing_color_key_raises(tmp_path):
    data = _valid_theme_data()
    del data["colors"]["gold"]
    _write_theme(tmp_path, data)
    with pytest.raises(ThemeError, match="colors"):
        Theme.load(tmp_path)


def test_invalid_color_format_raises(tmp_path):
    data = _valid_theme_data()
    data["colors"]["gold"] = "not-a-color"
    _write_theme(tmp_path, data)
    with pytest.raises(ThemeError, match="color"):
        Theme.load(tmp_path)


def test_missing_font_key_raises(tmp_path):
    data = _valid_theme_data()
    del data["fonts"]["script"]
    _write_theme(tmp_path, data)
    with pytest.raises(ThemeError, match="fonts"):
        Theme.load(tmp_path)


def test_missing_font_file_raises(tmp_path):
    data = _valid_theme_data()
    _write_theme(tmp_path, data)
    (tmp_path / "fonts" / "script.ttf").unlink()
    with pytest.raises(ThemeError, match="fonts.script"):
        Theme.load(tmp_path)


def test_missing_frame_asset_raises(tmp_path):
    data = _valid_theme_data()
    _write_theme(tmp_path, data)
    (tmp_path / "assets" / "frame.png").unlink()
    with pytest.raises(ThemeError, match="frame"):
        Theme.load(tmp_path)


def test_couple_photo_not_required_when_disabled(tmp_path):
    data = _valid_theme_data()
    data["show_couple_photo"] = False
    del data["assets"]["couple_photo"]
    _write_theme(tmp_path, data)
    theme = Theme.load(tmp_path)
    assert theme.couple_photo_path is None


def test_couple_photo_required_when_enabled(tmp_path):
    data = _valid_theme_data()
    del data["assets"]["couple_photo"]
    _write_theme(tmp_path, data)
    with pytest.raises(ThemeError, match="couple_photo"):
        Theme.load(tmp_path)


def test_missing_text_key_raises(tmp_path):
    data = _valid_theme_data()
    del data["text"]["welcome_title"]
    _write_theme(tmp_path, data)
    with pytest.raises(ThemeError, match="text"):
        Theme.load(tmp_path)


def test_show_couple_photo_must_be_bool(tmp_path):
    data = _valid_theme_data()
    data["show_couple_photo"] = "yes"
    _write_theme(tmp_path, data)
    with pytest.raises(ThemeError, match="show_couple_photo"):
        Theme.load(tmp_path)


def test_sounds_and_icons_default_to_empty_when_absent(tmp_path):
    _write_theme(tmp_path, _valid_theme_data())
    theme = Theme.load(tmp_path)
    assert theme.sounds == {}
    assert theme.icon_paths == {}


def test_sounds_resolved_when_present(tmp_path):
    data = _valid_theme_data()
    _write_theme(tmp_path, data)
    (tmp_path / "sounds").mkdir()
    (tmp_path / "sounds" / "pickup.wav").touch()
    data["sounds"] = {"pickup": "sounds/pickup.wav"}
    (tmp_path / "theme.json").write_text(json.dumps(data), encoding="utf-8")

    theme = Theme.load(tmp_path)
    assert theme.sounds["pickup"] == (tmp_path / "sounds" / "pickup.wav").resolve()


def test_sounds_missing_file_raises(tmp_path):
    data = _valid_theme_data()
    data["sounds"] = {"pickup": "sounds/does-not-exist.wav"}
    _write_theme(tmp_path, data)
    with pytest.raises(ThemeError, match="sounds.pickup"):
        Theme.load(tmp_path)


def test_icons_resolved_when_present(tmp_path):
    data = _valid_theme_data()
    _write_theme(tmp_path, data)
    (tmp_path / "assets" / "icon-camera.png").touch()
    data["icons"] = {"camera": "assets/icon-camera.png"}
    (tmp_path / "theme.json").write_text(json.dumps(data), encoding="utf-8")

    theme = Theme.load(tmp_path)
    assert theme.icon_paths["camera"] == (tmp_path / "assets" / "icon-camera.png").resolve()


def test_icons_missing_file_raises(tmp_path):
    data = _valid_theme_data()
    data["icons"] = {"camera": "assets/does-not-exist.png"}
    _write_theme(tmp_path, data)
    with pytest.raises(ThemeError, match="icons.camera"):
        Theme.load(tmp_path)


def test_sounds_must_be_object(tmp_path):
    data = _valid_theme_data()
    data["sounds"] = "not-an-object"
    _write_theme(tmp_path, data)
    with pytest.raises(ThemeError, match="sounds"):
        Theme.load(tmp_path)


def test_real_bundled_theme_loads():
    """Regression guard: the shipped default theme must always be valid."""
    theme = Theme.load(REAL_THEME_DIR)
    assert theme.name
    assert all(k in theme.colors for k in REQUIRED_COLOR_KEYS)
    assert all(theme.font_paths[k].is_file() for k in REQUIRED_FONT_KEYS)
    assert theme.frame_path.is_file()
