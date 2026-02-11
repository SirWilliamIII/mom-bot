import os
import time
import threading

from PIL import Image, ImageDraw, ImageFont
from ui.utils import ColorUtils, ImageUtils, TextUtils


class DisplayState:
    def __init__(self):
        self.status = "Hello"
        self.emoji = "🐷"
        self.text = "Press and hold to talk to me!"
        self.battery_level = 100
        self.battery_color = (85, 255, 0)
        self.rgb_color = (0, 0, 85)
        self.scroll_top = 0
        self.scroll_speed = 3
        self.image_path = ""
        self.image_obj = None
        self.game_surface = None

    def update(self, **kwargs):
        if "text" in kwargs and kwargs["text"] is not None:
            new_text = kwargs["text"]
            if not new_text.startswith(self.text):
                self.scroll_top = 0
                TextUtils.clear_cache()
            self.text = new_text
        if "status" in kwargs and kwargs["status"] is not None:
            self.status = kwargs["status"]
        if "emoji" in kwargs and kwargs["emoji"] is not None:
            self.emoji = kwargs["emoji"]
        if "battery_level" in kwargs and kwargs["battery_level"] is not None:
            self.battery_level = kwargs["battery_level"]
        if "battery_color" in kwargs and kwargs["battery_color"] is not None:
            self.battery_color = kwargs["battery_color"]
        if "rgb_color" in kwargs and kwargs["rgb_color"] is not None:
            self.rgb_color = kwargs["rgb_color"]
        if "scroll_speed" in kwargs and kwargs["scroll_speed"] is not None:
            self.scroll_speed = kwargs["scroll_speed"]
        if "image_path" in kwargs:
            self.image_path = kwargs["image_path"] or ""
            self.image_obj = None
        if "game_surface" in kwargs:
            self.game_surface = kwargs["game_surface"]


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
        self._cached_text = ""

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

    def _render_frame(self):
        state = display_state
        W = self.board.LCD_WIDTH
        H = self.board.LCD_HEIGHT

        if state.game_surface is not None:
            data = ImageUtils.image_to_rgb565(state.game_surface, W, H)
            self.board.draw_image(0, 0, W, H, data)
            return

        if state.image_path:
            self._render_image(state, W, H)
            return

        header_h = 98
        header = Image.new("RGBA", (W, header_h), (0, 100, 0, 255))
        hd = ImageDraw.Draw(header)
        self._render_header(header, hd, state, W)
        self.board.draw_image(
            0, 0, W, header_h,
            ImageUtils.image_to_rgb565(header, W, header_h),
        )

        text_h = H - header_h
        text_img = Image.new("RGBA", (W, text_h), (0, 100, 0, 255))
        self._render_text_area(text_img, text_h, state, W)
        self.board.draw_image(
            0, header_h, W, text_h,
            ImageUtils.image_to_rgb565(text_img, W, text_h),
        )

    def _render_header(self, image, draw, state, width):
        TextUtils.draw_mixed_text(
            draw, image, state.status, self.status_font, (20, 0)
        )

        emoji_bbox = self.emoji_font.getbbox(state.emoji)
        emoji_w = emoji_bbox[2] - emoji_bbox[0]
        TextUtils.draw_mixed_text(
            draw, image, state.emoji, self.emoji_font,
            ((width - emoji_w) // 2, 32),
        )

        self._render_battery(draw, state, width)

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

    def _render_text_area(self, image, area_height, state, width):
        if not state.text:
            return

        lines = TextUtils.wrap_text(state.text, self.main_font, width - 20)
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

        if self._cached_text != render_text:
            self._cached_text = render_text
            cache_h = max(len(display_lines) * lh, 1)
            self._text_cache_img = Image.new("RGBA", (width, cache_h + lh * 2), (0, 0, 0, 255))
            td = ImageDraw.Draw(self._text_cache_img)
            ry = 0
            for line, _ in display_lines:
                TextUtils.draw_mixed_text(td, self._text_cache_img, line, self.main_font, (10, ry))
                ry += lh

        if self._text_cache_img:
            image.paste(self._text_cache_img, (0, -state.scroll_top), self._text_cache_img)

        total_h = len(lines) * lh
        if state.scroll_speed > 0 and state.scroll_top < total_h - area_height + lh:
            state.scroll_top += state.scroll_speed

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
