"""Kawaii pig sprite generator for the 240x280 LCD.

Generates cute round pig sprites at runtime using Pillow drawing
primitives. Each mood has 2-4 animation frames. Sprites are 56x56
RGBA with transparent backgrounds, cached at startup.

Style reference: round blob body, tiny ears, crescent/dot eyes,
small oval snout, rosy cheeks, stubby legs. Kawaii aesthetic.
"""

import math
import time
from PIL import Image, ImageDraw


# --- Color palette ---
BODY = (255, 200, 183)          # Light peachy pink
BODY_SHADOW = (245, 180, 163)   # Slightly darker for depth
EAR_OUTER = (255, 185, 168)     # Ear main color
EAR_INNER = (255, 160, 148)     # Inner ear pink
CHEEK = (255, 145, 145, 100)    # Semi-transparent blush
SNOUT = (255, 175, 158)         # Snout oval
NOSTRIL = (210, 145, 130)       # Nostril dots
EYE_COLOR = (55, 35, 35)        # Dark brown eyes
MOUTH_COLOR = (210, 130, 120)   # Mouth/smile line
HIGHLIGHT = (255, 230, 225, 160)  # Subtle body highlight

SPRITE_SIZE = 56
_HALF = SPRITE_SIZE // 2


class PigSpriteSheet:
    """Pre-generates all pig animation frames at init time.

    Usage:
        sprites = PigSpriteSheet()
        frame = sprites.get_frame("idle", time.time())
    """

    # Animation timing
    BLINK_INTERVAL = 3.5    # seconds between blinks
    BLINK_DURATION = 0.15   # seconds eyes stay closed
    TALK_CYCLE = 0.18       # seconds per talk frame
    THINK_CYCLE = 0.8       # seconds per think frame

    def __init__(self, size=SPRITE_SIZE):
        self.size = size
        self._frames = {}  # mood -> list of PIL Images
        self._generate_all()

    def _generate_all(self):
        """Generate all mood sprite frames."""
        self._frames["idle"] = [
            self._draw_pig(eyes="crescent", mouth="smile", ears="relaxed"),
            self._draw_pig(eyes="closed", mouth="smile", ears="relaxed"),  # blink
        ]
        self._frames["sleep"] = [
            self._draw_pig(eyes="closed", mouth="smile", ears="droopy", extra="zzz"),
        ]
        self._frames["listening"] = [
            self._draw_pig(eyes="wide", mouth="open_small", ears="perked"),
            self._draw_pig(eyes="wide", mouth="smile", ears="perked"),
        ]
        self._frames["thinking"] = [
            self._draw_pig(eyes="look_up_right", mouth="oh", ears="relaxed"),
            self._draw_pig(eyes="look_up_left", mouth="oh", ears="relaxed"),
        ]
        self._frames["talking"] = [
            self._draw_pig(eyes="happy", mouth="open_wide", ears="relaxed"),
            self._draw_pig(eyes="happy", mouth="closed", ears="relaxed"),
            self._draw_pig(eyes="happy", mouth="open_small", ears="relaxed"),
        ]
        self._frames["happy"] = [
            self._draw_pig(eyes="sparkle", mouth="big_smile", ears="perked", extra="blush"),
        ]
        self._frames["error"] = [
            self._draw_pig(eyes="sad", mouth="frown", ears="droopy"),
        ]
        self._frames["ready"] = [
            self._draw_pig(eyes="normal", mouth="smile", ears="relaxed"),
            self._draw_pig(eyes="closed", mouth="smile", ears="relaxed"),  # blink
        ]
        self._frames["waking"] = [
            self._draw_pig(eyes="squint", mouth="oh", ears="relaxed"),
            self._draw_pig(eyes="half_open", mouth="smile", ears="relaxed"),
        ]
        self._frames["music"] = [
            self._draw_pig(eyes="crescent", mouth="smile", ears="perked", extra="note_left"),
            self._draw_pig(eyes="crescent", mouth="smile", ears="perked", extra="note_right"),
        ]

    def get_frame(self, mood: str, t: float = None) -> Image.Image:
        """Get the current animation frame for a mood.

        Args:
            mood: One of idle, sleep, listening, thinking, talking, happy,
                  error, ready, waking, music
            t: Current time (defaults to time.time())
        """
        if t is None:
            t = time.time()

        frames = self._frames.get(mood, self._frames["idle"])

        if mood in ("idle", "ready"):
            # Blink every BLINK_INTERVAL seconds
            cycle_pos = t % self.BLINK_INTERVAL
            if cycle_pos < self.BLINK_DURATION and len(frames) > 1:
                return frames[1]  # blink frame
            return frames[0]

        elif mood == "talking":
            idx = int(t / self.TALK_CYCLE) % len(frames)
            return frames[idx]

        elif mood == "thinking":
            idx = int(t / self.THINK_CYCLE) % len(frames)
            return frames[idx]

        elif mood == "listening":
            # Alternate slowly
            idx = int(t / 0.5) % len(frames)
            return frames[idx]

        elif mood == "waking":
            idx = int(t / 0.6) % len(frames)
            return frames[idx]

        elif mood == "music":
            idx = int(t / 0.35) % len(frames)
            return frames[idx]

        # Static moods (sleep, happy, error)
        return frames[0]

    # --- Drawing helpers ---

    def _new_sprite(self):
        return Image.new("RGBA", (self.size, self.size), (0, 0, 0, 0))

    def _draw_pig(self, eyes="normal", mouth="smile", ears="relaxed", extra=None):
        """Draw a complete pig sprite with the given expression."""
        img = self._new_sprite()
        d = ImageDraw.Draw(img)
        s = self.size
        cx, cy = s // 2, s // 2 + 4  # center body slightly lower for ears

        # --- Body (main blob) ---
        body_r = 20
        body_box = (cx - body_r, cy - body_r + 2, cx + body_r, cy + body_r + 2)
        d.ellipse(body_box, fill=BODY)

        # Subtle shadow on lower body
        shadow_box = (cx - body_r + 3, cy + 4, cx + body_r - 3, cy + body_r + 2)
        d.ellipse(shadow_box, fill=BODY_SHADOW)

        # Highlight on upper body
        hl_box = (cx - 10, cy - body_r + 4, cx + 6, cy - 6)
        d.ellipse(hl_box, fill=HIGHLIGHT)

        # --- Ears ---
        self._draw_ears(d, cx, cy, body_r, style=ears)

        # --- Legs (tiny stubs) ---
        self._draw_legs(d, cx, cy, body_r)

        # --- Tail (tiny curl) ---
        self._draw_tail(d, cx, cy, body_r)

        # --- Snout ---
        snout_cx, snout_cy = cx, cy + 6
        snout_rx, snout_ry = 8, 5
        d.ellipse(
            (snout_cx - snout_rx, snout_cy - snout_ry,
             snout_cx + snout_rx, snout_cy + snout_ry),
            fill=SNOUT,
        )
        # Nostrils
        d.ellipse((snout_cx - 4, snout_cy - 1, snout_cx - 1, snout_cy + 2), fill=NOSTRIL)
        d.ellipse((snout_cx + 1, snout_cy - 1, snout_cx + 4, snout_cy + 2), fill=NOSTRIL)

        # --- Cheeks (rosy blush, positioned on the lighter body area) ---
        cheek_y = cy + 3
        d.ellipse((cx - 16, cheek_y - 2, cx - 9, cheek_y + 3), fill=(255, 160, 160, 70))
        d.ellipse((cx + 9, cheek_y - 2, cx + 16, cheek_y + 3), fill=(255, 160, 160, 70))

        # --- Eyes ---
        self._draw_eyes(d, cx, cy, style=eyes)

        # --- Mouth ---
        self._draw_mouth(d, cx, cy, style=mouth)

        # --- Extra decorations ---
        if extra:
            self._draw_extra(d, img, cx, cy, body_r, extra)

        return img

    def _draw_ears(self, d, cx, cy, body_r, style="relaxed"):
        """Draw two triangular pig ears."""
        ear_w = 8
        ear_h = 12

        if style == "perked":
            # Ears pointing up, alert
            # Left ear
            pts_l = [(cx - 14, cy - body_r + 6),
                     (cx - 8, cy - body_r - 8),
                     (cx - 4, cy - body_r + 4)]
            d.polygon(pts_l, fill=EAR_OUTER)
            # Inner
            pts_li = [(cx - 12, cy - body_r + 5),
                      (cx - 8, cy - body_r - 4),
                      (cx - 6, cy - body_r + 4)]
            d.polygon(pts_li, fill=EAR_INNER)

            # Right ear
            pts_r = [(cx + 4, cy - body_r + 4),
                     (cx + 8, cy - body_r - 8),
                     (cx + 14, cy - body_r + 6)]
            d.polygon(pts_r, fill=EAR_OUTER)
            pts_ri = [(cx + 6, cy - body_r + 4),
                      (cx + 8, cy - body_r - 4),
                      (cx + 12, cy - body_r + 5)]
            d.polygon(pts_ri, fill=EAR_INNER)

        elif style == "droopy":
            # Ears folded down, sleepy/sad
            pts_l = [(cx - 16, cy - body_r + 8),
                     (cx - 12, cy - body_r),
                     (cx - 6, cy - body_r + 4)]
            d.polygon(pts_l, fill=EAR_OUTER)
            pts_r = [(cx + 6, cy - body_r + 4),
                     (cx + 12, cy - body_r),
                     (cx + 16, cy - body_r + 8)]
            d.polygon(pts_r, fill=EAR_OUTER)

        else:
            # Relaxed / neutral
            pts_l = [(cx - 15, cy - body_r + 7),
                     (cx - 9, cy - body_r - 5),
                     (cx - 4, cy - body_r + 4)]
            d.polygon(pts_l, fill=EAR_OUTER)
            pts_li = [(cx - 13, cy - body_r + 6),
                      (cx - 9, cy - body_r - 2),
                      (cx - 6, cy - body_r + 4)]
            d.polygon(pts_li, fill=EAR_INNER)

            pts_r = [(cx + 4, cy - body_r + 4),
                     (cx + 9, cy - body_r - 5),
                     (cx + 15, cy - body_r + 7)]
            d.polygon(pts_r, fill=EAR_OUTER)
            pts_ri = [(cx + 6, cy - body_r + 4),
                      (cx + 9, cy - body_r - 2),
                      (cx + 13, cy - body_r + 6)]
            d.polygon(pts_ri, fill=EAR_INNER)

    def _draw_legs(self, d, cx, cy, body_r):
        """Draw tiny stubby legs at the bottom of the body."""
        leg_w, leg_h = 5, 6
        leg_y = cy + body_r - 2

        # Left leg
        d.ellipse((cx - 12, leg_y, cx - 12 + leg_w, leg_y + leg_h), fill=BODY_SHADOW)
        # Right leg
        d.ellipse((cx + 7, leg_y, cx + 7 + leg_w, leg_y + leg_h), fill=BODY_SHADOW)

    def _draw_tail(self, d, cx, cy, body_r):
        """Draw a tiny curly tail on the right side."""
        tx = cx + body_r - 2
        ty = cy + 2
        # Simple curl using arcs
        d.arc((tx - 2, ty - 4, tx + 6, ty + 4), start=180, end=360, fill=BODY_SHADOW, width=2)
        d.arc((tx + 1, ty - 2, tx + 7, ty + 2), start=0, end=180, fill=BODY_SHADOW, width=2)

    def _draw_eyes(self, d, cx, cy, style="normal"):
        """Draw eyes based on expression style."""
        eye_y = cy - 5
        left_x = cx - 8
        right_x = cx + 6

        if style == "normal":
            # Small round dot eyes
            r = 2
            d.ellipse((left_x, eye_y, left_x + r * 2, eye_y + r * 2), fill=EYE_COLOR)
            d.ellipse((right_x, eye_y, right_x + r * 2, eye_y + r * 2), fill=EYE_COLOR)
            # Tiny highlight
            d.point((left_x + 1, eye_y + 1), fill=(255, 255, 255))
            d.point((right_x + 1, eye_y + 1), fill=(255, 255, 255))

        elif style == "wide":
            # Big round eyes (surprised/listening)
            r = 3
            d.ellipse((left_x - 1, eye_y - 1, left_x + r * 2, eye_y + r * 2), fill=EYE_COLOR)
            d.ellipse((right_x - 1, eye_y - 1, right_x + r * 2, eye_y + r * 2), fill=EYE_COLOR)
            # Bigger highlight
            d.ellipse((left_x, eye_y, left_x + 2, eye_y + 2), fill=(255, 255, 255))
            d.ellipse((right_x, eye_y, right_x + 2, eye_y + 2), fill=(255, 255, 255))

        elif style == "crescent" or style == "happy":
            # Happy closed eyes (^_^) - upward arcs
            d.arc((left_x - 1, eye_y - 1, left_x + 5, eye_y + 5),
                  start=200, end=340, fill=EYE_COLOR, width=2)
            d.arc((right_x - 1, eye_y - 1, right_x + 5, eye_y + 5),
                  start=200, end=340, fill=EYE_COLOR, width=2)

        elif style == "closed":
            # Fully closed eyes (blink) - horizontal lines
            d.line((left_x, eye_y + 2, left_x + 4, eye_y + 2), fill=EYE_COLOR, width=2)
            d.line((right_x, eye_y + 2, right_x + 4, eye_y + 2), fill=EYE_COLOR, width=2)

        elif style == "look_up_right":
            # Eyes looking upper-right (thinking)
            r = 2
            off = 1
            d.ellipse((left_x + off, eye_y - off, left_x + off + r * 2, eye_y - off + r * 2), fill=EYE_COLOR)
            d.ellipse((right_x + off, eye_y - off, right_x + off + r * 2, eye_y - off + r * 2), fill=EYE_COLOR)

        elif style == "look_up_left":
            r = 2
            off = 1
            d.ellipse((left_x - off, eye_y - off, left_x - off + r * 2, eye_y - off + r * 2), fill=EYE_COLOR)
            d.ellipse((right_x - off, eye_y - off, right_x - off + r * 2, eye_y - off + r * 2), fill=EYE_COLOR)

        elif style == "sad":
            # Downturned eyes
            d.arc((left_x - 1, eye_y, left_x + 5, eye_y + 6),
                  start=20, end=160, fill=EYE_COLOR, width=2)
            d.arc((right_x - 1, eye_y, right_x + 5, eye_y + 6),
                  start=20, end=160, fill=EYE_COLOR, width=2)

        elif style == "sparkle":
            # Star-like sparkle eyes (excited)
            for ex in (left_x + 2, right_x + 2):
                ey = eye_y + 2
                # Small cross/star
                d.line((ex - 2, ey, ex + 2, ey), fill=EYE_COLOR, width=1)
                d.line((ex, ey - 2, ex, ey + 2), fill=EYE_COLOR, width=1)
                d.point((ex - 1, ey - 1), fill=EYE_COLOR)
                d.point((ex + 1, ey - 1), fill=EYE_COLOR)
                d.point((ex - 1, ey + 1), fill=EYE_COLOR)
                d.point((ex + 1, ey + 1), fill=EYE_COLOR)

        elif style == "squint":
            # Just waking up, barely open
            d.line((left_x, eye_y + 2, left_x + 4, eye_y + 1), fill=EYE_COLOR, width=2)
            d.line((right_x, eye_y + 1, right_x + 4, eye_y + 2), fill=EYE_COLOR, width=2)

        elif style == "half_open":
            # Half-open eyes
            d.ellipse((left_x, eye_y + 1, left_x + 4, eye_y + 3), fill=EYE_COLOR)
            d.ellipse((right_x, eye_y + 1, right_x + 4, eye_y + 3), fill=EYE_COLOR)

    def _draw_mouth(self, d, cx, cy, style="smile"):
        """Draw mouth based on expression."""
        mouth_y = cy + 12

        if style == "smile":
            # Small gentle smile arc
            d.arc((cx - 4, mouth_y - 3, cx + 4, mouth_y + 3),
                  start=10, end=170, fill=MOUTH_COLOR, width=1)

        elif style == "big_smile":
            # Wider smile
            d.arc((cx - 6, mouth_y - 4, cx + 6, mouth_y + 4),
                  start=10, end=170, fill=MOUTH_COLOR, width=2)

        elif style == "open_wide":
            # Talking - mouth open
            d.ellipse((cx - 4, mouth_y - 2, cx + 4, mouth_y + 4), fill=MOUTH_COLOR)
            # Inner mouth
            d.ellipse((cx - 2, mouth_y, cx + 2, mouth_y + 3), fill=(180, 100, 90))

        elif style == "open_small":
            # Small open mouth
            d.ellipse((cx - 2, mouth_y - 1, cx + 2, mouth_y + 2), fill=MOUTH_COLOR)

        elif style == "oh":
            # Small 'o' shape (thinking/surprised)
            d.ellipse((cx - 2, mouth_y - 1, cx + 2, mouth_y + 2),
                      outline=MOUTH_COLOR, width=1)

        elif style == "frown":
            # Sad frown
            d.arc((cx - 4, mouth_y - 1, cx + 4, mouth_y + 5),
                  start=200, end=340, fill=MOUTH_COLOR, width=1)

        elif style == "closed":
            # Neutral closed mouth (thin line)
            d.line((cx - 3, mouth_y, cx + 3, mouth_y), fill=MOUTH_COLOR, width=1)

    def _draw_extra(self, d, img, cx, cy, body_r, extra):
        """Draw extra decorations (zzz, notes, blush, etc.)."""
        if extra == "zzz":
            # Floating Z's above head
            zx, zy = cx + 14, cy - body_r - 8
            for i, (dx, dy, sz) in enumerate([(0, 0, 6), (5, -5, 5), (9, -9, 4)]):
                _draw_z(d, zx + dx, zy + dy, sz)

        elif extra == "blush":
            # Extra rosy cheeks
            cheek_y = cy + 2
            d.ellipse((cx - 20, cheek_y - 4, cx - 10, cheek_y + 5),
                      fill=(255, 130, 130, 130))
            d.ellipse((cx + 10, cheek_y - 4, cx + 20, cheek_y + 5),
                      fill=(255, 130, 130, 130))

        elif extra == "note_left":
            # Music note floating to the left
            nx, ny = cx - body_r - 2, cy - body_r
            _draw_music_note(d, nx, ny)

        elif extra == "note_right":
            # Music note floating to the right
            nx, ny = cx + body_r - 2, cy - body_r - 4
            _draw_music_note(d, nx, ny)


