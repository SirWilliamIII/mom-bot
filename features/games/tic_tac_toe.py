import threading
import time

from PIL import Image, ImageDraw, ImageFont
from ui.renderer import display_state


NUMPAD_TO_CELL = {
    7: (0, 0), 8: (0, 1), 9: (0, 2),
    4: (1, 0), 5: (1, 1), 6: (1, 2),
    1: (2, 0), 2: (2, 1), 3: (2, 2),
}


class TicTacToeGame:
    SCREEN_W = 240
    SCREEN_H = 280

    def __init__(self):
        self.board = [[" "] * 3 for _ in range(3)]
        self.current_player = "X"
        self.winner = None
        self.game_over = False
        self.running = False
        self.state_machine = None
        self.status_text = "Your turn! Say your move."
        self._press_time = 0
        self._render_thread = None
        self._cursor = [1, 1]
        self._waiting_for_voice = True

    def start(self, state_machine):
        self.state_machine = state_machine
        self.running = True
        self._render_thread = threading.Thread(target=self._render_loop, daemon=True)
        self._render_thread.start()

    def stop(self):
        self.running = False
        self.game_over = True

    def on_button_press(self):
        self._press_time = time.time()

    def on_button_release(self):
        hold = time.time() - self._press_time
        if hold > 1.0:
            self.running = False
            if self.state_machine:
                self.state_machine.exit_game()
            return

        if not self.game_over and self.current_player == "X":
            r, c = self._cursor[0], self._cursor[1]
            if self.board[r][c] == " ":
                self._place_move(r, c, "X")
            else:
                c = (c + 1) % 3
                if c == 0:
                    r = (r + 1) % 3
                self._cursor = [r, c]

    def human_move_voice(self, text):
        text = text.lower().strip()
        pos_map = {
            "top left": (0, 0), "top center": (0, 1), "top middle": (0, 1), "top right": (0, 2),
            "middle left": (1, 0), "center left": (1, 0), "center": (1, 1), "middle": (1, 1),
            "middle right": (1, 2), "center right": (1, 2),
            "bottom left": (2, 0), "bottom center": (2, 1), "bottom middle": (2, 1), "bottom right": (2, 2),
        }
        for phrase, (r, c) in pos_map.items():
            if phrase in text:
                if self.board[r][c] == " ":
                    self._place_move(r, c, "X")
                    return f"You placed X at {phrase}."
                else:
                    return f"That spot is taken! Try another."
        return "I didn't understand. Try saying 'top left', 'center', 'bottom right', etc."

    def ai_move(self, position):
        if self.game_over:
            return "The game is already over!"
        if self.current_player != "O":
            return "It's not my turn yet!"
        if position in NUMPAD_TO_CELL:
            r, c = NUMPAD_TO_CELL[position]
            if self.board[r][c] == " ":
                self._place_move(r, c, "O")
                pos_name = self._cell_name(r, c)
                if self.winner:
                    return f"I placed O at {pos_name}. {self._result_text()}"
                return f"I placed O at {pos_name}. Your turn!"
            else:
                return "That spot is taken. I need to pick another spot."
        return "Invalid position."

    def _place_move(self, r, c, player):
        self.board[r][c] = player
        w = self._check_winner()
        if w:
            self.winner = w
            self.game_over = True
            self.status_text = self._result_text()
        elif self._is_full():
            self.game_over = True
            self.winner = "draw"
            self.status_text = "It's a draw!"
        else:
            self.current_player = "O" if player == "X" else "X"
            if self.current_player == "X":
                self.status_text = "Your turn!"
            else:
                self.status_text = "My turn, thinking..."

    def _result_text(self):
        if self.winner == "X":
            return "You win! Great job!"
        elif self.winner == "O":
            return "I win! Good game though!"
        return "It's a draw!"

    def _cell_name(self, r, c):
        rows = ["top", "middle", "bottom"]
        cols = ["left", "center", "right"]
        return f"{rows[r]} {cols[c]}"

    def _check_winner(self):
        b = self.board
        for i in range(3):
            if b[i][0] == b[i][1] == b[i][2] != " ":
                return b[i][0]
            if b[0][i] == b[1][i] == b[2][i] != " ":
                return b[0][i]
        if b[0][0] == b[1][1] == b[2][2] != " ":
            return b[0][0]
        if b[0][2] == b[1][1] == b[2][0] != " ":
            return b[0][2]
        return None

    def _is_full(self):
        return all(self.board[r][c] != " " for r in range(3) for c in range(3))

    def _render_loop(self):
        while self.running:
            self._render()
            time.sleep(0.1)

    def _render(self):
        W, H = self.SCREEN_W, self.SCREEN_H
        img = Image.new("RGB", (W, H), (40, 0, 60))
        draw = ImageDraw.Draw(img)

        try:
            font = ImageFont.truetype("assets/fonts/NotoSansSC-Bold.ttf", 14)
            big_font = ImageFont.truetype("assets/fonts/NotoSansSC-Bold.ttf", 36)
        except Exception:
            font = ImageFont.load_default()
            big_font = font

        draw.text((10, 5), self.status_text, fill="white", font=font)

        grid_size = 180
        cell_size = grid_size // 3
        ox = (W - grid_size) // 2
        oy = 40

        for i in range(1, 3):
            y = oy + i * cell_size
            draw.line([(ox, y), (ox + grid_size, y)], fill=(200, 200, 200), width=2)
            x = ox + i * cell_size
            draw.line([(x, oy), (x, oy + grid_size)], fill=(200, 200, 200), width=2)

        for r in range(3):
            for c in range(3):
                cx = ox + c * cell_size + cell_size // 2
                cy = oy + r * cell_size + cell_size // 2
                piece = self.board[r][c]
                if piece == "X":
                    s = cell_size // 3
                    draw.line([(cx - s, cy - s), (cx + s, cy + s)], fill=(255, 100, 150), width=4)
                    draw.line([(cx + s, cy - s), (cx - s, cy + s)], fill=(255, 100, 150), width=4)
                elif piece == "O":
                    s = cell_size // 3
                    draw.ellipse(
                        [(cx - s, cy - s), (cx + s, cy + s)],
                        outline=(100, 200, 255), width=4,
                    )

        if not self.game_over and self.current_player == "X":
            cr, cc = self._cursor
            cx = ox + cc * cell_size + cell_size // 2
            cy = oy + cr * cell_size + cell_size // 2
            s = cell_size // 2 - 4
            draw.rectangle(
                [(cx - s, cy - s), (cx + s, cy + s)],
                outline=(255, 255, 0), width=2,
            )

        if self.game_over:
            overlay = Image.new("RGBA", (W, 50), (0, 0, 0, 160))
            img.paste(Image.alpha_composite(
                Image.new("RGBA", (W, 50), (0, 0, 0, 0)), overlay
            ).convert("RGB"), (0, H - 70))
            draw.text(
                (W // 2 - 60, H - 65), self.status_text,
                fill=(255, 255, 100), font=font,
            )

        display_state.game_surface = img
