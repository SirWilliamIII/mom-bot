import copy
import os
import time
import threading

from PIL import Image, ImageDraw, ImageFont
from ui.framework import (
    Components,
    Layout,
    PigletCharacter,
    ThemeRegistry,
    alert_color_for_level,
    hint_for_status,
    infer_turn,
    pulse,
)
from ui.utils import ColorUtils, ImageUtils, TextUtils


class DisplayState:
    def __init__(self):
        self._lock = threading.Lock()
        self.turn = "sleep"  # "green" | "red" | "amber" | "sleep" | "paused"
        self.status = "Hello"
        self.emoji = "🐷"
        self.text = "Press and hold to talk to me!"
        self.ui_theme = "piglet_candy"
        self.battery_level = 100
        self.battery_color = (85, 255, 0)
        self.rgb_color = (0, 0, 85)
        self.status_since = time.time()
        self.scroll_top = 0
        self.scroll_speed = 3
        self.alert_text = ""
        self.alert_level = "info"
        self.alert_until = 0.0
        self.image_path = ""
        self.image_obj = None
        self.game_surface = None

    def update(self, **kwargs):
        with self._lock:
            if "turn" in kwargs and kwargs["turn"] is not None:
                self.turn = kwargs["turn"]
            if "text" in kwargs and kwargs["text"] is not None:
                new_text = kwargs["text"]
                if not new_text.startswith(self.text):
                    self.scroll_top = 0
                    TextUtils.clear_cache()
                self.text = new_text
            if "status" in kwargs and kwargs["status"] is not None:
                if kwargs["status"] != self.status:
                    self.status_since = time.time()
                self.status = kwargs["status"]
            if "emoji" in kwargs and kwargs["emoji"] is not None:
                self.emoji = kwargs["emoji"]
            if "ui_theme" in kwargs and kwargs["ui_theme"] is not None:
                self.ui_theme = kwargs["ui_theme"]
            if "battery_level" in kwargs and kwargs["battery_level"] is not None:
                self.battery_level = kwargs["battery_level"]
            if "battery_color" in kwargs and kwargs["battery_color"] is not None:
                self.battery_color = kwargs["battery_color"]
            if "rgb_color" in kwargs and kwargs["rgb_color"] is not None:
                self.rgb_color = kwargs["rgb_color"]
            if "scroll_speed" in kwargs and kwargs["scroll_speed"] is not None:
                self.scroll_speed = kwargs["scroll_speed"]
            if "alert_text" in kwargs and kwargs["alert_text"]:
                self.alert_text = kwargs["alert_text"]
                self.alert_level = kwargs.get("alert_level", "info")
                self.alert_until = time.time() + float(kwargs.get("alert_duration", 2.8))
            if "clear_alert" in kwargs and kwargs["clear_alert"]:
                self.alert_text = ""
                self.alert_until = 0.0
            if "image_path" in kwargs:
                self.image_path = kwargs["image_path"] or ""
                self.image_obj = None
            if "game_surface" in kwargs:
                self.game_surface = kwargs["game_surface"]

    def snapshot(self):
        with self._lock:
            s = copy.copy(self)
        # Don't copy the lock into the snapshot
        s._lock = None
        return s


display_state = DisplayState()


