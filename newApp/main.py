import signal
import sys
import time
import threading

from config import Config
from core.state_machine import StateMachine
from ui.renderer import RenderThread


def main():
    if not Config.validate():
        print("Configuration errors found. Please check your .env file.")
        print("Copy env.template to .env and fill in your API keys.")
        sys.exit(1)

    try:
        from driver.whisplay import WhisplayBoard
        board = WhisplayBoard()
        print(f"[LCD] Initialized: {board.LCD_WIDTH}x{board.LCD_HEIGHT}")
    except Exception as e:
        print(f"[Driver] Failed to initialize Whisplay board: {e}")
        print("[Driver] Running in headless mode (no display/GPIO)")
        board = None

    font_path = Config.CUSTOM_FONT_PATH or "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    render_thread = None
    if board:
        render_thread = RenderThread(board, font_path, fps=30)
        render_thread.start()
        board.set_backlight(100)

    sm = StateMachine(board, render_thread)

    def cleanup(signum=None, frame=None):
        print("\n[System] Shutting down...")
        sm.stop()
        if render_thread:
            render_thread.stop()
        if board:
            board.set_rgb(0, 0, 0)
            board.set_backlight(0)
            board.cleanup()
        sys.exit(0)

    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        cleanup()


if __name__ == "__main__":
    main()
