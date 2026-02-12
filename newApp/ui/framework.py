from dataclasses import dataclass
from functools import lru_cache
import math
import time

from PIL import ImageDraw


def _clamp(v: int) -> int:
    return max(0, min(255, v))


def _shade(base: tuple[int, int, int], factor: float) -> tuple[int, int, int, int]:
    """Scale an RGB base colour by *factor* and return RGBA."""
    return (_clamp(int(base[0] * factor)),
            _clamp(int(base[1] * factor)),
            _clamp(int(base[2] * factor)),
            255)


TURN_BASES = {
    "green": (0, 210, 80),      # User talking
    "red":   (220, 30, 45),     # Bot talking / not user turn
    "amber": (255, 165, 0),     # Thinking
    "sleep": (90, 35, 110),     # Idle purple
    "paused":(120, 120, 140),   # Neutral grey
}


@dataclass(frozen=True)
class UITheme:
    name: str
    background: tuple[int, int, int, int]
    panel: tuple[int, int, int, int]
    panel_alt: tuple[int, int, int, int]
    border: tuple[int, int, int, int]
    text_soft: tuple[int, int, int, int]
    alert_info: tuple[int, int, int, int]
    alert_warn: tuple[int, int, int, int]
    alert_error: tuple[int, int, int, int]


class ThemeRegistry:
    """Simple on-device theme registry for the LCD UI framework."""

    THEMES = {
        "classic": UITheme(
            name="classic",
            background=(11, 36, 20, 255),
            panel=(20, 64, 37, 255),
            panel_alt=(7, 25, 14, 255),
            border=(80, 170, 120, 255),
            text_soft=(220, 255, 230, 255),
            alert_info=(50, 130, 255, 255),
            alert_warn=(255, 174, 66, 255),
            alert_error=(255, 86, 102, 255),
        ),
        "birthday": UITheme(
            name="birthday",
            background=(50, 15, 40, 255),
            panel=(92, 32, 78, 255),
            panel_alt=(33, 8, 27, 255),
            border=(255, 160, 210, 255),
            text_soft=(255, 232, 245, 255),
            alert_info=(112, 183, 255, 255),
            alert_warn=(255, 190, 120, 255),
            alert_error=(255, 110, 138, 255),
        ),
        "piglet_candy": UITheme(
            name="piglet_candy",
            background=(68, 20, 58, 255),
            panel=(117, 47, 101, 255),
            panel_alt=(46, 13, 39, 255),
            border=(255, 179, 223, 255),
            text_soft=(255, 239, 248, 255),
            alert_info=(134, 208, 255, 255),
            alert_warn=(255, 202, 132, 255),
            alert_error=(255, 120, 147, 255),
        ),
    }

    @classmethod
    def get(cls, name: str):
        return cls.THEMES.get(name, cls.THEMES["classic"])

    @classmethod
    @lru_cache(maxsize=32)
    def turn_theme(cls, turn: str) -> UITheme:
        base = TURN_BASES.get(turn, TURN_BASES["red"])
        return UITheme(
            name=f"turn:{turn}",
            background=_shade(base, 0.18),
            panel=_shade(base, 0.35),
            panel_alt=_shade(base, 0.12),
            border=_shade(base, 0.85),
            text_soft=(245, 245, 245, 255),
            alert_info=(70, 150, 255, 255),
            alert_warn=(255, 190, 80, 255),
            alert_error=(255, 86, 102, 255),
        )


class Layout:
    """Shared layout contract for all screens/components."""

    HEADER_H = 90
    FOOTER_H = 26
    PAD = 10


