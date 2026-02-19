import threading
import time
import random
import math

from PIL import Image, ImageDraw, ImageFont
from ui.renderer import display_state


class BrickBreakerGame:
    SCREEN_W = 240
    SCREEN_H = 280

    def __init__(self):
        self.running = False
        self.state_machine = None
        self._press_time = 0
        self._game_thread = None

        self.paddle_x = self.SCREEN_W // 2
        self.paddle_w = 50
        self.paddle_h = 8
        self.paddle_speed = 5
        self.paddle_dir = 0

        self.ball_x = float(self.SCREEN_W // 2)
        self.ball_y = float(self.SCREEN_H - 40)
        self.ball_r = 5
        self.ball_dx = 3.0
        self.ball_dy = -3.0

        self.bricks = []
        self.score = 0
        self.lives = 3
        self.game_over = False

        self._init_bricks()

    def _init_bricks(self):
        colors = [
            (255, 100, 150), (255, 150, 100), (255, 200, 80),
            (100, 255, 150), (100, 200, 255),
        ]
        brick_w = 28
        brick_h = 12
        gap = 2
        cols = (self.SCREEN_W - 10) // (brick_w + gap)
        rows = 5
        start_x = (self.SCREEN_W - cols * (brick_w + gap) + gap) // 2
        start_y = 30

        self.bricks = []
        for row in range(rows):
            for col in range(cols):
                x = start_x + col * (brick_w + gap)
                y = start_y + row * (brick_h + gap)
                self.bricks.append({
                    "x": x, "y": y, "w": brick_w, "h": brick_h,
                    "color": colors[row % len(colors)], "alive": True,
                })

    def start(self, state_machine):
        self.state_machine = state_machine
        self.running = True
        self._game_thread = threading.Thread(target=self._game_loop, daemon=True)
        self._game_thread.start()

    def stop(self):
        self.running = False
        self.game_over = True

    def on_button_press(self):
        self._press_time = time.time()
        if not self.game_over:
            self.paddle_dir = -self.paddle_dir if self.paddle_dir != 0 else 1

    def on_button_release(self):
        hold = time.time() - self._press_time
        if hold > 1.5:
            self.running = False
            if self.state_machine:
                self.state_machine.exit_game()

    def _game_loop(self):
        tick = 1.0 / 40.0
        while self.running:
            if not self.game_over:
                self._update()
            self._render()
            time.sleep(tick)

    def _update(self):
        if self.paddle_dir != 0:
            self.paddle_x += self.paddle_speed * self.paddle_dir
            self.paddle_x = max(
                self.paddle_w // 2,
                min(self.SCREEN_W - self.paddle_w // 2, self.paddle_x),
            )

        self.ball_x += self.ball_dx
        self.ball_y += self.ball_dy

        if self.ball_x - self.ball_r <= 0 or self.ball_x + self.ball_r >= self.SCREEN_W:
            self.ball_dx = -self.ball_dx
            self.ball_x = max(self.ball_r, min(self.SCREEN_W - self.ball_r, self.ball_x))

        if self.ball_y - self.ball_r <= 0:
            self.ball_dy = abs(self.ball_dy)

        paddle_top = self.SCREEN_H - 25
        if (
            self.ball_dy > 0
            and paddle_top <= self.ball_y + self.ball_r <= paddle_top + self.paddle_h
            and self.paddle_x - self.paddle_w // 2 <= self.ball_x <= self.paddle_x + self.paddle_w // 2
        ):
            offset = (self.ball_x - self.paddle_x) / (self.paddle_w / 2)
            self.ball_dx = offset * 4.0
            self.ball_dy = -abs(self.ball_dy)
            speed = math.sqrt(self.ball_dx ** 2 + self.ball_dy ** 2)
            target = 4.0 + self.score * 0.02
            if speed < target:
                factor = target / max(speed, 0.1)
                self.ball_dx *= factor
                self.ball_dy *= factor

        for brick in self.bricks:
            if not brick["alive"]:
                continue
            bx, by, bw, bh = brick["x"], brick["y"], brick["w"], brick["h"]
            if (
                bx <= self.ball_x <= bx + bw
                and by <= self.ball_y <= by + bh
            ):
                brick["alive"] = False
                self.score += 10
                self.ball_dy = -self.ball_dy
                break

        if self.ball_y > self.SCREEN_H:
            self.lives -= 1
            if self.lives <= 0:
                self.game_over = True
            else:
                self._reset_ball()

        if all(not b["alive"] for b in self.bricks):
            self._init_bricks()
            self._reset_ball()
            self.ball_dx *= 1.1
            self.ball_dy *= 1.1

    def _reset_ball(self):
        self.ball_x = float(self.SCREEN_W // 2)
        self.ball_y = float(self.SCREEN_H - 40)
        self.ball_dx = random.choice([-3.0, 3.0])
        self.ball_dy = -3.0
        self.paddle_dir = 0

    def _render(self):
        W, H = self.SCREEN_W, self.SCREEN_H
        img = Image.new("RGB", (W, H), (10, 5, 25))
        draw = ImageDraw.Draw(img)

        try:
            font = ImageFont.truetype("assets/fonts/NotoSansSC-Bold.ttf", 12)
        except Exception:
            font = ImageFont.load_default()

        draw.text((5, 2), f"Score: {self.score}", fill="white", font=font)
        hearts = "♥" * self.lives
        draw.text((W - 50, 2), hearts, fill=(255, 100, 150), font=font)

        for brick in self.bricks:
            if brick["alive"]:
                x, y, w, h = brick["x"], brick["y"], brick["w"], brick["h"]
                draw.rounded_rectangle(
                    [(x, y), (x + w, y + h)],
                    radius=2, fill=brick["color"],
                )

        paddle_top = H - 25
        px = self.paddle_x - self.paddle_w // 2
        draw.rounded_rectangle(
            [(px, paddle_top), (px + self.paddle_w, paddle_top + self.paddle_h)],
            radius=3, fill=(255, 200, 220),
        )

        bx, by = int(self.ball_x), int(self.ball_y)
        draw.ellipse(
            [(bx - self.ball_r, by - self.ball_r), (bx + self.ball_r, by + self.ball_r)],
            fill=(255, 255, 255),
        )

        if self.game_over:
            overlay_y = H // 2 - 30
            draw.rectangle([(0, overlay_y), (W, overlay_y + 60)], fill=(0, 0, 0))
            draw.text(
                (W // 2 - 50, overlay_y + 5), "GAME OVER",
                fill=(255, 100, 150), font=font,
            )
            draw.text(
                (W // 2 - 55, overlay_y + 25), f"Final Score: {self.score}",
                fill="white", font=font,
            )
            draw.text(
                (W // 2 - 60, overlay_y + 42), "Long-press to exit",
                fill=(180, 180, 180), font=font,
            )

        display_state.game_surface = img
