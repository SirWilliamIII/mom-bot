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

    States:
        idle    - booting, no connection yet
        ready   - Voice Agent connected, always listening. Button = slow down.
        game    - local game running, Voice Agent paused
        music   - music playing, Voice Agent still active
    """

    SLOW_DOWN_MESSAGE = (
        "Oh wait, let me slow down! I think I was going a bit fast there. "
        "Let me repeat what I just said, but simpler and slower this time."
    )

    # Minimum seconds between RGB changes to avoid strobe effect
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

        from services.audio import set_volume, set_capture_volume
        set_volume(70)
        set_capture_volume(100)

        self._set_state("idle")

    def start_agent(self):
        """Connect the Voice Agent and transition to ready state."""
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

        # Connect in a thread so we don't block the main thread
        def do_connect():
            self._agent.connect()
            if self._agent.is_running:
                self._set_state("ready")
            else:
                self._update_display(
                    text="Couldn't connect. Check WiFi?",
                    rgb=(255, 0, 0),
                )

        thread = threading.Thread(target=do_connect, daemon=True)
        thread.start()

    def stop(self):
        self.running = False
        if self._agent:
            self._agent.disconnect()
        if self._active_game:
            self._active_game.stop()

    # --- State management ---

    def _set_state(self, new_state, **kwargs):
        old = self.state
        self.state = new_state
        print(f"[State] {old} -> {new_state}")

        # Clear button handlers
        if self.board:
            self.board.on_button_press(None)
            self.board.on_button_release(None)

        handler = {
            "idle": self._enter_idle,
            "ready": self._enter_ready,
            "game": self._enter_game,
            "music": self._enter_music,
        }.get(new_state)

        if handler:
            handler(**kwargs)

    def _enter_idle(self, **kwargs):
        text = kwargs.get("text", f"{Config.COMPANION_NAME} is waking up...")
        self._update_display(
            status="idle",
            emoji="🐷",
            text=text,
            rgb=(255, 200, 200),
            scroll_speed=0,
        )

    def _enter_ready(self, **kwargs):
        name = Config.COMPANION_NAME
        self._update_display(
            status="ready",
            emoji="🐷",
            text=kwargs.get("text", f"Talk to {name}!"),
            rgb=(0, 0, 85),
            scroll_speed=0,
        )

        # Button: short press = magic slow-down, long press = reconnect
        if self.board:
            self.board.on_button_press(self._on_button_press)
            self.board.on_button_release(self._on_button_release)

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
        # Button still does slow-down in music mode
        if self.board:
            self.board.on_button_press(self._on_button_press)
            self.board.on_button_release(self._on_button_release)

    def exit_game(self):
        if self._active_game:
            self._active_game.stop()
            self._active_game = None
        display_state.game_surface = None
        self._set_state("ready", text="That was fun! Want to play again?")

    # --- Magic slow-down button ---

    def _on_button_press(self):
        self._button_press_time = time.time()
        # Schedule long-press check
        self._long_press_timer = threading.Timer(2.0, self._on_long_press)
        self._long_press_timer.start()

    def _on_button_release(self):
        # Cancel long-press timer
        if self._long_press_timer:
            self._long_press_timer.cancel()
            self._long_press_timer = None

        hold_time = time.time() - self._button_press_time
        if hold_time < 2.0:
            # Short press = magic slow-down button
            self._magic_button()

    def _magic_button(self):
        """The magic slow-down button! Inject a message to make Piglet repeat/simplify."""
        print("[Button] Magic button pressed! Injecting slow-down message...")
        self._update_display(
            text="OK OK, let me try again!",
            emoji="🐷",
            rgb=(255, 150, 200),
        )
        if self._agent:
            self._agent.inject_user_message(self.SLOW_DOWN_MESSAGE)

    def _on_long_press(self):
        """Long press = reconnect the Voice Agent."""
        print("[Button] Long press detected -- reconnecting...")
        self._update_display(
            text="Reconnecting...",
            emoji="🔄",
            rgb=(255, 165, 0),
        )
        if self._agent:
            self._agent.disconnect()
            time.sleep(1)
            self.start_agent()

    # --- Voice Agent event handler ---

    def _on_agent_event(self, event_type, data):
        """Called from the Voice Agent receiver thread on WebSocket events.

        We keep visual updates calm — only change RGB for meaningful
        state transitions, and avoid rapid text flicker.
        """
        if event_type in ("ready", "connected"):
            pass  # start_agent handles the transition

        elif event_type == "user_speaking":
            # Only update if we're not already showing "listening"
            if display_state.status != "listening":
                self._update_display(
                    status="listening",
                    emoji="🎤",
                    text="I'm listening...",
                    rgb=(0, 255, 0),
                )

        elif event_type == "agent_thinking":
            self._update_display(
                status="thinking",
                emoji="🤔",
                text="Let me think...",
                rgb=(255, 165, 0),
            )

        elif event_type == "agent_speaking":
            # Just set status, don't flash the text
            if display_state.status != "talking":
                self._update_display(
                    status="talking",
                    rgb=(0, 100, 200),
                )

        elif event_type == "conversation_text":
            role = data.get("role", "")
            content = data.get("content", "")
            if role == "assistant" and content:
                emojis = _extract_emojis(content)
                # Update text only (no RGB change) to avoid flicker
                self._update_display(
                    text=content,
                    emoji=emojis or "🐷",
                    scroll_speed=3,
                )
            # Skip displaying user text — it's noisy with streaming partials

        elif event_type == "agent_audio_done":
            if self.state not in ("game", "music"):
                self._update_display(
                    status="ready",
                    rgb=(0, 0, 85),
                )

        elif event_type == "function_call":
            name = data.get("name", "")
            self._update_display(text=f"Doing: {name}...")

        elif event_type == "error":
            desc = data.get("description", "Something went wrong")
            self._update_display(
                text=desc,
                emoji="😟",
                rgb=(255, 0, 0),
            )

        elif event_type == "disconnected":
            reason = data.get("reason", "")
            if self.running and reason:
                # Unexpected disconnect -- try to reconnect
                print(f"[State] Unexpected disconnect: {reason}, reconnecting...")
                time.sleep(2)
                self.start_agent()

    # --- Display helper ---

    def _update_display(self, **kwargs):
        if "rgb" in kwargs and self.board:
            rgb = kwargs.pop("rgb")
            now = time.time()
            # Only change RGB if it's a different color AND enough time has passed
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
