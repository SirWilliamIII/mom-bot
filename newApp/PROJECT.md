# MomBot (Piglet) - Project Status & Handoff Document

## Overview
Building a companion device for Mom's birthday -- a cute pink pig chatbot that lives on a Raspberry Pi Zero 2W with a PiSugar Whisplay HAT (240x280 LCD, speaker, mic, RGB LED, 1 button). It chats via voice, plays songs, and runs simple games.

**Repo**: https://github.com/SirWilliamIII/mom-bot
**Branch**: `main`
**App location**: `newApp/`

---

## What Was Built (Complete)

### Architecture Decision
Rewrote the original Node.js + Python hybrid chatbot as a **pure Python** app. Rationale:
- Pi Zero 2W only has 512MB RAM; Node.js alone eats ~80MB
- Eliminates the TCP socket bridge between Node.js and Python processes
- The Whisplay driver and display renderer were already Python
- Python has excellent cloud API clients (openai, google-generativeai)
- Single codebase = easier to debug over SSH

### Modernized Whisplay Driver (`driver/whisplay.py`)
- **Replaced deprecated `RPi.GPIO` with `gpiozero` + `lgpio`**
- This was the core problem: `RPi.GPIO` throws version errors on newer Raspberry Pi OS (Bookworm)
- Added auto-detection of GPIO chip (`gpiochip0` vs `gpiochip4`) via `LGPIOFactory` for PWM/output pins
- **Button uses lgpio directly** with a 20ms polling thread -- gpiozero's `Button` class could not reliably handle the Whisplay's active-high button circuit
- Board-to-BCM pin mapping table for all 40 pins
- SPI display control via `spidev` (unchanged from original -- SPI wasn't affected by GPIO deprecation)
- PWM backlight control, RGB LED control
- Hardware version detection (Pi Zero vs Pi Zero 2W for backlight mode)

### Core App (`core/`)
- **`state_machine.py`** -- Heart of the app. States: `idle` -> `listening` -> `thinking` -> `speaking` -> back to `idle`. Also `game` and `music` states. Button press at idle starts recording; release stops recording and triggers STT -> LLM -> TTS pipeline. Button press during speaking interrupts and starts new recording. Release callback is registered immediately in `_set_state()` before any async work to prevent race conditions.
- **`companion.py`** -- System prompt defining Piglet's personality. Warm, friendly pink pig. Knows it lives in a small device. Keeps responses concise for speaker output. Instructions for playing tic-tac-toe, using tools, etc.
- **`conversation.py`** -- Chat history management with auto-reset after configurable idle time (default 5 min).

### Cloud Services (`services/`)
All services support **both OpenAI and Google Gemini**, configurable via `.env`:
- **`stt.py`** -- Speech-to-text. OpenAI Whisper API or Gemini multimodal.
- **`llm.py`** -- Chat with streaming + function calling. OpenAI GPT-4o-mini or Gemini Flash. Streams partial responses and handles tool calls.
- **`tts.py`** -- Text-to-speech. OpenAI TTS-1 (with voice selection) or Gemini. Includes sentence splitting for faster perceived response time. **Note: Gemini TTS does NOT work** -- the `google-generativeai` SDK doesn't support `audio/wav` response mime type. Use OpenAI for TTS.
- **`audio.py`** -- Low-level audio: `arecord` for recording (ALSA via `plughw`), `aplay` for all playback (routed through `plughw:wm8960soundcard`). MP3/OGG/FLAC files are converted to WAV via `ffmpeg` before playback. Volume control via `amixer`.

### UI Layer (`ui/`)
- **`renderer.py`** -- 30fps render thread. Green background with orange text for readability. DejaVu Sans Bold at 28pt. Renders header (status text + emoji + battery indicator), scrolling text area, or game surface to 240x280 LCD. Converts PIL images to RGB565 for SPI transfer.
- **`utils.py`** -- Text wrapping with per-character width measurement, emoji detection + SVG rendering (via cairosvg), RGB565 conversion using numpy, color utilities. Text color is orange `(255, 165, 0)`.

### Features (`features/`)
- **`music_player.py`** -- Scans `assets/music/` for MP3/WAV/OGG/FLAC files. Play by name (fuzzy match), random play, pause, resume, skip, stop.
- **`tools.py`** -- LLM function calling definitions and handlers. Tools: `play_song`, `list_songs`, `stop_music`, `set_volume`, `increase_volume`, `decrease_volume`, `start_game`, `make_game_move`. Each tool returns a string response that gets spoken via TTS.
- **`games/tic_tac_toe.py`** -- Voice-controlled tic-tac-toe. User says moves like "top left", "center". LLM plays as O via `make_game_move` tool. Renders 3x3 grid on LCD with X/O pieces and cursor. Long-press button exits game.
- **`games/brick_breaker.py`** -- Button-controlled brick breaker. Tap button to change paddle direction. 5 rows of colored bricks, ball physics with angle-based paddle reflection, lives system, score tracking. 40fps render loop. Long-press exits.

### Deployment (`install.sh`, `run.sh`, `mombot.service`)
- `install.sh` -- One-shot setup: apt packages, Python venv with `--system-site-packages`, pip dependencies, font copy, .env template
- `run.sh` -- Activates venv and runs `main.py`
- `mombot.service` -- systemd unit for auto-start on boot (user=pi, WorkingDirectory=/home/pi/mombot)

### Configuration (`config.py`, `env.template`)
- All settings via `.env` file
- Required: `OPENAI_API_KEY` and/or `GEMINI_API_KEY` depending on provider choices
- Optional: model selection, TTS voice, volume level, sound card name, custom font path, music directory, chat history reset time, companion name

---

## Bugs Fixed During Integration Testing (Session 1)

### 1. GPIO Chip Detection (gpiochip4 on Bookworm)
**Problem**: Raspberry Pi OS Bookworm moved GPIO from `gpiochip0` to `gpiochip4`. All pin reads returned stale values.
**Fix**: Auto-detect `/dev/gpiochip4` and use `LGPIOFactory(chip=4)` for gpiozero devices. Button polling uses `lgpio.gpiochip_open(chip)` directly.

### 2. First Whisplay Board Was Defective
**Problem**: Exhaustive GPIO scanning (all pins 0-27 on both chip 0 and chip 4, plus offset 512-539) showed zero state changes when button was pressed. LCD and SPI worked fine.
**Fix**: Swapped to a second Whisplay board. Button immediately worked -- idle=0 (LOW), pressed=1 (HIGH).

### 3. Button Active-High Configuration
**Problem**: gpiozero `Button` class defaults to active-low (pull-up). Whisplay button is active-high (680 ohm pull-down to GND, button connects to 5V).
**Fix**: Tried `pull_up=False`, `pull_up=None, active_state=True` -- gpiozero kept rejecting configs. **Replaced gpiozero Button entirely with direct lgpio polling thread** at 20ms interval. Simple, reliable, no abstraction issues.

### 4. Button Poll Thread Blocking
**Problem**: Button press callback (`_set_state("listening")`) ran synchronously in the poll thread, blocking it from detecting the release event.
**Fix**: Dispatch all button callbacks via `threading.Thread(target=cb, daemon=True).start()` so the poll loop keeps running.

### 5. Release Callback Race Condition
**Problem**: Press callback spawned a thread to run `_set_state("listening")` which cleared callbacks then set the release callback. But the release often happened before the thread got to registering it, resulting in "No release callback registered!".
**Fix**: Register the release callback **immediately** in `_set_state()` before entering the handler, specifically for the `listening` state: `if new_state == "listening": self.board.on_button_release(self._on_release_from_listening)`.

### 6. Recording Lock Deadlock
**Problem**: `start_recording()` acquired `_recording_lock` and called `stop_recording()` inside it. `stop_recording()` also tried to acquire `_recording_lock`. Deadlock.
**Fix**: Inlined the stop logic directly in `start_recording()` instead of calling `stop_recording()`.

### 7. WM8960 Audio Format
**Problem**: `arecord -D hw:wm8960soundcard -f S16_LE` failed -- WM8960 only supports S24_LE and S32_LE natively.
**Fix**: Use `plughw:` (ALSA plugin layer for format conversion) instead of `hw:`. Also required installing `libasound2-plugins` for the sample rate converter (`libasound_module_rate_samplerate.so`).
**Final command**: `arecord -D plughw:wm8960soundcard -f S16_LE -r 16000 -c 1`

### 8. Pi-hole Blocking OpenAI API
**Problem**: `api.openai.com` resolved to `127.0.0.1` because the Pi's DNS pointed to a Pi-hole that was blocking it.
**Fix**: Whitelisted `api.openai.com` in Pi-hole. Temporary workaround: `nameserver 8.8.8.8` in `/etc/resolv.conf`.

### 9. OpenAI Project Model Access
**Problem**: `403 - Project does not have access to model whisper-1`. The OpenAI API key's project didn't have Whisper enabled.
**Fix**: Switched STT to Gemini (`STT_PROVIDER=gemini`). OpenAI Whisper requires enabling Audio models in the project settings at https://platform.openai.com/settings.

### 10. Gemini TTS Broken
**Problem**: `google-generativeai` SDK doesn't support `response_mime_type="audio/wav"`. Returns 400 error.
**Fix**: Use OpenAI for TTS (`TTS_PROVIDER=openai`). Gemini TTS would require the newer `google-genai` SDK or REST API -- not implemented yet.

### 11. Audio Playback Silent
**Problem**: TTS generates MP3 files. `pygame.mixer` was playing them but not routing to the WM8960 sound card (went to default ALSA device which is HDMI/headphone jack).
**Fix**: Replaced all `pygame.mixer` playback with `aplay -D plughw:wm8960soundcard`. MP3 files are first converted to WAV via `ffmpeg -y -i input.mp3 -ar 48000 -ac 2 output.wav`, then played with `aplay`.

### 12. Missing System Dependencies
**Problem**: Various missing packages on fresh Pi OS install.
**Fix**: `install.sh` updated. Manual installs needed: `sudo apt-get install -y libasound2-plugins ffmpeg`. The `openai` pip package was missing from the venv -- needed `./install.sh` re-run.

---

## Current Working State

### What Works
- [x] LCD display (240x280, SPI, ST7789)
- [x] RGB LED mood lighting (changes per state)
- [x] PWM backlight
- [x] Button press/release detection (lgpio polling)
- [x] Audio recording (plughw + S16_LE via ALSA plugins)
- [x] STT via Gemini (transcription works)
- [x] LLM via Gemini (streaming responses, function calling)
- [x] Full conversation flow: idle -> listening -> thinking -> speaking -> idle
- [x] Green background + orange text UI at 28pt
- [x] Button interrupt during speaking (re-record)

### What's Not Working / Untested
- [ ] **TTS audio playback** -- OpenAI TTS generates MP3, ffmpeg converts to WAV, aplay should play it, but no sound heard yet. `speaker-test` works. Need to debug the specific TTS playback path.
- [ ] **Gemini TTS** -- SDK doesn't support audio output. Use OpenAI for TTS.
- [ ] **Music player** -- not yet tested. Need to add MP3 files to `assets/music/`
- [ ] **Games** -- tic-tac-toe and brick breaker not yet tested on device
- [ ] **Cold start latency** -- first API call is slow (~5-8s) due to HTTPS connection setup. Need warmup call at startup.
- [ ] **Font rendering** -- DejaVu Sans Bold is set but user reported font "didn't change". May need to verify the font file exists at `/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf` on the Pi.

---

## Provider Configuration (Current)
```env
STT_PROVIDER=gemini
LLM_PROVIDER=gemini
TTS_PROVIDER=openai
OPENAI_API_KEY=<set>
GEMINI_API_KEY=<set>
```

**Recommended provider combos:**
| Combo | Pros | Cons |
|-------|------|------|
| All Gemini (STT+LLM) + OpenAI TTS | Free STT/LLM (Gemini free tier), reliable TTS | Gemini TTS broken, need OpenAI for voice |
| All OpenAI | Single API key, consistent quality | Needs Whisper access on project, costs ~$0.17/day |
| Gemini STT + OpenAI LLM + OpenAI TTS | Best quality combo | Two API keys needed |

---

## File Structure
```
newApp/
├── main.py                          # Entry point, signal handling, startup
├── config.py                        # .env loader, validation
├── env.template                     # Template for .env (copy and fill API keys)
├── requirements.txt                 # Python dependencies
├── install.sh                       # One-shot Pi setup (apt + venv + pip)
├── run.sh                           # Launch script (activates .venv)
├── mombot.service                   # systemd unit for auto-start
├── plan.md                          # Original build plan with architecture diagrams
├── PROJECT.md                       # This file
├── chatbot-inspiration.png          # Reference image: fat cute pink pigs
├── Whisplay-pinout.csv              # Pin mapping reference
├── driver/
│   ├── __init__.py
│   └── whisplay.py                  # Whisplay driver (gpiozero + lgpio polling for button)
├── core/
│   ├── __init__.py
│   ├── state_machine.py             # App state machine (idle/listen/think/speak/game/music)
│   ├── companion.py                 # Piglet personality system prompt
│   └── conversation.py              # Chat history with auto-reset
├── services/
│   ├── __init__.py
│   ├── audio.py                     # Record (arecord/plughw) + playback (aplay/plughw + ffmpeg)
│   ├── stt.py                       # Speech-to-text (OpenAI Whisper / Gemini)
│   ├── llm.py                       # LLM chat streaming + function calling
│   └── tts.py                       # Text-to-speech (OpenAI TTS-1 only -- Gemini broken)
├── features/
│   ├── __init__.py
│   ├── tools.py                     # LLM tool definitions + execution
│   ├── music_player.py              # Local MP3 player
│   └── games/
│       ├── __init__.py
│       ├── tic_tac_toe.py           # Voice-controlled tic-tac-toe
│       └── brick_breaker.py         # Button-controlled brick breaker
├── ui/
│   ├── __init__.py
│   ├── renderer.py                  # 30fps LCD render thread (green bg, orange text, 28pt)
│   └── utils.py                     # Text, emoji, color, image utilities
└── assets/
    ├── fonts/                       # (empty -- using system DejaVu Sans Bold)
    ├── emoji_svg/                   # Optional: Noto emoji SVGs
    ├── images/                      # Logo, pig avatar
    └── music/                       # Drop MP3 files here
```

---

## What Still Needs To Be Done

### Immediate (Next Session)
- [ ] **Fix TTS audio playback** -- debug why `aplay` isn't producing sound for TTS-generated WAV files. Test manually: generate MP3 via OpenAI TTS API, convert with ffmpeg, play with aplay.
- [ ] **Add startup warmup** -- pre-connect to cloud APIs on boot to eliminate first-request latency
- [ ] **Verify font rendering** -- confirm DejaVu Sans Bold exists at `/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf`
- [ ] **Test music player** -- add MP3 files, say "play me a song"
- [ ] **Test games** -- say "let's play tic tac toe" / "let's play brick breaker"

### Short Term
- [ ] Fix Gemini TTS (use REST API or `google-genai` package instead of `google-generativeai`)
- [ ] Add error handling for network drops (retry logic, offline message)
- [ ] Set up `mombot.service` for auto-start on boot
- [ ] Persist DNS fix (Pi-hole whitelist or static DNS in NetworkManager config)

### Nice To Have (Future)
- [ ] Pig avatar on idle screen (render a cute pig image in `assets/images/logo.png`)
- [ ] Boot animation / greeting ("Good morning!" based on time of day)
- [ ] Battery level display (PiSugar battery integration)
- [ ] Wake word detection (instead of button press, say "Hey Piglet")
- [ ] More games (trivia, 20 questions, word games -- LLM-driven)
- [ ] Web config UI (change settings without SSH)
- [ ] OTA updates (git pull via button combo or voice command)

---

## Key Dependencies
| Package | Purpose | Pi-specific? |
|---------|---------|-------------|
| gpiozero | GPIO control for PWM/output pins | Yes |
| lgpio | Direct GPIO for button polling | Yes |
| rpi-lgpio | lgpio backend for gpiozero | Yes |
| spidev | SPI bus for LCD | Yes |
| Pillow | Image creation/manipulation for LCD | No |
| numpy | Fast RGB565 conversion | No |
| pygame | Mixer init only (playback moved to aplay) | No |
| openai | OpenAI API (STT, LLM, TTS) | No |
| google-generativeai | Gemini API (STT, LLM) | No |
| python-dotenv | .env config loading | No |
| cairosvg | Optional: emoji SVG rendering | No |
| ffmpeg | System package: MP3 to WAV conversion for aplay | Yes (apt) |
| libasound2-plugins | ALSA rate/format conversion plugins | Yes (apt) |

## System Packages Required (apt)
```bash
sudo apt-get install -y python3 python3-pip python3-venv python3-spidev python3-numpy python3-pil libcairo2-dev libgirepository1.0-dev alsa-utils sox libsox-fmt-all fonts-noto-cjk git ffmpeg libasound2-plugins
```

## Cost Estimate (OpenAI, normal daily use)
- STT (Whisper): ~$0.05/day
- LLM (GPT-4o-mini): ~$0.02/day
- TTS (TTS-1): ~$0.10/day
- **Total: ~$0.17/day** (~$5/month)
- Gemini free tier may cover STT + LLM entirely

---

## Hardware Notes

### Whisplay Board
- **LCD**: 240x280 ST7789 via SPI (spidev0.0)
- **Audio codec**: WM8960 (I2S, card 1)
  - Recording: only supports S24_LE / S32_LE natively; use `plughw:` for format conversion
  - Playback: `aplay -D plughw:wm8960soundcard` works; `speaker-test` confirms
  - Volume: `amixer -D hw:wm8960soundcard sset Speaker <0-127>`
- **Button**: BOARD pin 11 / BCM GPIO17, active-high (pull-down to GND, pressed = 5V)
- **RGB LED**: PWM on BOARD pins 22 (red/BCM25), 18 (green/BCM24), 16 (blue/BCM23). Common anode (1.0 = off, 0.0 = full brightness)
- **Backlight**: PWM on BOARD pin 15 / BCM22. Inverted: value 0.0 = full brightness, 1.0 = off
- **DC pin**: BOARD 13 / BCM27
- **RST pin**: BOARD 7 / BCM4
- **Docs**: https://docs.pisugar.com/docs/product-wiki/whisplay/overview
- **Schematic**: https://cdn.pisugar.com/pisugar-docs/documents/whisplay/Whisplay.pdf

### Pi Zero 2W Specifics
- Raspberry Pi OS Bookworm (64-bit)
- GPIO on `gpiochip4` (not `gpiochip0` like older Pi OS)
- `gpiochip0` GPIOs start at offset 512 (512-565)
- `/boot/firmware/config.txt` (not `/boot/config.txt`)

---

## Original Codebase Reference
The original code lives in the same repo:
- `Whisplay/` -- Original Whisplay driver (RPi.GPIO based, Chinese comments)
- `whisplay-ai-chatbot/` -- Original Node.js + Python hybrid chatbot
  - `src/` -- TypeScript: ChatFlow, StreamResponsor, display, audio, battery, LLM config
  - `python/` -- Python: whisplay display renderer, camera, LED control, key input
  - Connected via TCP socket (Node.js sends display commands to Python process)
  - Used Google Gemini for LLM, Google Cloud TTS, Whisper for STT

---

## Quick Start (from scratch)
```bash
# On the Pi
git clone https://github.com/SirWilliamIII/mom-bot.git
cd mom-bot/newApp
sudo apt-get install -y ffmpeg libasound2-plugins
./install.sh
cp env.template .env
nano .env  # Add OPENAI_API_KEY and/or GEMINI_API_KEY
# Kill any old python holding GPIO
sudo pkill -9 -f python; sleep 2
./run.sh
```

## Debugging Cheat Sheet
```bash
# GPIO busy?
sudo pkill -9 -f python; sleep 2

# Test button
python3 -c "import lgpio,time; h=lgpio.gpiochip_open(4); lgpio.gpio_claim_input(h,17); [print(lgpio.gpio_read(h,17)) or time.sleep(0.3) for _ in range(30)]"

# Test recording
arecord -D plughw:wm8960soundcard -f S16_LE -r 16000 -c 1 -d 3 /tmp/test.wav

# Test playback
speaker-test -D plughw:wm8960soundcard -t wav -c 2 -l 1

# Test TTS manually
ffmpeg -y -i /tmp/test_tts.mp3 -ar 48000 -ac 2 /tmp/test_tts.wav && aplay -D plughw:wm8960soundcard /tmp/test_tts.wav

# Check DNS
ping -c 1 api.openai.com

# Check sound card
arecord -l
amixer -D hw:wm8960soundcard sget Speaker
```
