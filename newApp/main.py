import os
import signal
import subprocess
import sys
import time
import threading

from config import Config
from core.state_machine import create_state_machine
from ui.renderer import RenderThread


def _force_kill_audio():
    """Kill any lingering arecord/aplay processes."""
    for proc_name in ("arecord", "aplay"):
        try:
            subprocess.run(["pkill", "-9", "-f", proc_name],
                           capture_output=True, timeout=2)
        except Exception:
            pass


def main():
    if not Config.validate():
        print("Configuration errors found. Please check your .env file.")
        print("Copy env.template to .env and fill in your API keys.")
        sys.exit(1)

    # Kill any orphaned audio processes from a previous crash
    _force_kill_audio()

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

    sm = create_state_machine(board, render_thread)

    def cleanup(signum=None, frame=None):
        print("\n[System] Shutting down...")
        try:
            sm.stop()
        except Exception:
            pass
        try:
            if render_thread:
                render_thread.stop()
        except Exception:
            pass
        try:
            if board:
                board.set_rgb(0, 0, 0)
                board.set_backlight(0)
                board.cleanup()
        except Exception:
            pass
        _force_kill_audio()
        # Force exit -- don't let daemon threads hang the process
        os._exit(0)

    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    # Start Voice Agent if in that mode
    if Config.VOICE_AGENT_MODE:
        sm.start_agent()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        cleanup()


if __name__ == "__main__":
    main()
