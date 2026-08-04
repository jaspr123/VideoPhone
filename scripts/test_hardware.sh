#!/usr/bin/env bash
# Hardware readiness check for the video guestbook booth (Milestone 1).
#
# Confirms:
#   - camera device exists
#   - microphone ALSA device exists
#   - ffmpeg and ffprobe are installed
#   - the recordings output directory is writable
#
# Usage: scripts/test_hardware.sh [path-to-config.json]

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_PATH="${1:-$REPO_ROOT/config/booth.default.json}"

PASS=0
FAIL=0

pass() {
    echo "[PASS] $1"
    PASS=$((PASS + 1))
}

fail() {
    echo "[FAIL] $1"
    FAIL=$((FAIL + 1))
}

if [[ ! -f "$CONFIG_PATH" ]]; then
    fail "config file not found: $CONFIG_PATH"
    echo ""
    echo "Results: $PASS passed, $FAIL failed"
    exit 1
fi

mapfile -t CONFIG_VALUES < <(python3 - "$CONFIG_PATH" <<'PYEOF'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)

print(data.get("camera_device", ""))
print(data.get("audio_device", ""))
print(data.get("output_dir", ""))
PYEOF
)
CAMERA_DEVICE="${CONFIG_VALUES[0]:-}"
AUDIO_DEVICE="${CONFIG_VALUES[1]:-}"
OUTPUT_DIR="${CONFIG_VALUES[2]:-}"

echo "Using config: $CONFIG_PATH"
echo "  camera_device = $CAMERA_DEVICE"
echo "  audio_device  = $AUDIO_DEVICE"
echo "  output_dir    = $OUTPUT_DIR"
echo ""

# 1. ffmpeg / ffprobe present
if command -v ffmpeg >/dev/null 2>&1; then
    pass "ffmpeg found: $(command -v ffmpeg)"
else
    fail "ffmpeg not found on PATH"
fi

if command -v ffprobe >/dev/null 2>&1; then
    pass "ffprobe found: $(command -v ffprobe)"
else
    fail "ffprobe not found on PATH"
fi

# 2. Camera device exists
if [[ -n "$CAMERA_DEVICE" && -e "$CAMERA_DEVICE" ]]; then
    pass "camera device exists: $CAMERA_DEVICE"
else
    fail "camera device not found: ${CAMERA_DEVICE:-<not set>}"
fi

# 3. Microphone ALSA device exists
#
# `arecord -l` prints lines like:
#   card 3: Device [FDUCE SL40 Audio Device], device 0: USB Audio [USB Audio]
# The short card ID ("Device") sits right after "card N: ", NOT inside the
# brackets (that's the longer human-readable description). Match on the ID
# for a "CARD=NAME" audio_device, or on the numeric index for "hw:N,M" /
# "plughw:N,M" style device strings.
if command -v arecord >/dev/null 2>&1; then
    ARECORD_OUTPUT=$(arecord -l 2>/dev/null)
    CARD_NAME=$(echo "$AUDIO_DEVICE" | sed -n 's/.*CARD=\([^,]*\).*/\1/p')
    CARD_INDEX=$(echo "$AUDIO_DEVICE" | sed -n 's/^[a-z]*hw:\([0-9]\+\).*/\1/p')

    if [[ -n "$CARD_NAME" ]]; then
        if echo "$ARECORD_OUTPUT" | grep -qiE "^card [0-9]+: ${CARD_NAME}\b"; then
            pass "microphone ALSA card '$CARD_NAME' found (arecord -l)"
        else
            fail "microphone ALSA card '$CARD_NAME' not found in 'arecord -l'"
        fi
    elif [[ -n "$CARD_INDEX" ]]; then
        if echo "$ARECORD_OUTPUT" | grep -qE "^card ${CARD_INDEX}:"; then
            pass "microphone ALSA card index $CARD_INDEX found (arecord -l)"
        else
            fail "microphone ALSA card index $CARD_INDEX not found in 'arecord -l'"
        fi
    else
        fail "could not parse a card name or index from audio_device '$AUDIO_DEVICE'"
    fi
else
    fail "arecord not found on PATH (install alsa-utils)"
fi

# 4. Output directory writable
if [[ -n "$OUTPUT_DIR" ]]; then
    case "$OUTPUT_DIR" in
        /*) OUTPUT_DIR_ABS="$OUTPUT_DIR" ;;
        *) OUTPUT_DIR_ABS="$REPO_ROOT/$OUTPUT_DIR" ;;
    esac
    mkdir -p "$OUTPUT_DIR_ABS" 2>/dev/null
    if [[ -d "$OUTPUT_DIR_ABS" && -w "$OUTPUT_DIR_ABS" ]]; then
        pass "output directory writable: $OUTPUT_DIR_ABS"
    else
        fail "output directory not writable: $OUTPUT_DIR_ABS"
    fi
else
    fail "output_dir not set in config"
fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]] && exit 0 || exit 1
