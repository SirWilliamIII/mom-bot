#!/bin/bash
# Kill any previous instance, release GPIO, then start fresh.
cd "$(dirname "$0")"

<<<<<<< HEAD
echo "[run.sh] Starting mom-bot..."

# Stop systemd service if running (so we don't fight over GPIO)
sudo systemctl stop mombot 2>/dev/null && echo "[run.sh] Stopped mombot service"

# Kill orphaned audio processes (can hold Python in D-state via dsnoop/dmix)
pkill -9 -f arecord 2>/dev/null
pkill -9 -f aplay   2>/dev/null

# Remove stale ALSA IPC segments
ipcrm -M 666666 2>/dev/null
ipcrm -M 555555 2>/dev/null

sleep 1

# Kill anything still holding GPIO chip handles — this is the only
# reliable way to free pins after a crash (lgpio.gpio_free doesn't
# work across process boundaries)
sudo fuser -k /dev/gpiochip4 2>/dev/null
sudo fuser -k /dev/gpiochip0 2>/dev/null

sleep 2

echo "[run.sh] GPIO cleared, launching..."
=======
echo "[run.sh] Cleaning up previous instances..."

# Stop systemd service if running
sudo systemctl stop mombot 2>/dev/null

# Kill ANY python process running main.py (venv or system)
pkill -9 -f "python.*main\.py" 2>/dev/null

# Kill orphaned audio processes
pkill -9 -f arecord 2>/dev/null
pkill -9 -f aplay 2>/dev/null

# Force-release GPIO chips
sudo fuser -k /dev/gpiochip4 2>/dev/null
sudo fuser -k /dev/gpiochip0 2>/dev/null

# Wait for kernel to release GPIO handles
sleep 2

echo "[run.sh] Starting mom-bot..."
>>>>>>> 7eb4ff4 (back at it)
source .venv/bin/activate
exec python main.py
