import os
import time
import threading
import tempfile

from config import Config
from core.conversation import Conversation
from ui.renderer import display_state, RenderThread
from ui.utils import ColorUtils
from ui.framework import TURN_BASES

# LED colors derived from the turn palette
TURN_RGB = {
    "green":  TURN_BASES["green"],    # (0, 210, 80)
    "red":    TURN_BASES["red"],      # (220, 30, 45)
    "amber":  TURN_BASES["amber"],    # (255, 165, 0)
    "sleep":  TURN_BASES["sleep"],    # (90, 35, 110)
    "paused": TURN_BASES["paused"],   # (120, 120, 140)
}


# ---------- VOICE AGENT MODE ----------

class VoiceAgentStateMachine:
    """State machine for Deepgram Voice Agent mode.

    Conversation flow:
        asleep  → screen off, LED off, process alive, button polling active
                → double-click = wake up → idle
        idle    → showing sleep screen, waiting for user
                → hold button = connect agent + start talking → active
                → 2 min idle → asleep
        active  → hold button = push-to-talk (mic live while held)
                → release button = agent responds after 0.5s delay
                → double-click = end conversation → asleep
                → hold ≥10s = deep sleep → asleep
                → user says 'bye'/'goodbye' = end conversation → asleep
                → 30s no activity = end conversation → asleep
        game    → local game running, agent paused
        music   → music playing, agent still active

    Threading safety:
        All state mutations and timer callbacks are guarded by self._lock.
        Every timer callback captures an epoch at creation time and bails
        if the epoch has changed (meaning a state transition happened).
    """

    BYE_WORDS = {"bye", "goodbye", "ok bye", "good bye", "see ya", "see you", "bye bye"}
    IDLE_TIMEOUT_SEC = 30
    IDLE_SLEEP_SEC = 120     # 2 min idle → deep sleep
    DOUBLE_CLICK_SEC = 0.4
    RESPONSE_DELAY_SEC = 0.5

    _RGB_MIN_INTERVAL = 0.4

    def __init__(self, board, render_thread):
        self.board = board
        self.render_thread = render_thread
        self._lock = threading.RLock()
        self._epoch = 0           # incremented on every state change; stale timers bail out
        self.state = "idle"
        self.running = True
        self._active_game = None
        self._agent = None
        self._button_press_time = 0
        self._holding = False
        self._last_click_time = 0
        self._current_rgb = None
        self._last_rgb_time = 0

        # Timers (all guarded by epoch)
        self._idle_timer = None

        from services.audio import set_volume, set_capture_volume
        set_volume(70)
        set_capture_volume(100)

        self._set_state("asleep")

    # --- Timer helpers (epoch-safe) ---

    def _start_timer(self, seconds, callback):
        """Create a daemon timer that checks epoch before firing."""
        my_epoch = self._epoch

        def _guarded():
            with self._lock:
                if self._epoch != my_epoch or not self.running:
                    return
                callback()

        t = threading.Timer(seconds, _guarded)
        t.daemon = True
        t.start()
        return t

    def _cancel_timer(self, attr_name):
        """Cancel a timer stored in self.<attr_name>."""
        timer = getattr(self, attr_name, None)
        if timer:
            timer.cancel()
            setattr(self, attr_name, None)

    def _cancel_all_timers(self):
        for name in ("_idle_timer",):
            self._cancel_timer(name)

    # --- Agent lifecycle ---

    def start_agent(self):
        from services.voice_agent import VoiceAgent

        self._update_display(
            status="listening",
            emoji="🎤",
            text="I'm listening...",
            turn="green",
            scroll_speed=0,
            image_path="",
        )

        self._agent = VoiceAgent(on_event=self._on_agent_event)
        self._agent.set_state_machine(self)

        def do_connect():
            self._agent.connect()
            with self._lock:
                if not self._agent or not self._agent.is_running:
                    self._update_display(
                        text="Couldn't connect. Check WiFi?",
                        turn="red",
                    )
                    self._set_state("asleep")
                    return
                # Check actual button state at connect time
                if self._holding:
                    self._agent.set_input_enabled(True)
                else:
                    self._agent.set_input_enabled(False)
                    self._agent.suppress_output_for(self.RESPONSE_DELAY_SEC)
                self._set_state("active")

        threading.Thread(target=do_connect, daemon=True).start()

    def _end_conversation(self, reason=""):
        with self._lock:
            print(f"[State] Ending conversation: {reason}")
            agent = self._agent
            self._agent = None
        if agent:
            if reason != "timeout":
                agent.inject_user_message(
                    "[SYSTEM: The conversation is ending. Say a brief, warm goodbye "
                    "in 1 sentence. Be sweet about it.]"
                )
                time.sleep(3)
            agent.disconnect()
        with self._lock:
            self._set_state("asleep")

    def stop(self):
        with self._lock:
            self.running = False
            self._cancel_all_timers()
        if self._agent:
            self._agent.disconnect()
        if self._active_game:
            self._active_game.stop()
        # Graceful screen shutdown
        if self.board:
            try:
                self.board.set_rgb(0, 0, 0)
                self.board.screen_off()
            except Exception:
                pass

    # --- Activity tracking & idle timeout ---

    def _touch_activity(self):
        self._restart_idle_timer()

    def _restart_idle_timer(self):
        self._cancel_timer("_idle_timer")
        self._idle_timer = self._start_timer(
            self.IDLE_TIMEOUT_SEC, self._on_idle_timeout
        )

    def _on_idle_timeout(self):
        if self.state == "active":
            print(f"[State] No activity for {self.IDLE_TIMEOUT_SEC}s")
            threading.Thread(
                target=self._end_conversation, args=("timeout",), daemon=True
            ).start()

    # --- Bye detection ---

    def _check_for_bye(self, text):
        if not text:
            return False
        cleaned = text.lower().strip().rstrip(".!?,")
        return any(cleaned == bye or cleaned.endswith(bye) for bye in self.BYE_WORDS)

    # --- State management ---

    def _set_state(self, new_state, **kwargs):
        """Transition to a new state. Caller should hold self._lock."""
        old = self.state
        self.state = new_state
        self._epoch += 1
        self._cancel_all_timers()
        print(f"[State] {old} -> {new_state}")

        if self.board:
            self.board.on_button_press(None)
            self.board.on_button_release(None)

        handler = {
            "idle": self._enter_idle,
            "active": self._enter_active,
            "game": self._enter_game,
            "music": self._enter_music,
            "asleep": self._enter_asleep,
        }.get(new_state)

        if handler:
            handler(**kwargs)

    def _enter_idle(self, **kwargs):
        name = Config.COMPANION_NAME
        text = kwargs.get("text", f"Hold button to talk to {name}!")
        # Show family photo on idle screen (if configured), otherwise fall back
        idle_img = Config.IDLE_IMAGE_PATH
        if idle_img and os.path.exists(idle_img):
            self._update_display(
                status="sleeping",
                emoji="🐷",
                text=text,
                turn="sleep",
                scroll_speed=0,
                image_path=idle_img,
            )
        else:
            self._update_display(
                status="sleeping",
                emoji="🐷",
                text=text,
                turn="sleep",
                scroll_speed=0,
            )
        if self.board:
            self.board.on_button_press(self._on_button_press_idle)
            self.board.on_button_release(self._on_button_release_idle)
        # Auto-sleep after 2 min idle
        self._idle_timer = self._start_timer(
            self.IDLE_SLEEP_SEC, self._on_idle_sleep
        )

    def _enter_active(self, **kwargs):
        name = Config.COMPANION_NAME
        # Clear idle image — active states use Piglet sprite
        display_state.update(image_path="")
        # Don't overwrite "listening" display if user is still holding button
        if not self._holding:
            self._update_display(
                status="ready",
                emoji="🐷",
                text=kwargs.get("text", f"{name} is here!"),
                turn="red",
                scroll_speed=0,
            )
        self._touch_activity()
        if self.board:
            self.board.on_button_press(self._on_button_press_active)
            self.board.on_button_release(self._on_button_release_active)

    def _enter_game(self, **kwargs):
        game = kwargs.get("game")
        if game:
            self._active_game = game
            self._update_display(
                status="playing",
                emoji="🎮",
                rgb=(255, 0, 255),
            )
            if self.board:
                self.board.on_button_press(game.on_button_press)
                self.board.on_button_release(game.on_button_release)
            game.start(self)

    def _enter_music(self, **kwargs):
        self._update_display(
            status="playing music",
            emoji="🎵",
            rgb=(255, 105, 180),
            scroll_speed=0,
        )
        self._touch_activity()
        if self.board:
            self.board.on_button_press(self._on_button_press_active)
            self.board.on_button_release(self._on_button_release_active)

    def exit_game(self):
        with self._lock:
            if self._active_game:
                self._active_game.stop()
                self._active_game = None
            display_state.game_surface = None
            self._set_state("active", text="That was fun! Want to play again?")

    # --- Deep sleep (screen off, process alive) ---

    def _enter_asleep(self, **kwargs):
        """Deep sleep: screen off, LED off, process stays alive.

        Button polling continues — double-click wakes back to idle.
        """
        self._holding = False
        # Stop rendering first so no frame draws over our black screen
        if self.render_thread:
            self.render_thread.running = False
            time.sleep(0.05)  # let current frame finish
        # Go fully dark — LCD sleep mode + backlight off
        if self.board:
            self.board.set_rgb(0, 0, 0)
            self.board.screen_off()
        print("[State] Deep sleep — double-click to wake")
        # Register wake-up button handler (double-click only)
        self._last_click_time = 0
        if self.board:
            self.board.on_button_press(self._on_button_press_asleep)

    def _on_button_press_asleep(self):
        """Double-click in deep sleep wakes the device."""
        with self._lock:
            now = time.time()
            if now - self._last_click_time < self.DOUBLE_CLICK_SEC:
                print("[Button] Double-click (asleep) -> waking up")
                self._last_click_time = 0
                self._wake_up()
            else:
                self._last_click_time = now

    def _wake_up(self):
        """Restart the render thread and go to idle."""
        if self.board:
            self.board.screen_on()
        # Spin up a fresh render thread (old one exited its loop)
        if self.render_thread:
            new_render = RenderThread(
                self.render_thread.board,
                self.render_thread.font_path,
                self.render_thread.fps,
            )
            new_render.start()
            self.render_thread = new_render
        self._set_state("idle")

    def _on_idle_sleep(self):
        """Idle screen too long — go to deep sleep to save power."""
        if self.state == "idle":
            print(f"[State] Idle for {self.IDLE_SLEEP_SEC}s -> deep sleep")
            self._set_state("asleep")

    # --- Notification flash (backlight + LED) ---

    _flash_lock = threading.Lock()

    def _notify_flash(self, times=2, on_ms=80, off_ms=60):
        """Quick backlight + LED pulse to signal 'new content'."""
        if not self.board:
            return
        if not self._flash_lock.acquire(blocking=False):
            return

        def _do_flash():
            try:
                saved_rgb = self._current_rgb or (0, 0, 0)
                for _ in range(times):
                    self.board.set_rgb(255, 255, 255)
                    self.board.set_backlight(0)
                    time.sleep(off_ms / 1000)
                    self.board.set_rgb(*saved_rgb)
                    self.board.set_backlight(100)
                    time.sleep(on_ms / 1000)
            except Exception as e:
                print(f"[Flash] Error: {e}")
            finally:
                self._flash_lock.release()

        threading.Thread(target=_do_flash, daemon=True).start()

    # --- Button handling: IDLE state ---

    def _on_button_press_idle(self):
        with self._lock:
            now = time.time()
            if now - self._last_click_time < self.DOUBLE_CLICK_SEC:
                print("[Button] Double-click (idle) -> starting conversation")
                self._last_click_time = 0
                self._button_press_time = now
                self._holding = True
                self.start_agent()
            else:
                self._last_click_time = now

    def _on_button_release_idle(self):
        with self._lock:
            self._holding = False
            if self._agent:
                self._agent.set_input_enabled(False)
                self._agent.suppress_output_for(self.RESPONSE_DELAY_SEC)
                self._update_display(
                    status="thinking",
                    emoji="🤔",
                    text="Let me think...",
                    turn="amber",
                )

    # --- Button handling: ACTIVE state ---

    def _on_button_press_active(self):
        with self._lock:
            self._button_press_time = time.time()
            self._holding = True

            now = time.time()
            if now - self._last_click_time < self.DOUBLE_CLICK_SEC:
                print("[Button] Double-click -> ending conversation")
                self._last_click_time = 0
                self._holding = False
                threading.Thread(
                    target=self._end_conversation,
                    args=("double_click",),
                    daemon=True,
                ).start()
                return

            # Silence agent and enable mic (push-to-talk)
            if self._agent:
                self._agent.silence_agent()
                self._agent.set_input_enabled(True)
            self._update_display(
                status="listening",
                emoji="🎤",
                text="I'm listening...",
                turn="green",
            )
            self._touch_activity()

    def _on_button_release_active(self):
        with self._lock:
            self._holding = False
            self._last_click_time = time.time()

            if self._agent:
                self._agent.set_input_enabled(False)
                self._agent.suppress_output_for(self.RESPONSE_DELAY_SEC)
            self._update_display(
                status="thinking",
                emoji="🤔",
                text="Let me think...",
                turn="amber",
            )
            self._touch_activity()

    # --- Voice Agent event handler ---

    def _on_agent_event(self, event_type, data):
        """Called from VoiceAgent receiver thread."""
        with self._lock:
            if not self.running or self.state not in ("active", "music", "game"):
                return
            self._handle_agent_event(event_type, data)

    def _handle_agent_event(self, event_type, data):
        if event_type in ("ready", "connected"):
            pass

        elif event_type == "user_speaking":
            self._touch_activity()

        elif event_type == "agent_thinking":
            self._touch_activity()
            if not self._holding:
                self._update_display(
                    status="thinking",
                    emoji="🤔",
                    text="Let me think...",
                    turn="amber",
                )

        elif event_type == "agent_speaking":
            self._touch_activity()
            if not self._holding:
                self._update_display(
                    status="talking",
                    turn="red",
                )

        elif event_type == "conversation_text":
            role = data.get("role", "")
            content = data.get("content", "")

            if role == "user" and content:
                self._touch_activity()
                if self._check_for_bye(content):
                    print(f"[State] Bye detected in: '{content}'")
                    threading.Thread(
                        target=self._end_conversation,
                        args=("bye",),
                        daemon=True,
                    ).start()
                    return

            if role == "assistant" and content:
                self._touch_activity()
                self._notify_flash(times=2)
                emojis = _extract_emojis(content)
                self._update_display(
                    text=content,
                    emoji=emojis or "🐷",
                    scroll_speed=3,
                )

        elif event_type == "agent_audio_done":
            self._touch_activity()
            if self.state not in ("game", "music") and not self._holding:
                self._update_display(
                    status="ready",
                    turn="red",
                )

        elif event_type == "function_call":
            self._touch_activity()
            name = data.get("name", "")
            self._update_display(
                text=f"Doing: {name}...",
                alert_text=f"Working on: {name}",
                alert_level="info",
                alert_duration=2.0,
            )

        elif event_type == "error":
            desc = data.get("description", "Something went wrong")
            self._update_display(
                text=desc,
                emoji="😟",
                turn="red",
                alert_text="Oops -- hit a snag",
                alert_level="error",
                alert_duration=3.2,
            )

        elif event_type == "disconnected":
            reason = data.get("reason", "")
            if self.state == "active" and reason:
                print(f"[State] Unexpected disconnect: {reason}, reconnecting...")
                self._update_display(
                    alert_text="Connection dropped -- retrying",
                    alert_level="warn",
                    alert_duration=3.0,
                )

                def _reconnect():
                    time.sleep(2)
                    with self._lock:
                        if self.running and self.state == "active":
                            self.start_agent()

                threading.Thread(target=_reconnect, daemon=True).start()

    # --- Display helper ---

    def _update_display(self, **kwargs):
        # If turn is specified, derive RGB from it
        turn = kwargs.get("turn")
        if turn and turn in TURN_RGB:
            kwargs["rgb"] = TURN_RGB[turn]

        if "rgb" in kwargs and self.board:
            rgb = kwargs.pop("rgb")
            now = time.time()
            if rgb != self._current_rgb and (now - self._last_rgb_time) >= self._RGB_MIN_INTERVAL:
                self._current_rgb = rgb
                self._last_rgb_time = now
                self.board.set_rgb(*rgb)
            kwargs["rgb_color"] = rgb
        display_state.update(**kwargs)


