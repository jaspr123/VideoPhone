#!/usr/bin/env python3
"""Show or precisely set the microphone's ALSA capture gain.

Unlike alsamixer's TUI, this reports and sets an exact percentage, so
there's no ambiguity about what's actually configured or whether a change
was persisted. Uses the same runtime-discovered mixer control as the app's
countdown-time gain nudge (video_guestbook.media.mixer).

Usage:
    python3 scripts/mic_gain.py                     # show current card/control/level
    python3 scripts/mic_gain.py --set 40             # set capture level to 40%
    python3 scripts/mic_gain.py --device plughw:3,0  # override device (skip config)
    python3 scripts/mic_gain.py --set 40 --persist   # also run 'sudo alsactl store'
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

try:
    from video_guestbook.config import BoothConfig, ConfigError
    from video_guestbook.media.mixer import (
        MixerError,
        find_capture_control,
        get_capture_percent,
        resolve_card_index,
        set_capture_percent,
    )
except ImportError:
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from video_guestbook.config import BoothConfig, ConfigError
    from video_guestbook.media.mixer import (
        MixerError,
        find_capture_control,
        get_capture_percent,
        resolve_card_index,
        set_capture_percent,
    )


def resolve_default_config_path() -> Path | None:
    local = REPO_ROOT / "config" / "booth.local.json"
    if local.exists():
        return local
    default = REPO_ROOT / "config" / "booth.default.json"
    if default.exists():
        return default
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=None, help="Booth config JSON to read audio_device from")
    parser.add_argument("--device", type=str, default=None, help="ALSA device, e.g. plughw:CARD=Device,DEV=0 (overrides config)")
    parser.add_argument("--set", type=int, default=None, metavar="PERCENT", help="Set capture level to this percent (0-100)")
    parser.add_argument("--persist", action="store_true", help="Run 'sudo alsactl store' after setting, so it survives reboot")
    return parser.parse_args()


def resolve_device(args: argparse.Namespace) -> str:
    if args.device:
        return args.device

    config_path = args.config or resolve_default_config_path()
    if config_path is None:
        print(
            "No --device given and no config file found. Pass --device directly, "
            "or run from the repo with a config/booth.local.json or "
            "config/booth.default.json present.",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        config = BoothConfig.from_file(config_path, base_dir=REPO_ROOT)
    except ConfigError as exc:
        print(f"Configuration error reading {config_path}: {exc}", file=sys.stderr)
        sys.exit(2)

    return args.device or config.audio_device


def main() -> int:
    args = parse_args()
    device = resolve_device(args)

    try:
        card_index = resolve_card_index(device)
        control = find_capture_control(card_index)
        current = get_capture_percent(card_index, control)
    except MixerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"device={device}")
    print(f"card={card_index}  control='{control}'  current={current}%")

    if args.set is None:
        return 0

    target = max(0, min(100, args.set))
    try:
        set_capture_percent(card_index, control, target)
        new_value = get_capture_percent(card_index, control)
    except MixerError as exc:
        print(f"ERROR: could not set gain: {exc}", file=sys.stderr)
        return 1

    print(f"set '{control}' to {target}%  (now reads back as {new_value}%)")

    if args.persist:
        result = subprocess.run(["sudo", "alsactl", "store"], capture_output=True, text=True)
        if result.returncode == 0:
            print("persisted with 'sudo alsactl store'")
        else:
            print(f"WARNING: 'sudo alsactl store' failed: {result.stderr.strip()}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
