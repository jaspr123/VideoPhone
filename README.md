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
    audio_levels.py            Mic level sampling/classification (dBFS via arecord)
    mixer.py                   ALSA capture-gain discovery + one-shot nudge
scripts/
  install.sh                 System + Python dependency installer
  test_hardware.sh            Camera/mic/amixer/ffmpeg/output-dir readiness check
  mic_test.py                 Standalone mic level meter + log, no camera needed
  mic_gain.py                  Show/set exact ALSA capture gain (no alsamixer TUI)
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

`config/booth.default.json` is a **tracked file with generic defaults** —
treat it the same as any other source file: don't hand-edit it on a
deployed Pi, because a future `git pull` will conflict with your local
changes.

For device-specific values (`camera_device`, `audio_device`,
`av_sync_offset_ms`, and anything else particular to one Pi/camera/mic),
make a **local, untracked copy** instead:

```bash
cp config/booth.default.json config/booth.local.json
```

`config/booth.local.json` is gitignored, so it's yours to edit freely and
`git pull` will never touch it. Run the app against it:

```bash
python3 -m video_guestbook.main --config config/booth.local.json
```

(and pass the same path to `scripts/test_hardware.sh config/booth.local.json`).
All fields are validated on load either way; the app refuses to start with
an invalid configuration and prints the reason.

Key fields:

| Field                  | Meaning                                            |
|-------------------------|-----------------------------------------------------|
| `camera_device`         | v4l2 device path, e.g. `/dev/video0`                |
| `record_resolution`     | `WIDTHxHEIGHT`, e.g. `1280x720`                     |
| `record_fps`             | Recording frame rate (1-60)                         |
| `record_input_format`    | `h264` (copy, no CPU cost) or `mjpeg` (software re-encode, real CPU cost). See "Camera compatibility" below. |
| `audio_device`           | Stable ALSA identifier, e.g. `plughw:CARD=Device,DEV=0` |
| `audio_sample_rate`      | 8000/16000/22050/32000/44100/48000                  |
| `audio_channels`         | 1 (mono) or 2 (stereo)                              |
| `audio_bitrate`          | AAC bitrate, e.g. `160k`                            |
| `countdown_seconds`      | Countdown length before recording starts            |
| `max_recording_seconds`  | Recording auto-stop limit                           |
| `output_dir` / `log_dir` | Paths relative to the repository root               |
| `preview_resolution` / `preview_fps` | Live preview quality (independent of recording quality) |
| `av_sync_offset_ms`      | Manual A/V sync correction, in milliseconds. `0` = no correction (default). See "Fixing audio/video sync" below. |

## Camera compatibility (H.264 vs MJPEG)

Not every USB webcam has an onboard H.264 encoder. Before assuming
`record_input_format: "h264"` will work, check what your camera actually
offers:

```bash
v4l2-ctl -d /dev/video0 --list-formats-ext
```

- **If `H264` is listed** for your target resolution/fps: set
  `record_input_format: "h264"`. The camera's H.264 bitstream is copied
  straight into the MP4 with zero CPU cost (this is the original design,
  per PROJECT_SPEC.md section 16).
- **If only `MJPG`/`YUYV` are listed** (no `H264`): set
  `record_input_format: "mjpeg"`. Video is then software-encoded to H.264
  on the Pi's CPU (`libx264`, `ultrafast` preset, ~4 Mbps) instead of
  copied. This has **not** been verified to hold 1280x720@30fps in real
  time on a Pi 4 — test it with a real recording and watch for dropped
  frames or preview lag. If it can't keep up, try a lower
  `record_resolution` or `record_fps` before anything else.

Setting `record_input_format` to a value the camera doesn't actually
support (e.g. `h264` on a camera with no H.264 output) will fail to start
recording — this is the single most common cause of "the app won't
record" after swapping cameras.

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

## Testing the microphone (`scripts/mic_test.py`)

