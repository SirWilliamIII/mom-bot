# MomBot (Piglet) - Project Status & Handoff Document

## Overview
Companion device for Mom's birthday -- a cute pink pig chatbot on a Raspberry Pi Zero 2W with PiSugar Whisplay HAT (240x280 LCD, speaker, mic, RGB LED, 1 button). Streams voice conversations via Deepgram Voice Agent API.

**Repo**: https://github.com/SirWilliamIII/mom-bot
**Branch**: `main`
**App location**: `newApp/`

---

## Architecture (Current)

**Deepgram Voice Agent mode** -- single WebSocket to `wss://agent.deepgram.com/v1/agent/converse` handles STT + LLM + TTS. Audio streams in both directions over the same connection. No batch pipeline (record -> transcribe -> LLM -> speak). Real-time, full-duplex conversation.

```
Mic (arecord) -> PCM -> WebSocket -> Deepgram (Flux STT -> GPT-4o-mini -> Deepgram TTS) -> PCM -> aplay -> Speaker
```

### Key Config
```env
VOICE_AGENT_MODE=true
DEEPGRAM_API_KEY=<set>
DEEPGRAM_STT_MODEL=flux-general-en      # Flux for turn detection
DEEPGRAM_LLM_PROVIDER=open_ai
DEEPGRAM_LLM_MODEL=gpt-4o-mini
DEEPGRAM_TTS_MODEL=aura-2-thalia-en
DEEPGRAM_INPUT_SAMPLE_RATE=16000
DEEPGRAM_TTS_SAMPLE_RATE=16000
```

### ALSA Audio
- **Official Waveshare WM8960 asound.conf** with dmix (ipc_key 555555) + dsnoop (ipc_key 666666)
- Devices use `"default"` which routes through dmix/dsnoop for concurrent mic+speaker access
- `asound.conf` is synced to `~/.asoundrc` on every startup by `main.py`
- **pygame fully removed** -- it was stealing the ALSA device at import time

---

## What Works (Verified on Device)

- [x] **Full voice conversation** -- real-time streaming, feels like a phone call
- [x] **Flux turn detection** -- `flux-general-en` STT model handles end-of-turn natively
- [x] **Echo suppression** -- mic sends silence while agent speaks, with 800ms drain delay after AgentAudioDone
- [x] **Double-response guard** -- agent can't speak twice without user input in between
- [x] **Magic button (short press)** -- instantly silences agent (kills aplay), then injects system message telling LLM to chill and listen. Debounced (rapid presses only fire one inject via sequence number)
- [x] **Magic button (long press 2s)** -- reconnects Voice Agent WebSocket
- [x] **Backlight flash** -- quick 2x blink when assistant text arrives (notification ping)
- [x] **Function calling** -- web_search tool works through Voice Agent (LLM calls tool, we execute, send result back)
- [x] **Malay language support** -- responds in Bahasa Melayu when spoken to in Malay
- [x] **LCD display** -- 240x280, scrolling text, emoji rendering, status indicators
- [x] **RGB LED** -- changes color per state (green=listening, orange=thinking, blue=talking)
- [x] **systemd auto-start** -- `mombot.service`

---

## Push-to-Talk Architecture (`voice_agent.py`)

The speaker and mic are millimeters apart on the Whisplay HAT. Echo suppression is solved by **push-to-talk**: the mic only sends real audio when the user holds the button (`_input_enabled = True`). All other times, silence frames are sent to keep the Deepgram connection alive without leaking speaker audio.

**VoiceAgent flags (only 3):**
- `_input_enabled` -- True when button held, mic sends real audio
- `_paused` -- True in pause mode, mic sends silence AND agent audio is discarded
- `_output_suppress_until` -- timestamp; agent audio is buffered (not discarded) until this time, then flushed. Used for the 0.5s response delay after button release.

**State machine threading safety:**
- `_lock` (RLock) guards all state mutations
- `_epoch` counter increments on every state transition; stale timer callbacks bail out
- `_end_conversation` and `_on_long_press_active` run blocking work in separate threads to avoid holding the lock during sleep

---

## Silence Button Architecture

`silence_agent()` in `voice_agent.py`:
1. Kills the aplay subprocess immediately (instant silence)
2. Restarts a fresh speaker pipe (so future audio still plays)
3. After 2s delay, injects `InjectAgentMessage` with system instruction to stop talking and ask a short check-in question
4. Debounced via `_silence_seq` counter -- only the latest press's inject fires; earlier ones see a stale seq and bail

---

## Known Issues / Not Yet Fixed

