from dataclasses import dataclass, field
from functools import lru_cache
import math
import random
import time

from PIL import Image, ImageDraw


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
    PAD = 16


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
        return "Hold to talk · 2x tap to end"
    if status == "listening":
        return "Speak now! Release when done"
    if status in ("thinking", "waking up"):
        return "Piglet is thinking..."
    if status in ("talking", "answering"):
        return "Hold to interrupt · 2x tap to end"
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


# --- Smooth transitions ---

def _lerp_color(a: tuple, b: tuple, t: float) -> tuple:
    """Linearly interpolate between two RGBA color tuples."""
    t = max(0.0, min(1.0, t))
    return tuple(_clamp(int(a[i] + (b[i] - a[i]) * t)) for i in range(min(len(a), len(b))))


def _ease_out_cubic(t: float) -> float:
    """Ease-out cubic for smooth deceleration."""
    return 1.0 - (1.0 - t) ** 3


class TransitionManager:
    """Manages smooth color transitions between UI states.

    Tracks the previous theme and current theme, interpolating
    colors over a configurable duration with easing.
    """

    DURATION = 0.3  # seconds for full transition

    def __init__(self):
        self._prev_turn = None
        self._curr_turn = None
        self._transition_start = 0.0
        self._prev_theme = None
        self._curr_theme = None

    def update(self, turn: str) -> UITheme:
        """Call each frame with the current turn. Returns the interpolated theme."""
        if turn != self._curr_turn:
            # New transition
            self._prev_turn = self._curr_turn
            self._prev_theme = self._curr_theme
            self._curr_turn = turn
            self._curr_theme = ThemeRegistry.turn_theme(turn)
            self._transition_start = time.time()

        if self._curr_theme is None:
            self._curr_theme = ThemeRegistry.turn_theme(turn)
            return self._curr_theme

        if self._prev_theme is None:
            return self._curr_theme

        elapsed = time.time() - self._transition_start
        if elapsed >= self.DURATION:
            return self._curr_theme

        t = _ease_out_cubic(elapsed / self.DURATION)
        return UITheme(
            name=f"transition:{self._prev_turn}->{self._curr_turn}",
            background=_lerp_color(self._prev_theme.background, self._curr_theme.background, t),
            panel=_lerp_color(self._prev_theme.panel, self._curr_theme.panel, t),
            panel_alt=_lerp_color(self._prev_theme.panel_alt, self._curr_theme.panel_alt, t),
            border=_lerp_color(self._prev_theme.border, self._curr_theme.border, t),
            text_soft=_lerp_color(self._prev_theme.text_soft, self._curr_theme.text_soft, t),
            alert_info=_lerp_color(self._prev_theme.alert_info, self._curr_theme.alert_info, t),
            alert_warn=_lerp_color(self._prev_theme.alert_warn, self._curr_theme.alert_warn, t),
            alert_error=_lerp_color(self._prev_theme.alert_error, self._curr_theme.alert_error, t),
        )

    @property
    def is_transitioning(self) -> bool:
        return (time.time() - self._transition_start) < self.DURATION


# --- Particle system ---

@dataclass
class Particle:
    x: float
    y: float
    vx: float
    vy: float
    life: float       # remaining life in seconds
    max_life: float
    kind: str          # "heart", "sparkle", "note"
    size: int = 4


class ParticleSystem:
    """Lightweight particle effects for the LCD display.

    Spawns small hearts, sparkles, or music notes that float
    upward and fade out. Designed to be cheap on the Pi Zero.
    Max ~12 particles at a time to keep render cost negligible.
    """

    MAX_PARTICLES = 12

    def __init__(self):
        self._particles: list[Particle] = []
        self._last_update = time.time()
        self._last_spawn = 0.0

    def emit(self, kind: str, x: int, y: int, count: int = 3):
        """Spawn particles at (x, y). kind: 'heart', 'sparkle', 'note'."""
        for _ in range(min(count, self.MAX_PARTICLES - len(self._particles))):
            life = random.uniform(0.8, 1.6)
            self._particles.append(Particle(
                x=x + random.uniform(-8, 8),
                y=y + random.uniform(-4, 4),
                vx=random.uniform(-12, 12),
                vy=random.uniform(-30, -15),
                life=life,
                max_life=life,
                kind=kind,
                size=random.randint(3, 5),
            ))

    def update_and_draw(self, image: Image.Image, dt: float = None):
        """Advance physics and draw surviving particles onto image."""
        now = time.time()
        if dt is None:
            dt = now - self._last_update
        self._last_update = now
        dt = min(dt, 0.1)  # cap to avoid jumps

        surviving = []
        d = ImageDraw.Draw(image)

        for p in self._particles:
            p.life -= dt
            if p.life <= 0:
                continue
            p.x += p.vx * dt
            p.y += p.vy * dt
            p.vy += 8 * dt  # slight gravity (slows upward drift)

            alpha = int(255 * (p.life / p.max_life))
            ix, iy = int(p.x), int(p.y)

            if p.kind == "heart":
                _draw_tiny_heart(d, ix, iy, p.size, alpha)
            elif p.kind == "sparkle":
                _draw_tiny_sparkle(d, ix, iy, p.size, alpha)
            elif p.kind == "note":
                _draw_tiny_note(d, ix, iy, p.size, alpha)

            surviving.append(p)

        self._particles = surviving

    @property
    def active(self) -> bool:
        return len(self._particles) > 0

    def clear(self):
        self._particles.clear()


def _draw_tiny_heart(d, x, y, size, alpha):
    """Draw a tiny heart shape."""
    c = (255, 120, 150, max(60, alpha))
    hs = size // 2
    # Two small circles + triangle to approximate heart
    d.ellipse((x - hs, y - hs, x, y), fill=c)
    d.ellipse((x, y - hs, x + hs, y), fill=c)
    d.polygon([(x - hs, y - 1), (x + hs, y - 1), (x, y + hs)], fill=c)


def _draw_tiny_sparkle(d, x, y, size, alpha):
    """Draw a tiny 4-point sparkle."""
    c = (255, 255, 200, max(60, alpha))
    d.line((x - size, y, x + size, y), fill=c, width=1)
    d.line((x, y - size, x, y + size), fill=c, width=1)


def _draw_tiny_note(d, x, y, size, alpha):
    """Draw a tiny music note."""
    c = (255, 200, 220, max(60, alpha))
    d.ellipse((x, y, x + size, y + size - 1), fill=c)
    d.line((x + size, y - size, x + size, y + 1), fill=c, width=1)
