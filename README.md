# Video Guestbook Booth

A Raspberry Pi video guestbook phone application. See [PROJECT_SPEC.md](PROJECT_SPEC.md)
for the full project specification.

## Milestone 1 scope

This repository currently implements **Milestone 1 only**, per
[PROJECT_SPEC.md section 23](PROJECT_SPEC.md#23-implementation-prompt-for-codex-or-claude-code):

- Clean Python package structure (`src/video_guestbook/`)
- Configuration moved into a validated JSON file (`config/booth.default.json`)
- MJPEG live preview via OpenCV, full-screen window
- 1280x720 @ 30 fps H.264 recording (video copied straight from the webcam)
- USB microphone recording via the stable ALSA device `plughw:CARD=Device,DEV=0`
- AAC audio, MP4 output
- Graceful FFmpeg stop via stdin `q`
- Spacebar start/stop, `q`/`Esc` to quit
- `READY`, `COUNTDOWN`, `RECORDING`, `SAVING`, `SAVED`, `ERROR` states
- Detailed rotating logs (`logs/booth.log`)
- Automated unit tests (state transitions, filenames, config validation, recording validation)
- A hardware readiness script (`scripts/test_hardware.sh`)

Google Drive sync, themes, the admin UI and hook-switch integration are **out of
scope** for this milestone and are not implemented (see later milestones in
PROJECT_SPEC.md).

> **Note:** [`legacy/booth.py`](legacy/booth.py) is a verbatim backup of the
> confirmed-working prototype from `~/video-booth/app/booth.py`, tested on the
> actual Raspberry Pi hardware. Its proven behavior has been folded into this
> package — in particular:
> - The USB webcam can only be opened by one process at a time, so the OpenCV
>   preview capture is released just before ffmpeg opens the camera device to
>   record, and reopened (with retries) once ffmpeg exits (`main.py`,
>   `_reopen_camera_with_retry`).
> - The ffmpeg audio flags (`-thread_queue_size`, `-use_wallclock_as_timestamps`,
>   `-af aresample=async=1:first_pts=0`, `-avoid_negative_ts make_zero`) that
>   fix real audio/video sync drift on this hardware (`media/ffmpeg.py`).
> - The stdin `q` stop sequence with a `SIGINT` fallback if the pipe write
>   fails (`media/recorder.py`).
>
> Do not edit `legacy/booth.py` — it's kept only as a reference/fallback.

## Repository layout

```text
config/booth.default.json   Default, validated booth configuration
src/video_guestbook/        Application package
  config.py                 Config loading/validation
  state_machine.py          Booth state machine
  logging_setup.py          Rotating file + console logging
  main.py                   Guest UI loop (OpenCV preview, Spacebar control)
  media/
    ffmpeg.py                Centralized ffmpeg/ffprobe command construction
    recorder.py               Recording session lifecycle (start/stop)
    validation.py              Post-recording validation via ffprobe
scripts/
  install.sh                 System + Python dependency installer
  test_hardware.sh            Camera/mic/ffmpeg/output-dir readiness check
tests/unit/                 Automated unit tests (pytest)
legacy/booth.py              Backup of the confirmed-working prototype (reference only)
```

## Installation (Raspberry Pi OS 64-bit)

```bash
git clone <this-repo> ~/video-guestbook
cd ~/video-guestbook
./scripts/install.sh
source .venv/bin/activate
```

`install.sh` installs `ffmpeg`, `alsa-utils`, `v4l-utils` via `apt`, creates a
virtual environment, and installs Python dependencies from `requirements.txt`.

For development (running the test suite), also install dev dependencies:

```bash
pip install -r requirements-dev.txt
```

## Running

Confirm hardware is ready first:

```bash
./scripts/test_hardware.sh
```

This checks that the camera device exists, the configured microphone ALSA
card is present, `ffmpeg`/`ffprobe` are installed, and the recordings output
directory is writable. All four checks must pass before running an event.

Then launch the booth app:

```bash
python3 -m video_guestbook.main
# or: PYTHONPATH=src python3 src/video_guestbook/main.py
```

Optional: point at a different config file:

```bash
python3 -m video_guestbook.main --config /path/to/custom.json
```

### Guest controls

```text
Spacebar -> start recording (from READY) / stop and save (from RECORDING)
Q        -> quit the app
Escape   -> quit the app
```

## Configuration

Edit `config/booth.default.json` (or supply `--config`) to change camera
device, resolution, frame rate, audio device, countdown duration, and
maximum recording length. All fields are validated on load; the app refuses
to start with an invalid configuration and prints the reason.

Key fields:

| Field                  | Meaning                                            |
|-------------------------|-----------------------------------------------------|
| `camera_device`         | v4l2 device path, e.g. `/dev/video0`                |
| `record_resolution`     | `WIDTHxHEIGHT`, e.g. `1280x720`                     |
| `record_fps`             | Recording frame rate (1-60)                         |
| `record_input_format`    | `h264` or `mjpeg` (camera capture mode)             |
| `audio_device`           | Stable ALSA identifier, e.g. `plughw:CARD=Device,DEV=0` |
| `audio_sample_rate`      | 8000/16000/22050/32000/44100/48000                  |
| `audio_channels`         | 1 (mono) or 2 (stereo)                              |
| `audio_bitrate`          | AAC bitrate, e.g. `160k`                            |
| `countdown_seconds`      | Countdown length before recording starts            |
| `max_recording_seconds`  | Recording auto-stop limit                           |
| `output_dir` / `log_dir` | Paths relative to the repository root               |
| `preview_resolution` / `preview_fps` | Live preview quality (independent of recording quality) |
| `av_sync_offset_ms`      | Manual A/V sync correction, in milliseconds. `0` = no correction (default). See "Fixing audio/video sync" below. |

## Fixing audio/video sync

If recordings play back with audio and video out of sync, first figure out
whether it's a **constant offset** (same gap from start to end of the clip)
or **drift** (gets worse the longer the recording runs):

1. Record a short test clip that includes a sharp, distinct sound synced to
   a visible action — a single clap works well.
2. Play it back and note which is ahead, and by roughly how much (in
   milliseconds), at the start of the clip vs. near the end.

**Constant offset** (most common — usually caused by the USB webcam's
onboard H.264 encoder adding more latency than the microphone's audio
path): set `av_sync_offset_ms` in your config:

- Video plays *before* its matching sound (video ahead) → use a **positive**
  value, e.g. `150`, to delay video by that many milliseconds.
- Audio plays *before* its matching action (audio ahead) → use a
  **negative** value, e.g. `-150`, to delay audio instead.

Start with a rough estimate, re-record the clap test, and adjust the value
up or down until the clap lines up. Typical offsets are well under a
second (the config accepts -5000 to 5000 ms).

**Drift that grows over the recording** is a different, harder problem
(usually a clock-rate mismatch between the camera and microphone) and is
not fixed by `av_sync_offset_ms`. If you see this, note how many seconds
it drifts over how long a recording, and it'll need investigating on the
actual hardware (e.g. re-encoding video to a constant frame rate instead
of copying it, at a CPU cost).

## Logs

Rotating logs are written to `logs/booth.log` (5 MB per file, 5 backups).
Each recording session logs a unique session ID alongside every message.

## Recordings

Saved MP4s are written to `recordings/<timestamp>_<session-id>.mp4`. A
recording is only considered complete after ffmpeg exits and `ffprobe`
confirms the file has both a video and an audio stream and meets the
minimum duration.

## Testing

Automated unit tests do not require a camera, microphone, or ffmpeg binary
to be installed (rule 18: testable without physical hardware):

```bash
pip install -r requirements-dev.txt
python3 -m pytest tests/unit -v
```

Covers:

- State machine transitions (valid/invalid, guest-input gating during SAVING)
- Filename/session-ID generation
- Configuration validation (valid + many invalid cases)
- Recording validation logic (missing file, empty file, missing streams,
  short duration, ffprobe failures) using a mocked `ffprobe`

### Manual acceptance tests (must be run on the actual Raspberry Pi)

These require real hardware and have **not** been run by the coding agent —
do not consider Milestone 1 done until they pass on-device:

1. Run `./scripts/test_hardware.sh` — all four checks pass.
2. Launch `python3 -m video_guestbook.main`. Confirm the live preview appears
   full-screen with low lag.
3. Press Spacebar. Confirm a 3-2-1 countdown appears, then recording starts
   (red "REC" indicator, elapsed timer).
4. Press Spacebar again. Confirm "SAVING..." appears briefly, then "Message
   saved" / "Thank you!", then the app returns to READY.
5. Repeat step 3-4 **10 times in a row** without restarting the app.
   Confirm every saved MP4 in `recordings/` plays back correctly with both
   video and audio, and the app never enters an unrecoverable state.
6. Confirm `logs/booth.log` contains one full record of state transitions
   and session IDs for each of the 10 recordings.
7. Press `q` or `Esc` to quit; confirm the app exits cleanly (no orphaned
   ffmpeg process — check with `pgrep ffmpeg`).

## What's explicitly out of scope for Milestone 1

- Google Drive / rclone sync
- Event/theme system and theme overlays
- Admin UI
- Physical hook-switch (GPIO) integration
- Audio normalization/limiting post-processing
