#!/bin/bash
# install-service.sh — Install MomBot as a systemd service on Raspberry Pi
#
# Usage:
#   chmod +x install-service.sh
#   ./install-service.sh
#
# After install:
#   sudo systemctl start mombot       # Start now
#   sudo systemctl status mombot      # Check status
#   journalctl -u mombot -f           # Tail logs
#   sudo systemctl stop mombot        # Stop
#   sudo systemctl restart mombot     # Restart
#   sudo systemctl disable mombot     # Disable auto-start

set -euo pipefail

SERVICE_NAME="mombot"
SERVICE_FILE="mombot.service"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== MomBot Service Installer ==="
echo ""

# Check we're on the Pi
if [[ ! -f /proc/device-tree/model ]]; then
    echo "Warning: doesn't look like a Raspberry Pi. Continuing anyway..."
fi

# Check service file exists
if [[ ! -f "$SCRIPT_DIR/$SERVICE_FILE" ]]; then
    echo "Error: $SERVICE_FILE not found in $SCRIPT_DIR"
    exit 1
fi

# Check venv exists
VENV_PATH="$SCRIPT_DIR/.venv"
if [[ ! -d "$VENV_PATH" ]]; then
    echo "Error: Python venv not found at $VENV_PATH"
    echo "Create it with: python3 -m venv $VENV_PATH && $VENV_PATH/bin/pip install -r requirements.txt"
    exit 1
fi

# Check .env exists
if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
    echo "Warning: .env file not found — copy env.template and add your API keys"
fi

# Stop existing service if running
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    echo "Stopping existing $SERVICE_NAME service..."
    sudo systemctl stop "$SERVICE_NAME"
fi

# Copy service file
echo "Installing service file..."
sudo cp "$SCRIPT_DIR/$SERVICE_FILE" /etc/systemd/system/
sudo systemctl daemon-reload

# Enable auto-start on boot
echo "Enabling auto-start on boot..."
sudo systemctl enable "$SERVICE_NAME"

# Start the service
echo "Starting $SERVICE_NAME..."
sudo systemctl start "$SERVICE_NAME"

echo ""
echo "=== Done! ==="
echo ""
echo "  Status:   sudo systemctl status $SERVICE_NAME"
echo "  Logs:     journalctl -u $SERVICE_NAME -f"
echo "  Stop:     sudo systemctl stop $SERVICE_NAME"
echo "  Restart:  sudo systemctl restart $SERVICE_NAME"
echo "  Disable:  sudo systemctl disable $SERVICE_NAME"
echo ""

# Show status
sudo systemctl status "$SERVICE_NAME" --no-pager || true