A standalone level meter and logger for diagnosing mic issues (feedback,
clipping, dropouts, wrong gain) without needing the camera or the full
app running:

```bash
python3 scripts/mic_test.py                     # uses config/booth.local.json (or booth.default.json)
python3 scripts/mic_test.py --device plughw:3,0 --rate 48000 --channels 1
python3 scripts/mic_test.py --seconds 30         # stop automatically after 30s
```

Ctrl+C to stop at any time. It shows a live bar meter and status
(`MICROPHONE READY` / `SPEAK CLOSER` / `TOO LOUD - MOVE SLIGHTLY AWAY`,
using the same thresholds as the app), flags clipping (`CLIP!`) and
possible dropouts (audio going silent right after being active) in real
time, and writes everything to a timestamped file under `logs/` (e.g.
`logs/mic_test_20260101_120000.log`). **Send that log file along** if
you're reporting an audio problem — it has timestamped RMS/peak dBFS,
every clip event, and every possible dropout for the whole run.

This uses the exact same level-analysis code
(`video_guestbook.media.audio_levels`) as the app's countdown-time check
below, so a reading you see here is what the app would also see.

## Setting the microphone gain precisely (`scripts/mic_gain.py`)

`alsamixer`'s TUI works, but it's easy to fumble or forget whether a change
actually took (and whether it survived a reboot). For a precise, scriptable
alternative:

```bash
python3 scripts/mic_gain.py                 # show current card/control/level, no changes
python3 scripts/mic_gain.py --set 40         # set capture level to exactly 40%
python3 scripts/mic_gain.py --set 40 --persist   # also run 'sudo alsactl store'
```

It reports back the control name it found and the level it actually read
back after setting, so there's no ambiguity about what's configured. Pair
this with `scripts/mic_test.py`: set a level, run the test, check the clip
count, adjust, repeat.

> **Combined Playback/Capture controls:** confirmed on real hardware — this
> device's "Mic" control bundles *two different levels under one name*: a
> Playback/monitoring (sidetone) level (visible in `alsamixer`'s `F3` view)
> and the actual Capture level that recording uses (visible in `F4`), and
> they can read very differently (e.g. Playback at 7% while Capture sits
> at 79%). `mic_gain.py` and the app always read/report the **Capture**
> line specifically — if you're checking by hand in `alsamixer`, make sure
> you're on the `F4` (Capture) view, not `F3` (Playback), or the number
> won't match what's feeding the recording.
>
> Also confirmed: `amixer sset` on this device moves **both** elements
> together (no reliable way around that in alsa-utils). That's harmless
> here since nothing in this app uses mic sidetone/monitoring — only the
> Capture side matters for what actually gets recorded. Use `--raw` on
> `mic_gain.py` if you ever need to double-check both lines yourself.

## Automatic countdown-time mic level check

While the countdown is running, the app samples the microphone in the
background (no extra wiring needed — this is always on) and shows the
same live bar meter + status text under the countdown number. Right
before recording starts, it takes the *average* level seen during the
whole countdown and, if it wasn't in the good range, applies a **one-time**
±15% nudge to the microphone's ALSA capture gain before ffmpeg opens the
device — never continuously during the recording itself (PROJECT_SPEC.md
section 6 explicitly warns against continuous gain changes mid-message,
since it causes pumping/tone artifacts).

This nudge is best-effort: the ALSA mixer control it needs to adjust
(usually named something like `Mic` or `Capture`) is discovered at
runtime via `amixer scontrols` rather than hardcoded, since every USB
audio adapter names it differently. If no such control can be found, or
`amixer` fails for any reason, the nudge is skipped and a warning is
logged — recording is never blocked by this. Check `logs/booth.log` after
a session for lines starting with `countdown mic check:` to see what it
measured and whether/how it adjusted the gain.

`scripts/test_hardware.sh` now also checks that `amixer` is installed and
that a Mic/Capture-like control exists on your configured audio device,
so you'll know ahead of time whether the automatic nudge will be able to
do anything.

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
