"""Guest-facing screen rendering.

Composites each BoothState screen (background art, typography, animated
accents, live camera when relevant) as a Pillow RGBA image, then flattens
to a BGR numpy array for cv2.imshow. All colors, copy, fonts and border
art come from the active Theme (ui/theme.py) -- nothing here is
hard-coded per event.

This recreates the visual design of the "Guestbook Kiosk" mockup
(Claude Design handoff) within the existing OpenCV/ffmpeg pipeline rather
than adopting a browser runtime, so it reuses the camera/ffmpeg handoff
and CPU budget already proven on hardware this project. See CHANGELOG.md
for the fuller trade-off record.
"""

from __future__ import annotations

import math

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from video_guestbook.ui.theme import Theme

# Shared layout geometry for the wide camera panel + right-hand tips column
# + horizontal sound-check bar used by GET READY, COUNTDOWN and RECORDING
# (see _camera_panel/_tips_column/_sound_check_bar). Expressed as fractions
# of canvas width/height so they scale with preview_resolution, but derived
# from the design's 1024x576 mockup pixel values for an exact match at the
# default resolution.
_PANEL_X = 36 / 1024
_PANEL_Y = 90 / 576
_PANEL_W = 824 / 1024
_PANEL_H = 402 / 576
_TIPS_X = 872 / 1024
_TIPS_W = 116 / 1024
_SOUNDCHECK_Y = 444 / 576
_SOUNDCHECK_BAR_W = 440 / 1024

# WELCOME's fixed couple-photo panel and shared text center (the panel
# occupies the right of the canvas, everything else centers in the
# remaining left column rather than the full canvas width).
_WELCOME_CONTENT_CX = 374 / 1024
_WELCOME_PHOTO_X = 700 / 1024
_WELCOME_PHOTO_Y = 61 / 576
_WELCOME_PHOTO_W = 300 / 1024
_WELCOME_PHOTO_H = 408 / 576


def _rgba(color: tuple[int, int, int], alpha: int = 255) -> tuple[int, int, int, int]:
    return (color[0], color[1], color[2], alpha)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, t))


def _blend_rgb(bg: tuple[int, int, int], fg: tuple[int, int, int], fraction: float) -> tuple[int, int, int]:
    """Pre-blend fg over a known flat bg at `fraction` opacity.

    ImageDraw does not alpha-composite when drawing directly onto an image
    (it just stores the given alpha value verbatim), so a semi-transparent
    fill only reads as faded if we resolve the final opaque color ourselves
    before drawing. Used for "unlit"/pulsing accents over a known-flat local
    background; ambient/animated layers that need to blend against textured
    art instead go through a separate overlay + Image.alpha_composite.
    """
    fraction = max(0.0, min(1.0, fraction))
    return tuple(int(round(_lerp(bg[i], fg[i], fraction))) for i in range(3))  # type: ignore[return-value]