- [ ] **duckduckgo_search package renamed to ddgs** -- `pip install ddgs` needed, current code shows deprecation warning
- [ ] **web_search returns empty results sometimes** -- "I couldn't find anything about..." for valid queries. May need to switch search backend or add retry logic
- [ ] **No error recovery on WebSocket disconnect** -- if Deepgram drops the connection, need manual long-press to reconnect. Should auto-reconnect with backoff
- [ ] **Music player untested with Voice Agent mode** -- converted from pygame to aplay but not tested on device
- [ ] **Games untested** -- tic-tac-toe and brick breaker exist but haven't been tested in this session
- [ ] **Dead config entries** -- `DEEPGRAM_EOT_THRESHOLD` and `DEEPGRAM_EOT_TIMEOUT_MS` in config.py are unused (those params don't apply to Voice Agent API, only standalone Flux). Clean up or remove.
- [ ] **Legacy batch mode (`VoiceAgentMode=false`)** -- old STT/LLM/TTS pipeline code is still in the codebase but hasn't been maintained. May be broken.

---

## File Structure (Key Files)
```
newApp/
├── main.py                          # Entry, pycache cleanup, ALSA sync, signal handling
├── config.py                        # .env loader (Deepgram, LLM, TTS, turn detection)
├── asound.conf                      # Official Waveshare WM8960 dmix/dsnoop config
├── driver/
│   └── whisplay.py                  # LCD, GPIO, RGB LED, button, backlight (gpiozero+lgpio)
├── core/
│   ├── state_machine.py             # VoiceAgentStateMachine (ready/game/music states)
│   │                                #   - Magic button (silence + inject)
│   │                                #   - Backlight flash on assistant text
│   │                                #   - Agent event handlers (speaking, thinking, etc.)
│   ├── companion.py                 # Piglet system prompt (personality, tools, Malay)
│   └── conversation.py              # Chat history (used by legacy mode only)
├── services/
│   ├── voice_agent.py               # Deepgram Voice Agent WebSocket client
│   │                                #   - Echo suppression (mic mute/unmute)
│   │                                #   - Double-response guard
│   │                                #   - Silence button + debounce
│   │                                #   - Function call handling
│   │                                #   - Settings builder
│   ├── audio.py                     # arecord/aplay subprocess management (no pygame)
│   ├── stt.py                       # Legacy: OpenAI Whisper / Gemini STT
│   ├── llm.py                       # Legacy: OpenAI / Gemini LLM streaming
│   └── tts.py                       # Legacy: OpenAI TTS
├── features/
│   ├── tools.py                     # Voice Agent function definitions + handlers
│   ├── web_search.py                # DuckDuckGo search (needs ddgs rename)
│   ├── music_player.py              # Local music player (aplay based)
│   └── games/
│       ├── tic_tac_toe.py           # Voice-controlled tic-tac-toe
│       └── brick_breaker.py         # Button-controlled brick breaker
├── ui/
│   ├── renderer.py                  # 30fps LCD render thread
│   └── utils.py                     # Text, emoji, color utilities
└── assets/
    ├── fonts/
    ├── emoji_svg/
    ├── images/
    └── music/
```

---

## Debugging Cheat Sheet
```bash
# Kill previous instance + stale audio
sudo pkill -9 -f python; sleep 1; pkill -f arecord; pkill -f aplay

# Test mic
arecord -D default -f S16_LE -r 16000 -c 1 -d 3 /tmp/test.wav

# Test speaker
aplay -D default -r 16000 -f S16_LE -c 1 /tmp/test.wav

# Test full-duplex (both at once -- should work with dmix/dsnoop)
arecord -D default -f S16_LE -r 16000 -c 1 -d 3 /tmp/test.wav &
aplay -D default -r 16000 -f S16_LE -c 1 /tmp/test.wav

# Check ALSA config
cat ~/.asoundrc
arecord -l
aplay -l

# Corrupted git? Fresh clone:
cd ~ && mv mom-bot mom-bot-bak && git clone https://github.com/SirWilliamIII/mom-bot.git
cp mom-bot-bak/newApp/.env mom-bot/newApp/.env

# Run
cd ~/mom-bot/newApp && source .venv/bin/activate && python main.py
```

---

## Hardware Notes

### Whisplay Board
- **LCD**: 240x280 ST7789 via SPI (spidev0.0)
- **Audio codec**: WM8960 (I2S), card name `wm8960soundcard`
- **Button**: BCM GPIO17, active-high (pull-down to GND, pressed = HIGH)
- **RGB LED**: PWM on BCM25 (red), BCM24 (green), BCM23 (blue). Common anode (inverted)
- **Backlight**: PWM on BCM22. Inverted: 0 = full brightness, 100 = off
- **Mic + Speaker**: on-board, very close together (echo is a real problem)

### Pi Zero 2W
- Raspberry Pi OS Bookworm (64-bit)
- GPIO on `gpiochip4` (not `gpiochip0`)
- 512MB RAM -- keep processes lean

---

## Session History

### Session 1-2: Initial Build
- Built pure Python app replacing Node.js+Python hybrid
- Modernized Whisplay driver (RPi.GPIO -> gpiozero+lgpio)
- Batch mode pipeline (record -> STT -> LLM -> TTS -> play)
- Fixed 12 integration bugs (GPIO, ALSA, DNS, API access, etc.)

### Session 3: Voice Agent Migration
- Integrated Deepgram Voice Agent API (single WebSocket for STT+LLM+TTS)
- Removed pygame (was stealing ALSA device at import time)
- Applied official Waveshare asound.conf (dmix/dsnoop)
- **First successful real-time voice conversation on device**

### Session 4 (Current): Turn-Taking & Echo
- Switched STT from nova-3 to flux-general-en (Flux turn detection)
- Built echo suppression (mic mute during speech + delayed unmute)
- Built double-response guard (agent can't talk twice without user input)
- Built silence button with debounce (kills speaker + injects calm-down message)
- Added backlight flash notification
- Added Malay language support in system prompt
- Fixed corrupted git repo on Pi (fresh clone)

### What to Work on Next
1. **Auto-reconnect** -- detect WebSocket drop, reconnect with exponential backoff
2. **Fix web_search** -- switch from duckduckgo_search to ddgs package, add retry
3. **Test music player + games** in Voice Agent mode
4. **Clean up dead config** -- remove DEEPGRAM_EOT_THRESHOLD/TIMEOUT_MS
5. **Startup sound** -- play a short oink/greeting sound on boot before Deepgram connects
6. **Battery indicator** -- PiSugar battery level on LCD
