#!/usr/bin/env bash
# Install system and Python dependencies for the video guestbook booth
# (Milestone 1). Intended for Raspberry Pi OS 64-bit.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "Installing system packages (ffmpeg, alsa-utils, v4l-utils)..."
sudo apt-get update
sudo apt-get install -y ffmpeg alsa-utils v4l-utils python3-venv python3-pip

echo "Creating Python virtual environment at $REPO_ROOT/.venv..."
python3 -m venv "$REPO_ROOT/.venv"
source "$REPO_ROOT/.venv/bin/activate"

echo "Installing Python dependencies..."
pip install --upgrade pip
pip install -r "$REPO_ROOT/requirements.txt"
pip install -e "$REPO_ROOT"

echo ""
echo "Install complete. Activate the environment with:"
echo "  source $REPO_ROOT/.venv/bin/activate"
