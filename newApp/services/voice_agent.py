"""Deepgram Voice Agent API client.

Single WebSocket handles STT + LLM + TTS. Audio in, audio out.
Binary frames = raw PCM audio. JSON text frames = control messages.
"""

import json
import struct
import threading
import time
from datetime import datetime

from config import Config
from core.companion import get_system_prompt
from features.tools import VOICE_AGENT_FUNCTIONS, execute_tool
from services.audio import (
    start_recording_stream,
    start_playback_stream,
    stop_playback,
)

AGENT_WS_URL = "wss://agent.deepgram.com/v1/agent/converse"
# 50ms of 16kHz 16-bit mono = 1600 bytes (2 bytes/sample * 16000 * 0.05)
MIC_CHUNK_BYTES = 1600


class VoiceAgent:
    """Manages the Deepgram Voice Agent WebSocket connection."""

    def __init__(self, on_event=None):
        """
        Args:
            on_event: callback(event_type: str, data: dict) for UI updates.
                      Called from the receiver thread.
        """
        self._on_event = on_event or (lambda t, d: None)
        self._ws = None
        self._mic_proc = None
        self._speaker_proc = None
        self._sender_thread = None
        self._receiver_thread = None
        self._running = False
        self._ready = threading.Event()
        self._state_machine = None  # set by state machine for tool calls
        self._audio_bytes_received = 0
        self._audio_bytes_written = 0
        self._mic_muted = False  # True while agent is speaking (echo suppression)
        self._unmute_at = 0      # timestamp when mic should actually unmute
        self._silence_seq = 0    # increments each silence_agent call; stale injects bail out
        self._agent_spoke = False # True after agent speaks; reset when user speaks
        self._dropping_turn = False  # True = actively suppressing a double-response
        self._force_listen = False   # True = button held, speaker muted, mic live

    @property
    def is_running(self):
        return self._running

    def set_state_machine(self, sm):
        self._state_machine = sm

    def connect(self):
        """Open WebSocket, start mic + speaker, begin streaming."""
        if self._running:
            return

        import websockets.sync.client

        self._running = True
        self._ready.clear()
        self._audio_bytes_received = 0
        self._audio_bytes_written = 0

        headers = {"Authorization": f"Token {Config.DEEPGRAM_API_KEY}"}

        print("[VoiceAgent] Connecting to Deepgram...")
        try:
            self._ws = websockets.sync.client.connect(
                AGENT_WS_URL,
                additional_headers=headers,
                close_timeout=5,
            )
        except Exception as e:
            print(f"[VoiceAgent] Connection failed: {e}")
            self._running = False
            self._on_event("error", {"message": str(e)})
            return

        # Send settings
        settings = self._build_settings()
        self._ws.send(json.dumps(settings))
        print("[VoiceAgent] Settings sent, waiting for ready...")

        # Start receiver first so we catch SettingsApplied
        self._receiver_thread = threading.Thread(
            target=self._receive_loop, daemon=True
        )
        self._receiver_thread.start()

        # Wait for agent to be ready (SettingsApplied)
        if not self._ready.wait(timeout=15):
            print("[VoiceAgent] Timeout waiting for SettingsApplied")
            self.disconnect()
            return

        # Start mic streaming (input rate for STT)
        self._mic_proc = start_recording_stream(
            sample_rate=Config.DEEPGRAM_INPUT_SAMPLE_RATE
        )
        self._sender_thread = threading.Thread(
            target=self._send_loop, daemon=True
        )
        self._sender_thread.start()

        # Start speaker (output rate for TTS)
        self._speaker_proc = start_playback_stream(
            sample_rate=Config.DEEPGRAM_TTS_SAMPLE_RATE
        )

        print("[VoiceAgent] Connected and streaming!")
        self._on_event("connected", {})

    def disconnect(self):
        """Tear down everything cleanly."""
        if not self._running:
            return
        self._running = False
        print("[VoiceAgent] Disconnecting...")

        # Kill mic
        if self._mic_proc:
            for pipe in (self._mic_proc.stdout, self._mic_proc.stderr):
                if pipe:
                    try:
                        pipe.close()
                    except Exception:
                        pass
            if self._mic_proc.poll() is None:
                self._mic_proc.terminate()
                try:
                    self._mic_proc.wait(timeout=2)
                except Exception:
                    self._mic_proc.kill()
        self._mic_proc = None

        # Kill speaker
        if self._speaker_proc:
            for pipe in (self._speaker_proc.stdin, self._speaker_proc.stdout, self._speaker_proc.stderr):
                if pipe:
                    try:
                        pipe.close()
                    except Exception:
                        pass
            if self._speaker_proc.poll() is None:
                self._speaker_proc.terminate()
        self._speaker_proc = None
        stop_playback()

        # Close WebSocket
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

        self._ready.clear()
        print("[VoiceAgent] Disconnected")
        self._on_event("disconnected", {})

    def inject_user_message(self, text):
        """Inject text as if the agent said it. Only works when agent is idle."""
        if self._ws and self._running:
            msg = {"type": "InjectAgentMessage", "message": text}
            try:
                self._ws.send(json.dumps(msg))
                print(f"[VoiceAgent] Injected message: {text[:60]}")
            except Exception as e:
                print(f"[VoiceAgent] Inject failed: {e}")

    def silence_agent(self, then_inject=None):
        """Immediately kill speaker output, then optionally inject a message.

        Debounced: rapid presses only restart the speaker once and only
        the last press's inject fires (earlier ones see a stale seq and bail).
        """
        self._silence_seq += 1
        my_seq = self._silence_seq
        print(f"[VoiceAgent] Silencing agent (seq={my_seq})...")

        # 1. Kill the speaker process — instant silence
        if self._speaker_proc and self._speaker_proc.poll() is None:
            try:
                self._speaker_proc.stdin.close()
            except Exception:
                pass
            self._speaker_proc.terminate()
        self._speaker_proc = None

        # 2. Restart a fresh speaker pipe so future audio still plays
        self._speaker_proc = start_playback_stream(
            sample_rate=Config.DEEPGRAM_TTS_SAMPLE_RATE
        )

        # 3. After a delay, inject — but only if no newer press happened
        if then_inject:
            def _delayed_inject():
                time.sleep(2.0)
                if self._silence_seq != my_seq:
                    print(f"[VoiceAgent] Inject skipped (stale seq={my_seq}, current={self._silence_seq})")
                    return
                self.inject_user_message(then_inject)
            threading.Thread(target=_delayed_inject, daemon=True).start()

    def update_prompt(self, new_prompt):
        """Update the system prompt mid-conversation."""
        if self._ws and self._running:
            msg = {"type": "UpdatePrompt", "prompt": new_prompt}
            try:
                self._ws.send(json.dumps(msg))
            except Exception as e:
                print(f"[VoiceAgent] Prompt update failed: {e}")

    def force_listen(self, active):
        """When active=True: mute speaker output, unmute mic (user is talking).
        When active=False: restore normal operation."""
        self._force_listen = active
        if active:
            self._mic_muted = False
            self._unmute_at = 0
            self._dropping_turn = True
            print("[VoiceAgent] Force-listen ON (speaker muted, mic live)")
        else:
            self._dropping_turn = False
            print("[VoiceAgent] Force-listen OFF")

    def send_keep_alive(self):
        """Send keepalive to prevent timeout."""
        if self._ws and self._running:
            try:
                self._ws.send(json.dumps({"type": "KeepAlive"}))
            except Exception:
                pass

    # --- Internal threads ---

    # Silence frame: same size as a mic chunk but all zeros
    _SILENCE = b"\x00" * MIC_CHUNK_BYTES
    _ECHO_GATE_RMS = 800
    _ECHO_GATE_DURATION = 3.0

    @staticmethod
    def _rms(pcm_bytes):
        """Compute RMS of 16-bit LE PCM audio."""
        n = len(pcm_bytes) // 2
        if n == 0:
            return 0
        samples = struct.unpack(f"<{n}h", pcm_bytes[:n * 2])
        return int((sum(s * s for s in samples) / n) ** 0.5)

    def _send_loop(self):
        """Read mic audio and send as binary WebSocket frames.

        Echo suppression layers:
        1. Hard mute while agent is speaking (_mic_muted)
        2. After unmute, energy gate for _ECHO_GATE_DURATION seconds:
           only send audio if RMS > _ECHO_GATE_RMS (real speech),
           otherwise send silence (suppress echo tail)
        """
        print("[VoiceAgent] Mic sender started")
        gate_until = 0
        try:
            while self._running and self._mic_proc and self._mic_proc.poll() is None:
                chunk = self._mic_proc.stdout.read(MIC_CHUNK_BYTES)
                if not chunk:
                    break

                # Check if it's time to unmute
                if self._mic_muted and self._unmute_at and time.time() >= self._unmute_at:
                    self._mic_muted = False
                    self._unmute_at = 0
                    gate_until = time.time() + self._ECHO_GATE_DURATION
                    print("[VoiceAgent] Mic unmuted -> energy gate active")

                send_chunk = chunk
                if self._mic_muted:
                    send_chunk = self._SILENCE
                elif time.time() < gate_until:
                    rms = self._rms(chunk)
                    if rms < self._ECHO_GATE_RMS:
                        send_chunk = self._SILENCE

                if self._ws and self._running:
                    try:
                        self._ws.send(send_chunk)
                    except Exception as e:
                        print(f"[VoiceAgent] Send error: {e}")
                        break
        except Exception as e:
            print(f"[VoiceAgent] Sender crashed: {e}")
        print("[VoiceAgent] Mic sender stopped")

    def _receive_loop(self):
        """Read WebSocket messages: binary = audio, text = JSON events."""
        print("[VoiceAgent] Receiver started")
        try:
            while self._running and self._ws:
                try:
                    message = self._ws.recv(timeout=5)
                except TimeoutError:
                    continue
                except Exception as e:
                    if self._running:
                        print(f"[VoiceAgent] Recv error: {e}")
                    break

                if isinstance(message, bytes):
                    # Raw PCM audio from Deepgram TTS -> pipe to speaker
                    self._handle_audio(message)
                else:
                    # JSON control message
                    try:
                        data = json.loads(message)
                        self._handle_message(data)
                    except json.JSONDecodeError:
                        print(f"[VoiceAgent] Bad JSON: {message[:100]}")
        except Exception as e:
            if self._running:
                print(f"[VoiceAgent] Receiver crashed: {e}")

        print("[VoiceAgent] Receiver stopped")
        if self._running:
            self._on_event("disconnected", {"reason": "receiver_exit"})
            self._running = False

    def _handle_audio(self, data):
        """Write audio bytes to the aplay stdin pipe."""
        if self._dropping_turn or self._force_listen:
            return  # discard: user is talking (force_listen) or suppressed double-response

        self._audio_bytes_received += len(data)

        # Log first chunk and periodically
        if self._audio_bytes_received == len(data):
            print(f"[VoiceAgent] First audio chunk received: {len(data)} bytes")
        elif self._audio_bytes_received % 32000 < len(data):
            print(f"[VoiceAgent] Audio: {self._audio_bytes_received} bytes received, "
                  f"{self._audio_bytes_written} written")

        if not self._speaker_proc:
            print("[VoiceAgent] No speaker process! Restarting...")
            self._speaker_proc = start_playback_stream(
                sample_rate=Config.DEEPGRAM_TTS_SAMPLE_RATE
            )

        if self._speaker_proc.poll() is not None:
            # Process died — check why
            stderr_out = ""
            if self._speaker_proc.stderr:
                try:
                    stderr_out = self._speaker_proc.stderr.read().decode(errors="replace")
                except Exception:
                    pass
            print(f"[VoiceAgent] Speaker died (rc={self._speaker_proc.returncode}): {stderr_out}")
            self._speaker_proc = start_playback_stream(
                sample_rate=Config.DEEPGRAM_TTS_SAMPLE_RATE
            )

        try:
            self._speaker_proc.stdin.write(data)
            self._speaker_proc.stdin.flush()
            self._audio_bytes_written += len(data)
        except (BrokenPipeError, OSError) as e:
            print(f"[VoiceAgent] Speaker pipe error: {e}, restarting...")
            self._speaker_proc = start_playback_stream(
                sample_rate=Config.DEEPGRAM_TTS_SAMPLE_RATE
            )

    def _handle_message(self, data):
        """Dispatch a JSON event from the Voice Agent."""
        msg_type = data.get("type", "unknown")

        if msg_type == "Welcome":
            print(f"[VoiceAgent] Welcome! request_id={data.get('request_id', '?')}")

        elif msg_type in ("SettingsApplied", "SettingsUpdated"):
            print(f"[VoiceAgent] {msg_type} -- agent ready!")
            self._ready.set()
            self._on_event("ready", {})

        elif msg_type == "ConversationText":
            role = data.get("role", "?")
            content = data.get("content", "")
            print(f"[VoiceAgent] [{role}]: {content[:80]}")
            self._on_event("conversation_text", {"role": role, "content": content})

        elif msg_type == "UserStartedSpeaking":
            self._agent_spoke = False  # user spoke, allow next agent response
            self._dropping_turn = False
            self._mic_muted = False  # user is talking, make sure mic is live
            self._on_event("user_speaking", {})

        elif msg_type == "AgentThinking":
            content = data.get("content", "")
            self._on_event("agent_thinking", {"content": content})

        elif msg_type == "AgentStartedSpeaking":
            # If user is holding the button (force_listen), suppress agent speech
            if self._force_listen:
                self._dropping_turn = True
                print("[VoiceAgent] Dropping agent speech (user is holding button)")
                return

            # Double-response guard: if agent already spoke and user hasn't
            # said anything, suppress this turn entirely.
            if self._agent_spoke:
                self._dropping_turn = True
                print("[VoiceAgent] Dropping double-response (no user input since last agent turn)")
                return

            self._dropping_turn = False
            self._agent_spoke = True
            self._mic_muted = True  # suppress echo while speaking
            self._unmute_at = 0    # cancel any pending unmute
            latency = data.get("total_latency", 0)
            tts_lat = data.get("tts_latency", 0)
            print(f"[VoiceAgent] Agent speaking (latency: {latency:.2f}s, tts: {tts_lat:.2f}s) [mic muted]")
            self._on_event("agent_speaking", {
                "total_latency": latency,
                "tts_latency": tts_lat,
            })

        elif msg_type == "AgentAudioDone":
            if self._dropping_turn:
                self._dropping_turn = False
                print("[VoiceAgent] Dropped turn audio done")
                return
            # Schedule unmute after a delay — aplay buffer still has audio
            # queued after Deepgram says done. The send loop checks the clock.
            self._unmute_at = time.time() + 2.0
            print("[VoiceAgent] Agent audio done [mic unmutes in 2000ms]")
            self._on_event("agent_audio_done", {})

        elif msg_type == "FunctionCallRequest":
            self._handle_function_call(data)

        elif msg_type in ("Error", "Warning"):
            desc = data.get("description", str(data))
            code = data.get("code", "?")
            print(f"[VoiceAgent] {msg_type}: [{code}] {desc}")
            self._on_event("error" if msg_type == "Error" else "warning", {
                "description": desc, "code": code
            })

        elif msg_type in ("PromptUpdated", "SpeakUpdated", "History",
                          "FunctionCallResponse", "InjectionRefused"):
            # FunctionCallResponse = our own response echoed back
            # InjectionRefused = tried to inject while agent was speaking (expected)
            # History = conversation history replay on reconnect
            if msg_type == "InjectionRefused":
                print("[VoiceAgent] Injection refused (agent busy), will retry")
            pass

        else:
            print(f"[VoiceAgent] Unhandled message type: {msg_type}")

    def _handle_function_call(self, data):
        """Execute a function call from the LLM and send the result back."""
        functions = data.get("functions", [data])

        for func in functions:
            func_name = func.get("name", func.get("function_name", ""))
            func_id = func.get("id", func.get("function_call_id", ""))
            args_raw = func.get("arguments", func.get("input", "{}"))

            if isinstance(args_raw, str):
                try:
                    args = json.loads(args_raw)
                except json.JSONDecodeError:
                    args = {}
            else:
                args = args_raw

            print(f"[VoiceAgent] Function call: {func_name}({args})")
            self._on_event("function_call", {"name": func_name, "args": args})

            # Execute the tool
            try:
                result = execute_tool(func_name, args, self._state_machine)
            except Exception as e:
                result = f"Error executing {func_name}: {e}"
                print(f"[VoiceAgent] Tool error: {e}")

            # Send result back
            response = {
                "type": "FunctionCallResponse",
                "id": func_id,
                "name": func_name,
                "content": result if isinstance(result, str) else json.dumps(result),
            }
            try:
                self._ws.send(json.dumps(response))
                print(f"[VoiceAgent] Function result sent: {str(result)[:80]}")
            except Exception as e:
                print(f"[VoiceAgent] Failed to send function result: {e}")

    # --- Settings builder ---

    def _build_settings(self):
        """Build the Voice Agent settings message."""
        now = datetime.now()
        hour = now.hour
        if hour < 12:
            greeting_time = "Good morning"
        elif hour < 17:
            greeting_time = "Good afternoon"
        elif hour < 21:
            greeting_time = "Good evening"
        else:
            greeting_time = "Hey there, night owl"

        name = Config.COMPANION_NAME
        greeting = f"{greeting_time}! It's me, {name}! What's on your mind?"

        return {
            "type": "Settings",
            "audio": {
                "input": {
                    "encoding": "linear16",
                    "sample_rate": Config.DEEPGRAM_INPUT_SAMPLE_RATE,
                },
                "output": {
                    "encoding": "linear16",
                    "sample_rate": Config.DEEPGRAM_TTS_SAMPLE_RATE,
                    "container": "none",
                },
            },
            "agent": {
                "language": "en",
                "listen": {
                    "provider": {
                        "type": "deepgram",
                        "model": Config.DEEPGRAM_STT_MODEL,
                    }
                },
                "think": {
                    "provider": {
                        "type": Config.DEEPGRAM_LLM_PROVIDER,
                        "model": Config.DEEPGRAM_LLM_MODEL,
                    },
                    "prompt": get_system_prompt(),
                    "functions": VOICE_AGENT_FUNCTIONS,
                },
                "speak": {
                    "provider": {
                        "type": "deepgram",
                        "model": Config.DEEPGRAM_TTS_MODEL,
                    }
                },
                "greeting": greeting,
            },
        }
