#!/usr/bin/env python3
"""Standalone microphone level tester -- no camera/video required.

Shows a live level meter in the terminal and writes a timestamped log file
with every reading, clipping events, and possible dropouts, so it can be
sent along for diagnosis. Uses the same level-analysis code
(video_guestbook.media.audio_levels) as the guest app's countdown-time
check, so numbers here match what the app will see.

Usage:
    python3 scripts/mic_test.py                          # use config/booth.local.json
    python3 scripts/mic_test.py --config path/to.json
    python3 scripts/mic_test.py --device plughw:3,0 --rate 48000 --channels 1
    python3 scripts/mic_test.py --seconds 30              # stop after 30s
    Ctrl+C to stop at any time.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

try:
    from video_guestbook.config import BoothConfig, ConfigError
    from video_guestbook.media.audio_levels import (
        CLIP_PEAK_DBFS,
        LOUD_PEAK_DBFS,
        QUIET_RMS_DBFS,
        AudioLevelReader,
        LevelReading,
        MicrophoneError,
        SILENCE_DBFS,
    )
except ImportError:
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from video_guestbook.config import BoothConfig, ConfigError
    from video_guestbook.media.audio_levels import (
        CLIP_PEAK_DBFS,
        LOUD_PEAK_DBFS,
        QUIET_RMS_DBFS,
        AudioLevelReader,
        LevelReading,
        MicrophoneError,
        SILENCE_DBFS,
    )

BAR_WIDTH = 40
BAR_FLOOR_DBFS = -60.0
BAR_CEILING_DBFS = 0.0
DROPOUT_CONSECUTIVE_SILENT_CHUNKS = 3


def render_bar(peak_dbfs: float) -> str:
    clamped = max(BAR_FLOOR_DBFS, min(BAR_CEILING_DBFS, peak_dbfs))
    fraction = (clamped - BAR_FLOOR_DBFS) / (BAR_CEILING_DBFS - BAR_FLOOR_DBFS)
    filled = int(round(fraction * BAR_WIDTH))
    return "#" * filled + "-" * (BAR_WIDTH - filled)


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
    parser.add_argument("--config", type=Path, default=None, help="Booth config JSON to read audio settings from")
    parser.add_argument("--device", type=str, default=None, help="ALSA device, e.g. plughw:CARD=Device,DEV=0 (overrides config)")
    parser.add_argument("--rate", type=int, default=None, help="Sample rate in Hz (overrides config, default 48000)")
    parser.add_argument("--channels", type=int, default=None, help="Channel count (overrides config, default 1)")
    parser.add_argument("--chunk-ms", type=int, default=100, help="Level update interval in milliseconds (default 100)")
    parser.add_argument("--seconds", type=float, default=None, help="Stop automatically after this many seconds (default: run until Ctrl+C)")
    parser.add_argument("--log-dir", type=Path, default=REPO_ROOT / "logs", help="Directory to write the timestamped log file (default: logs/)")
    return parser.parse_args()


def resolve_audio_settings(args: argparse.Namespace) -> tuple[str, int, int]:
    if args.device:
        return args.device, args.rate or 48000, args.channels or 1

    config_path = args.config or resolve_default_config_path()
    if config_path is None:
        print(
            "No --device given and no config file found. Either pass "
            "--device/--rate/--channels directly, or run from the repo "
            "with a config/booth.local.json or config/booth.default.json present.",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        config = BoothConfig.from_file(config_path, base_dir=REPO_ROOT)
    except ConfigError as exc:
        print(f"Configuration error reading {config_path}: {exc}", file=sys.stderr)
        sys.exit(2)

    return (
        args.device or config.audio_device,
        args.rate or config.audio_sample_rate,
        args.channels or config.audio_channels,
    )


def main() -> int:
    args = parse_args()
    device, rate, channels = resolve_audio_settings(args)

    args.log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = args.log_dir / f"mic_test_{timestamp}.log"

    header_lines = [
        f"Microphone test started {datetime.now(timezone.utc).isoformat()}",
        f"device={device} rate={rate}Hz channels={channels} chunk_ms={args.chunk_ms}",
        f"thresholds: quiet<{QUIET_RMS_DBFS}dBFS(RMS)  loud>{LOUD_PEAK_DBFS}dBFS(peak)  clip>={CLIP_PEAK_DBFS}dBFS(peak)",
        "-" * 70,
    ]
    for line in header_lines:
        print(line)

    log_file = open(log_path, "w", encoding="utf-8")
    for line in header_lines:
        log_file.write(line + "\n")
    log_file.flush()

    reader = AudioLevelReader(device=device, sample_rate=rate, channels=channels, chunk_ms=args.chunk_ms)
    try:
        reader.start()
    except MicrophoneError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        log_file.write(f"ERROR: {exc}\n")
        log_file.close()
        return 1

    print("Listening... speak at a normal volume. Press Ctrl+C to stop.\n")

    chunk_count = 0
    clip_count = 0
    dropout_count = 0
    consecutive_silent = 0
    was_active = False
    rms_values: list[float] = []
    peak_values: list[float] = []
    start_time = time.monotonic()

    try:
        while True:
            if args.seconds is not None and (time.monotonic() - start_time) >= args.seconds:
                break

            try:
                reading: LevelReading = reader.read()
            except MicrophoneError as exc:
                print(f"\nERROR: {exc}", file=sys.stderr)
                log_file.write(f"ERROR: {exc}\n")
                break

            chunk_count += 1
            rms_values.append(reading.rms_dbfs)
            peak_values.append(reading.peak_dbfs)

            if reading.clipped:
                clip_count += 1

            is_silent = reading.rms_dbfs <= SILENCE_DBFS + 0.01
            if is_silent:
                consecutive_silent += 1
                if was_active and consecutive_silent == DROPOUT_CONSECUTIVE_SILENT_CHUNKS:
                    dropout_count += 1
                    msg = "  [POSSIBLE DROPOUT] audio went silent after being active"
                    print(f"\n{msg}")
                    log_file.write(
                        f"{time.strftime('%H:%M:%S', time.localtime(reading.timestamp))} {msg}\n"
                    )
            else:
                consecutive_silent = 0
                was_active = True

            bar = render_bar(reading.peak_dbfs)
            clip_marker = " CLIP!" if reading.clipped else ""
            line = (
                f"[{bar}] RMS {reading.rms_dbfs:6.1f} dBFS  "
                f"Peak {reading.peak_dbfs:6.1f} dBFS  {reading.status:<24}{clip_marker}"
            )
            print(f"\r{line}", end="", flush=True)

            log_file.write(
                f"{time.strftime('%H:%M:%S', time.localtime(reading.timestamp))} "
                f"rms={reading.rms_dbfs:.1f} peak={reading.peak_dbfs:.1f} "
                f"status={reading.status} clipped={reading.clipped}\n"
            )
            if chunk_count % 10 == 0:
                log_file.flush()

    except KeyboardInterrupt:
        pass
    finally:
        reader.stop()

    duration = time.monotonic() - start_time
    summary_lines = ["", "-" * 70, "Summary:"]
    if rms_values:
        summary_lines.append(f"  duration:        {duration:.1f}s ({chunk_count} chunks)")
        summary_lines.append(
            f"  RMS dBFS:        min={min(rms_values):.1f}  "
            f"avg={sum(rms_values)/len(rms_values):.1f}  max={max(rms_values):.1f}"
        )
        summary_lines.append(
            f"  Peak dBFS:       min={min(peak_values):.1f}  "
            f"avg={sum(peak_values)/len(peak_values):.1f}  max={max(peak_values):.1f}"
        )
        summary_lines.append(f"  clipping events: {clip_count}")
        summary_lines.append(f"  possible dropouts: {dropout_count}")
    else:
        summary_lines.append("  no readings captured")
    summary_lines.append(f"  log file:        {log_path}")

    for line in summary_lines:
        print(line)
        log_file.write(line + "\n")
    log_file.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
