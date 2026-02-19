"""PiSugar battery monitor — polls level, updates display, flashes LED on low."""

import socket
import threading
import time

# Battery level thresholds
LOW_THRESHOLD = 20       # percent — start flashing
CRITICAL_THRESHOLD = 10  # percent — faster flash
POLL_INTERVAL = 60       # seconds between PiSugar polls
FLASH_INTERVAL = 60      # seconds between low-battery LED alerts
FLASH_DURATION = 5       # seconds of rapid blinking per alert
FLASH_RATE = 0.15        # seconds per on/off cycle during flash

PISUGAR_HOST = "127.0.0.1"
PISUGAR_PORT = 8423


def _query_pisugar(command):
    """Send a command to the PiSugar daemon and return the response."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(3)
            s.connect((PISUGAR_HOST, PISUGAR_PORT))
            s.sendall((command + "\n").encode())
            data = s.recv(256).decode().strip()
            return data
    except Exception:
        return None


def get_battery_level():
    """Return battery percentage (0-100) or None if unavailable."""
    resp = _query_pisugar("get battery")
    if resp and ":" in resp:
        try:
            return float(resp.split(":")[1].strip())
        except (ValueError, IndexError):
            pass
    return None


def get_charging():
    """Return True if charging, False if not, None if unavailable."""
    resp = _query_pisugar("get battery_charging")
    if resp and ":" in resp:
        val = resp.split(":")[1].strip().lower()
        return val == "true"
    return None


def _battery_color(level, charging):
    """Pick display color for battery indicator."""
    if charging:
        return (100, 200, 255)   # light blue
    if level <= CRITICAL_THRESHOLD:
        return (255, 0, 0)       # red
    if level <= LOW_THRESHOLD:
        return (255, 140, 0)     # orange
    if level <= 50:
        return (255, 220, 0)     # yellow
    return (85, 255, 0)          # green


class BatteryMonitor:
    """Background thread: polls PiSugar, updates display, flashes LED on low."""

    def __init__(self, board, display_state):
        self.board = board
        self.display_state = display_state
        self._running = False
        self._thread = None
        self._last_flash_time = 0
        self._flash_lock = threading.Lock()

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _run(self):
        # Quick initial check
        level = get_battery_level()
        if level is None:
            print("[Battery] PiSugar not detected, monitor disabled")
            return
        print(f"[Battery] Monitor started (level={level:.0f}%)")

        while self._running:
            try:
                self._poll()
            except Exception as e:
                print(f"[Battery] Poll error: {e}")
            time.sleep(POLL_INTERVAL)

    def _poll(self):
        level = get_battery_level()
        if level is None:
            return

        charging = get_charging()
        color = _battery_color(level, charging or False)

        self.display_state.update(
            battery_level=int(level),
            battery_color=color,
        )

        # Low-battery LED alert
        if level <= LOW_THRESHOLD and not charging:
            now = time.time()
            if now - self._last_flash_time >= FLASH_INTERVAL:
                self._last_flash_time = now
                self._flash_low_battery(level)

    def _flash_low_battery(self, level):
        """Rapid red LED blink for FLASH_DURATION seconds."""
        if not self.board:
            return
        if not self._flash_lock.acquire(blocking=False):
            return

        rate = FLASH_RATE
        if level <= CRITICAL_THRESHOLD:
            rate = FLASH_RATE / 2  # twice as fast when critical

        def _do_flash():
            try:
                # Save current LED color
                saved = (
                    getattr(self.board, "_current_r", 0),
                    getattr(self.board, "_current_g", 0),
                    getattr(self.board, "_current_b", 0),
                )
                end_time = time.time() + FLASH_DURATION
                on = False
                while time.time() < end_time and self._running:
                    on = not on
                    if on:
                        self.board.set_rgb(255, 0, 0)
                    else:
                        self.board.set_rgb(0, 0, 0)
                    time.sleep(rate)
                # Restore
                self.board.set_rgb(*saved)

                # Also show an alert on screen
                self.display_state.update(
                    alert_text=f"Battery low: {int(level)}%",
                    alert_level="warn" if level > CRITICAL_THRESHOLD else "error",
                    alert_duration=3.0,
                )
            except Exception as e:
                print(f"[Battery] Flash error: {e}")
            finally:
                self._flash_lock.release()

        threading.Thread(target=_do_flash, daemon=True).start()
