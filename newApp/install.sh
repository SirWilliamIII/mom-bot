#!/bin/bash
set -e

echo "=== MomBot (Piglet) - Raspberry Pi Setup ==="
echo ""

if [ ! -f /etc/os-release ] || ! grep -qi "raspbian\|debian" /etc/os-release; then
    echo "[WARN] This script is designed for Raspberry Pi OS (Debian-based)."
fi

echo "[1/6] Updating system packages..."
sudo apt-get update -qq

echo "[2/6] Installing system dependencies..."
sudo apt-get install -y -qq \
    python3 python3-pip python3-venv \
    python3-spidev python3-numpy python3-pil \
    libcairo2-dev libgirepository1.0-dev \
    alsa-utils sox libsox-fmt-all \
    fonts-noto-cjk \
    git

echo "[3/6] Creating Python virtual environment..."
cd "$(dirname "$0")"
python3 -m venv .venv --system-site-packages
source .venv/bin/activate

echo "[4/6] Installing Python packages..."
pip install --upgrade pip
pip install rpi-lgpio
pip install openai google-generativeai
pip install python-dotenv cairosvg pygame Pillow numpy
pip install websockets duckduckgo_search

echo "[5/6] Setting up ALSA for full-duplex audio..."
if [ ! -f ~/.asoundrc ]; then
    cp asound.conf ~/.asoundrc
    echo "[ALSA] Installed ~/.asoundrc for simultaneous mic + speaker"
else
    echo "[ALSA] ~/.asoundrc already exists (skipping)"
    echo "  If audio doesn't work, try: cp $(pwd)/asound.conf ~/.asoundrc"
fi

echo "[6/6] Setting up configuration..."
if [ ! -f .env ]; then
    cp env.template .env
    echo ""
    echo "  IMPORTANT: Edit .env and add your API key(s)!"
    echo "  nano $(pwd)/.env"
    echo ""
fi

FONT_SRC="/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
FONT_DST="assets/fonts/NotoSansSC-Bold.ttf"
if [ -f "$FONT_SRC" ] && [ ! -f "$FONT_DST" ]; then
    cp "$FONT_SRC" "$FONT_DST" 2>/dev/null || true
    echo "[Font] Copied system Noto font to assets/fonts/"
fi

echo ""
echo "=== Setup complete! ==="
echo ""
echo "To run:"
echo "  cd $(pwd)"
echo "  source .venv/bin/activate"
echo "  python main.py"
echo ""
echo "To run on boot, add to /etc/rc.local or create a systemd service."