class Components:
    """Reusable drawing primitives for the on-device frontend framework."""

    @staticmethod
    def draw_panel(draw: ImageDraw.ImageDraw, x0, y0, x1, y1, fill, border, radius=10):
        draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=fill, outline=border, width=2)

    @staticmethod
    def draw_footer(draw: ImageDraw.ImageDraw, width, height, hint, font, theme: UITheme):
        y0 = height - Layout.FOOTER_H
        Components.draw_panel(
            draw,
            0,
            y0,
            width - 1,
            height - 1,
            fill=theme.panel_alt,
            border=theme.border,
            radius=0,
        )
        if not hint:
            return
        tb = font.getbbox(hint)
        tw = tb[2] - tb[0]
        tx = max((width - tw) // 2, 6)
        ty = y0 + 5
        draw.text((tx, ty), hint, font=font, fill=theme.text_soft)


@dataclass(frozen=True)
class CharacterMood:
    emoji: str
    subtitle: str
    anim_style: str  # idle_breathe | listen_pulse | think_blink | talk_bob | celebrate
    accent_shift: tuple[int, int, int]


class PigletCharacter:
    """Maps system state to Piglet personality + animation behavior."""

    MOODS = {
        "sleep":     CharacterMood("🌙", "zzz...", "idle_breathe", (10, 5, 18)),
        "sleeping":  CharacterMood("🌙", "zzz...", "idle_breathe", (10, 5, 18)),
        "idle":      CharacterMood("🌙", "zzz...", "idle_breathe", (10, 5, 18)),
        "ready":     CharacterMood("🐷", "not your turn", "idle_breathe", (25, 8, 8)),
        "wait":      CharacterMood("🐷", "not your turn", "idle_breathe", (25, 8, 8)),
        "listening": CharacterMood("🎤", "your turn!", "listen_pulse", (8, 25, 8)),
        "talk":      CharacterMood("🎤", "your turn!", "listen_pulse", (8, 25, 8)),
        "thinking":  CharacterMood("⏳", "thinking...", "think_blink", (22, 16, 0)),
        "think":     CharacterMood("⏳", "thinking...", "think_blink", (22, 16, 0)),
        "talking":   CharacterMood("🐷", "piglet speaking", "talk_bob", (25, 8, 8)),
        "answering": CharacterMood("🐷", "piglet speaking", "talk_bob", (25, 8, 8)),
        "paused":    CharacterMood("⏸️", "paused", "idle_breathe", (12, 12, 12)),
        "playing":   CharacterMood("🎮", "play time!", "celebrate", (20, 8, 22)),
        "playing music": CharacterMood("🎵", "grooving", "celebrate", (24, 10, 18)),
        "error":     CharacterMood("😟", "oops", "think_blink", (25, 5, 5)),
        "waking up": CharacterMood("🌅", "warming up", "idle_breathe", (20, 15, 8)),
    }

    @classmethod
    def mood_for_status(cls, status: str) -> CharacterMood:
        return cls.MOODS.get((status or "").lower(), cls.MOODS["ready"])


def pulse(scale_min: float = 0.92, scale_max: float = 1.08, speed: float = 1.0) -> float:
    t = time.time() * speed
    s = (math.sin(t) + 1.0) * 0.5
    return scale_min + (scale_max - scale_min) * s


def alert_color_for_level(theme: UITheme, level: str):
    level = (level or "info").lower()
    if level == "error":
        return theme.alert_error
    if level in ("warn", "warning"):
        return theme.alert_warn
    return theme.alert_info


def hint_for_status(status: str) -> str:
    status = (status or "").lower()
    if status in ("sleeping", "idle", "sleep"):
        return "Hold button to start talking"
    if status in ("ready", "wait"):
        return "Hold button to talk"
    if status == "listening":
        return "Speak now! Release when done"
    if status in ("thinking", "waking up"):
        return "Piglet is thinking..."
    if status in ("talking", "answering"):
        return "Hold button to interrupt"
    if status == "paused":
        return "Double-click to unpause"
    if status in ("playing", "playing music"):
        return "Long-press to exit"
    return ""


def infer_turn(status: str) -> str:
    """Infer turn from status string (legacy compatibility)."""
    status = (status or "").lower()
    if status in ("listening",):
        return "green"
    if status in ("thinking", "waking up"):
        return "amber"
    if status in ("talking", "answering"):
        return "red"
    if status in ("paused",):
        return "paused"
    return "sleep"
