#!/bin/bash
# Kill any previous instance, release GPIO, then start fresh.
cd "$(dirname "$0")"

echo "[run.sh] Cleaning up previous instances..."

# Stop systemd service if running
sudo systemctl stop mombot 2>/dev/null && echo "[run.sh] Stopped mombot service"

# Kill ANY python process running main.py (venv or system)
pkill -9 -f "python.*main\.py" 2>/dev/null

# Kill orphaned audio processes
pkill -9 -f arecord 2>/dev/null
pkill -9 -f aplay   2>/dev/null

# Remove stale ALSA IPC segments
ipcrm -M 666666 2>/dev/null
ipcrm -M 555555 2>/dev/null

# Force-release GPIO chips
sudo fuser -k /dev/gpiochip4 2>/dev/null
sudo fuser -k /dev/gpiochip0 2>/dev/null

# Wait for kernel to release GPIO handles
sleep 2

echo "[run.sh] Starting mom-bot..."
source .venv/bin/activate
exec python main.py
