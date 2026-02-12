import os
import time
import threading
import tempfile
import re

from config import Config
from core.conversation import Conversation
from ui.renderer import display_state, RenderThread
from ui.utils import ColorUtils


# ---------- VOICE AGENT MODE ----------

class VoiceAgentStateMachine:
    """State machine for Deepgram Voice Agent mode.

    Conversation flow:
        idle    → user holds button and speaks → connect agent → active
        active  → user holds button while speaking (push-to-talk)
                → bot waits at least 1s before responding after user releases
                → double-click OR hold ≥2s = pause (no user talking, agent stays connected)
                → long press (≥5s) = end conversation → idle
                → user says 'bye'/'goodbye' = end conversation → idle
                → 60s no activity = end conversation → idle
        game    → local game running, agent paused
        music   → music playing, agent still active
    """

    BYE_WORDS = {"bye", "goodbye", "ok bye", "good bye", "see ya", "see you", "bye bye"}
    IDLE_TIMEOUT_SEC = 60
    PAUSE_HOLD_SEC = 2.0
    LONG_PRESS_SEC = 5.0
    DOUBLE_CLICK_SEC = 0.4
    RESPONSE_DELAY_SEC = 1.0

    _RGB_MIN_INTERVAL = 0.4

    def __init__(self, board, render_thread):
        self.board = board
        self.render_thread = render_thread
        self.state = "idle"
        self.running = True
        self._active_game = None
        self._agent = None
        self._button_press_time = 0
        self._long_press_timer = None
        self._current_rgb = None
        self._last_rgb_time = 0
        self._last_activity_time = 0
        self._idle_timer = None
        self._last_click_time = 0
        self._holding = False
        self._paused = False
        self._response_delay_timer = None
        self._pause_timer = None

        from services.audio import set_volume, set_capture_volume
        set_volume(70)
        set_capture_volume(100)

        self._set_state("idle")

    def start_agent(self):
        from services.voice_agent import VoiceAgent

        self._update_display(
            status="waking up",
            emoji="🐷",
            text=f"{Config.COMPANION_NAME} is waking up...",
            rgb=(255, 200, 200),
            scroll_speed=0,
        )

        self._agent = VoiceAgent(on_event=self._on_agent_event)
        self._agent.set_state_machine(self)

        def do_connect():
            self._agent.connect()
            if self._agent.is_running:
                self._set_state("active")
            else:
                self._update_display(
                    text="Couldn't connect. Check WiFi?",
                    rgb=(255, 0, 0),
                )
                self._set_state("idle")

        thread = threading.Thread(target=do_connect, daemon=True)
        thread.start()

    def _end_conversation(self, reason=""):
        print(f"[State] Ending conversation: {reason}")
        if self._agent:
            if reason != "timeout":
                self._agent.inject_user_message(
                    "[SYSTEM: The conversation is ending. Say a brief, warm goodbye "
                    "in 1 sentence. Be sweet about it.]"
                )
                time.sleep(3)
            self._agent.disconnect()
            self._agent = None
        self._cancel_idle_timer()
        self._set_state("idle")

    def stop(self):
        self.running = False
        self._cancel_idle_timer()
        self._cancel_response_delay()
        self._cancel_pause_timer()
        if self._agent:
            self._agent.disconnect()
        if self._active_game:
            self._active_game.stop()

    # --- Activity tracking & idle timeout ---

    def _touch_activity(self):
        self._last_activity_time = time.time()
        self._restart_idle_timer()

    def _restart_idle_timer(self):
        self._cancel_idle_timer()
        self._idle_timer = threading.Timer(
            self.IDLE_TIMEOUT_SEC, self._on_idle_timeout
        )
        self._idle_timer.daemon = True
        self._idle_timer.start()

    def _cancel_idle_timer(self):
        if self._idle_timer:
            self._idle_timer.cancel()
            self._idle_timer = None

    def _on_idle_timeout(self):
        if self.state == "active":
            print(f"[State] No activity for {self.IDLE_TIMEOUT_SEC}s")
            self._end_conversation("timeout")

    # --- Bye detection ---

    def _check_for_bye(self, text):
        if not text:
            return False
        cleaned = text.lower().strip().rstrip(".!?,")
        for bye in self.BYE_WORDS:
            if cleaned == bye or cleaned.endswith(bye):
                return True
        return False

    # --- State management ---

    def _set_state(self, new_state, **kwargs):
        old = self.state
        self.state = new_state
        print(f"[State] {old} -> {new_state}")

        if self.board:
            self.board.on_button_press(None)
            self.board.on_button_release(None)

        handler = {
            "idle": self._enter_idle,
            "active": self._enter_active,
            "game": self._enter_game,
            "music": self._enter_music,
        }.get(new_state)

        if handler:
            handler(**kwargs)

    def _enter_idle(self, **kwargs):
        name = Config.COMPANION_NAME
        text = kwargs.get("text", f"Hold button and talk to {name}!")
        self._update_display(
            status="sleeping",
            emoji="🐷",
            text=text,
            rgb=(50, 20, 50),
            scroll_speed=0,
        )

        if self.board:
            self.board.on_button_press(self._on_button_press_idle)
            self.board.on_button_release(self._on_button_release_idle)

    def _enter_active(self, **kwargs):
        name = Config.COMPANION_NAME
        self._update_display(
            status="ready",
            emoji="🐷",
            text=kwargs.get("text", f"{name} is here!"),
            rgb=(0, 0, 85),
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
        if self._active_game:
            self._active_game.stop()
            self._active_game = None
        display_state.game_surface = None
        self._set_state("active", text="That was fun! Want to play again?")

    # --- Backlight flash ---

    _flash_lock = threading.Lock()

    def _flash_backlight(self, times=2, on_ms=80, off_ms=60):
        if not self.board:
            return
        if not self._flash_lock.acquire(blocking=False):
            return
        def _do_flash():
            try:
                for _ in range(times):
                    self.board.set_backlight(0)
                    time.sleep(off_ms / 1000)
                    self.board.set_backlight(100)
                    time.sleep(on_ms / 1000)
            except Exception as e:
                print(f"[Flash] Error: {e}")
            finally:
                self._flash_lock.release()
        threading.Thread(target=_do_flash, daemon=True).start()

    # --- Button handling: IDLE state ---

    def _on_button_press_idle(self):
        """User holds button and speaks to initiate conversation."""
        print("[Button] PRESSED (idle) -> starting conversation")
        self._button_press_time = time.time()
        self._holding = True
        self.start_agent()

    def _on_button_release_idle(self):
        hold_time = time.time() - self._button_press_time
        print(f"[Button] RELEASED (idle) hold={hold_time:.2f}s")
        self._holding = False
        # Agent is connecting/connected; release triggers response delay
        if self._agent:
            self._agent.force_listen(False)
            self._schedule_response_delay()

    # --- Button handling: ACTIVE state ---

    def _on_button_press_active(self):
        self._button_press_time = time.time()
        self._holding = True

        # Cancel any pending response delay (user is talking again)
        self._cancel_response_delay()

        now = time.time()
        if now - self._last_click_time < self.DOUBLE_CLICK_SEC:
            print("[Button] Double-click -> pausing (no user talking)")
            self._last_click_time = 0
            self._holding = False
            self._toggle_pause()
            return

        # If paused, unpause on button press
        if self._paused:
            self._toggle_pause()

        # Silence any ongoing agent speech and switch to listening
        if self._agent:
            self._agent.silence_agent()
            self._agent.force_listen(True)
        self._update_display(
            status="listening",
            emoji="🎤",
            text="I'm listening...",
            rgb=(0, 255, 0),
        )
        self._touch_activity()

        # 2s hold = pause (same as double-click)
        self._cancel_pause_timer()
        self._pause_timer = threading.Timer(
            self.PAUSE_HOLD_SEC, self._on_pause_hold
        )
        self._pause_timer.daemon = True
        self._pause_timer.start()

        self._long_press_timer = threading.Timer(
            self.LONG_PRESS_SEC, self._on_long_press_active
        )
        self._long_press_timer.start()

    def _on_button_release_active(self):
        if self._long_press_timer:
            self._long_press_timer.cancel()
            self._long_press_timer = None
        self._cancel_pause_timer()

        self._holding = False
        self._last_click_time = time.time()

        # If pause fired while held, don't schedule a response
        if self._paused:
            if self._agent:
                self._agent.force_listen(False)
            return

        if self._agent:
            self._agent.force_listen(False)

        # Wait at least RESPONSE_DELAY_SEC before bot responds
        self._schedule_response_delay()
        self._touch_activity()

    def _schedule_response_delay(self):
        """Wait at least RESPONSE_DELAY_SEC after user stops talking before bot responds."""
        self._cancel_response_delay()
        self._update_display(
            status="thinking",
            emoji="🤔",
            text="Let me think...",
            rgb=(255, 165, 0),
        )
        self._response_delay_timer = threading.Timer(
            self.RESPONSE_DELAY_SEC, self._on_response_delay_done
        )
        self._response_delay_timer.daemon = True
        self._response_delay_timer.start()

    def _cancel_response_delay(self):
        timer = getattr(self, "_response_delay_timer", None)
        if timer:
            timer.cancel()
            self._response_delay_timer = None

    def _on_response_delay_done(self):
        """Response delay elapsed — bot can now respond."""
        self._response_delay_timer = None
        if self.state == "active" and not self._holding:
            self._update_display(
                status="ready",
                emoji="🐷",
                rgb=(0, 0, 85),
            )

    def _on_pause_hold(self):
        """Fired when button is held for 2s — triggers pause (same as double-click)."""
        print("[Button] 2s hold -> pausing (no user talking)")
        self._pause_timer = None
        # Stop listening while we transition to paused
        if self._agent:
            self._agent.force_listen(False)
        self._toggle_pause()

    def _cancel_pause_timer(self):
        if self._pause_timer:
            self._pause_timer.cancel()
            self._pause_timer = None

    def _toggle_pause(self):
        """Double-click or 2s hold toggles pause — mutes mic, silences agent, keeps connected."""
        self._paused = not self._paused
        if self._paused:
            print("[Button] Paused (muted)")
            if self._agent:
                self._agent.silence_agent()
                self._agent.set_paused(True)
            self._update_display(
                status="paused",
                emoji="⏸️",
                text="Paused — hold button to talk",
                rgb=(100, 100, 0),
            )
        else:
            print("[Button] Unpaused")
            if self._agent:
                self._agent.set_paused(False)
            self._update_display(
                status="ready",
                emoji="🐷",
                text=f"{Config.COMPANION_NAME} is here!",
                rgb=(0, 0, 85),
            )
        self._touch_activity()

    def _on_long_press_active(self):
        print("[Button] Long press (≥5s) in active -> ending conversation")
        self._end_conversation("button")

    # --- Voice Agent event handler ---

    def _on_agent_event(self, event_type, data):
        if event_type in ("ready", "connected"):
            pass

        elif event_type == "user_speaking":
            self._touch_activity()
            if display_state.status != "listening":
                self._update_display(
                    status="listening",
                    emoji="🎤",
                    text="I'm listening...",
                    rgb=(0, 255, 0),
                )

        elif event_type == "agent_thinking":
            self._touch_activity()
            self._update_display(
                status="thinking",
                emoji="🤔",
                text="Let me think...",
                rgb=(255, 165, 0),
            )

        elif event_type == "agent_speaking":
            self._touch_activity()
            if display_state.status != "talking":
                self._update_display(
                    status="talking",
                    rgb=(0, 100, 200),
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
                self._flash_backlight(times=2)
                emojis = _extract_emojis(content)
                self._update_display(
                    text=content,
                    emoji=emojis or "🐷",
                    scroll_speed=3,
                )

        elif event_type == "agent_audio_done":
            self._touch_activity()
            if self.state not in ("game", "music"):
                self._update_display(
                    status="ready",
                    rgb=(0, 0, 85),
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
                rgb=(255, 0, 0),
                alert_text="Oops -- hit a snag",
                alert_level="error",
                alert_duration=3.2,
            )

        elif event_type == "disconnected":
            reason = data.get("reason", "")
            if self.running and self.state == "active" and reason:
                print(f"[State] Unexpected disconnect: {reason}, reconnecting...")
                self._update_display(
                    alert_text="Connection dropped -- retrying",
                    alert_level="warn",
                    alert_duration=3.0,
                )
                time.sleep(2)
                self.start_agent()

    # --- Display helper ---

    def _update_display(self, **kwargs):
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
    """Original batch-mode state machine (button → record → STT → LLM → TTS)."""

    def __init__(self, board, render_thread):
        from services import stt, llm, tts
        from services.audio import start_recording, stop_recording, set_volume, set_capture_volume

        self.board = board
        self.render_thread = render_thread
        self.conversation = Conversation()
        self.state = "idle"
        self.running = True
        self._recording_path = ""
        self._answer_id = 0
        self._active_game = None

        set_volume(70)
        set_capture_volume(100)

        self._set_state("idle")

    def stop(self):
        self.running = False
        from services.audio import stop_recording
        stop_recording()
        if self._active_game:
            self._active_game.stop()

    def _set_state(self, new_state, **kwargs):
        old = self.state
        self.state = new_state
        print(f"[State] {old} -> {new_state}")

        if self.board:
            self.board.on_button_press(None)
            self.board.on_button_release(None)

            if new_state == "listening":
                self.board.on_button_release(self._on_release_from_listening)

        handler = {
            "idle": self._enter_idle,
            "listening": self._enter_listening,
            "thinking": self._enter_thinking,
            "speaking": self._enter_speaking,
            "game": self._enter_game,
            "music": self._enter_music,
        }.get(new_state)

        if handler:
            handler(**kwargs)

    def _enter_idle(self, **kwargs):
        self._update_display(
            status="idle",
            emoji="🐷",
            text=kwargs.get("text", "Press and hold to talk to me!"),
            rgb=(0, 0, 85),
            scroll_speed=0,
        )

        if self.board:
            self.board.on_button_press(lambda: self._set_state("listening"))

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
            rgb=(0, 255, 0),
            scroll_speed=0,
        )

        start_recording(self._recording_path)

    def _on_release_from_listening(self):
        from services.audio import stop_recording
        print("[State] Release detected, stopping recording...")
        stop_recording()
        self._update_display(rgb=(255, 165, 0))
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

    def _enter_thinking(self, **kwargs):
        self._update_display(
            status="thinking",
            emoji="🤔",
            text="Let me think...",
            rgb=(255, 165, 0),
            scroll_speed=0,
        )

        if self.board:
            self.board.on_button_press(lambda: self._set_state("listening"))

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
            rgb=(0, 100, 200),
            scroll_speed=3,
        )

        if self.board:
            self.board.on_button_press(lambda: self._interrupt_and_listen())

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
