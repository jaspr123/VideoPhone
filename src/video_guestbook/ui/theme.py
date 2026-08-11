"""Theme loading for the guest-facing UI (PROJECT_SPEC.md section 11).

A theme is a directory containing theme.json plus font and image assets.
Colors, fonts, copy and border art are data, never hard-coded into the
renderer -- swapping to a different theme directory in config is enough
to rebrand the booth for a new event.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_HEX_COLOR_RE = re.compile(r"^#([0-9a-fA-F]{6})$")

REQUIRED_COLOR_KEYS = (
    "bg_cream",
    "ink",
    "gold",
    "gold_dark",
    "dark_bar",
    "recording_red",
    "meter_green",
    "meter_gold",
    "meter_amber",
    "meter_red",
)
REQUIRED_FONT_KEYS = ("script", "heading", "heading_medium", "body")
REQUIRED_TEXT_KEYS = (
    "welcome_eyebrow",
    "welcome_title",
    "ready_prompt_1",
    "ready_prompt_2",
    "ready_footer",
    "get_ready_title",
    "tip_camera",
    "tip_speak",
    "tip_smile",
    "countdown_caption",
    "countdown_go",
    "countdown_footer",
    "recording_label",
    "recording_footer",
    "cam_placeholder",
    "cam_note_frozen",
    "mic_label",
    "mic_sublabel",
    "saving_title",
    "saving_sub",
    "saving_footer",
    "thanks_title",
    "thanks_sub",
    "thanks_sub2",
    "thanks_close",
    "thanks_footer",
    "error_title",
    "error_footer",
)


class ThemeError(ValueError):
    """Raised when a theme directory or theme.json is missing or invalid."""


def _hex_to_rgb(value: object, field_name: str) -> tuple[int, int, int]:
    match = _HEX_COLOR_RE.match(value.strip()) if isinstance(value, str) else None
    if not match:
        raise ThemeError(f"{field_name} must be a '#RRGGBB' color, got {value!r}")
    hex_digits = match.group(1)
    r, g, b = (int(hex_digits[i : i + 2], 16) for i in (0, 2, 4))
    return (r, g, b)


@dataclass(frozen=True)
class Theme:
    name: str
    couple_names: str
    event_date: str
    show_couple_photo: bool
    colors: dict[str, tuple[int, int, int]]
    font_paths: dict[str, Path]
    frame_path: Path
    couple_photo_path: Path | None
    text: dict[str, str]
    # Optional overrides -- absent keys fall back to built-in behavior
    # (no sound played; procedural vector icon drawn). Unlike the required
    # sections above, these are not validated against a fixed key set:
    # any key the caller looks up (e.g. sounds["pickup"], icons["camera"])
    # is either present and file-checked, or simply not there.
    sounds: dict[str, Path]
    icon_paths: dict[str, Path]

    @classmethod
    def load(cls, theme_dir: Path) -> "Theme":
        theme_dir = Path(theme_dir)
        theme_json_path = theme_dir / "theme.json"
        try:
            raw_text = theme_json_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ThemeError(f"Could not read theme file {theme_json_path}: {exc}") from exc

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ThemeError(f"Theme file {theme_json_path} is not valid JSON: {exc}") from exc

        if not isinstance(data, dict):
            raise ThemeError(f"Theme file {theme_json_path} must contain a JSON object")

        name = str(data.get("name", "")).strip()
        if not name:
            raise ThemeError("theme.json 'name' must not be empty")

        couple_names = str(data.get("couple_names", "")).strip()
        event_date = str(data.get("event_date", "")).strip()

        show_couple_photo = data.get("show_couple_photo", True)
        if not isinstance(show_couple_photo, bool):
            raise ThemeError(f"show_couple_photo must be a boolean, got {show_couple_photo!r}")

        raw_colors = data.get("colors", {})
        if not isinstance(raw_colors, dict):
            raise ThemeError("theme.json 'colors' must be an object")
        missing_colors = [k for k in REQUIRED_COLOR_KEYS if k not in raw_colors]
        if missing_colors:
            raise ThemeError(f"theme.json 'colors' missing keys: {', '.join(sorted(missing_colors))}")
        colors = {k: _hex_to_rgb(raw_colors[k], f"colors.{k}") for k in REQUIRED_COLOR_KEYS}

        raw_fonts = data.get("fonts", {})
        if not isinstance(raw_fonts, dict):
            raise ThemeError("theme.json 'fonts' must be an object")
        missing_fonts = [k for k in REQUIRED_FONT_KEYS if k not in raw_fonts]
        if missing_fonts:
            raise ThemeError(f"theme.json 'fonts' missing keys: {', '.join(sorted(missing_fonts))}")
        font_paths: dict[str, Path] = {}
        for key in REQUIRED_FONT_KEYS:
            path = (theme_dir / str(raw_fonts[key])).resolve()
            if not path.is_file():
                raise ThemeError(f"theme.json fonts.{key} points at a missing file: {path}")
            font_paths[key] = path

        raw_assets = data.get("assets", {})
        if not isinstance(raw_assets, dict):
            raise ThemeError("theme.json 'assets' must be an object")
        if "frame" not in raw_assets:
            raise ThemeError("theme.json 'assets' missing key: frame")
        frame_path = (theme_dir / str(raw_assets["frame"])).resolve()
        if not frame_path.is_file():
            raise ThemeError(f"theme.json assets.frame points at a missing file: {frame_path}")

        couple_photo_path: Path | None = None
        if show_couple_photo:
            if "couple_photo" not in raw_assets:
                raise ThemeError(
                    "theme.json 'assets' missing key: couple_photo "
                    "(required when show_couple_photo is true)"
                )
            couple_photo_path = (theme_dir / str(raw_assets["couple_photo"])).resolve()
            if not couple_photo_path.is_file():
                raise ThemeError(
                    f"theme.json assets.couple_photo points at a missing file: {couple_photo_path}"
                )

        raw_text_map = data.get("text", {})
        if not isinstance(raw_text_map, dict):
            raise ThemeError("theme.json 'text' must be an object")
        missing_text = [k for k in REQUIRED_TEXT_KEYS if k not in raw_text_map]
        if missing_text:
            raise ThemeError(f"theme.json 'text' missing keys: {', '.join(sorted(missing_text))}")
        text = {k: str(raw_text_map[k]) for k in REQUIRED_TEXT_KEYS}

        sounds = _resolve_optional_file_map(data.get("sounds", {}), theme_dir, "sounds")
        icon_paths = _resolve_optional_file_map(data.get("icons", {}), theme_dir, "icons")

        return cls(
            name=name,
            couple_names=couple_names,
            event_date=event_date,
            show_couple_photo=show_couple_photo,
            colors=colors,
            font_paths=font_paths,
            frame_path=frame_path,
            couple_photo_path=couple_photo_path,
            text=text,
            sounds=sounds,
            icon_paths=icon_paths,
        )


def _resolve_optional_file_map(raw: object, theme_dir: Path, field_name: str) -> dict[str, Path]:
    """Resolve an optional {key: relative_path} theme.json section.

    Every key present must point at a real file (typos should fail loudly
    at startup), but the section itself -- and any individual key -- may be
    omitted entirely to fall back to built-in behavior.
    """
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise ThemeError(f"theme.json {field_name!r} must be an object")
    resolved: dict[str, Path] = {}
    for key, rel_path in raw.items():
        path = (theme_dir / str(rel_path)).resolve()
        if not path.is_file():
            raise ThemeError(f"theme.json {field_name}.{key} points at a missing file: {path}")
        resolved[key] = path
    return resolved
