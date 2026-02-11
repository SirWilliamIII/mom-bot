import os
import unicodedata
from io import BytesIO

import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    import cairosvg
    _CAIRO_AVAILABLE = True
except ImportError:
    _CAIRO_AVAILABLE = False


class ColorUtils:
    @staticmethod
    def hex_to_rgb(hex_color):
        hex_color = hex_color.lstrip("#")
        if len(hex_color) >= 6:
            return (
                int(hex_color[0:2], 16),
                int(hex_color[2:4], 16),
                int(hex_color[4:6], 16),
            )
        return (0, 0, 0)

    @staticmethod
    def luminance(rgb):
        if rgb is None:
            return 0
        r, g, b = rgb
        return 0.299 * r + 0.587 * g + 0.114 * b


class ImageUtils:
    @staticmethod
    def image_to_rgb565(image, width, height):
        image = image.convert("RGB")
        image.thumbnail((width, height), Image.LANCZOS)
        bg = Image.new("RGB", (width, height), (0, 0, 0))
        x = (width - image.width) // 2
        y = (height - image.height) // 2
        bg.paste(image, (x, y))
        np_img = np.array(bg)
        r = (np_img[:, :, 0] >> 3).astype(np.uint16)
        g = (np_img[:, :, 1] >> 2).astype(np.uint16)
        b = (np_img[:, :, 2] >> 3).astype(np.uint16)
        rgb565 = (r << 11) | (g << 5) | b
        high = (rgb565 >> 8).astype(np.uint8)
        low = (rgb565 & 0xFF).astype(np.uint8)
        return np.dstack((high, low)).flatten().tolist()


class EmojiUtils:
    @staticmethod
    def is_emoji(char):
        return unicodedata.category(char) in ("So", "Sk") or ord(char) > 0x1F000

    @staticmethod
    def emoji_to_filename(char):
        return "-".join(f"{ord(c):x}" for c in char) + ".svg"

    @staticmethod
    def get_emoji_image(char, size, emoji_dir="assets/emoji_svg"):
        if not _CAIRO_AVAILABLE:
            return None
        filename = EmojiUtils.emoji_to_filename(char)
        path = os.path.join(emoji_dir, filename)
        if not os.path.exists(path):
            return None
        try:
            png_bytes = cairosvg.svg2png(url=path, output_width=size, output_height=size)
            return Image.open(BytesIO(png_bytes)).convert("RGBA")
        except Exception:
            return None


_char_size_cache = {}
_line_image_cache = {}


class TextUtils:
    @staticmethod
    def get_char_size(font, char):
        key = (font.getname(), font.size, char)
        if key in _char_size_cache:
            return _char_size_cache[key]
        if EmojiUtils.is_emoji(char):
            img = EmojiUtils.get_emoji_image(char, size=font.size)
            if img:
                _char_size_cache[key] = (img.width, img.height)
                return img.width, img.height
        bbox = font.getbbox(char)
        result = (bbox[2] - bbox[0], bbox[3] - bbox[1])
        _char_size_cache[key] = result
        return result

    @staticmethod
    def wrap_text(text, font, max_width):
        lines = []
        current_line = ""
        current_width = 0
        for char in text:
            char_w = TextUtils.get_char_size(font, char)[0]
            if current_width + char_w <= max_width:
                current_line += char
                current_width += char_w
            else:
                if current_line:
                    lines.append(current_line)
                current_line = char
                current_width = char_w
        if current_line:
            lines.append(current_line)
        return lines

    @staticmethod
    def draw_mixed_text(draw, image, text, font, start_xy):
        img = TextUtils._get_line_image(text, font)
        image.paste(img, start_xy, img)

    @staticmethod
    def _get_line_image(text, font):
        key = (font.getname(), font.size, text)
        if key in _line_image_cache:
            return _line_image_cache[key]

        ascent, descent = font.getmetrics()
        line_height = ascent + descent
        width = sum(TextUtils.get_char_size(font, c)[0] for c in text)
        img = Image.new("RGBA", (max(width, 1), line_height), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)

        x = 0
        baseline = ascent
        for char in text:
            if EmojiUtils.is_emoji(char):
                emoji_img = EmojiUtils.get_emoji_image(char, size=font.size)
                if emoji_img:
                    img.paste(emoji_img, (x, baseline - emoji_img.height), emoji_img)
                    x += emoji_img.width
            else:
                d.text((x, 0), char, font=font, fill=(255, 165, 0))
                x += TextUtils.get_char_size(font, char)[0]

        _line_image_cache[key] = img
        return img

    @staticmethod
    def clear_cache():
        global _line_image_cache
        _line_image_cache = {}
