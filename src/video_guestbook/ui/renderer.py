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
from PIL import Image, ImageDraw, ImageFont

from video_guestbook.ui.theme import Theme

# Bottom-to-top mic meter segment colors (matches the design's VU-style
# gradient: quiet=green, loud=red).
_METER_SEGMENT_COLOR_KEYS = (
    ["meter_green"] * 7 + ["meter_gold"] * 3 + ["meter_amber"] * 1 + ["meter_red"] * 2
)
_METER_SEGMENTS = len(_METER_SEGMENT_COLOR_KEYS)
_UNLIT_OPACITY = 0.14


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
        box_w, box_h = int(self.w * 0.22), int(self.h * 0.68)
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

    def _mic_meter(
        self,
        canvas: Image.Image,
        x: float,
        y: float,
        width: float,
        height: float,
        fraction: float,
    ) -> None:
        draw = ImageDraw.Draw(canvas)
        lit = round(max(0.0, min(1.0, fraction)) * _METER_SEGMENTS)
        gap = 3
        seg_h = (height - gap * (_METER_SEGMENTS - 1)) / _METER_SEGMENTS
        panel_bg = self.theme.colors["bg_cream"]
        for i, color_key in enumerate(_METER_SEGMENT_COLOR_KEYS):
            # index 0 is the bottom segment.
            seg_y = y + height - (i + 1) * (seg_h + gap) + gap
            color = self.theme.colors[color_key]
            lit_segment = i < lit
            fill = _rgba(color) if lit_segment else self._blend_full(panel_bg, color, int(255 * _UNLIT_OPACITY))
            draw.rounded_rectangle([x, seg_y, x + width, seg_y + seg_h], radius=2, fill=fill)

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
        t = self.theme
        canvas = self._canvas()
        draw = ImageDraw.Draw(canvas)
        ink = _rgba(t.colors["ink"])
        gold = _rgba(t.colors["gold"])

        photo_reserved = 0.0
        if self._couple_photo is not None:
            photo_reserved = self._couple_photo.width + self.w * 0.05

        content_cx = (self.w - photo_reserved) / 2
        y = self.h * 0.12

        eyebrow_font = self._font("heading", int(self.h * 0.052))
        self._draw_tracked(draw, content_cx, y, t.text["welcome_eyebrow"].upper(), eyebrow_font, ink, spacing=4)
        y += self.h * 0.075

        self._divider(draw, content_cx, y, self.w * 0.34, gold)
        y += self.h * 0.075

        script_font = self._font("script", int(self.h * 0.24))
        draw.text((content_cx, y), t.text["welcome_title"], font=script_font, fill=ink, anchor="ma")
        y += self.h * 0.24

        self._divider(draw, content_cx, y, self.w * 0.28, gold)
        y += self.h * 0.075

        names_font = self._font("heading", int(self.h * 0.075))
        self._draw_tracked(draw, content_cx, y, t.couple_names.upper(), names_font, ink, spacing=3)
        y += self.h * 0.09

        date_font = self._font("heading_medium", int(self.h * 0.048))
        self._draw_tracked(draw, content_cx, y, t.event_date.upper(), date_font, gold, spacing=5)
        y += self.h * 0.09

        prompt_font = self._font("heading", int(self.h * 0.065))
        self._draw_tracked(draw, content_cx, y, t.text["ready_prompt_1"].upper(), prompt_font, ink, spacing=3)
        y += self.h * 0.075

        prompt2_font = self._font("heading_medium", int(self.h * 0.042))
        prompt2_color = self._blend_full(t.colors["bg_cream"], t.colors["ink"], 210)
        self._draw_tracked(draw, content_cx, y, t.text["ready_prompt_2"].upper(), prompt2_font, prompt2_color, spacing=2)

        if self._couple_photo is not None:
            photo_x = self.w - photo_reserved + self.w * 0.02
            photo_y = (self.h - self._couple_photo.height) / 2 - self.h * 0.04
            border_pad = 8
            draw.rounded_rectangle(
                [photo_x - border_pad, photo_y - border_pad,
                 photo_x + self._couple_photo.width + border_pad, photo_y + self._couple_photo.height + border_pad],
                radius=12, outline=gold, width=1, fill=_rgba(t.colors["bg_cream"]),
            )
            canvas.paste(self._couple_photo, (int(photo_x), int(photo_y)))

        self._footer_bar(canvas, t.text["ready_footer"], now=now, pulse_hearts=True)
        return self._to_bgr(canvas)

    def render_countdown(
        self,
        now: float,
        remaining_seconds: float,
        total_seconds: int,
        mic_status: str | None,
        mic_fraction: float | None,
    ) -> np.ndarray:
        t = self.theme
        canvas = self._canvas()
        draw = ImageDraw.Draw(canvas)
        ink = _rgba(t.colors["ink"])
        gold_rgb = t.colors["gold"]
        gold = _rgba(gold_rgb)
        cream = t.colors["bg_cream"]

        cx = self.w / 2
        y = self.h * 0.035

        header_font = self._font("heading", int(self.h * 0.036))
        names_u, date_u = t.couple_names.upper(), t.event_date.upper()
        names_w = self._tracked_width(draw, names_u, header_font, 3)
        date_w = self._tracked_width(draw, date_u, header_font, 3)
        diamond_gap = self.w * 0.028
        total_header_w = names_w + diamond_gap * 2 + date_w
        start_x = cx - total_header_w / 2
        end_x = self._draw_tracked_left(draw, start_x, y, names_u, header_font, gold, spacing=3)
        self._draw_diamond(draw, end_x + diamond_gap / 2, y, 4, fill=gold)
        self._draw_tracked_left(draw, end_x + diamond_gap, y, date_u, header_font, gold, spacing=3)
        y += self.h * 0.085

        script_font = self._font("script", int(self.h * 0.14))
        draw.text((cx, y), t.text["get_ready_title"], font=script_font, fill=ink, anchor="ma")
        y += self.h * 0.175

        tips = [
            ("camera", self._icon_camera, t.text["tip_camera"]),
            ("mic", self._icon_mic, t.text["tip_speak"]),
            ("smile", self._icon_smile, t.text["tip_smile"]),
        ]
        tips_w = self.w * 0.86
        col_w = tips_w / 3
        tips_x0 = cx - tips_w / 2
        icon_size = self.h * 0.1
        tip_label_font = self._font("heading", int(self.h * 0.024))
        draw.line([(tips_x0, y), (tips_x0 + tips_w, y)], fill=gold, width=1)
        row_h = self.h * 0.145
        for i, (key, icon_fn, label) in enumerate(tips):
            col_cx = tips_x0 + col_w * (i + 0.5)
            self._draw_tip_icon(canvas, draw, key, icon_fn, col_cx, y + row_h * 0.36, icon_size, ink)
            self._draw_tracked(draw, col_cx, y + row_h * 0.74, label.upper(), tip_label_font, ink, spacing=1.0)
            if i > 0:
                draw.line(
                    [(tips_x0 + col_w * i, y), (tips_x0 + col_w * i, y + row_h)],
                    fill=self._blend_full(cream, gold_rgb, 110), width=1,
                )
        y += row_h
        draw.line([(tips_x0, y), (tips_x0 + tips_w, y)], fill=gold, width=1)
        y += self.h * 0.03

        caption_font = self._font("heading_medium", int(self.h * 0.03))
        self._draw_tracked(draw, cx, y, t.text["countdown_caption"].upper(), caption_font, ink, spacing=2)
        y += self.h * 0.055

        ring_r = self.h * 0.105
        ring_cx = cx
        ring_cy = y + ring_r
        fraction = 0.0 if total_seconds <= 0 else 1 - max(0.0, remaining_seconds) / total_seconds
        bbox = [ring_cx - ring_r, ring_cy - ring_r, ring_cx + ring_r, ring_cy + ring_r]
        track_width = int(self.h * 0.013)
        draw.arc(bbox, 0, 360, fill=self._blend_full(cream, gold_rgb, 70), width=track_width)
        draw.arc(bbox, -90, -90 + 360 * fraction, fill=gold, width=track_width)

        count = math.ceil(remaining_seconds)
        label = str(count) if count > 0 else t.text["countdown_go"]
        self._draw_pop_number(canvas, ring_cx, ring_cy, label, now, count)
        y = ring_cy + ring_r + self.h * 0.03

        if mic_status and mic_fraction is not None:
            note_font = self._font("heading_medium", int(self.h * 0.03))
            draw.text((cx, y), mic_status.upper(), font=note_font, fill=ink, anchor="ma")
            bar_w, bar_h = self.w * 0.22, self.h * 0.015
            bar_x, bar_y = cx - bar_w / 2, y + self.h * 0.045
            draw.rounded_rectangle(
                [bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], radius=3,
                fill=self._blend_full(cream, t.colors["ink"], 30),
            )
            fill_w = bar_w * max(0.0, min(1.0, mic_fraction))
            fill_color = t.colors["recording_red"] if mic_fraction > 0.9 else t.colors["gold"]
            if fill_w > 0:
                draw.rounded_rectangle([bar_x, bar_y, bar_x + fill_w, bar_y + bar_h], radius=3, fill=_rgba(fill_color))

        self._footer_bar(canvas, t.text["countdown_footer"])
        return self._to_bgr(canvas)

    def _draw_pop_number(self, canvas: Image.Image, cx: float, cy: float, label, now: float, count_key) -> None:
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

        tmp = Image.new("RGBA", (int(self.w * 0.5), int(self.h * 0.5)), (0, 0, 0, 0))
        tdraw = ImageDraw.Draw(tmp)
        tcx, tcy = tmp.width / 2, tmp.height / 2
        tdraw.text((tcx, tcy), str(label), font=font, fill=_rgba(self.theme.colors["ink"]), anchor="mm")
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
        elapsed: int,
        remaining: int,
        mic_fraction: float,
        mic_clipped: bool,
    ) -> np.ndarray:
        t = self.theme
        canvas = self._canvas(dimmed=True)
        draw = ImageDraw.Draw(canvas)
        ink = _rgba(t.colors["ink"])
        gold = t.colors["gold"]

        pad = self.w * 0.035
        top_h = self.h * 0.16
        footer_h = max(48, int(self.h * 0.135))
        content_y0 = pad + top_h
        content_y1 = self.h - footer_h - pad

        cream = t.colors["bg_cream"]
        pulse_alpha = int(255 * self._pulse(now, period=1.2, lo=0.35, hi=1.0))
        dot_r = self.h * 0.018
        dot_cx, dot_cy = pad * 2.2 + dot_r, pad + top_h * 0.4
        draw.ellipse(
            [dot_cx - dot_r, dot_cy - dot_r, dot_cx + dot_r, dot_cy + dot_r],
            fill=self._blend_full(cream, t.colors["recording_red"], pulse_alpha),
        )
        rec_font = self._font("heading", int(self.h * 0.05))
        self._draw_tracked_left(
            draw, dot_cx + dot_r * 2.4, dot_cy, t.text["recording_label"].upper(), rec_font,
            _rgba(t.colors["recording_red"]), spacing=4,
        )

        names_font = self._font("heading", int(self.h * 0.045))
        self._draw_tracked(draw, self.w / 2, dot_cy, t.couple_names.upper(), names_font, ink, spacing=2)

        timer_label_font = self._font("heading_medium", int(self.h * 0.026))
        timer_val_font = self._font("heading", int(self.h * 0.07))
        timer_label_color = self._blend_full(cream, t.colors["ink"], 200)

        def _fmt(seconds: int) -> str:
            m, s = divmod(max(0, seconds), 60)
            return f"{m}:{s:02d}"

        right_x = self.w - pad * 2.2
        draw.text((right_x, dot_cy - self.h * 0.035), "REMAINING", font=timer_label_font, fill=timer_label_color, anchor="ra")
        remain_color = t.colors["recording_red"] if remaining <= 10 else t.colors["ink"]
        draw.text((right_x, dot_cy + self.h * 0.005), _fmt(remaining), font=timer_val_font, fill=_rgba(remain_color), anchor="ra")

        elapsed_x = right_x - self.w * 0.17
        draw.text((elapsed_x, dot_cy - self.h * 0.035), "ELAPSED", font=timer_label_font, fill=timer_label_color, anchor="ra")
        draw.text((elapsed_x, dot_cy + self.h * 0.005), _fmt(elapsed), font=timer_val_font, fill=ink, anchor="ra")

        meter_w = self.w * 0.09
        cam_w = (self.w - pad * 3 - meter_w)
        cam_h = content_y1 - content_y0
        cam_x0, cam_y0 = pad, content_y0

        panel_bg = (34, 48, 31)
        draw.rectangle([cam_x0, cam_y0, cam_x0 + cam_w, cam_y0 + cam_h], fill=_rgba(panel_bg))
        if camera_frame_bgr is not None:
            fitted = _fit_cover_bgr(camera_frame_bgr, int(cam_w), int(cam_h))
            fitted = cv2.flip(fitted, 1)
            frame_img = Image.fromarray(cv2.cvtColor(fitted, cv2.COLOR_BGR2RGB)).convert("RGBA")
            canvas.alpha_composite(frame_img, (int(cam_x0), int(cam_y0)))
        else:
            cam_note_font = self._font("heading_medium", int(self.h * 0.03))
            draw.text((cam_x0 + cam_w / 2, cam_y0 + cam_h / 2), t.text["cam_placeholder"].upper(), font=cam_note_font, fill=_rgba((245, 239, 227)), anchor="mm")

        draw.rectangle([cam_x0, cam_y0, cam_x0 + cam_w, cam_y0 + cam_h], outline=self._blend_full(panel_bg, gold, 150), width=1)
        bracket = self.h * 0.03
        for bx, by, dx, dy in (
            (cam_x0 + 8, cam_y0 + 8, 1, 1), (cam_x0 + cam_w - 8, cam_y0 + 8, -1, 1),
            (cam_x0 + 8, cam_y0 + cam_h - 8, 1, -1), (cam_x0 + cam_w - 8, cam_y0 + cam_h - 8, -1, -1),
        ):
            draw.line([(bx, by), (bx + bracket * dx, by)], fill=_rgba(gold), width=2)
            draw.line([(bx, by), (bx, by + bracket * dy)], fill=_rgba(gold), width=2)

        meter_x0 = cam_x0 + cam_w + pad
        draw.rounded_rectangle(
            [meter_x0, content_y0, meter_x0 + meter_w, content_y0 + cam_h], radius=4,
            outline=_rgba(gold), fill=_rgba(cream),
        )
        mic_label_font = self._font("heading", int(self.h * 0.028))
        mic_label_color = _rgba(t.colors["recording_red"]) if mic_clipped else ink
        draw.text((meter_x0 + meter_w / 2, content_y0 + self.h * 0.03), t.text["mic_label"].upper(), font=mic_label_font, fill=mic_label_color, anchor="ma")
        self._mic_meter(
            canvas, meter_x0 + meter_w * 0.18, content_y0 + self.h * 0.075,
            meter_w * 0.64, cam_h - self.h * 0.16, mic_fraction,
        )
        sub_font = self._font("heading_medium", int(self.h * 0.024))
        sub_color = self._blend_full(cream, t.colors["ink"], 180)
        draw.text((meter_x0 + meter_w / 2, content_y0 + cam_h - self.h * 0.03), t.text["mic_sublabel"].upper(), font=sub_font, fill=sub_color, anchor="ma")

        self._footer_bar(canvas, t.text["recording_footer"], now=now, pulse_hearts=True)
        return self._to_bgr(canvas)

    def render_saving(self, now: float) -> np.ndarray:
        t = self.theme
        canvas = self._canvas()
        draw = ImageDraw.Draw(canvas)
        ink = _rgba(t.colors["ink"])
        gold_rgb = t.colors["gold"]
        gold = _rgba(gold_rgb)
        cream = t.colors["bg_cream"]
        cx = self.w / 2

        y = self.h * 0.05
        names_font = self._font("heading", int(self.h * 0.05))
        self._draw_tracked(draw, cx, y, t.couple_names.upper(), names_font, ink, spacing=2)
        y += self.h * 0.065
        date_font = self._font("heading_medium", int(self.h * 0.032))
        self._draw_tracked(draw, cx, y, t.event_date.upper(), date_font, gold, spacing=4)
        y += self.h * 0.075

        script_font = self._font("script", int(self.h * 0.12))
        draw.text((cx, y), t.text["saving_title"], font=script_font, fill=ink, anchor="ma")
        y += self.h * 0.15

        ring_r = self.h * 0.1
        ring_cy = y + ring_r
        bbox = [cx - ring_r, ring_cy - ring_r, cx + ring_r, ring_cy + ring_r]
        breathe = self._pulse(now, period=2.0, lo=0.4, hi=1.0)
        track_width = int(self.h * 0.011)
        draw.arc(bbox, 0, 360, fill=self._blend_full(cream, gold_rgb, int(70 * breathe)), width=track_width)
        sweep_deg = (now * 240) % 360
        draw.arc(bbox, sweep_deg, sweep_deg + 100, fill=gold, width=track_width)
        y += ring_r * 2 + self.h * 0.035

        sub_font = self._font("heading_medium", int(self.h * 0.036))
        sub_color = self._blend_full(cream, t.colors["ink"], 200)
        draw.text((cx, y), t.text["saving_sub"], font=sub_font, fill=sub_color, anchor="ma")

        self._footer_bar(canvas, t.text["saving_footer"])
        return self._to_bgr(canvas)

    def render_saved(self, now: float, time_in_state: float) -> np.ndarray:
        t = self.theme
        canvas = self._canvas()
        draw = ImageDraw.Draw(canvas)
        ink = _rgba(t.colors["ink"])
        gold_rgb = t.colors["gold"]
        gold = _rgba(gold_rgb)
        cream = t.colors["bg_cream"]
        cx = self.w / 2

        y = self.h * 0.03
        names_font = self._font("heading", int(self.h * 0.045))
        self._draw_tracked(draw, cx, y, t.couple_names.upper(), names_font, ink, spacing=2)
        y += self.h * 0.06
        date_font = self._font("heading_medium", int(self.h * 0.03))
        self._draw_tracked(draw, cx, y, t.event_date.upper(), date_font, gold, spacing=4)
        y += self.h * 0.065

        script_font = self._font("script", int(self.h * 0.14))
        draw.text((cx, y), t.text["thanks_title"], font=script_font, fill=ink, anchor="ma")
        y += self.h * 0.165

        ring_r = self.h * 0.09
        ring_cy = y + ring_r
        bbox = [cx - ring_r, ring_cy - ring_r, cx + ring_r, ring_cy + ring_r]
        draw.ellipse(bbox, outline=gold, width=int(self.h * 0.009))
        self._draw_checkmark(draw, cx, ring_cy, ring_r * 0.62, time_in_state, ink)
        y += ring_r * 2 + self.h * 0.03

        sub_font = self._font("heading", int(self.h * 0.036))
        draw.text((cx, y), t.text["thanks_sub"].upper(), font=sub_font, fill=ink, anchor="ma")
        y += self.h * 0.06

        self._divider(draw, cx, y, self.w * 0.26, gold)
        y += self.h * 0.05

        sub2_font = self._font("heading_medium", int(self.h * 0.03))
        sub2_color = self._blend_full(cream, t.colors["ink"], 220)
        draw.text((cx, y), t.text["thanks_sub2"], font=sub2_font, fill=sub2_color, anchor="ma")
        y += self.h * 0.058

        close_font = self._font("script", int(self.h * 0.085))
        draw.text((cx, y), t.text["thanks_close"], font=close_font, fill=gold, anchor="ma")

        self._footer_bar(canvas, t.text["thanks_footer"])
        return self._to_bgr(canvas)

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
        t = self.theme
        canvas = self._canvas()
        draw = ImageDraw.Draw(canvas)
        cx = self.w / 2
        red = t.colors["recording_red"]

        y = self.h * 0.3
        title_font = self._font("heading", int(self.h * 0.075))
        self._draw_tracked(draw, cx, y, t.text["error_title"].upper(), title_font, _rgba(red), spacing=3)
        y += self.h * 0.12

        if reason:
            reason_font = self._font("heading_medium", int(self.h * 0.04))
            reason_color = self._blend_full(t.colors["bg_cream"], t.colors["ink"], 210)
            draw.text((cx, y), reason, font=reason_font, fill=reason_color, anchor="ma")

        self._footer_bar(canvas, t.text["error_footer"])
        return self._to_bgr(canvas)


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
