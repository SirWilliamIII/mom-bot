import os
import time
import threading
import tempfile
import re

from config import Config
from core.conversation import Conversation
from services import stt, llm, tts
from services.audio import start_recording, stop_recording, set_volume, set_capture_volume
from ui.renderer import display_state, RenderThread
from ui.utils import ColorUtils


class StateMachine:
    def __init__(self, board, render_thread):
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

        if self.board:
            self.board.on_button_release(self._on_release_from_listening)

    def _on_release_from_listening(self):
        stop_recording()
        self._update_display(rgb=(255, 165, 0))
        time.sleep(0.2)

        if os.path.exists(self._recording_path):
            fsize = os.path.getsize(self._recording_path)
            if fsize < 5000:
                print("[State] Recording too short, back to idle")
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
        user_text = kwargs.get("user_text", "")

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


def _extract_emojis(text):
    import unicodedata
    emojis = ""
    for ch in text:
        if unicodedata.category(ch) in ("So", "Sk") or ord(ch) > 0x1F000:
            emojis += ch
    return emojis[-1] if emojis else ""