class Renderer:
    """Stateful per-run renderer: caches resized theme art and font handles."""

    def __init__(self, theme: Theme, width: int, height: int) -> None:
        self.theme = theme
        self.w = width
        self.h = height
        self._font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}
        self._bg = self._load_background()
        self._bg_dimmed = self._dim_background(self._bg, 0.28)
        self._couple_photo = self._load_couple_photo() if theme.show_couple_photo else None
        self._custom_icons = self._load_custom_icons()
        self._pop_state: tuple[object, float] | None = None
        # Updated on every render_preview() call to the on-screen "tap to
        # record" button's pixel rect in this renderer's own w x h space, so
        # main.py can hit-test raw mouse/touch callback coordinates against
        # it without duplicating this layout math. None until the first
        # PREVIEW frame is drawn.
        self.record_button_rect: tuple[int, int, int, int] | None = None
        # Same idea for RECORDING's "Cancel & restart" button.
        self.cancel_button_rect: tuple[int, int, int, int] | None = None
        # Named touch-target rects for SETTINGS/DEVELOPER, updated every
        # render_settings()/render_developer() call so main.py's touch
        # handler can hit-test admin controls by name without duplicating
        # this layout math (same pattern as record_button_rect above).
        self.settings_rects: dict[str, tuple[int, int, int, int]] = {}
        self.developer_rects: dict[str, tuple[int, int, int, int]] = {}

        # Per-screen static chrome, rendered once and reused. Key is the
        # screen name plus any value baked into it (e.g. the ERROR reason
        # string), so a changed value invalidates naturally. See
        # _static_layer(). Screens whose static share is small (PREVIEW,
        # RECORDING, COUNTDOWN -- dominated by the live/blurred camera
        # panel) are left out; the win there is marginal.
        self._static_cache: dict[tuple, Image.Image] = {}

    # ── one-time asset preparation ──────────────────────────────────
    def _load_background(self) -> Image.Image:
        art = Image.open(self.theme.frame_path).convert("RGB")
        return art.resize((self.w, self.h), Image.LANCZOS)

    def _dim_background(self, bg: Image.Image, opacity: float) -> Image.Image:
        cream = Image.new("RGB", bg.size, self.theme.colors["bg_cream"])
        return Image.blend(cream, bg, opacity)

    def _load_couple_photo(self) -> Image.Image | None:
        if self.theme.couple_photo_path is None:
            return None
        try:
            photo = Image.open(self.theme.couple_photo_path).convert("RGB")
        except OSError:
            return None
        box_w, box_h = int(self.w * _WELCOME_PHOTO_W), int(self.h * _WELCOME_PHOTO_H)
        return _cover_resize(photo, box_w, box_h)

    def _load_custom_icons(self) -> dict[str, Image.Image]:
        """Load any theme-provided tip icons (Theme.load already verified
        the files exist); a corrupt/unreadable file falls back to the
        built-in procedural icon rather than crashing the app."""
        icons: dict[str, Image.Image] = {}
        for key, path in self.theme.icon_paths.items():
            try:
                icons[key] = Image.open(path).convert("RGBA")
            except OSError:
                continue
        return icons

    def _font(self, key: str, size: int) -> ImageFont.FreeTypeFont:
        size = max(1, int(size))
        cache_key = (key, size)
        font = self._font_cache.get(cache_key)
        if font is None:
            font = ImageFont.truetype(str(self.theme.font_paths[key]), size)
            self._font_cache[cache_key] = font
        return font

    def _canvas(self, dimmed: bool = False) -> Image.Image:
        base = self._bg_dimmed if dimmed else self._bg
        return base.convert("RGBA").copy()

    def _static_layer(self, key: tuple, build, dimmed: bool = False) -> Image.Image:
        """Return a copy of the cached static chrome for `key`, building it once.

        `build(canvas, draw)` draws everything that does NOT change between
        frames on a screen; callers draw only animated elements onto the
        returned copy. The copy is required -- callers mutate it -- but
        copying a finished layer is far cheaper than re-running the layout
        and text shaping every frame (see CHANGELOG.md for the perf note
        this addresses: every render() used to redraw all static chrome
        from scratch, ~20x/second on Pi-class hardware).
        """
        layer = self._static_cache.get(key)
        if layer is None:
            layer = self._canvas(dimmed=dimmed)
            build(layer, ImageDraw.Draw(layer))
            self._static_cache[key] = layer
        return layer.copy()

    # ── shared drawing helpers ──────────────────────────────────────
    def _tracked_width(self, draw: ImageDraw.ImageDraw, text: str, font, spacing: float) -> float:
        if not text:
            return 0.0
        total = sum(draw.textlength(ch, font=font) for ch in text)
        return total + spacing * (len(text) - 1)

    def _draw_tracked(
        self,
        draw: ImageDraw.ImageDraw,
        cx: float,
        cy: float,
        text: str,
        font,
        fill,
        spacing: float = 3.0,
    ) -> None:
        total_w = self._tracked_width(draw, text, font, spacing)
        x = cx - total_w / 2
        for ch in text:
            draw.text((x, cy), ch, font=font, fill=fill, anchor="lm")
            x += draw.textlength(ch, font=font) + spacing

    def _draw_tracked_left(
        self,
        draw: ImageDraw.ImageDraw,
        x: float,
        cy: float,
        text: str,
        font,
        fill,
        spacing: float = 3.0,
    ) -> float:
        """Like _draw_tracked but anchored at its left edge; returns the end x."""
        for ch in text:
            draw.text((x, cy), ch, font=font, fill=fill, anchor="lm")
            x += draw.textlength(ch, font=font) + spacing
        return x

    def _draw_diamond(self, draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, fill) -> None:
        draw.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], fill=fill)

    def _blend_full(self, bg: tuple[int, int, int], fg: tuple[int, int, int], alpha_255: int):
        """Resolve a partial-alpha fg-over-bg color to an opaque RGBA tuple.

        See _blend_rgb: ImageDraw does not composite semi-transparent fills
        against existing pixels, so "faded" colors must be pre-blended
        against a known local background before drawing.
        """
        return _rgba(_blend_rgb(bg, fg, alpha_255 / 255.0), 255)

    def _pulse(self, now: float, period: float = 2.4, phase: float = 0.0, lo: float = 0.55, hi: float = 1.0) -> float:
        wave = (math.sin((now / period + phase) * 2 * math.pi) + 1) / 2
        return _lerp(lo, hi, wave)

    def _divider(self, draw: ImageDraw.ImageDraw, cx: float, y: float, width: float, color) -> None:
        gap = 10
        draw.line([(cx - width / 2, y), (cx - gap, y)], fill=color, width=1)
        draw.line([(cx + gap, y), (cx + width / 2, y)], fill=color, width=1)
        self._draw_diamond(draw, cx, y, 4, fill=self.theme.colors["gold"] + (255,))

    def _footer_bar(
        self,
        canvas: Image.Image,
        text: str,
        now: float | None = None,
        pulse_hearts: bool = False,
    ) -> None:
        draw = ImageDraw.Draw(canvas)
        bar_h = max(48, int(self.h * 0.135))
        y0 = self.h - bar_h
        dark = self.theme.colors["dark_bar"]
        draw.rectangle([0, y0, self.w, self.h], fill=_rgba(dark))
        draw.line([(0, y0), (self.w, y0)], fill=self.theme.colors["gold"] + (255,), width=3)

        cream = self.theme.colors["bg_cream"]
        font = self._font("heading", max(14, int(self.h * 0.038)))
        cy = y0 + bar_h / 2
        text_w = self._tracked_width(draw, text.upper(), font, 3.0)
        self._draw_tracked(draw, self.w / 2, cy, text.upper(), font, _rgba(cream), spacing=3.0)

        gold = self.theme.colors["gold"]
        alpha = int(255 * (self._pulse(now, phase=0.0) if (pulse_hearts and now is not None) else 0.85))
        alpha2 = int(255 * (self._pulse(now, phase=0.5) if (pulse_hearts and now is not None) else 0.85))
        gap = 22
        self._draw_diamond(draw, self.w / 2 - text_w / 2 - gap, cy, 6, fill=self._blend_full(dark, gold, alpha))
        self._draw_diamond(draw, self.w / 2 + text_w / 2 + gap, cy, 6, fill=self._blend_full(dark, gold, alpha2))

    def _to_bgr(self, canvas: Image.Image) -> np.ndarray:
        rgb = canvas.convert("RGB")
        return np.array(rgb)[:, :, ::-1].copy()

    def _draw_tracked_shadowed(
        self,
        draw: ImageDraw.ImageDraw,
        cx: float,
        cy: float,
        text: str,
        font,
        fill,
        spacing: float = 3.0,
    ) -> None:
        """Like _draw_tracked, but with a soft dark contact shadow -- for
        text placed directly over live/blurred camera footage (no scrim
        card underneath it), where a flat fill alone can lose contrast
        against bright frames."""
        off = max(1, int(font.size * 0.05))
        self._draw_tracked(draw, cx + off, cy + off, text, font, (8, 14, 8, 170), spacing)
        self._draw_tracked(draw, cx, cy, text, font, fill, spacing)

    def _draw_shadowed(
        self,
        draw: ImageDraw.ImageDraw,
        xy: tuple[float, float],
        text: str,
        font,
        fill,
        anchor: str = "ma",
    ) -> None:
        """Plain (non-tracked) text with a soft dark contact shadow -- for
        script titles/captions placed directly over live or blurred camera
        footage, with no scrim card underneath to guarantee contrast."""
        off = max(1, int(font.size * 0.04))
        x, y = xy
        draw.text((x + off, y + off), text, font=font, fill=(8, 14, 8, 170), anchor=anchor)
        draw.text((x, y), text, font=font, fill=fill, anchor=anchor)

    def _panel_scrim(
        self,
        w: int,
        h: int,
        base_rgb: tuple[int, int, int],
        top_opacity: float,
        mid_opacity: float,
        mid_stop: float = 0.58,
    ) -> Image.Image:
        """Vertical fade-to-transparent scrim for text legibility over the
        camera panel: top_opacity at y=0, mid_opacity at mid_stop, fully
        transparent at the bottom (matches the design's 3-stop CSS
        gradient behind panel-overlay headlines)."""
        h = max(1, int(h))
        w = max(1, int(w))
        col = Image.new("RGBA", (1, h))
        px = col.load()
        for y in range(h):
            frac = y / max(1, h - 1)
            if frac <= mid_stop:
                a = _lerp(top_opacity, mid_opacity, frac / mid_stop if mid_stop else 0.0)
            else:
                a = _lerp(mid_opacity, 0.0, (frac - mid_stop) / (1 - mid_stop))
            px[0, y] = (*base_rgb, int(255 * a))
        return col.resize((w, h))

    def _wrap_tracked_lines(
        self, draw: ImageDraw.ImageDraw, text: str, font, spacing: float, max_width: float
    ) -> list[str]:
        """Greedy word-wrap of letter-tracked text against a rendered pixel
        width -- used where theme copy (tip labels) may run longer than a
        narrow column allows."""
        words = text.split()
        if not words:
            return []
        lines: list[str] = []
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if self._tracked_width(draw, candidate, font, spacing) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return lines

    def _couple_header_bar(self, draw: ImageDraw.ImageDraw) -> None:
        """Compact 'NAMES · gold hairline · DATE' bar at the top of every
        screen except WELCOME (which centers name/date within its own hero
        layout) and the attendant-only SETTINGS/DEVELOPER screens."""
        t = self.theme
        ink = _rgba(t.colors["ink"])
        gold_dark = _rgba(t.colors["gold_dark"])
        # Nudged down from the design's top:24px -- this theme's frame art
        # (unlike the mockup's stand-in photo background) has corner
        # foliage that reaches far enough in at y=24 to clip "2024"; by
        # y=46 it has receded enough to clear the full-width name/date row.
        y = self.h * (46 / 576)
        font = self._font("heading_medium", int(self.h * (26 / 576)))
        names_u, date_u = t.couple_names.upper(), t.event_date.upper()
        gap = self.w * (28 / 1024)
        names_w = self._tracked_width(draw, names_u, font, 7)
        date_w = self._tracked_width(draw, date_u, font, 7)
        start_x = self.w / 2 - (names_w + gap * 2 + date_w) / 2
        end_x = self._draw_tracked_left(draw, start_x, y, names_u, font, ink, spacing=7)
        half_h = self.h * (10 / 576)
        hairline_x = end_x + gap / 2
        draw.line([(hairline_x, y - half_h), (hairline_x, y + half_h)], fill=gold_dark, width=1)
        self._draw_tracked_left(draw, end_x + gap, y, date_u, font, gold_dark, spacing=7)

    def _camera_panel(
        self,
        canvas: Image.Image,
        draw: ImageDraw.ImageDraw,
        camera_frame_bgr: np.ndarray | None,
        placeholder_text: str,
        blur_px: float = 0.0,
    ) -> tuple[float, float, float, float]:
        """Full-width bordered/mirrored camera panel with corner brackets
        (36,90,824,402 @ 1024x576) -- shared by GET READY (genuinely live),
        COUNTDOWN and RECORDING (both a blurred still; see
        state_machine.py's module docstring for why ffmpeg, not this
        panel, owns the camera at that point). No meter column any more --
        see _sound_check_bar/_tips_column for what replaced it. Returns
        (x0, y0, w, h) so callers can layer a scrim/caption on top.
        """
        t = self.theme
        gold = t.colors["gold"]
        x0, y0 = self.w * _PANEL_X, self.h * _PANEL_Y
        w, h = self.w * _PANEL_W, self.h * _PANEL_H
        iw, ih = int(w), int(h)

        panel_bg = (34, 48, 31)
        draw.rectangle([x0, y0, x0 + w, y0 + h], fill=_rgba(panel_bg))
        if camera_frame_bgr is not None:
            if blur_px > 0:
                # Cover-fit a padded crop straight from the source frame
                # (not the already-cropped panel image) so the blur has
                # real pixels to sample past every edge, rather than
                # feathering to transparent/black at the panel border.
                pad = max(1, int(blur_px * 3))
                fitted = _fit_cover_bgr(camera_frame_bgr, iw + pad * 2, ih + pad * 2)
                fitted = cv2.flip(fitted, 1)
                big = Image.fromarray(cv2.cvtColor(fitted, cv2.COLOR_BGR2RGB)).convert("RGBA")
                big = big.filter(ImageFilter.GaussianBlur(blur_px))
                frame_img = big.crop((pad, pad, pad + iw, pad + ih))
            else:
                fitted = _fit_cover_bgr(camera_frame_bgr, iw, ih)
                fitted = cv2.flip(fitted, 1)
                frame_img = Image.fromarray(cv2.cvtColor(fitted, cv2.COLOR_BGR2RGB)).convert("RGBA")
            canvas.alpha_composite(frame_img, (int(x0), int(y0)))
        else:
            note_font = self._font("heading_medium", int(self.h * 0.03))
            draw.text((x0 + w / 2, y0 + h / 2), placeholder_text.upper(), font=note_font, fill=_rgba((245, 239, 227)), anchor="mm")

        draw.rectangle([x0, y0, x0 + w, y0 + h], outline=self._blend_full(panel_bg, gold, 150), width=1)
        bracket = self.h * 0.03
        for bx, by, dx, dy in (
            (x0 + 8, y0 + 8, 1, 1), (x0 + w - 8, y0 + 8, -1, 1),
            (x0 + 8, y0 + h - 8, 1, -1), (x0 + w - 8, y0 + h - 8, -1, -1),
        ):
            draw.line([(bx, by), (bx + bracket * dx, by)], fill=_rgba(gold), width=2)
            draw.line([(bx, by), (bx, by + bracket * dy)], fill=_rgba(gold), width=2)

        return x0, y0, w, h

    def _tips_column(self, canvas: Image.Image, draw: ImageDraw.ImageDraw) -> None:
        """Camera / mic / smile tip icons stacked in the column to the
        right of the camera panel (872,90,116,402 @ 1024x576) -- shared by
        GET READY, COUNTDOWN and RECORDING. Replaced the old per-screen
        vertical mic meter."""
        t = self.theme
        ink = _rgba(t.colors["ink"])
        gold = t.colors["gold"]
        x0 = self.w * _TIPS_X
        y0 = self.h * _PANEL_Y
        w = self.w * _TIPS_W
        h = self.h * _PANEL_H
        tips = [
            ("camera", self._icon_camera, t.text["tip_camera"]),
            ("mic", self._icon_mic, t.text["tip_speak"]),
            ("smile", self._icon_smile, t.text["tip_smile"]),
        ]
        row_h = h / 3
        icon_size = self.h * 0.055
        label_font = self._font("heading", int(self.h * 0.024))
        line_h = self.h * 0.03
        for i, (key, icon_fn, label) in enumerate(tips):
            row_top = y0 + row_h * i
            row_cx, row_cy = x0 + w / 2, row_top + row_h / 2
            lines = self._wrap_tracked_lines(draw, label.upper(), label_font, 1.0, w * 0.94)
            icon_cy = row_cy - row_h * 0.16 - (len(lines) - 1) * line_h * 0.3
            self._draw_tip_icon(canvas, draw, key, icon_fn, row_cx, icon_cy, icon_size, ink)
            text_y = icon_cy + icon_size * 0.62
            for line in lines:
                self._draw_tracked(draw, row_cx, text_y, line, label_font, ink, spacing=1.0)
                text_y += line_h
            if i > 0:
                draw.line(
                    [(x0, row_top), (x0 + w, row_top)],
                    fill=self._blend_full(t.colors["bg_cream"], gold, 130), width=1,
                )

    def _sound_check_bar(
        self, canvas: Image.Image, draw: ImageDraw.ImageDraw, mic_fraction: float, mic_clipped: bool
    ) -> None:
        """Horizontal sound-check status line + pill track, fixed at
        (36,444,824,~50 @ 1024x576) on GET READY, COUNTDOWN and RECORDING
        -- same coordinates on all three so it holds still as the guest
        moves between them (matches the design's rationale for pinning it
        there rather than inside each screen's own content flow)."""
        t = self.theme
        cream = t.colors["bg_cream"]
        x0 = self.w * _PANEL_X
        w = self.w * _PANEL_W
        cx = x0 + w / 2
        y = self.h * _SOUNDCHECK_Y

        status_font = self._font("heading", int(self.h * 0.0347))
        status_text = t.text["soundcheck_clip"] if mic_clipped else t.text["soundcheck_ok"]
        status_color = (240, 167, 156, 255) if mic_clipped else _rgba(cream)
        self._draw_tracked_shadowed(draw, cx, y, status_text.upper(), status_font, status_color, spacing=4)
        y += self.h * 0.03

        bar_w = self.w * _SOUNDCHECK_BAR_W
        bar_h = self.h * 0.0208
        bar_x = cx - bar_w / 2
        track = Image.new("RGBA", (max(1, int(bar_w)), max(1, int(bar_h))), (0, 0, 0, 0))
        ImageDraw.Draw(track).rounded_rectangle(
            [0, 0, bar_w, bar_h], radius=int(bar_h / 2), fill=(*cream, 61)
        )
        canvas.alpha_composite(track, (int(bar_x), int(y)))

        fill_w = bar_w * max(0.0, min(1.0, mic_fraction))
        fill_color = (217, 83, 74) if mic_clipped else t.colors["gold"]
        if fill_w > 1:
            draw.rounded_rectangle([bar_x, y, bar_x + fill_w, y + bar_h], radius=int(bar_h / 2), fill=_rgba(fill_color))

    # ── icons (Get Ready tips row) ───────────────────────────────────
    def _icon_camera(self, draw: ImageDraw.ImageDraw, cx: float, cy: float, s: float, color) -> None:
        w, h = s * 1.15, s * 0.72
        draw.rounded_rectangle([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], radius=s * 0.1, outline=color, width=2)
        draw.ellipse([cx - h * 0.32, cy - h * 0.32, cx + h * 0.32, cy + h * 0.32], outline=color, width=2)
        bump_w = w * 0.28
        draw.line(
            [(cx - bump_w / 2, cy - h / 2), (cx - bump_w / 2 + s * 0.06, cy - h / 2 - s * 0.14),
             (cx + bump_w / 2 - s * 0.06, cy - h / 2 - s * 0.14), (cx + bump_w / 2, cy - h / 2)],
            fill=color, width=2,
        )

    def _icon_mic(self, draw: ImageDraw.ImageDraw, cx: float, cy: float, s: float, color) -> None:
        head_w, head_h = s * 0.34, s * 0.55
        top = cy - s * 0.5
        draw.rounded_rectangle(
            [cx - head_w / 2, top, cx + head_w / 2, top + head_h], radius=head_w / 2, outline=color, width=2
        )
        stand_r = s * 0.32
        draw.arc([cx - stand_r, top + head_h * 0.35, cx + stand_r, top + head_h + stand_r], 0, 180, fill=color, width=2)
        draw.line([(cx, top + head_h + stand_r), (cx, cy + s * 0.45)], fill=color, width=2)
        draw.line([(cx - s * 0.18, cy + s * 0.45), (cx + s * 0.18, cy + s * 0.45)], fill=color, width=2)

    def _icon_smile(self, draw: ImageDraw.ImageDraw, cx: float, cy: float, s: float, color) -> None:
        r = s * 0.48
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=2)
        eye_r = s * 0.045
        draw.ellipse([cx - r * 0.38 - eye_r, cy - r * 0.15 - eye_r, cx - r * 0.38 + eye_r, cy - r * 0.15 + eye_r], fill=color)
        draw.ellipse([cx + r * 0.38 - eye_r, cy - r * 0.15 - eye_r, cx + r * 0.38 + eye_r, cy - r * 0.15 + eye_r], fill=color)
        draw.arc([cx - r * 0.55, cy - r * 0.1, cx + r * 0.55, cy + r * 0.65], 20, 160, fill=color, width=2)

    def _draw_tip_icon(self, canvas, draw, key, icon_fn, cx: float, cy: float, size: float, color) -> None:
        """Paste a theme-provided PNG for `key` if one was supplied, else
        draw the built-in procedural icon."""
        custom = self._custom_icons.get(key)
        if custom is not None:
            fitted = _contain_resize(custom, int(size), int(size))
            px, py = int(cx - fitted.width / 2), int(cy - fitted.height / 2)
            canvas.alpha_composite(fitted, (px, py))
        else:
            icon_fn(draw, cx, cy, size, color)

    # ── screens ───────────────────────────────────────────────────
    def render_ready(self, now: float) -> np.ndarray:
        """WELCOME: static hero screen, couple photo in a fixed side panel
        -- the only screen not built on the shared header bar/camera panel
        (see 1 Welcome.dc.html in the design handoff). Cached: everything
        here is static except the footer's pulsing diamonds, redrawn on a
        copy of the cached layer each frame.
        """
        t = self.theme

        def build(canvas: Image.Image, draw: ImageDraw.ImageDraw) -> None:
            ink = _rgba(t.colors["ink"])
            gold = _rgba(t.colors["gold"])
            cx = self.w * _WELCOME_CONTENT_CX

            eyebrow_font = self._font("heading", int(self.h * (30 / 576)))
            self._draw_tracked(draw, cx, self.h * (57 / 576), t.text["welcome_eyebrow"].upper(), eyebrow_font, ink, spacing=4)
            self._divider(draw, cx, self.h * (112 / 576), self.w * (348 / 1024), gold)

            script_font = self._font("script", int(self.h * (110 / 576)))
            draw.text((cx, self.h * (140 / 576)), t.text["welcome_title"], font=script_font, fill=ink, anchor="ma")

            self._divider(draw, cx, self.h * (293 / 576), self.w * (287 / 1024), gold)

            names_font = self._font("heading", int(self.h * (43 / 576)))
            self._draw_tracked(draw, cx, self.h * (314 / 576), t.couple_names.upper(), names_font, ink, spacing=3)

            date_font = self._font("heading_medium", int(self.h * (28 / 576)))
            self._draw_tracked(draw, cx, self.h * (374 / 576), t.event_date.upper(), date_font, gold, spacing=5)

            prompt_font = self._font("heading", int(self.h * (37 / 576)))
            self._draw_tracked(draw, cx, self.h * (421 / 576), t.text["ready_prompt_1"].upper(), prompt_font, ink, spacing=3)

            prompt2_font = self._font("heading_medium", int(self.h * (24 / 576)))
            prompt2_color = self._blend_full(t.colors["bg_cream"], t.colors["ink"], 210)
            self._draw_tracked(draw, cx, self.h * (471 / 576), t.text["ready_prompt_2"].upper(), prompt2_font, prompt2_color, spacing=2)

            if self._couple_photo is not None:
                photo_x, photo_y = self.w * _WELCOME_PHOTO_X, self.h * _WELCOME_PHOTO_Y
                border_pad = 8
                draw.rounded_rectangle(
                    [photo_x - border_pad, photo_y - border_pad,
                     photo_x + self._couple_photo.width + border_pad, photo_y + self._couple_photo.height + border_pad],
                    radius=12, outline=gold, width=1, fill=_rgba(t.colors["bg_cream"]),
                )
                canvas.paste(self._couple_photo, (int(photo_x), int(photo_y)))

        layer = self._static_layer(("ready",), build)
        self._footer_bar(layer, t.text["ready_footer"], now=now, pulse_hearts=True)
        return self._to_bgr(layer)

    def render_preview(
        self,
        now: float,
        camera_frame_bgr: np.ndarray | None,
        mic_fraction: float,
        mic_clipped: bool,
    ) -> np.ndarray:
        """GET READY: header, live camera panel with an over-video
        headline, tips column, sound check and the big Tap to Record
        button.

        Sets self.record_button_rect to the button's current pixel rect
        every call so main.py's mouse/touch handler can hit-test against it
        (see state_machine.py's module docstring for why this screen
        exists). The camera here is genuinely live -- ffmpeg hasn't opened
        the device yet at this point in the flow -- unlike COUNTDOWN and
        RECORDING, which both freeze and blur the last frame captured here.
        """
        t = self.theme
        canvas = self._canvas(dimmed=True)
        draw = ImageDraw.Draw(canvas)

        self._couple_header_bar(draw)

        x0, y0, w, h = self._camera_panel(canvas, draw, camera_frame_bgr, t.text["cam_placeholder"])

        scrim = self._panel_scrim(int(w), int(h * 0.26), (18, 26, 17), 0.55, 0.385)
        canvas.alpha_composite(scrim, (int(x0), int(y0)))
        draw = ImageDraw.Draw(canvas)
        cxp = x0 + w / 2
        title_font = self._font("script", int(self.h * (63 / 576)))
        self._draw_shadowed(draw, (cxp, y0 + self.h * (14 / 576)), t.text["get_ready_title"], title_font, _rgba((245, 239, 227)))
        caption_font = self._font("heading_medium", int(self.h * (16 / 576)))
        self._draw_tracked_shadowed(
            draw, cxp, y0 + self.h * (98 / 576), t.text["preview_caption"].upper(), caption_font,
            _rgba((245, 239, 227)), spacing=3,
        )

        self._tips_column(canvas, draw)
        self._sound_check_bar(canvas, draw, mic_fraction, mic_clipped)

        btn_x0, btn_y0 = self.w * (208 / 1024), self.h * (300 / 576)
        btn_w, btn_h = self.w * (480 / 1024), self.h * (84 / 576)
        glow = self._pulse(now, period=1.6, lo=0.85, hi=1.0)
        halo_pad = self.w * 0.007
        halo = Image.new("RGBA", (int(btn_w + halo_pad * 2), int(btn_h + halo_pad * 2)), (0, 0, 0, 0))
        ImageDraw.Draw(halo).rounded_rectangle(
            [halo_pad, halo_pad, halo_pad + btn_w, halo_pad + btn_h], radius=int(btn_h * 0.31),
            fill=(*t.colors["gold"], int(60 * glow)),
        )
        canvas.alpha_composite(halo, (int(btn_x0 - halo_pad), int(btn_y0 - halo_pad)))
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle(
            [btn_x0, btn_y0, btn_x0 + btn_w, btn_y0 + btn_h], radius=int(btn_h * 0.31),
            fill=_rgba(t.colors["gold"]), outline=_rgba(t.colors["gold_dark"]), width=2,
        )
        btn_font = self._font("heading", int(self.h * (32 / 576)))
        self._draw_tracked(
            draw, btn_x0 + btn_w / 2, btn_y0 + btn_h / 2, t.text["preview_button"].upper(), btn_font,
            _rgba(t.colors["dark_bar"]), spacing=4,
        )
        self.record_button_rect = (int(btn_x0), int(btn_y0), int(btn_x0 + btn_w), int(btn_y0 + btn_h))

        self._footer_bar(canvas, t.text["preview_footer"])
        return self._to_bgr(canvas)

    def render_countdown(
        self,
        now: float,
        remaining_seconds: float,
        total_seconds: int,
        camera_frame_bgr: np.ndarray | None,
        mic_fraction: float | None,
        mic_clipped: bool,
    ) -> np.ndarray:
        """COUNTDOWN: same base as GET READY/RECORDING -- header, camera
        panel (blurred: the guest's focus should be on the ring, not fine
        preview detail), tips column, sound check. The feed is still
        genuinely live here (see state_machine.py's module docstring for
        why -- ffmpeg hasn't touched the camera yet), just blurred and
        scrimmed behind the countdown ring.
        """
        t = self.theme
        canvas = self._canvas(dimmed=True)
        draw = ImageDraw.Draw(canvas)

        self._couple_header_bar(draw)

        x0, y0, w, h = self._camera_panel(canvas, draw, camera_frame_bgr, t.text["cam_placeholder"], blur_px=8.0)

        scrim = Image.new("RGBA", (int(w), int(h)), (18, 26, 17, 140))
        canvas.alpha_composite(scrim, (int(x0), int(y0)))
        draw = ImageDraw.Draw(canvas)
        cxp = x0 + w / 2

        content_h = h - self.h * (96 / 576)  # leaves visual room below for the sound-check bar
        caption_font = self._font("heading_medium", int(self.h * (27 / 576)))
        caption_y = y0 + content_h * 0.22
        self._draw_tracked_shadowed(
            draw, cxp, caption_y, t.text["countdown_caption"].upper(), caption_font,
            _rgba((245, 239, 227)), spacing=5,
        )

        ring_r = self.h * (90 / 576)
        ring_cy = caption_y + self.h * 0.09 + ring_r
        bbox = [cxp - ring_r, ring_cy - ring_r, cxp + ring_r, ring_cy + ring_r]
        track_width = int(self.h * 0.0139)
        fraction = 0.0 if total_seconds <= 0 else 1 - max(0.0, remaining_seconds) / total_seconds
        draw.arc(bbox, 0, 360, fill=(245, 239, 227, 71), width=track_width)
        draw.arc(bbox, -90, -90 + 360 * fraction, fill=_rgba(t.colors["gold"]), width=track_width)

        count = math.ceil(remaining_seconds)
        label = str(count) if count > 0 else t.text["countdown_go"]
        self._draw_pop_number(canvas, cxp, ring_cy, label, now, count, color=(245, 239, 227))

        self._tips_column(canvas, draw)
        self._sound_check_bar(canvas, draw, mic_fraction if mic_fraction is not None else 0.0, mic_clipped)

        self._footer_bar(canvas, t.text["countdown_footer"])
        return self._to_bgr(canvas)

    def _draw_pop_number(
        self, canvas: Image.Image, cx: float, cy: float, label, now: float, count_key,
        color: tuple[int, int, int] | None = None,
    ) -> None:
        if self._pop_state is None or self._pop_state[0] != count_key:
            self._pop_state = (count_key, now)
        _, started_at = self._pop_state
        age = max(0.0, now - started_at)
        dur = 0.35
        p = min(1.0, age / dur)
        ease = 1 - (1 - p) ** 3
        scale = _lerp(1.6, 1.0, ease)
        alpha = _lerp(0.0, 1.0, ease)

        is_word = isinstance(label, str) and not label.isdigit()
        font_key = "script" if is_word else "heading"
        size = int(self.h * (0.13 if is_word else 0.24))
        font = self._font(font_key, size)
        fill_color = color if color is not None else self.theme.colors["ink"]

        tmp = Image.new("RGBA", (int(self.w * 0.5), int(self.h * 0.5)), (0, 0, 0, 0))
        tdraw = ImageDraw.Draw(tmp)
        tcx, tcy = tmp.width / 2, tmp.height / 2
        tdraw.text((tcx, tcy), str(label), font=font, fill=_rgba(fill_color), anchor="mm")
        bbox = tdraw.textbbox((tcx, tcy), str(label), font=font, anchor="mm")
        pad = 20
        crop_box = (max(0, bbox[0] - pad), max(0, bbox[1] - pad), min(tmp.width, bbox[2] + pad), min(tmp.height, bbox[3] + pad))
        cropped = tmp.crop(crop_box)
        new_size = (max(1, int(cropped.width * scale)), max(1, int(cropped.height * scale)))
        scaled = cropped.resize(new_size, Image.LANCZOS)
        if alpha < 1.0:
            a = scaled.split()[3].point(lambda v: int(v * alpha))
            scaled.putalpha(a)
        paste_x, paste_y = int(cx - scaled.width / 2), int(cy - scaled.height / 2)
        canvas.alpha_composite(scaled, (paste_x, paste_y))

    def render_recording(
        self,
        now: float,
        camera_frame_bgr: np.ndarray | None,
        remaining: int,
        mic_fraction: float,
        mic_clipped: bool,
    ) -> np.ndarray:
        """RECORDING: same base as GET READY/COUNTDOWN -- header, camera
        panel showing the still frame frozen when COUNTDOWN ended (blurred;
        ffmpeg owns the camera now, see state_machine.py's module
        docstring), tips column, sound check, plus a status pill with the
        time left and a Cancel & restart action.

        Sets self.cancel_button_rect the same way render_preview sets
        record_button_rect, for main.py's touch handler to hit-test (see
        main.py's _cancel_recording).
        """
        t = self.theme
        canvas = self._canvas(dimmed=True)
        draw = ImageDraw.Draw(canvas)

        self._couple_header_bar(draw)

        x0, y0, w, h = self._camera_panel(canvas, draw, camera_frame_bgr, t.text["cam_placeholder"], blur_px=8.0)

        scrim = self._panel_scrim(int(w), int(h * 0.34), (18, 26, 17), 0.9, 0.63)
        canvas.alpha_composite(scrim, (int(x0), int(y0)))
        draw = ImageDraw.Draw(canvas)
        cxp = x0 + w / 2
        title_font = self._font("script", int(self.h * (63 / 576)))
        self._draw_shadowed(draw, (cxp, y0 + self.h * (14 / 576)), t.text["recording_title"], title_font, _rgba((245, 239, 227)))

        look_here, hang_up = (p.strip() for p in t.text["recording_look_here"].split("·", 1))
        caption_font = self._font("heading_medium", int(self.h * (27 / 576)))
        cap_y = y0 + self.h * (108 / 576)
        self._draw_tracked_shadowed(draw, cxp, cap_y, f"· {look_here} ·".upper(), caption_font, _rgba((245, 239, 227)), spacing=4)
        self._draw_tracked_shadowed(draw, cxp, cap_y + self.h * (34 / 576), hang_up.upper(), caption_font, _rgba((245, 239, 227)), spacing=4)

        self._tips_column(canvas, draw)
        self._sound_check_bar(canvas, draw, mic_fraction, mic_clipped)

        pill_y0 = self.h * (352 / 576)
        pill_h = self.h * (66 / 576)
        pill_w = self.w * (410 / 1024)
        cancel_w = self.w * (250 / 1024)
        gap = self.w * (16 / 1024)
        total_w = pill_w + gap + cancel_w
        row_x0 = cxp - total_w / 2

        row = Image.new("RGBA", (max(1, int(total_w)), max(1, int(pill_h))), (0, 0, 0, 0))
        rdraw = ImageDraw.Draw(row)
        dark = t.colors["dark_bar"]
        rdraw.rounded_rectangle(
            [0, 0, pill_w, pill_h], radius=int(pill_h * 0.3),
            fill=(*dark, 255), outline=(*t.colors["gold_dark"], 255), width=2,
        )
        pulse_alpha = int(255 * self._pulse(now, period=1.2, lo=0.35, hi=1.0))
        dot_r = pill_h * 0.12
        dot_cx, dot_cy = pill_h * 0.42, pill_h / 2
        rdraw.ellipse(
            [dot_cx - dot_r, dot_cy - dot_r, dot_cx + dot_r, dot_cy + dot_r],
            fill=self._blend_full(dark, t.colors["recording_red"], pulse_alpha),
        )
        label_font = self._font("heading", int(self.h * (22 / 576)))
        label_end = self._draw_tracked_left(
            rdraw, dot_cx + dot_r * 1.8, dot_cy, t.text["recording_label"].upper(), label_font,
            _rgba((245, 239, 227)), spacing=3,
        )
        sep_x = label_end + self.w * 0.012
        rdraw.line([(sep_x, pill_h * 0.18), (sep_x, pill_h * 0.82)], fill=(245, 239, 227, 89), width=1)

        def _fmt(seconds: int) -> str:
            m, s = divmod(max(0, seconds), 60)
            return f"{m}:{s:02d}"

        val_font = self._font("heading", int(self.h * (26 / 576)))
        remain_color = t.colors["recording_red"] if remaining <= 10 else (245, 239, 227)
        val_x = sep_x + self.w * 0.018
        rdraw.text((val_x, dot_cy), _fmt(remaining), font=val_font, fill=_rgba(remain_color), anchor="lm")
        val_w = rdraw.textlength(_fmt(remaining), font=val_font)
        suffix_font = self._font("heading_medium", int(self.h * (13 / 576)))
        rdraw.text(
            (val_x + val_w + self.w * 0.008, dot_cy), t.text["recording_time_left_suffix"].upper(),
            font=suffix_font, fill=(245, 239, 227, 153), anchor="lm",
        )

        cancel_x0 = pill_w + gap
        rdraw.rounded_rectangle(
            [cancel_x0, 0, cancel_x0 + cancel_w, pill_h], radius=int(pill_h * 0.3),
            fill=(22, 34, 26, 179), outline=(245, 239, 227, 140), width=2,
        )
        cancel_label_font = self._font("heading", int(self.h * (19 / 576)))
        cancel_sub_font = self._font("heading_medium", int(self.h * (12 / 576)))
        self._draw_tracked(rdraw, cancel_x0 + cancel_w / 2, pill_h * 0.37, t.text["recording_cancel_label"].upper(), cancel_label_font, _rgba((245, 239, 227)), spacing=3)
        self._draw_tracked(rdraw, cancel_x0 + cancel_w / 2, pill_h * 0.68, t.text["recording_cancel_sublabel"].upper(), cancel_sub_font, (245, 239, 227, 166), spacing=2)

        canvas.alpha_composite(row, (int(row_x0), int(pill_y0)))
        self.cancel_button_rect = (
            int(row_x0 + cancel_x0), int(pill_y0), int(row_x0 + cancel_x0 + cancel_w), int(pill_y0 + pill_h),
        )

        self._footer_bar(canvas, t.text["recording_footer"], now=now, pulse_hearts=True)
        return self._to_bgr(canvas)

    def render_saving(self, now: float) -> np.ndarray:
        """SAVING: header, script title, spinner ring, sub-copy, footer.
        Cached: everything is static except the ring's breathing track and
        sweeping arc.
        """
        t = self.theme

        def build(canvas: Image.Image, draw: ImageDraw.ImageDraw) -> None:
            ink = _rgba(t.colors["ink"])
            cx = self.w / 2
            self._couple_header_bar(draw)
            script_font = self._font("script", int(self.h * (69 / 576)))
            draw.text((cx, self.h * (96 / 576)), t.text["saving_title"], font=script_font, fill=ink, anchor="ma")
            sub_font = self._font("heading", int(self.h * (34 / 576)))
            sub_color = self._blend_full(t.colors["bg_cream"], t.colors["ink"], 230)
            self._draw_tracked(draw, cx, self.h * (331 / 576), t.text["saving_sub"], sub_font, sub_color, spacing=2)
            self._footer_bar(canvas, t.text["saving_footer"])

        layer = self._static_layer(("saving",), build)
        draw = ImageDraw.Draw(layer)
        cx = self.w / 2
        gold_rgb = t.colors["gold"]
        cream = t.colors["bg_cream"]
        ring_r = self.h * (58 / 576)
        ring_cy = self.h * (253 / 576)
        bbox = [cx - ring_r, ring_cy - ring_r, cx + ring_r, ring_cy + ring_r]
        breathe = self._pulse(now, period=2.0, lo=0.4, hi=1.0)
        track_width = int(self.h * 0.0104)
        draw.arc(bbox, 0, 360, fill=self._blend_full(cream, gold_rgb, int(70 * breathe)), width=track_width)
        sweep_deg = (now * 240) % 360
        draw.arc(bbox, sweep_deg, sweep_deg + 100, fill=_rgba(gold_rgb), width=track_width)
        return self._to_bgr(layer)

    def render_saved(self, now: float, time_in_state: float) -> np.ndarray:
        """THANK YOU: header, script title, checkmark ring, sub-copy,
        divider, closing script line, footer. Cached: everything is static
        except the checkmark's stroke-on animation.
        """
        t = self.theme
        ring_r = self.h * (52 / 576)
        ring_cy = self.h * (236 / 576)

        def build(canvas: Image.Image, draw: ImageDraw.ImageDraw) -> None:
            ink = _rgba(t.colors["ink"])
            gold = _rgba(t.colors["gold"])
            cream = t.colors["bg_cream"]
            cx = self.w / 2
            self._couple_header_bar(draw)
            script_font = self._font("script", int(self.h * (81 / 576)))
            draw.text((cx, self.h * (78 / 576)), t.text["thanks_title"], font=script_font, fill=ink, anchor="ma")
            bbox = [cx - ring_r, ring_cy - ring_r, cx + ring_r, ring_cy + ring_r]
            draw.ellipse(bbox, outline=gold, width=int(self.h * 0.0087))
            sub_font = self._font("heading", int(self.h * (21 / 576)))
            self._draw_tracked(draw, cx, self.h * (305 / 576), t.text["thanks_sub"].upper(), sub_font, ink, spacing=1)
            self._divider(draw, cx, self.h * (340 / 576), self.w * (266 / 1024), gold)
            sub2_font = self._font("heading_medium", int(self.h * (17 / 576)))
            sub2_color = self._blend_full(cream, t.colors["ink"], 220)
            draw.text((cx, self.h * (361 / 576)), t.text["thanks_sub2"], font=sub2_font, fill=sub2_color, anchor="ma")
            close_font = self._font("script", int(self.h * (49 / 576)))
            draw.text((cx, self.h * (390 / 576)), t.text["thanks_close"], font=close_font, fill=gold, anchor="ma")
            self._footer_bar(canvas, t.text["thanks_footer"])

        layer = self._static_layer(("saved",), build)
        draw = ImageDraw.Draw(layer)
        self._draw_checkmark(draw, self.w / 2, ring_cy, ring_r * 0.62, time_in_state, _rgba(t.colors["ink"]))
        return self._to_bgr(layer)

    def _draw_checkmark(self, draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, age: float, color) -> None:
        dur = 0.7
        progress = min(1.0, max(0.0, age / dur))
        p1 = (cx - r * 0.65, cy + r * 0.05)
        p2 = (cx - r * 0.15, cy + r * 0.55)
        p3 = (cx + r * 0.75, cy - r * 0.45)
        seg1_len = math.dist(p1, p2)
        seg2_len = math.dist(p2, p3)
        total = seg1_len + seg2_len
        drawn = progress * total
        width = max(2, int(r * 0.16))
        if drawn <= 0:
            return
        if drawn >= seg1_len:
            draw.line([p1, p2], fill=color, width=width, joint="curve")
            remaining = min(drawn - seg1_len, seg2_len)
            if remaining > 0:
                frac = remaining / seg2_len if seg2_len else 0
                end = (_lerp(p2[0], p3[0], frac), _lerp(p2[1], p3[1], frac))
                draw.line([p2, end], fill=color, width=width, joint="curve")
        else:
            frac = drawn / seg1_len if seg1_len else 0
            end = (_lerp(p1[0], p2[0], frac), _lerp(p1[1], p2[1], frac))
            draw.line([p1, end], fill=color, width=width, joint="curve")

    def render_error(self, now: float, reason: str) -> np.ndarray:
        """ERROR: header, red title, divider, a bounded word-wrapped cream
        card for the failure reason, footer -- brings ERROR into the same
        visual system the other six screens share (see
        renderer-patch-notes.md in the design handoff). Cached per
        (reason,), since ERROR is otherwise fully static -- see
        _static_layer.
        """
        t = self.theme

        def build(canvas: Image.Image, draw: ImageDraw.ImageDraw) -> None:
            cx = self.w / 2
            red = t.colors["recording_red"]
            gold = _rgba(t.colors["gold"])
            cream = t.colors["bg_cream"]
            self._couple_header_bar(draw)

            title_font = self._font("heading", int(self.h * (43 / 576)))
            self._draw_tracked(draw, cx, self.h * (117 / 576), t.text["error_title"].upper(), title_font, _rgba(red), spacing=3)
            self._divider(draw, cx, self.h * (193 / 576), self.w * (266 / 1024), gold)

            if reason:
                reason_font = self._font("heading_medium", int(self.h * (22 / 576)))
                reason_color = self._blend_full(cream, t.colors["ink"], 240)
                max_text_w = self.w * (608 / 1024)
                lines = _wrap_text(draw, reason, reason_font, max_text_w)[:3]
                line_h = self.h * (30 / 576)
                pad_x, pad_y = self.w * (36 / 1024), self.h * (18 / 576)
                card_w = max(
                    self.w * (680 / 1024),
                    min(self.w * (952 / 1024), max(draw.textlength(ln, font=reason_font) for ln in lines) + pad_x * 2),
                )
                card_h = line_h * len(lines) + pad_y * 2 - (line_h - self.h * (25 / 576))
                card_y = self.h * (228 / 576)
                draw.rounded_rectangle(
                    [cx - card_w / 2, card_y, cx + card_w / 2, card_y + card_h],
                    radius=int(self.h * 0.02),
                    fill=_rgba(cream),
                    outline=self._blend_full(cream, t.colors["gold"], 150),
                    width=1,
                )
                ty = card_y + pad_y
                for line in lines:
                    draw.text((cx, ty), line, font=reason_font, fill=reason_color, anchor="ma")
                    ty += line_h

            self._footer_bar(canvas, t.text["error_footer"])

        layer = self._static_layer(("error", reason), build)
        return self._to_bgr(layer)

    # ── attendant admin screens ──────────────────────────────────────
    # SETTINGS and DEVELOPER are attendant-only (see state_machine.py's
    # module docstring): flat cream background, no theme frame art, and
    # copy is fixed English chrome rather than theme.text -- unlike the
    # six guest screens above, nothing here is meant to be re-skinned per
    # event.
    _ADMIN_BG = (245, 239, 227)
    _ADMIN_INK = (31, 46, 34)
    _ADMIN_GOLD = (176, 141, 87)
    _ADMIN_GOLD_DARK = (130, 105, 48)
    _ADMIN_GOOD = (62, 122, 74)
    _ADMIN_RED = (178, 58, 44)

    def _admin_canvas(self) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        canvas = Image.new("RGBA", (self.w, self.h), (*self._ADMIN_BG, 255))
        return canvas, ImageDraw.Draw(canvas)

    def _admin_pill_button(
        self,
        draw: ImageDraw.ImageDraw,
        x0: float, y0: float, x1: float, y1: float,
        label: str,
        *,
        filled: bool = False,
        danger: bool = False,
    ) -> None:
        """Small outline (or filled) pill button shared by every admin
        control row -- steppers' +/- aside, which are drawn inline."""
        radius = int((y1 - y0) * 0.32)
        border = self._ADMIN_RED if danger else self._ADMIN_GOLD
        ink = self._ADMIN_RED if danger else self._ADMIN_GOLD_DARK
        if filled:
            draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=_rgba(self._ADMIN_GOLD), outline=_rgba(self._ADMIN_GOLD_DARK), width=2)
            text_fill = _rgba((22, 34, 26))
        else:
            draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, outline=_rgba(border), width=2 if danger else 1)
            text_fill = _rgba(ink)
        font = self._font("heading", int((y1 - y0) * 0.42))
        self._draw_tracked(draw, (x0 + x1) / 2, (y0 + y1) / 2, label.upper(), font, text_fill, spacing=2.5)

    def _admin_stepper(
        self, draw: ImageDraw.ImageDraw, right_x: float, cy: float, value_text: str, key: str
    ) -> None:
        """Right-aligned '- value +' control, used for the numeric
        Recording-column rows. Registers key+'_minus'/key+'_plus' rects."""
        btn = self.h * (34 / 576)
        val_w = self.w * (86 / 1024)
        gap = self.w * (10 / 1024)
        plus_x1 = right_x
        plus_x0 = plus_x1 - btn
        val_x1 = plus_x0 - gap
        val_x0 = val_x1 - val_w
        minus_x1 = val_x0 - gap
        minus_x0 = minus_x1 - btn
        y0, y1 = cy - btn / 2, cy + btn / 2

        for x0, x1, glyph, name in ((minus_x0, minus_x1, "−", "minus"), (plus_x0, plus_x1, "+", "plus")):
            draw.rounded_rectangle([x0, y0, x1, y1], radius=int(btn * 0.24), outline=_rgba(self._ADMIN_GOLD), width=1)
            gf = self._font("heading", int(btn * 0.62))
            draw.text(((x0 + x1) / 2, (y0 + y1) / 2), glyph, font=gf, fill=_rgba(self._ADMIN_GOLD_DARK), anchor="mm")
            self.settings_rects[f"{key}_{name}"] = (int(x0), int(y0), int(x1), int(y1))

        val_font = self._font("heading", int(btn * 0.62))
        draw.text(((val_x0 + val_x1) / 2, cy), value_text, font=val_font, fill=_rgba(self._ADMIN_INK), anchor="mm")

    def _admin_toggle(self, canvas: Image.Image, right_x: float, cy: float, on: bool, key: str) -> None:
        w, h = self.w * (64 / 1024), self.h * (32 / 576)
        x0, x1 = right_x - w, right_x
        y0, y1 = cy - h / 2, cy + h / 2
        # Off-state track is translucent -- needs real alpha compositing
        # (a plain ImageDraw fill stores alpha verbatim rather than
        # blending against the canvas beneath it, see _sound_check_bar),
        # so this is built on its own small overlay rather than drawn
        # straight onto the admin canvas.
        overlay = Image.new("RGBA", (max(1, int(w)), max(1, int(h))), (0, 0, 0, 0))
        odraw = ImageDraw.Draw(overlay)
        track = self._ADMIN_GOLD if on else self._ADMIN_INK
        track_alpha = 255 if on else 40
        odraw.rounded_rectangle([0, 0, w, h], radius=int(h / 2), fill=(*track, track_alpha), outline=(*self._ADMIN_GOLD_DARK, 255), width=1)
        knob_r = h * 0.36
        knob_cx = w - h * 0.5 if on else h * 0.5
        odraw.ellipse([knob_cx - knob_r, h / 2 - knob_r, knob_cx + knob_r, h / 2 + knob_r], fill=(*self._ADMIN_BG, 255))
        canvas.alpha_composite(overlay, (int(x0), int(y0)))
        self.settings_rects[key] = (int(x0), int(y0), int(x1), int(y1))

    def _admin_segmented(
        self, draw: ImageDraw.ImageDraw, x0: float, y: float, options: list[tuple[str, str]], active_key: str, h: float | None = None,
    ) -> float:
        """Left-aligned segmented control (e.g. Guestbook/Extended,
        Quality/Compat). `options` is [(key, label), ...]; returns the
        control's right edge x. Registers a rect per option key."""
        h = h or self.h * (34 / 576)
        pad_x = self.w * (18 / 1024)
        font = self._font("heading", int(h * 0.42))
        x = x0
        seg_y0, seg_y1 = y - h / 2, y + h / 2
        widths = [self._tracked_width(draw, label.upper(), font, 2) + pad_x * 2 for _, label in options]
        total_w = sum(widths)
        draw.rounded_rectangle([x0, seg_y0, x0 + total_w, seg_y1], radius=int(h * 0.28), outline=_rgba(self._ADMIN_GOLD), width=1)
        for (key, label), seg_w in zip(options, widths):
            active = key == active_key
            if active:
                draw.rounded_rectangle([x, seg_y0, x + seg_w, seg_y1], radius=int(h * 0.28), fill=_rgba(self._ADMIN_GOLD))
            fill = _rgba((22, 34, 26)) if active else _rgba(self._ADMIN_GOLD_DARK)
            self._draw_tracked(draw, x + seg_w / 2, y, label.upper(), font, fill, spacing=2)
            self.settings_rects[key] = (int(x), int(seg_y0), int(x + seg_w), int(seg_y1))
            x += seg_w
        return x

    def render_settings(
        self,
        now: float,
        *,
        max_recording_seconds: int,
        countdown_seconds: int,
        recording_mode: str,
        live_mic_meter_enabled: bool,
        extended_mode: bool,
        message_count: int,
        storage_free_gb: float,
        camera_ok: bool,
        camera_device: str,
        mic_ok: bool,
        mic_device: str,
        hook_ok: bool,
        hook_label: str,
        theme_name: str,
    ) -> np.ndarray:
        """SETTINGS: attendant screen for the fields grounded in
        config/booth.default.json plus a read-only hardware status column.
        Populates self.settings_rects with every tappable control's pixel
        rect so main.py's touch handler can hit-test by name (see
        main.py's _on_mouse / _apply_settings_change). "Create new event"
        and "Test recording" are UI-only placeholders -- there is no
        multi-event config schema yet (see PROJECT_SPEC.md section 10,
        an explicitly later milestone); main.py logs the tap and no-ops.
        """
        t = self.theme
        self.settings_rects = {}
        canvas, draw = self._admin_canvas()
        ink = _rgba(self._ADMIN_INK)
        gold_dark = _rgba(self._ADMIN_GOLD_DARK)

        # Current event row.
        y = self.h * (24 / 576)
        label_font = self._font("heading_medium", int(self.h * (16 / 576)))
        names_font = self._font("heading_medium", int(self.h * (26 / 576)))
        change_w, change_h = self.w * (94 / 1024), self.h * (32 / 576)
        label_w = self._tracked_width(draw, "CURRENT EVENT:", label_font, 3)
        names_w = self._tracked_width(draw, t.couple_names.upper(), names_font, 7)
        row_w = label_w + self.w * 0.012 + names_w + self.w * 0.02 + change_w
        x = self.w / 2 - row_w / 2
        x = self._draw_tracked_left(draw, x, y, "CURRENT EVENT:", label_font, gold_dark, spacing=3) + self.w * 0.012
        x = self._draw_tracked_left(draw, x, y, t.couple_names.upper(), names_font, ink, spacing=7) + self.w * 0.02
        self._admin_pill_button(draw, x, y - change_h / 2, x + change_w, y + change_h / 2, "Change")
        self.settings_rects["change_event"] = (int(x), int(y - change_h / 2), int(x + change_w), int(y + change_h / 2))

        # Switch / title / Developer row.
        y2 = self.h * (76 / 576)
        seg_h = self.h * (34 / 576)
        self._admin_segmented(
            draw, self.w * (36 / 1024), y2,
            [("guestbook", "Guestbook"), ("extended", "Extended")],
            "extended" if extended_mode else "guestbook", h=seg_h,
        )
        title_font = self._font("script", int(self.h * (44 / 576)))
        draw.text((self.w / 2, y2), "Booth Settings", font=title_font, fill=ink, anchor="mm")
        dev_w = self.w * (110 / 1024)
        dev_x1 = self.w - self.w * (36 / 1024)
        self._admin_pill_button(draw, dev_x1 - dev_w, y2 - seg_h / 2, dev_x1, y2 + seg_h / 2, "Developer")
        self.settings_rects["developer"] = (int(dev_x1 - dev_w), int(y2 - seg_h / 2), int(dev_x1), int(y2 + seg_h / 2))

        draw.line([(self.w * (36 / 1024), self.h * (132 / 576)), (self.w * (988 / 1024), self.h * (132 / 576))], fill=(*self._ADMIN_GOLD, 140), width=1)

        # Two-column grid: Recording | Hardware.
        col_l_x = self.w * (36 / 1024)
        col_r_x = self.w / 2 + self.w * (28 / 1024)
        col_top = self.h * (152 / 576)
        row_h = self.h * (56 / 576)
        section_font = self._font("heading", int(self.h * (13 / 576)))
        field_font = self._font("heading_medium", int(self.h * (19 / 576)))

        self._draw_tracked_left(draw, col_l_x, col_top, "RECORDING", section_font, gold_dark, spacing=3)
        ry = col_top + self.h * (32 / 576)
        draw.text((col_l_x, ry), "Message length", font=field_font, fill=ink, anchor="lm")
        self._admin_stepper(draw, self.w * (476 / 1024), ry, f"{max_recording_seconds}s", "msg")
        ry += row_h
        draw.text((col_l_x, ry), "Countdown", font=field_font, fill=ink, anchor="lm")
        self._admin_stepper(draw, self.w * (476 / 1024), ry, f"{countdown_seconds}s", "cd")
        ry += row_h
        draw.text((col_l_x, ry), "Quality", font=field_font, fill=ink, anchor="lm")
        self._admin_segmented(
            draw, self.w * (322 / 1024), ry,
            [("quality", "Quality"), ("compat", "Compat")],
            "quality" if recording_mode == "quality" else "compat",
        )
        ry += row_h
        draw.text((col_l_x, ry), "Live mic meter", font=field_font, fill=ink, anchor="lm")
        self._admin_toggle(canvas, self.w * (476 / 1024), ry, live_mic_meter_enabled, "mic_toggle")

        self._draw_tracked_left(draw, col_r_x, col_top, "HARDWARE", section_font, gold_dark, spacing=3)
        hy = col_top + self.h * (32 / 576)
        hw_rows = [
            ("Camera", camera_ok, camera_device),
            ("Microphone", mic_ok, mic_device),
            ("Handset switch", hook_ok, hook_label),
        ]
        dot_r = self.h * 0.0078
        for label, ok, value in hw_rows:
            draw.text((col_r_x, hy), label, font=field_font, fill=ink, anchor="lm")
            val_font = self._font("heading_medium", int(self.h * (17 / 576)))
            val_w = draw.textlength(value, font=val_font)
            val_x1 = self.w * (988 / 1024)
            draw.text((val_x1, hy), value, font=val_font, fill=self._blend_full(self._ADMIN_BG, self._ADMIN_INK, 200), anchor="rm")
            dot_cx = val_x1 - val_w - self.w * 0.014
            dot_color = self._ADMIN_GOOD if ok else self._ADMIN_RED
            draw.ellipse([dot_cx - dot_r, hy - dot_r, dot_cx + dot_r, hy + dot_r], fill=_rgba(dot_color))
            hy += row_h
        draw.text((col_r_x, hy), "Storage", font=field_font, fill=ink, anchor="lm")
        storage_font = self._font("heading_medium", int(self.h * (17 / 576)))
        storage_text = f"{message_count} messages · {storage_free_gb:.0f} GB free"
        storage_w = draw.textlength(storage_text, font=storage_font)
        storage_x1 = self.w * (988 / 1024)
        draw.text((storage_x1, hy), storage_text, font=storage_font, fill=self._blend_full(self._ADMIN_BG, self._ADMIN_INK, 200), anchor="rm")
        dot_cx = storage_x1 - storage_w - self.w * 0.014
        draw.ellipse([dot_cx - dot_r, hy - dot_r, dot_cx + dot_r, hy + dot_r], fill=_rgba(self._ADMIN_GOLD))

        # Bottom action row.
        by = self.h * (446 / 576)
        btn_h = self.h * (48 / 576)
        create_w = self.w * (220 / 1024)
        self._admin_pill_button(draw, col_l_x, by, col_l_x + create_w, by + btn_h, "Create new event")
        self.settings_rects["create_event"] = (int(col_l_x), int(by), int(col_l_x + create_w), int(by + btn_h))
        theme_font = self._font("heading_medium", int(self.h * (15 / 576)))
        theme_color = self._blend_full(self._ADMIN_BG, self._ADMIN_INK, 155)
        self._draw_tracked_left(draw, col_l_x, by + btn_h + self.h * (22 / 576), f"THEME · {theme_name}".upper(), theme_font, theme_color, spacing=2)

        save_w = self.w * (190 / 1024)
        save_x1 = self.w * (988 / 1024)
        self._admin_pill_button(draw, save_x1 - save_w, by, save_x1, by + btn_h, "Save & exit", filled=True)
        self.settings_rects["save_exit"] = (int(save_x1 - save_w), int(by), int(save_x1), int(by + btn_h))
        test_w = self.w * (180 / 1024)
        test_x1 = save_x1 - save_w - self.w * (14 / 1024)
        self._admin_pill_button(draw, test_x1 - test_w, by, test_x1, by + btn_h, "Test recording")
        self.settings_rects["test_recording"] = (int(test_x1 - test_w), int(by), int(test_x1), int(by + btn_h))

        return self._to_bgr(canvas)

    def render_developer(
        self,
        now: float,
        *,
        camera_frame_bgr: np.ndarray | None,
        preview_fps: float,
        camera_device: str,
        dropped_frames: int,
        peak_db: float,
        room_base_db: float,
        clip_count: int,
        mic_device: str,
        hook_off_hook: bool,
        hook_pin_state: str,
        hook_held_seconds: float,
        overlay_draw_ms: float,
        cpu_temp_c: float | None,
        throttled: bool | None,
        cpu_load_pct: float | None,
        mem_used_mb: float | None,
        mem_total_mb: float | None,
        av_sync_offset_ms: int,
        disk_free_gb: float | None,
        disk_free_estimate_messages: int | None,
        encoder_status: str,
        uptime_seconds: float,
        last_error: str,
    ) -> np.ndarray:
        """DEVELOPER: live hardware/process telemetry for on-site debugging
        (see main.py's _developer_snapshot for where every value here
        comes from -- vcgencmd/psutil-free stdlib reads, best-effort and
        None where a reading genuinely isn't available off-Pi).
        Populates self.developer_rects the same way render_settings
        populates settings_rects.
        """
        self.developer_rects = {}
        canvas, draw = self._admin_canvas()
        ink = _rgba(self._ADMIN_INK)
        gold_dark = _rgba(self._ADMIN_GOLD_DARK)
        dim = self._blend_full(self._ADMIN_BG, self._ADMIN_INK, 165)

        # Title bar.
        y = self.h * (28 / 576)
        diag_font = self._font("heading", int(self.h * (14 / 576)))
        self._draw_tracked_left(draw, self.w * (36 / 1024), y, "DIAGNOSTICS · V1.4.0", diag_font, gold_dark, spacing=2)
        title_font = self._font("script", int(self.h * (44 / 576)))
        draw.text((self.w / 2, y), "Developer", font=title_font, fill=ink, anchor="mm")
        status_font = self._font("heading_medium", int(self.h * (15 / 576)))
        status_text = "ERROR" if last_error and last_error.lower() != "none" else "READY"
        status_color = self._ADMIN_RED if status_text == "ERROR" else self._ADMIN_GOOD
        status_w = draw.textlength(status_text, font=status_font)
        status_x1 = self.w * (988 / 1024)
        draw.text((status_x1, y), status_text, font=status_font, fill=gold_dark, anchor="rm")
        dot_r = self.h * 0.0078
        dot_cx = status_x1 - status_w - self.w * 0.014
        draw.ellipse([dot_cx - dot_r, y - dot_r, dot_cx + dot_r, y + dot_r], fill=_rgba(status_color))

        draw.line([(self.w * (36 / 1024), self.h * (80 / 576)), (self.w * (988 / 1024), self.h * (80 / 576))], fill=(*self._ADMIN_GOLD, 140), width=1)

        # Camera / Microphone / Receiver row.
        row_y = self.h * (100 / 576)
        label_font = self._font("heading", int(self.h * (13 / 576)))
        mono_small = self._font("heading_medium", int(self.h * (13 / 576)))

        cam_x = self.w * (36 / 1024)
        self._draw_tracked_left(draw, cam_x, row_y, "CAMERA", label_font, gold_dark, spacing=3)
        cam_w, cam_h = self.w * (246 / 1024), self.h * (139 / 576)
        cam_y = row_y + self.h * (26 / 576)
        # A small fixed-size thumbnail, not the full-width guest
        # _camera_panel -- built directly rather than reusing that helper.
        draw.rectangle([cam_x, cam_y, cam_x + cam_w, cam_y + cam_h], fill=(34, 48, 31, 255))
        if camera_frame_bgr is not None:
            fitted = _fit_cover_bgr(camera_frame_bgr, int(cam_w), int(cam_h))
            frame_img = Image.fromarray(cv2.cvtColor(fitted, cv2.COLOR_BGR2RGB)).convert("RGBA")
            canvas.alpha_composite(frame_img, (int(cam_x), int(cam_y)))
        draw.rectangle([cam_x, cam_y, cam_x + cam_w, cam_y + cam_h], outline=(*self._ADMIN_GOLD, 153), width=1)
        strip_h = self.h * (26 / 576)
        strip = Image.new("RGBA", (int(cam_w), int(strip_h)), (22, 34, 26, 184))
        canvas.alpha_composite(strip, (int(cam_x), int(cam_y + cam_h - strip_h)))
        draw.text((cam_x + self.w * 0.008, cam_y + cam_h - strip_h / 2), "1024×576", font=mono_small, fill=_rgba((245, 239, 227)), anchor="lm")
        draw.text((cam_x + cam_w - self.w * 0.008, cam_y + cam_h - strip_h / 2), f"{preview_fps:.0f} fps", font=mono_small, fill=_rgba((245, 239, 227)), anchor="rm")
        cap_y = cam_y + cam_h + self.h * (18 / 576)
        draw.text((cam_x, cap_y), f"{camera_device} · mjpeg", font=mono_small, fill=(*dim[:3], 191), anchor="lm")
        draw.text((cam_x + cam_w, cap_y), f"drop {dropped_frames}", font=mono_small, fill=(*dim[:3], 191), anchor="rm")

        # Columns follow the design's 300px / 1fr / 240px grid -- cam_w
        # above is the inner preview box's own width (left-aligned within
        # the 300px column), not the column width itself.
        mic_x = self.w * (36 / 1024) + self.w * (300 / 1024) + self.w * (28 / 1024)
        mic_w = self.w * (952 / 1024) - self.w * (300 / 1024) - self.w * (240 / 1024) - self.w * (56 / 1024)
        self._draw_tracked_left(draw, mic_x, row_y, "MICROPHONE", label_font, gold_dark, spacing=3)
        draw.text((mic_x + mic_w, row_y), mic_device, font=mono_small, fill=(*dim[:3], 179), anchor="rm")

        peak = max(-60.0, min(0.0, peak_db))
        room = max(-60.0, min(0.0, room_base_db))
        clipping = peak >= -1
        hot = peak >= -6
        verdict = "Clipping" if clipping else ("Hot" if hot else "Good")
        verdict_color = self._ADMIN_RED if clipping else ((192, 138, 46) if hot else self._ADMIN_GOOD)
        fill_color = verdict_color

        peak_y = row_y + self.h * (44 / 576)
        peak_font = self._font("heading_medium", int(self.h * (38 / 576)))
        draw.text((mic_x, peak_y), f"{peak:.1f} dB", font=peak_font, fill=ink, anchor="lm")
        peak_w = draw.textlength(f"{peak:.1f} dB", font=peak_font)
        peak_label_font = self._font("heading_medium", int(self.h * (15 / 576)))
        draw.text((mic_x + peak_w + self.w * 0.012, peak_y), "peak", font=peak_label_font, fill=(*dim[:3], 166), anchor="lm")
        vfont = self._font("heading", int(self.h * (16 / 576)))
        draw.text((mic_x + mic_w, peak_y), verdict.upper(), font=vfont, fill=_rgba(verdict_color), anchor="rm")

        bar_y = peak_y + self.h * (36 / 576)
        bar_h = self.h * (18 / 576)
        pct = lambda db: (db + 60) / 60  # noqa: E731
        bar_overlay = Image.new("RGBA", (max(1, int(mic_w)), max(1, int(bar_h))), (0, 0, 0, 0))
        bdraw = ImageDraw.Draw(bar_overlay)
        bdraw.rounded_rectangle([0, 0, mic_w, bar_h], radius=int(bar_h / 2), fill=(*self._ADMIN_INK, 36))
        canvas.alpha_composite(bar_overlay, (int(mic_x), int(bar_y)))
        level_w = mic_w * max(0.0, min(1.0, pct(peak)))
        if level_w > 1:
            draw.rounded_rectangle([mic_x, bar_y, mic_x + level_w, bar_y + bar_h], radius=int(bar_h / 2), fill=_rgba(fill_color))
        room_x = mic_x + mic_w * max(0.0, min(1.0, pct(room)))
        draw.line([(room_x, bar_y), (room_x, bar_y + bar_h)], fill=(*self._ADMIN_INK, 128), width=2)

        scale_y = bar_y + bar_h + self.h * (16 / 576)
        scale_font = self._font("heading_medium", int(self.h * (12 / 576)))
        for i, label in enumerate(("-60", "-40", "-20", "-12", "-6", "0 dB")):
            frac = i / 5
            draw.text((mic_x + mic_w * frac, scale_y), label, font=scale_font, fill=(*dim[:3], 153), anchor=("lm" if i == 0 else ("rm" if i == 5 else "mm")))

        stat_y = scale_y + self.h * (28 / 576)
        headroom = abs(peak)
        stats = [("Room base", f"{room:.1f} dB", ink), ("Headroom", f"{headroom:.1f} dB", ink), ("Clips (60s)", str(clip_count), _rgba(self._ADMIN_RED) if clip_count > 0 else ink)]
        stat_col_w = mic_w / 3
        for i, (label, value, color) in enumerate(stats):
            sx = mic_x + stat_col_w * i
            sfont = self._font("heading", int(self.h * (12 / 576)))
            self._draw_tracked_left(draw, sx, stat_y, label.upper(), sfont, (*dim[:3], 153), spacing=2)
            vfont2 = self._font("heading_medium", int(self.h * (17 / 576)))
            draw.text((sx, stat_y + self.h * (22 / 576)), value, font=vfont2, fill=color, anchor="lm")

        rec_x = mic_x + mic_w + self.w * (28 / 1024)
        rec_w = self.w * (240 / 1024)
        self._draw_tracked_left(draw, rec_x, row_y, "RECEIVER", label_font, gold_dark, spacing=3)
        card_y0 = row_y + self.h * (26 / 576)
        card_h = self.h * (139 / 576)
        hook_color = self._ADMIN_GOOD if hook_off_hook else self._ADMIN_GOLD_DARK
        draw.rounded_rectangle([rec_x, card_y0, rec_x + rec_w, card_y0 + card_h], radius=int(self.h * 0.01), outline=(*self._ADMIN_GOLD, 255), width=1)
        card_cx, card_cy = rec_x + rec_w / 2, card_y0 + card_h * 0.38
        dot_r2 = self.h * 0.0104
        glow = self._pulse(now, period=2.0, lo=0.5, hi=1.0) if hook_off_hook else 1.0
        if hook_off_hook:
            halo = Image.new("RGBA", (int(dot_r2 * 6), int(dot_r2 * 6)), (0, 0, 0, 0))
            hdraw = ImageDraw.Draw(halo)
            hc = dot_r2 * 3
            hdraw.ellipse([0, 0, hc * 2, hc * 2], fill=(*hook_color, int(90 * glow)))
            canvas.alpha_composite(halo, (int(card_cx - hc), int(card_cy - hc)))
        draw.ellipse([card_cx - dot_r2, card_cy - dot_r2, card_cx + dot_r2, card_cy + dot_r2], fill=_rgba(hook_color))
        hook_font = self._font("heading", int(self.h * (22 / 576)))
        hook_label = "OFF HOOK" if hook_off_hook else "ON HOOK"
        self._draw_tracked(draw, card_cx, card_cy + self.h * (30 / 576), hook_label, hook_font, _rgba(hook_color), spacing=4)
        gpio_font = self._font("heading_medium", int(self.h * (13 / 576)))
        draw.text((card_cx, card_cy + self.h * (58 / 576)), f"GPIO 17 · {hook_pin_state}", font=gpio_font, fill=(*dim[:3], 179), anchor="mm")
        under_y = card_y0 + card_h + self.h * (18 / 576)
        draw.text((rec_x, under_y), f"held {hook_held_seconds:.1f}s", font=mono_small, fill=(*dim[:3], 191), anchor="lm")
        draw.text((rec_x + rec_w, under_y), "bounce 0", font=mono_small, fill=(*dim[:3], 191), anchor="rm")

        # Metrics strip.
        strip_y0 = self.h * (306 / 576)
        draw.line([(self.w * (36 / 1024), strip_y0), (self.w * (988 / 1024), strip_y0)], fill=(*self._ADMIN_GOLD, 115), width=1)
        throttle_text = "n/a" if throttled is None else ("throttled" if throttled else "none")
        throttle_color = ink if throttled is None else (_rgba(self._ADMIN_RED) if throttled else _rgba(self._ADMIN_GOOD))
        cpu_temp_text = "n/a" if cpu_temp_c is None else f"{cpu_temp_c:.1f} °C"
        cpu_load_text = "n/a" if cpu_load_pct is None else f"{cpu_load_pct:.0f}%"
        mem_text = "n/a" if mem_used_mb is None or mem_total_mb is None else f"{mem_used_mb:.0f} / {mem_total_mb:.0f} MB"
        disk_text = "n/a" if disk_free_gb is None else f"{disk_free_gb:.1f} GB"
        if disk_free_estimate_messages is not None:
            disk_text += f" · ~{disk_free_estimate_messages}"

        def _fmt_uptime(seconds: float) -> str:
            h_, rem = divmod(max(0, int(seconds)), 3600)
            m_ = rem // 60
            return f"{h_}h {m_}m"

        metrics = [
            ("Overlay draw", f"{overlay_draw_ms:.1f} ms", ink),
            ("CPU temp", cpu_temp_text, ink),
            ("Throttling", throttle_text, throttle_color),
            ("CPU load", cpu_load_text, ink),
            ("Memory", mem_text, ink),
            ("A/V sync", f"{av_sync_offset_ms} ms", ink),
            ("Disk free", disk_text, ink),
            ("Encoder", encoder_status, ink),
            ("Uptime", _fmt_uptime(uptime_seconds), ink),
            ("Last error", last_error or "none", ink if last_error and last_error.lower() != "none" else (*dim[:3], 179)),
        ]
        cols = 5
        col_w = self.w * (952 / 1024) / cols
        row_gap = self.h * (42 / 576)
        m_font = self._font("heading", int(self.h * (12 / 576)))
        v_font = self._font("heading_medium", int(self.h * (17 / 576)))
        m_top = strip_y0 + self.h * (16 / 576)
        for i, (label, value, color) in enumerate(metrics):
            col, rownum = i % cols, i // cols
            mx = self.w * (36 / 1024) + col_w * col
            my = m_top + row_gap * rownum
            self._draw_tracked_left(draw, mx, my, label.upper(), m_font, (*dim[:3], 153), spacing=2)
            draw.text((mx, my + self.h * (22 / 576)), str(value), font=v_font, fill=color, anchor="lm")

        # Bottom row: notes + action buttons.
        bottom_y0 = self.h * (424 / 576)
        notes_w = self.w * (952 / 1024) - self.w * (212 / 1024) - self.w * (24 / 1024)
        notes_h = self.h * (137 / 576)
        draw.rounded_rectangle(
            [self.w * (36 / 1024), bottom_y0, self.w * (36 / 1024) + notes_w, bottom_y0 + notes_h],
            radius=int(self.h * 0.017), outline=(*self._ADMIN_GOLD, 179), width=1,
        )
        notes_font = self._font("heading_medium", int(self.h * (13 / 576)))
        draw.text(
            (self.w * (36 / 1024) + self.w * 0.014, bottom_y0 + self.h * 0.021),
            "Attendant notes -- mic gain lowered one notch at 18:40, room got loud after toasts.",
            font=notes_font, fill=(*dim[:3], 204), anchor="la",
        )

        btn_x1 = self.w * (988 / 1024)
        btn_w = self.w * (212 / 1024)
        btn_h = self.h * (44 / 576)
        gap = self.h * (10 / 576)
        by = bottom_y0
        self._admin_pill_button(draw, btn_x1 - btn_w, by, btn_x1, by + btn_h, "Back")
        self.developer_rects["back"] = (int(btn_x1 - btn_w), int(by), int(btn_x1), int(by + btn_h))
        by += btn_h + gap
        self._admin_pill_button(draw, btn_x1 - btn_w, by, btn_x1, by + btn_h, "Export logs")
        self.developer_rects["export_logs"] = (int(btn_x1 - btn_w), int(by), int(btn_x1), int(by + btn_h))
        by += btn_h + gap
        self._admin_pill_button(draw, btn_x1 - btn_w, by, btn_x1, by + btn_h, "Restart booth", danger=True)
        self.developer_rects["restart_booth"] = (int(btn_x1 - btn_w), int(by), int(btn_x1), int(by + btn_h))

        return self._to_bgr(canvas)


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: float) -> list[str]:
    """Greedy word wrap against a rendered pixel width.

    Error reasons come from validation.py/recorder.py and are not
    length-bounded, so the ERROR card wraps rather than letting a long
    reason run off both edges. A single word longer than max_width is
    left on its own line rather than being broken mid-word -- a
    filesystem path is more legible unbroken than hyphenated.
    """
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _cover_resize(img: Image.Image, box_w: int, box_h: int) -> Image.Image:
    src_w, src_h = img.size
    scale = max(box_w / src_w, box_h / src_h)
    new_w, new_h = max(1, int(src_w * scale)), max(1, int(src_h * scale))
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    x0 = (new_w - box_w) // 2
    y0 = (new_h - box_h) // 2
    return resized.crop((x0, y0, x0 + box_w, y0 + box_h))


def _contain_resize(img: Image.Image, box_w: int, box_h: int) -> Image.Image:
    """Scale down to fit within box_w x box_h, preserving aspect ratio."""
    src_w, src_h = img.size
    scale = min(box_w / src_w, box_h / src_h)
    new_w, new_h = max(1, int(src_w * scale)), max(1, int(src_h * scale))
    return img.resize((new_w, new_h), Image.LANCZOS)


def _fit_cover_bgr(frame_bgr: np.ndarray, box_w: int, box_h: int) -> np.ndarray:
    h, w = frame_bgr.shape[:2]
    if w <= 0 or h <= 0 or box_w <= 0 or box_h <= 0:
        return np.zeros((max(1, box_h), max(1, box_w), 3), dtype=np.uint8)
    scale = max(box_w / w, box_h / h)
    new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
    resized = cv2.resize(frame_bgr, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    x0 = max(0, (new_w - box_w) // 2)
    y0 = max(0, (new_h - box_h) // 2)
    return resized[y0 : y0 + box_h, x0 : x0 + box_w]
