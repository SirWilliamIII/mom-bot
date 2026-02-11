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
- **Replaced deprecated `RPi.GPIO` with `gpiozero` + `rpi-lgpio`**
- This was the core problem: `RPi.GPIO` throws version errors on newer Raspberry Pi OS (Bookworm)
- Added auto-detection of GPIO chip (`gpiochip0` vs `gpiochip4`) via `LGPIOFactory`
- Board-to-BCM pin mapping table for all 40 pins
- SPI display control via `spidev` (unchanged from original -- SPI wasn't affected by GPIO deprecation)
- PWM backlight control, RGB LED control, button input with callbacks
- Hardware version detection (Pi Zero vs Pi Zero 2W for backlight mode)

### Core App (`core/`)
- **`state_machine.py`** -- Heart of the app. States: `idle` -> `listening` -> `thinking` -> `speaking` -> back to `idle`. Also `game` and `music` states. Button press at idle starts recording; release stops recording and triggers STT -> LLM -> TTS pipeline. Button press during speaking interrupts and starts new recording.
- **`companion.py`** -- System prompt defining Piglet's personality. Warm, friendly pink pig. Knows it lives in a small device. Keeps responses concise for speaker output. Instructions for playing tic-tac-toe, using tools, etc.
- **`conversation.py`** -- Chat history management with auto-reset after configurable idle time (default 5 min).

### Cloud Services (`services/`)
All services support **both OpenAI and Google Gemini**, configurable via `.env`:
- **`stt.py`** -- Speech-to-text. OpenAI Whisper API or Gemini multimodal.
- **`llm.py`** -- Chat with streaming + function calling. OpenAI GPT-4o-mini or Gemini Flash. Streams partial responses and handles tool calls.
- **`tts.py`** -- Text-to-speech. OpenAI TTS-1 (with voice selection) or Gemini. Includes sentence splitting for faster perceived response time.
- **`audio.py`** -- Low-level audio: `arecord` for recording (ALSA), `pygame.mixer` for MP3 playback, `aplay` for WAV. Volume control via `amixer`.

### UI Layer (`ui/`)
- **`renderer.py`** -- 30fps render thread. Renders header (status text + emoji + battery indicator), scrolling text area, or game surface to 240x280 LCD. Converts PIL images to RGB565 for SPI transfer. Supports full-screen image mode for games.
- **`utils.py`** -- Text wrapping with per-character width measurement, emoji detection + SVG rendering (via cairosvg), RGB565 conversion using numpy, color utilities.

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
- Required: just `OPENAI_API_KEY` (or `GEMINI_API_KEY` if using Gemini)
- Optional: model selection, TTS voice, volume level, sound card name, custom font path, music directory, chat history reset time, companion name

---

## Current Status: BLOCKED on Button Input

### The Problem
The physical button on the Whisplay board is not being detected by any GPIO library or kernel interface. We've tested exhaustively:

### What We Tried
1. **gpiozero Button(17, pull_up=True)** -- no events fired
2. **lgpio direct on gpiochip0** -- `lgpio.gpio_read(h, 17)` always returns 1, no change on press
3. **lgpio direct on gpiochip4** -- same result
4. **Full pin scan (BCM 0-27) on both gpiochip0 and gpiochip4** -- no pin changed state when button pressed
5. **Offset pin scan (512-539) on gpiochip0** -- the debug output showed `gpiochip0: GPIOs 512-565`, so we tried offset pins too. No change detected.
6. **sysfs `/sys/class/gpio/gpio17`** -- not yet tested with new board (user is swapping boards)
7. **pinctrl** -- suggested but not yet confirmed

### Hardware Facts
- **Board**: PiSugar Whisplay HAT
- **Button**: DM1-117C-1 push button switch on BOARD pin 11 (BCM GPIO17)
- **Button circuit** (from schematic at `https://cdn.pisugar.com/pisugar-docs/documents/whisplay/Whisplay.pdf`):
  - Button connects GPIO17 to 5V when pressed
  - 680 ohm pull-down resistor (R4) to GND keeps pin LOW when not pressed
  - So: idle = LOW (0), pressed = HIGH (1)
  - **Note**: 5V into a 3.3V GPIO -- this is technically out of spec but typically works on Pi
- **LCD, SPI, RGB LED all work fine** -- only the button is non-functional
- **Docs confirm pin**: https://docs.pisugar.com/docs/product-wiki/whisplay/overview

### Kernel/GPIO Info
```
gpiochip0: GPIOs 512-565, parent: platform/3f200000.gpio, pinctrl-bcm2835
/dev/gpiochip0 and /dev/gpiochip4 both exist
dmesg: rpi-gpiomem 3f200000.gpiomem: window base 0x3f200000 size 0x00001000
```

### Config.txt (relevant)
```
dtparam=i2c_arm=on
dtparam=i2s=on
dtparam=spi=on
dtparam=audio=on
dtoverlay=vc4-kms-v3d
dtoverlay=i2s-mmap
dtoverlay=wm8960-soundcard
```

### Current Theory
Most likely a **physical connection issue** -- the Pi header pins for pin 11 may not be making contact with the Whisplay board. The LCD works because SPI pins are in a different section of the header. User is currently **swapping to a different Whisplay board** to test.

### If New Board Also Fails
- Check if the `wm8960-soundcard` overlay is somehow claiming GPIO17
- Try temporarily removing `dtoverlay=wm8960-soundcard` from config.txt to isolate
- Test with a simple jumper wire from pin 11 to 3.3V to simulate a button press
- Consider adding an external button wired to a known-working GPIO pin as a workaround

---

## File Structure
```
newApp/
├── main.py                          # Entry point, signal handling, startup
├── config.py                        # .env loader, validation
├── env.template                     # Template for .env (copy and fill API keys)
├── requirements.txt                 # Python dependencies
├── install.sh                       # One-shot Pi setup (apt + venv + pip)
├── run.sh                           # Launch script
├── mombot.service                   # systemd unit for auto-start
├── plan.md                          # Original build plan with architecture diagrams
├── chatbot-inspiration.png          # Reference image: fat cute pink pigs
├── driver/
│   ├── __init__.py
│   └── whisplay.py                  # Modernized Whisplay driver (gpiozero)
├── core/
│   ├── __init__.py
│   ├── state_machine.py             # App state machine (idle/listen/think/speak/game/music)
│   ├── companion.py                 # Piglet personality system prompt
│   └── conversation.py              # Chat history with auto-reset
├── services/
│   ├── __init__.py
│   ├── audio.py                     # Record (arecord) + playback (pygame/aplay)
│   ├── stt.py                       # Speech-to-text (OpenAI Whisper / Gemini)
│   ├── llm.py                       # LLM chat streaming + function calling
│   └── tts.py                       # Text-to-speech (OpenAI TTS-1 / Gemini)
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
│   ├── renderer.py                  # 30fps LCD render thread
│   └── utils.py                     # Text, emoji, color, image utilities
└── assets/
    ├── fonts/                       # Put NotoSansSC-Bold.ttf here
    ├── emoji_svg/                   # Optional: Noto emoji SVGs
    ├── images/                      # Logo, pig avatar
    └── music/                       # Drop MP3 files here
```

---

## What Still Needs To Be Done

### Immediate (Blocked)
- [ ] **Get button working** -- swap board, retest with sysfs/pinctrl, verify physical connection
- [ ] If button works: full end-to-end voice conversation test (button -> record -> STT -> LLM -> TTS -> speaker)

### Short Term
- [ ] **Add a font** -- place `NotoSansSC-Bold.ttf` (or any TTF) in `assets/fonts/`. The install script tries to copy from system fonts but may need manual placement.
- [ ] **Add music** -- drop MP3 files into `assets/music/`
- [ ] **Test audio recording** -- verify `arecord -D hw:wm8960soundcard` works. If the card name differs, update `SOUND_CARD_NAME` in `.env`
- [ ] **Test audio playback** -- verify speaker output works with `aplay` and `pygame.mixer`
- [ ] **Set up .env** -- copy `env.template` to `.env`, add `OPENAI_API_KEY`

### Nice To Have (Future)
- [ ] Pig avatar on idle screen (render a cute pig image in `assets/images/logo.png`)
- [ ] Boot animation / greeting ("Good morning!" based on time of day)
- [ ] Battery level display (PiSugar battery integration -- the original chatbot had this)
- [ ] Wake word detection (instead of button press, say "Hey Piglet")
- [ ] Adjust tic-tac-toe difficulty (currently LLM decides how hard to play)
- [ ] More games (trivia, 20 questions, word games -- these are easy to add as LLM-driven features)
- [ ] Web config UI (change settings without SSH)
- [ ] OTA updates (git pull via button combo or voice command)

---

## Key Dependencies
| Package | Purpose | Pi-specific? |
|---------|---------|-------------|
| gpiozero | GPIO control (replaces RPi.GPIO) | Yes |
| rpi-lgpio | lgpio backend for gpiozero on Bookworm | Yes |
| spidev | SPI bus for LCD | Yes |
| Pillow | Image creation/manipulation for LCD | No |
| numpy | Fast RGB565 conversion | No |
| pygame | MP3 audio playback | No |
| openai | OpenAI API (STT, LLM, TTS) | No |
| google-generativeai | Gemini API (alternative provider) | No |
| python-dotenv | .env config loading | No |
| cairosvg | Optional: emoji SVG rendering | No |

## Cost Estimate (OpenAI, normal daily use)
- STT (Whisper): ~$0.05/day
- LLM (GPT-4o-mini): ~$0.02/day
- TTS (TTS-1): ~$0.10/day
- **Total: ~$0.17/day** (~$5/month)
- Gemini free tier may cover light usage entirely

---

## Original Codebase Reference
The original code lives in the same repo:
- `Whisplay/` -- Original Whisplay driver (RPi.GPIO based, Chinese comments)
- `whisplay-ai-chatbot/` -- Original Node.js + Python hybrid chatbot
  - `src/` -- TypeScript: ChatFlow, StreamResponsor, display, audio, battery, LLM config
  - `python/` -- Python: whisplay display renderer, camera, LED control, key input
  - Connected via TCP socket (Node.js sends display commands to Python process)
  - Used Google Gemini for LLM, Google Cloud TTS, Whisper for STT
