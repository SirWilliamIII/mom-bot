# MomBot (Piglet) - Build Plan

## What We're Building
A cute pink pig companion device for the Raspberry Pi Zero 2W + Whisplay board that can:
- **Chat** via voice (press-and-hold to talk, cloud STT/LLM/TTS)
- **Play songs** from a local MP3 library
- **Play games** (tic-tac-toe via voice + brick breaker via button)
- Display status, emoji, and game graphics on the 240x280 LCD
- RGB LED mood lighting that changes with state

## Hardware
- Raspberry Pi Zero 2W (512MB RAM)
- Whisplay board: 240x280 LCD (SPI ST7789), speaker, mic, RGB LED, 1 button
- PiSugar battery (optional)

## Architecture: Pure Python
Single-language Python app -- no Node.js/TypeScript hybrid. Eliminates the socket bridge overhead and saves ~80MB RAM on the Pi Zero.

### Tech Stack
| Component | Library | Notes |
|-----------|---------|-------|
| GPIO | `gpiozero` + `rpi-lgpio` | Replaces deprecated `RPi.GPIO` |
| SPI/LCD | `spidev` + `Pillow` | Same SPI protocol, modern image rendering |
| Audio record | `arecord` (ALSA) | Direct hardware access |
| Audio play | `pygame.mixer` + `aplay` | MP3/WAV playback |
| STT | OpenAI Whisper API / Gemini | Cloud-based, configurable |
| LLM | OpenAI GPT-4o-mini / Gemini Flash | With function calling for tools |
| TTS | OpenAI TTS-1 / Gemini | Cloud-based, configurable |
| Config | `python-dotenv` | `.env` file for API keys |

### Project Structure
```
newApp/
├── main.py                  # Entry point
├── config.py                # .env loader
├── driver/
│   └── whisplay.py          # Modernized Whisplay driver (gpiozero)
├── core/
│   ├── state_machine.py     # App states: idle/listening/thinking/speaking/game/music
│   ├── companion.py         # System prompt (Piglet personality)
│   └── conversation.py      # Chat history management
├── services/
│   ├── audio.py             # Record/playback via ALSA + pygame
│   ├── stt.py               # Speech-to-text (OpenAI/Gemini)
│   ├── llm.py               # LLM chat with streaming + function calling
│   └── tts.py               # Text-to-speech (OpenAI/Gemini)
├── features/
│   ├── tools.py             # LLM function call definitions + handlers
│   ├── music_player.py      # Local MP3 player
│   └── games/
│       ├── tic_tac_toe.py   # Voice-controlled tic-tac-toe
│       └── brick_breaker.py # Button-controlled brick breaker
├── ui/
│   ├── renderer.py          # 30fps render thread -> LCD
│   └── utils.py             # Text wrapping, emoji, RGB565 conversion
├── assets/
│   ├── fonts/               # Put NotoSansSC-Bold.ttf here
│   ├── images/              # Logo, pig avatar, etc.
│   └── music/               # Drop MP3 files here
├── install.sh               # One-shot Pi setup script
├── run.sh                   # Launch script
├── mombot.service            # systemd unit for auto-start
├── requirements.txt
└── env.template             # Copy to .env, add API keys
```

## State Machine Flow
```
                    ┌──────────────┐
         ┌────────►│     IDLE     │◄────────────┐
         │         │  🐷 waiting   │             │
         │         └──────┬───────┘             │
         │          button press                │
         │                │                     │
         │         ┌──────▼───────┐             │
         │         │  LISTENING   │             │
         │         │  🎤 recording │             │
         │         └──────┬───────┘             │
         │         button release               │
         │                │                     │
         │         ┌──────▼───────┐      timeout/done
         │         │  THINKING    │─────────────┘
         │         │  🤔 STT+LLM  │
         │         └──────┬───────┘
         │           LLM response
         │                │
         │         ┌──────▼───────┐
         │         │  SPEAKING    │
         │         │  🐷 TTS play  │
         │         └──────┬───────┘
         │           done/interrupt
         │                │
         └────────────────┘

  Tool calls branch to:
  ├── GAME (tic-tac-toe / brick breaker)
  └── MUSIC (MP3 playback)
```

## How It Works

### Voice Conversation
1. User presses and holds the button
2. Audio records via `arecord` to a WAV file
3. On release, WAV is sent to cloud STT (Whisper/Gemini)
4. Transcription goes to LLM with conversation history
5. LLM response streams back, split into sentences
6. Each sentence is sent to cloud TTS, audio plays immediately
7. User can interrupt anytime by pressing the button again

### LLM Function Calling
The LLM has tools it can call:
- `play_song(name?)` - play a specific or random song
- `list_songs()` - list available music
- `stop_music()` - stop playback
- `set_volume(percent)` / `increase_volume()` / `decrease_volume()`
- `start_game(game_name)` - launch tic-tac-toe or brick breaker
- `make_game_move(position)` - LLM plays its tic-tac-toe turn

### Games
- **Tic-tac-toe**: Voice-controlled. User says moves like "top left", "center". LLM plays as opponent via function calling. Board renders on LCD.
- **Brick breaker**: Button taps change paddle direction. Rendered at 40fps on the LCD. Long-press exits.

## Deployment Steps

### 1. Flash Pi OS
- Flash Raspberry Pi OS Lite (Bookworm) to SD card
- Enable SSH, WiFi, SPI in `raspi-config`

### 2. Transfer Code
```bash
scp -r newApp/ pi@<pi-ip>:~/mombot/
```

### 3. Install
```bash
ssh pi@<pi-ip>
cd ~/mombot
./install.sh
nano .env  # Add your OPENAI_API_KEY
```

### 4. Add Music
```bash
scp *.mp3 pi@<pi-ip>:~/mombot/assets/music/
```

### 5. Add Font
The install script tries to copy the system Noto font. If it fails:
```bash
# Download and place a TTF font at:
# ~/mombot/assets/fonts/NotoSansSC-Bold.ttf
```

### 6. Test
```bash
cd ~/mombot && source .venv/bin/activate && python main.py
```

### 7. Auto-Start on Boot
```bash
sudo cp mombot.service /etc/systemd/system/
sudo systemctl enable mombot
sudo systemctl start mombot
```

## Provider Costs (Estimate per Day of Normal Use)
| Service | Provider | ~Cost |
|---------|----------|-------|
| STT | OpenAI Whisper | ~$0.05/day |
| LLM | GPT-4o-mini | ~$0.02/day |
| TTS | OpenAI TTS-1 | ~$0.10/day |
| **Total** | | **~$0.17/day** |

Gemini has a generous free tier that may cover light usage entirely.
