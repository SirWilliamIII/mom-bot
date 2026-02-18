import atexit
import os
import signal
import subprocess
import sys
import time
import threading

from config import Config
from core.state_machine import create_state_machine
from ui.renderer import RenderThread


def _clear_pycache():
    """Remove all __pycache__ dirs so stale bytecode never runs.

    This is cheap (<10ms) and prevents the maddening issue where
    git pull updates .py files but Python keeps running old .pyc.
    """
    app_dir = os.path.dirname(os.path.abspath(__file__))
    for root, dirs, _files in os.walk(app_dir):
        for d in dirs:
            if d == "__pycache__":
                cache_path = os.path.join(root, d)
                try:
                    import shutil
                    shutil.rmtree(cache_path)
                except Exception:
                    pass


def _force_kill_audio():
    """Kill any lingering arecord/aplay processes."""
    for proc_name in ("arecord", "aplay"):
        try:
            subprocess.run(["pkill", "-9", "-f", proc_name],
                           capture_output=True, timeout=2)
        except Exception:
            pass


def _kill_previous_instance():
    """Kill any previous Python process using main.py to free GPIO pins.

    GPIO pins (via lgpio/gpiozero) are held at the kernel level per-process.
    If a previous run crashed or was killed without cleanup, the only way
    to free the pins is to kill that process.

    Must also stop the systemd service first — otherwise Restart=on-failure
    will immediately respawn the killed process, re-claiming GPIO.
    """
    # Stop the systemd service if it's running (prevents respawn after kill)
    try:
        subprocess.run(["sudo", "systemctl", "stop", "mombot"],
                       capture_output=True, timeout=5)
        print("[Cleanup] Stopped mombot systemd service")
    except Exception:
        pass

    my_pid = os.getpid()
    killed_any = False
    for pattern in ("python.*main\\.py", "python.*chatbot-ui\\.py"):
        try:
            result = subprocess.run(
                ["pgrep", "-f", pattern],
                capture_output=True, text=True, timeout=3,
            )
            for line in result.stdout.strip().split("\n"):
                pid = line.strip()
                if pid and int(pid) != my_pid:
                    print(f"[Cleanup] Killing previous instance (PID {pid})")
                    subprocess.run(["kill", "-9", pid],
                                   capture_output=True, timeout=2)
                    killed_any = True
        except Exception:
            pass

    if killed_any:
        # Kernel needs time to close fds and release GPIO claims
        time.sleep(2.0)


def _sync_asoundrc():
    """Copy asound.conf to ~/.asoundrc so ALSA picks up our full-duplex config.

    Always overwrites -- we want git-tracked changes to propagate automatically.
    """
    src = os.path.join(os.path.dirname(__file__), "asound.conf")
    dst = os.path.expanduser("~/.asoundrc")
    if os.path.exists(src):
        try:
            import shutil
            shutil.copy2(src, dst)
            print(f"[ALSA] Synced {src} -> {dst}")
        except Exception as e:
            print(f"[ALSA] Failed to sync asoundrc: {e}")


def main():
    if not Config.validate():
        print("Configuration errors found. Please check your .env file.")
        print("Copy env.template to .env and fill in your API keys.")
        sys.exit(1)

    # Clear stale bytecode so git pull always takes effect
    _clear_pycache()

    # Kill any previous instance (frees GPIO) + orphaned audio processes
    _kill_previous_instance()
    _force_kill_audio()

    # Sync ALSA config so audio changes propagate on git pull
    _sync_asoundrc()

    from driver.whisplay import WhisplayBoard
    from gpiozero import Device
    board = None
    for attempt in range(3):
        try:
            board = WhisplayBoard()
            print(f"[LCD] Initialized: {board.LCD_WIDTH}x{board.LCD_HEIGHT}")
            break
        except Exception as e:
            # Close gpiozero's factory so the next attempt starts clean
            if Device.pin_factory is not None:
                try:
                    Device.pin_factory.close()
                except Exception:
                    pass
                Device.pin_factory = None
            if attempt < 2:
                print(f"[Driver] GPIO busy (attempt {attempt+1}/3), retrying in 3s...")
                time.sleep(3)
            else:
                import traceback
                print(f"[Driver] Failed to initialize Whisplay board: {e}")
                traceback.print_exc()
                print("[Driver] Running in headless mode (no display/GPIO)")
                board = None

    font_path = Config.CUSTOM_FONT_PATH or "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    render_thread = None
    if board:
        render_thread = RenderThread(board, font_path, fps=30)
        render_thread.start()
        board.set_backlight(100)

    sm = create_state_machine(board, render_thread)

    # --- Cleanup: runs on SIGTERM, SIGINT, and atexit ---
    _cleanup_done = threading.Event()

    def cleanup(signum=None, frame=None):
        if _cleanup_done.is_set():
            return
        _cleanup_done.set()

        sig_name = ""
        if signum is not None:
            sig_name = signal.Signals(signum).name if hasattr(signal, 'Signals') else str(signum)
        print(f"\n[System] Shutting down... (signal={sig_name or 'exit'})")

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
                board.screen_off()
                board.cleanup()
        except Exception:
            pass
        _force_kill_audio()
        print("[System] Cleanup complete")

    # atexit ensures cleanup runs even on normal exit or unhandled exception.
    # Signal handlers cover SIGTERM (systemd stop) and SIGINT (Ctrl-C).
    atexit.register(cleanup)
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))

    # Voice Agent mode: user holds button while speaking (push-to-talk).
    # Legacy mode: button press/release handled entirely by the state machine.

    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)


if __name__ == "__main__":
    main()