class RenderThread(threading.Thread):
    def __init__(self, board, font_path, fps=30):
        super().__init__(daemon=True)
        self.board = board
        self.font_path = font_path
        self.fps = fps
        self.running = True

        self._render_logo()
        time.sleep(0.5)

        try:
            self.main_font = ImageFont.truetype(self.font_path, 28)
            self.status_font = ImageFont.truetype(self.font_path, 28)
            self.emoji_font = ImageFont.truetype(self.font_path, 44)
            self.battery_font = ImageFont.truetype(self.font_path, 14)
        except Exception:
            self.main_font = ImageFont.load_default()
            self.status_font = self.main_font
            self.emoji_font = self.main_font
            self.battery_font = self.main_font

        self.line_height = sum(self.main_font.getmetrics())
        self._text_cache_img = None
        self._cached_text = None  # (text, theme_name) tuple for cache invalidation

    def _render_logo(self):
        logo_path = os.path.join("assets", "images", "logo.png")
        if os.path.exists(logo_path):
            logo = Image.open(logo_path).convert("RGBA")
            logo = logo.resize(
                (self.board.LCD_WIDTH, self.board.LCD_HEIGHT), Image.LANCZOS
            )
            data = ImageUtils.image_to_rgb565(logo, self.board.LCD_WIDTH, self.board.LCD_HEIGHT)
            self.board.set_backlight(100)
            self.board.draw_image(0, 0, self.board.LCD_WIDTH, self.board.LCD_HEIGHT, data)
        else:
            self.board.fill_screen(0xFCF3)
            self.board.set_backlight(100)

    def run(self):
        interval = 1.0 / self.fps
        while self.running:
            try:
                self._render_frame()
            except Exception as e:
                print(f"[Render] Error: {e}")
            time.sleep(interval)

    def stop(self):
        self.running = False

    def _hourglass_frame(self) -> str:
        frames = ["⌛", "⏳"]
        idx = int(time.time() * 2.8) % len(frames)
        return frames[idx]

    def _thinking_dots(self) -> str:
        n = int(time.time() * 2.5) % 4
        return "THINKING" + "." * n

    def _render_frame(self):
        state = display_state.snapshot()
        W = self.board.LCD_WIDTH
        H = self.board.LCD_HEIGHT

        # Determine turn: explicit if set, else infer from status
        turn = state.turn or infer_turn(state.status)
        theme = ThemeRegistry.turn_theme(turn)
        mood = PigletCharacter.mood_for_status(state.status)

        if state.game_surface is not None:
            data = ImageUtils.image_to_rgb565(state.game_surface, W, H)
            self.board.draw_image(0, 0, W, H, data)
            return

        if state.image_path:
            self._render_image(state, W, H)
            return

        header_h = Layout.HEADER_H
        footer_h = Layout.FOOTER_H

        header = Image.new("RGBA", (W, header_h), theme.background)
        hd = ImageDraw.Draw(header)
        self._render_header(header, hd, state, W, theme, mood)
        self.board.draw_image(
            0, 0, W, header_h,
            ImageUtils.image_to_rgb565(header, W, header_h),
        )

        text_h = H - header_h - footer_h
        text_img = Image.new("RGBA", (W, text_h), theme.panel_alt)
        td = ImageDraw.Draw(text_img)
        pad = Layout.PAD
        Components.draw_panel(
            td,
            pad // 2,
            pad // 2,
            W - pad // 2 - 1,
            text_h - pad // 2 - 1,
            fill=theme.panel,
            border=theme.border,
            radius=12,
        )
        self._render_text_area(text_img, text_h, state, W, theme)
        self.board.draw_image(
            0, header_h, W, text_h,
            ImageUtils.image_to_rgb565(text_img, W, text_h),
        )

        footer_img = Image.new("RGBA", (W, footer_h), theme.panel_alt)
        fd = ImageDraw.Draw(footer_img)
        Components.draw_footer(
            fd,
            width=W,
            height=footer_h,
            hint=hint_for_status(state.status),
            font=self.battery_font,
            theme=theme,
        )
        self.board.draw_image(
            0, header_h + text_h, W, footer_h,
            ImageUtils.image_to_rgb565(footer_img, W, footer_h),
        )

        if state.alert_text and time.time() < state.alert_until:
            self._render_alert(state, W, H, theme)

    def _render_header(self, image, draw, state, width, theme, mood):
        Components.draw_panel(
            draw,
            2,
            2,
            width - 3,
            image.height - 3,
            fill=theme.panel,
            border=theme.border,
            radius=12,
        )

        TextUtils.draw_mixed_text(
            draw, image, state.status.upper(), self.status_font, (20, 6),
            fill=theme.text_soft,
        )

        sub_font = self.battery_font
        is_thinking = (state.status or "").lower() in ("thinking", "think")
        subtitle = self._thinking_dots() if is_thinking else mood.subtitle.upper()
        TextUtils.draw_mixed_text(
            draw,
            image,
            subtitle,
            sub_font,
            (20, 30),
            fill=theme.text_soft,
        )

        self._render_status_glow(draw, image, width, mood)

        emoji_text = self._hourglass_frame() if is_thinking else (
            state.emoji if state.emoji and state.emoji != "🐷" else mood.emoji
        )
        emoji_bbox = self.emoji_font.getbbox(emoji_text)
        emoji_w = emoji_bbox[2] - emoji_bbox[0]
        emoji_x = (width - emoji_w) // 2
        emoji_y = self._emoji_y_for_anim(mood.anim_style)
        TextUtils.draw_mixed_text(
            draw, image, emoji_text, self.emoji_font,
            (emoji_x, emoji_y),
        )

        self._render_battery(draw, state, width)

    def _render_status_glow(self, draw, image, width, mood):
        amt = pulse(0.2, 1.0, speed=1.8)
        r, g, b = mood.accent_shift
        color = (min(255, int(120 + r * amt)), min(255, int(90 + g * amt)), min(255, int(120 + b * amt)), 255)
        y = image.height - 8
        draw.rounded_rectangle([16, y, width - 16, y + 4], radius=2, fill=color)

    def _emoji_y_for_anim(self, anim_style: str) -> int:
        if anim_style == "listen_pulse":
            return int(30 + pulse(-2, 3, speed=3.2))
        if anim_style == "talk_bob":
            return int(32 + pulse(-2, 4, speed=4.0))
        if anim_style == "celebrate":
            return int(31 + pulse(-3, 5, speed=4.8))
        if anim_style == "think_blink":
            return int(33 + pulse(-1, 2, speed=1.8))
        return int(32 + pulse(-1, 1, speed=1.0))

    def _render_alert(self, state, W, H, theme):
        alert_h = 34
        alert_img = Image.new("RGBA", (W, alert_h), (0, 0, 0, 0))
        ad = ImageDraw.Draw(alert_img)
        color = alert_color_for_level(theme, state.alert_level)
        Components.draw_panel(
            ad,
            6,
            2,
            W - 7,
            alert_h - 2,
            fill=(color[0], color[1], color[2], 235),
            border=theme.border,
            radius=8,
        )
        msg = state.alert_text[:46]
        TextUtils.draw_mixed_text(
            ad,
            alert_img,
            msg,
            self.battery_font,
            (12, 9),
            fill=theme.text_soft,
        )
        self.board.draw_image(
            0,
            H - alert_h - Layout.FOOTER_H,
            W,
            alert_h,
            ImageUtils.image_to_rgb565(alert_img, W, alert_h),
        )

    def _render_battery(self, draw, state, image_width):
        bw, bh = 26, 15
        margin = 20
        bx = image_width - bw - margin
        by = 5
        r = 3

        fill = state.battery_color or (0, 0, 0)
        outline = "white"

        draw.rounded_rectangle(
            [bx, by, bx + bw, by + bh], radius=r,
            outline=outline, width=2,
        )
        if fill != (0, 0, 0):
            draw.rounded_rectangle(
                [bx + 2, by + 2, bx + bw - 2, by + bh - 2],
                radius=max(r - 1, 1), fill=fill,
            )

        hx = bx + bw
        hy = by + (bh - 5) // 2
        draw.rectangle([hx, hy, hx + 2, hy + 5], fill="white")

        txt = str(state.battery_level)
        lum = ColorUtils.luminance(fill)
        txt_color = "black" if lum > 128 else "white"
        tb = self.battery_font.getbbox(txt)
        tw = tb[2] - tb[0]
        tx = bx + (bw - tw) // 2
        ty = by + 1
        draw.text((tx, ty), txt, font=self.battery_font, fill=txt_color)

    def _render_text_area(self, image, area_height, state, width, theme):
        if not state.text:
            return

        pad = Layout.PAD
        text_margin = pad + 6  # inner margin from panel edge
        lines = TextUtils.wrap_text(state.text, self.main_font, width - text_margin * 2)
        lh = self.line_height

        render_text = ""
        display_lines = []
        y_offset = 0
        for i, line in enumerate(lines):
            line_top = i * lh
            line_bottom = (i + 1) * lh
            if line_bottom >= state.scroll_top and line_top - state.scroll_top <= area_height:
                display_lines.append((line, y_offset))
                render_text += line
            y_offset += lh

        cache_key = (render_text, theme.name)
        if self._cached_text != cache_key:
            self._cached_text = cache_key
            cache_h = max(len(display_lines) * lh, 1)
            self._text_cache_img = Image.new("RGBA", (width, cache_h + lh * 2), theme.panel)
            td = ImageDraw.Draw(self._text_cache_img)
            ry = 0
            for line, _ in display_lines:
                TextUtils.draw_mixed_text(td, self._text_cache_img, line, self.main_font, (text_margin, ry), fill=theme.text_soft)
                ry += lh

        if self._text_cache_img:
            image.paste(self._text_cache_img, (0, -state.scroll_top), self._text_cache_img)

        total_h = len(lines) * lh
        if state.scroll_speed > 0 and state.scroll_top < total_h - area_height + lh:
            # Mutate the real display_state, not the snapshot
            display_state.scroll_top = state.scroll_top + state.scroll_speed

    def _render_image(self, state, W, H):
        if state.image_obj is None and os.path.exists(state.image_path):
            try:
                img = Image.open(state.image_path).convert("RGBA")
                iw, ih = img.size
                ratio = W / H
                ir = iw / ih
                if ir > ratio:
                    nw = int(ih * ratio)
                    left = (iw - nw) // 2
                    img = img.crop((left, 0, left + nw, ih))
                else:
                    nh = int(iw / ratio)
                    top = (ih - nh) // 2
                    img = img.crop((0, top, iw, top + nh))
                state.image_obj = img.resize((W, H), Image.LANCZOS)
            except Exception as e:
                print(f"[Render] Image load error: {e}")
                return

        if state.image_obj:
            data = ImageUtils.image_to_rgb565(state.image_obj, W, H)
            self.board.draw_image(0, 0, W, H, data)