def _draw_z(d, x, y, size):
    """Draw a small 'z' character."""
    color = (180, 160, 200)
    d.line((x, y, x + size, y), fill=color, width=1)
    d.line((x + size, y, x, y + size), fill=color, width=1)
    d.line((x, y + size, x + size, y + size), fill=color, width=1)


def _draw_music_note(d, x, y):
    """Draw a tiny music note."""
    color = (255, 180, 200)
    d.ellipse((x, y + 4, x + 4, y + 7), fill=color)
    d.line((x + 4, y, x + 4, y + 6), fill=color, width=1)
    d.line((x + 4, y, x + 6, y + 1), fill=color, width=1)


# --- Mood mapping (status string -> sprite mood) ---

STATUS_TO_SPRITE_MOOD = {
    "sleep": "sleep",
    "sleeping": "sleep",
    "idle": "idle",
    "ready": "ready",
    "wait": "ready",
    "listening": "listening",
    "talk": "listening",
    "thinking": "thinking",
    "think": "thinking",
    "talking": "talking",
    "answering": "talking",
    "paused": "idle",
    "playing": "happy",
    "playing music": "music",
    "error": "error",
    "waking up": "waking",
}


def sprite_mood_for_status(status: str) -> str:
    """Map a display status string to a sprite mood key."""
    return STATUS_TO_SPRITE_MOOD.get((status or "").lower(), "idle")