# ---------- LEGACY MODE (old batch pipeline) ----------

class LegacyStateMachine:
    """Batch-mode state machine (button → record → STT → LLM → TTS).

    Button behavior:
        double-click  = toggle sleep/wake (from any state)
        hold          = listen (record audio while held)
        release       = stop recording → think → speak
        press while speaking = silence Piglet, start listening

    States: asleep → idle → listening → thinking → speaking → idle
    """

    DOUBLE_CLICK_SEC = 0.4  # max gap between clicks to count as double-click
    PHOTO_CYCLE_SEC = 12    # seconds between photo changes in idle

    def __init__(self, board, render_thread):
        from services.audio import set_volume, set_capture_volume

        self.board = board
        self.render_thread = render_thread
        self.conversation = Conversation()
        self.state = "idle"
        self.running = True
        self._recording_path = ""
        self._answer_id = 0
        self._active_game = None
        self._last_press_time = 0
        self._holding = False
        self._photo_timer = None
        self._photos = self._scan_photos()
        self._photo_index = 0

        set_volume(100)
        set_capture_volume(100)

        self._set_state("asleep")

    def _scan_photos(self):
        """Scan photos directory for images."""
        photos_dir = Config.PHOTOS_DIR
        if not os.path.isdir(photos_dir):
            print(f"[Photos] Directory not found: {photos_dir}")
            return []
        exts = (".jpg", ".jpeg", ".png", ".bmp")
        photos = sorted([
            os.path.join(photos_dir, f)
            for f in os.listdir(photos_dir)
            if f.lower().endswith(exts)
        ])
        print(f"[Photos] Found {len(photos)} photos")
        return photos

    def stop(self):
        self.running = False
        from services.audio import stop_recording
        stop_recording()
        if self._active_game:
            self._active_game.stop()
        if self.board:
            self.board.set_rgb(0, 0, 0)
            self.board.screen_off()

    def _set_state(self, new_state, **kwargs):
        old = self.state
        self.state = new_state
        print(f"[State] {old} -> {new_state}")

        # Stop photo slideshow when leaving idle
        if self._photo_timer:
            self._photo_timer.cancel()
            self._photo_timer = None
        # Clear any displayed photo when leaving idle
        if old == "idle" and new_state != "idle":
            display_state.update(image_path="")

        if self.board:
            self.board.on_button_press(None)
            self.board.on_button_release(None)

        handler = {
            "idle": self._enter_idle,
            "listening": self._enter_listening,
            "thinking": self._enter_thinking,
            "speaking": self._enter_speaking,
            "game": self._enter_game,
            "music": self._enter_music,
            "asleep": self._enter_asleep,
        }.get(new_state)

        if handler:
            handler(**kwargs)

    # --- Double-click detection (works in all awake states) ---

    def _on_button_press(self):
        """Universal press handler for awake states.

        Double-click → sleep. Single press → start listening.
        We detect double-click on the SECOND press, so there's
        no delay on single press — listening starts immediately.
        """
        now = time.time()
        gap = now - self._last_press_time
        self._last_press_time = now

        if gap < self.DOUBLE_CLICK_SEC:
            # Double-click → sleep
            print("[Button] Double-click -> asleep")
            self._last_press_time = 0
            from services.audio import stop_playback, stop_recording
            self._answer_id += 1
            stop_playback()
            stop_recording()
            self._set_state("asleep")
            return

        # Single press → listen
        self._holding = True
        if self.state == "speaking":
            self._interrupt_and_listen()
        elif self.state != "listening":
            self._set_state("listening")

    def _on_button_release(self):
        """Universal release handler — stop recording if we were listening."""
        self._holding = False
        if self.state == "listening":
            self._on_release_from_listening()

    # --- Sleep / Wake ---

    def _enter_asleep(self, **kwargs):
        """Screen off, LED off. Double-click to wake."""
        self._last_press_time = 0
        if self.render_thread:
            self.render_thread.running = False
            time.sleep(0.05)
        if self.board:
            self.board.set_rgb(0, 0, 0)
            self.board.screen_off()
            self.board.on_button_press(self._on_button_press_asleep)
        print("[State] Asleep — double-click to wake")

    def _on_button_press_asleep(self):
        """Double-click while asleep wakes the device."""
        now = time.time()
        gap = now - self._last_press_time
        self._last_press_time = now

        if gap < self.DOUBLE_CLICK_SEC:
            print("[Button] Double-click -> waking up")
            self._last_press_time = 0
            self._wake_up()

    def _wake_up(self):
        """Restart render thread and go to idle."""
        if self.board:
            self.board.screen_on()
        if self.render_thread:
            new_render = RenderThread(
                self.render_thread.board,
                self.render_thread.font_path,
                self.render_thread.fps,
            )
            new_render.start()
            self.render_thread = new_render
        self._set_state("idle")

    # --- Idle ---

    def _enter_idle(self, **kwargs):
        self._update_display(
            status="idle",
            emoji="🐷",
            text=kwargs.get("text", "Hold button to talk!"),
            turn="sleep",
            scroll_speed=0,
        )

        # Start photo slideshow if photos exist
        if self._photos:
            self._show_next_photo()

        if self.board:
            self.board.on_button_press(self._on_button_press)
            self.board.on_button_release(self._on_button_release)

    def _show_next_photo(self):
        """Show next photo and schedule the one after."""
        if not self._photos or self.state != "idle":
            return
        photo = self._photos[self._photo_index % len(self._photos)]
        self._photo_index = (self._photo_index + 1) % len(self._photos)
        display_state.update(image_path=photo)
        # Schedule next photo
        self._photo_timer = threading.Timer(self.PHOTO_CYCLE_SEC, self._show_next_photo)
        self._photo_timer.daemon = True
        self._photo_timer.start()

    # --- Listening ---

    def _enter_listening(self, **kwargs):
        from services.audio import start_recording
        self._answer_id += 1
        self._recording_path = os.path.join(
            tempfile.gettempdir(), f"mombot_rec_{int(time.time())}.wav"
        )

        self._update_display(
            status="listening",
            emoji="🎤",
            text="I'm listening...",
            turn="green",
            scroll_speed=0,
        )

        start_recording(self._recording_path)

        if self.board:
            self.board.on_button_press(self._on_button_press)
            self.board.on_button_release(self._on_button_release)

    def _on_release_from_listening(self):
        from services.audio import stop_recording
        print("[State] Release detected, stopping recording...")
        stop_recording()
        self._update_display(turn="amber")
        time.sleep(0.2)

        if os.path.exists(self._recording_path):
            fsize = os.path.getsize(self._recording_path)
            print(f"[State] Recording file size: {fsize} bytes")
            if fsize < 5000:
                print("[State] Recording too short, back to idle")
                self._set_state("idle")
                return
        else:
            print(f"[State] Recording file not found: {self._recording_path}")
            self._set_state("idle")
            return
        self._set_state("thinking")

    # --- Thinking ---

    def _enter_thinking(self, **kwargs):
        self._update_display(
            status="thinking",
            emoji="🤔",
            text="Let me think...",
            turn="amber",
            scroll_speed=0,
        )

        if self.board:
            self.board.on_button_press(self._on_button_press)
            self.board.on_button_release(self._on_button_release)

        thread = threading.Thread(target=self._process_voice, daemon=True)
        thread.start()

    def _process_voice(self):
        from services import stt
        current_id = self._answer_id

        try:
            print("[STT] Recognizing...")
            text = stt.recognize(self._recording_path)
            print(f"[STT] Result: {text}")
        except Exception as e:
            print(f"[STT] Error: {e}")
            self._set_state("idle", text="Sorry, I couldn't hear that. Try again!")
            return

        if not text or len(text.strip()) < 2:
            self._set_state("idle")
            return

        if current_id != self._answer_id:
            return

        self.conversation.add_user_message(text)
        self._update_display(text=f"You said: {text}")

        self._set_state("speaking", user_text=text, answer_id=current_id)

    def _enter_speaking(self, **kwargs):
        answer_id = kwargs.get("answer_id", self._answer_id)

        self._update_display(
            status="answering",
            emoji="🐷",
            turn="red",
            scroll_speed=3,
        )

        if self.board:
            self.board.on_button_press(self._on_button_press)
            self.board.on_button_release(self._on_button_release)

        thread = threading.Thread(
            target=self._generate_and_speak,
            args=(answer_id,),
            daemon=True,
        )
        thread.start()

    def _interrupt_and_listen(self):
        self._answer_id += 1
        from services.audio import stop_playback
        stop_playback()
        self._set_state("listening")

    def _generate_and_speak(self, answer_id):
        from services import llm, tts
        messages = self.conversation.get_messages()
        full_response = ""
        sentence_buffer = ""
        tool_handled = False

        def on_partial(text):
            nonlocal full_response, sentence_buffer
            if answer_id != self._answer_id:
                return
            full_response += text
            sentence_buffer += text
            emojis = _extract_emojis(full_response)
            self._update_display(
                text=full_response,
                emoji=emojis or "🐷",
                scroll_speed=3,
            )

        def on_tool_call(name, args):
            nonlocal tool_handled
            tool_handled = True
            print(f"[LLM] Tool call: {name}({args})")
            self._update_display(text=f"Doing: {name}...")
            self._handle_tool_call(name, args, answer_id)

        def on_done(text):
            pass

        try:
            llm.chat_stream(messages, on_partial, on_tool_call, on_done)
        except Exception as e:
            print(f"[LLM] Error: {e}")
            self._set_state("idle", text="Oops, something went wrong. Try again!")
            return

        if answer_id != self._answer_id:
            return

        if tool_handled:
            return

        if full_response.strip():
            self.conversation.add_assistant_message(full_response)

            sentences, remaining = tts.split_sentences(full_response)
            if remaining:
                sentences.append(remaining)

            for sentence in sentences:
                if answer_id != self._answer_id:
                    return
                try:
                    tts.synthesize_and_play(sentence)
                except Exception as e:
                    print(f"[TTS] Error: {e}")

        if answer_id == self._answer_id:
            self._set_state("idle", text=full_response or "...")

    def _handle_tool_call(self, name, args, answer_id):
        from features.tools import execute_tool
        from services import tts
        result = execute_tool(name, args, self)

        if result and answer_id == self._answer_id:
            if self.state not in ("game",):
                self._update_display(text=result)
                try:
                    tts.synthesize_and_play(result)
                except Exception:
                    pass
                if answer_id == self._answer_id and self.state != "game":
                    self._set_state("idle", text=result)

    def _enter_game(self, **kwargs):
        game = kwargs.get("game")
        if game:
            self._active_game = game
            self._update_display(
                status="playing",
                emoji="🎮",
                rgb=(255, 0, 255),
            )
            if self.board:
                self.board.on_button_press(game.on_button_press)
                self.board.on_button_release(game.on_button_release)
            game.start(self)

    def _enter_music(self, **kwargs):
        self._update_display(
            status="playing music",
            emoji="🎵",
            rgb=(255, 105, 180),
            scroll_speed=0,
        )
        if self.board:
            self.board.on_button_press(lambda: self._set_state("listening"))

    def exit_game(self):
        if self._active_game:
            self._active_game.stop()
            self._active_game = None
        display_state.game_surface = None
        self._set_state("idle", text="That was fun! Want to play again?")

    def _update_display(self, **kwargs):
        turn = kwargs.get("turn")
        if turn and turn in TURN_RGB:
            kwargs["rgb"] = TURN_RGB[turn]

        if "rgb" in kwargs and self.board:
            r, g, b = kwargs.pop("rgb")
            self.board.set_rgb(r, g, b)
            kwargs["rgb_color"] = (r, g, b)
        display_state.update(**kwargs)


# ---------- Factory ----------

def create_state_machine(board, render_thread):
    """Create the appropriate state machine based on config."""
    if Config.VOICE_AGENT_MODE:
        print("[State] Using Voice Agent mode (Deepgram)")
        return VoiceAgentStateMachine(board, render_thread)
    else:
        print("[State] Using Legacy mode (batch STT/LLM/TTS)")
        return LegacyStateMachine(board, render_thread)


# ---------- Helpers ----------

def _extract_emojis(text):
    import unicodedata
    emojis = ""
    for ch in text:
        if unicodedata.category(ch) in ("So", "Sk") or ord(ch) > 0x1F000:
            emojis += ch
    return emojis[-1] if emojis else ""

