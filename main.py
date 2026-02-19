import atexit
import os
import signal
import subprocess
import sys
import time
import threading

from config import Config
from core.state_machine import create_state_machine
from services.battery import BatteryMonitor
from ui.renderer import RenderThread, display_state


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
    """Kill any lingering arecord/aplay processes and clean stale ALSA IPC.

    dsnoop (ipc_key 666666) and dmix (ipc_key 555555) create System V
    shared memory segments.  If the "server" process that owns the segment
    dies uncleanly, any subsequent arecord/aplay trying to attach blocks in
    uninterruptible D-state — and that blocks the Python process too.
    Removing the stale IPC forces a fresh segment on next open.
    """
    for proc_name in ("arecord", "aplay"):
        try:
            subprocess.run(["pkill", "-9", "-f", proc_name],
                           capture_output=True, timeout=2)
        except Exception:
            pass

    # Remove stale dsnoop/dmix shared-memory segments from asound.conf
    for ipc_key in (555555, 666666):
        try:
            subprocess.run(["ipcrm", "-M", str(ipc_key)],
                           capture_output=True, timeout=2)
        except Exception:
            pass


def _kill_previous_instance():
    """Kill any previous Python process using main.py to free GPIO pins.

    GPIO pins (via lgpio) are held at the kernel level per-process.
    If a previous run crashed or was killed without cleanup, the only way
    to free the pins is to kill that process.

    Critical ordering: kill audio subprocesses (arecord/aplay) FIRST.
    If the old Python process is blocked in a kernel audio call it enters
    uninterruptible sleep (D-state) and won't respond to SIGKILL until
    the blocking syscall returns.  Killing audio first unblocks it.

    Must also stop the systemd service first — otherwise Restart=on-failure
    will immediately respawn the killed process, re-claiming GPIO.
    """
    # Step 1: kill audio children so the old python can exit D-state.
    _force_kill_audio()
    time.sleep(0.5)

    # Stop the systemd service if it's running (prevents respawn after kill).
    # Skip if WE are the service (INVOCATION_ID is set by systemd) — otherwise
    # we'd stop ourselves and exit before doing anything useful.
    if not os.environ.get("INVOCATION_ID"):
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
                    # SIGTERM first — lets the process run cleanup/gpio_free
                    subprocess.run(["kill", "-15", pid],
                                   capture_output=True, timeout=2)
                    time.sleep(1)
                    # SIGKILL as backup
                    subprocess.run(["kill", "-9", pid],
                                   capture_output=True, timeout=2)
                    killed_any = True
        except Exception:
            pass

    # Also kill anything holding /dev/gpiochip* directly
    for chip in ("/dev/gpiochip4", "/dev/gpiochip0"):
        if os.path.exists(chip):
            try:
                result = subprocess.run(
                    ["sudo", "fuser", chip],
                    capture_output=True, text=True, timeout=3,
                )
                # fuser outputs PIDs to stderr, not stdout
                pids = (result.stderr + " " + result.stdout).strip().split()
                for pid in pids:
                    pid = pid.strip()
                    if pid and int(pid) != my_pid:
                        print(f"[Cleanup] Killing process holding {chip} (PID {pid})")
                        subprocess.run(["kill", "-9", pid],
                                       capture_output=True, timeout=2)
                        killed_any = True
            except Exception:
                pass

    if killed_any:
        # Give kernel time to close fds and release GPIO claims
        time.sleep(3.0)


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

    # Kill any previous instance (frees GPIO); audio is killed first inside
    # this call to unblock any D-state audio syscalls in the old process.
    _kill_previous_instance()

    # Sync ALSA config so audio changes propagate on git pull
    _sync_asoundrc()

    # Initialize WM8960 mixer (output routing switches, volumes).
    # Must happen after asoundrc sync, before any audio playback.
    from services.audio import init_mixer
    init_mixer()

    from driver.whisplay import WhisplayBoard
    board = None
    for attempt in range(3):
        try:
            board = WhisplayBoard()
            print(f"[LCD] Initialized: {board.LCD_WIDTH}x{board.LCD_HEIGHT}")
            break
        except Exception as e:
            import traceback
            print(f"[Driver] GPIO init failed (attempt {attempt+1}/3): {e}")
            traceback.print_exc()
            if attempt < 2:
                time.sleep(3)
            else:
                # GPIO is still busy after killing everything — the old process
                # is stuck in uninterruptible D-state (almost always a stuck
                # ALSA driver).  Only a reboot can free it; schedule one now.
                print("[Driver] GPIO permanently busy. Rebooting to self-heal...")
                try:
                    subprocess.run(["sudo", "systemctl", "reboot"],
                                   capture_output=True, timeout=10)
                except Exception:
                    pass
                time.sleep(30)   # wait for reboot to take effect
                sys.exit(1)

    font_path = Config.CUSTOM_FONT_PATH or "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

    render_thread = None
    if board:
        render_thread = RenderThread(board, font_path, fps=30)
        render_thread.start()
        board.set_backlight(100)

    sm = create_state_machine(board, render_thread)

    # --- Battery monitor ---
    battery_mon = BatteryMonitor(board, display_state)
    battery_mon.start()

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
            battery_mon.stop()
        except Exception:
            pass
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
