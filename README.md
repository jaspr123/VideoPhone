# Video Guestbook Booth

A Raspberry Pi video guestbook phone application. See [PROJECT_SPEC.md](PROJECT_SPEC.md)
for the full project specification.

## Scope

This repository implements **Milestone 1** in full, per
[PROJECT_SPEC.md section 23](PROJECT_SPEC.md#23-implementation-prompt-for-codex-or-claude-code),
plus hook-switch integration from **Milestone 2**:

- Clean Python package structure (`src/video_guestbook/`)
- Configuration moved into a validated JSON file (`config/booth.default.json`)
- MJPEG live preview via OpenCV, full-screen window
- 1280x720 @ 30 fps H.264 recording (copied straight from the webcam when it
  has onboard H.264, software-encoded otherwise -- see "Camera compatibility" below)
- USB microphone recording via a configurable ALSA device
- AAC audio, MP4 output
- Graceful FFmpeg stop via stdin `q`
- **Physical receiver hook switch** (lift to start, hang up to stop/save) AND
  Spacebar both work at the same time; `q`/`Esc` to quit
- `READY`, `COUNTDOWN`, `RECORDING`, `SAVING`, `SAVED`, `ERROR` states, each
  rendered as a themed, data-driven screen (see "Themed guest UI" below) --
  not just plain text overlays
- Detailed rotating logs (`logs/booth.log`)
- Automated unit tests (state transitions, filenames, config validation,
  recording validation, theme loading, screen rendering)
- A hardware readiness script (`scripts/test_hardware.sh`)

Google Drive sync and the admin UI are **out of scope** for now and are not
implemented (see later milestones in PROJECT_SPEC.md).

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
themes/classic-walnut/      Default theme: theme.json + fonts/ + assets/
src/video_guestbook/        Application package
  config.py                 Config loading/validation
  state_machine.py          Booth state machine
  logging_setup.py          Rotating file + console logging
  main.py                   Guest UI loop (OpenCV preview, hook switch + Spacebar control)
  hardware/
    hook_switch.py            GPIO receiver hook switch (gpiozero), debounced
  media/
    ffmpeg.py                Centralized ffmpeg/ffprobe command construction
    recorder.py               Recording session lifecycle (start/stop)
    validation.py              Post-recording validation via ffprobe
    audio_levels.py            Mic level sampling/classification (dBFS via arecord)
    mixer.py                   ALSA capture-gain discovery + one-shot nudge
    transcode.py                Background raw->H.264 transcode queue ("quality" mode)
    audio_playback.py            Fire-and-forget prompt-sound playback (aplay)
  ui/
    theme.py                  Theme loading/validation (theme.json + font/asset paths)
    renderer.py                Pillow-based screen renderer for all 6 booth states
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
Hook switch (lift)   -> start countdown, then recording (from READY)
Hook switch (hang up) -> stop and save (from RECORDING), or cancel (from COUNTDOWN)
Spacebar              -> same as lifting/hanging up, works at the same time as the hook switch
Q / Escape            -> quit the app
```

The physical hook switch and Spacebar both work simultaneously (architecture
rule 11) -- Spacebar is a permanent backup, not just a development stand-in.
If the hook switch's GPIO can't be claimed at startup (not wired, `gpiozero`
missing, pin already in use), the app logs a warning and falls back to
Spacebar-only rather than crashing; check `logs/booth.log` for `hook switch`
lines to see what happened.

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
| `record_input_format`    | `h264` (copy, no CPU cost) or `mjpeg` (see `recording_mode`). See "Camera compatibility" below. |
| `audio_device`           | Stable ALSA identifier, e.g. `plughw:CARD=Device,DEV=0` |
| `audio_sample_rate`      | 8000/16000/22050/32000/44100/48000                  |
| `audio_channels`         | 1 (mono) or 2 (stereo)                              |
| `audio_bitrate`          | AAC bitrate, e.g. `160k`                            |
| `countdown_seconds`      | Countdown length before recording starts            |
| `max_recording_seconds`  | Recording auto-stop limit                           |
| `output_dir` / `log_dir` | Paths relative to the repository root               |
| `preview_resolution` / `preview_fps` | Live preview quality (independent of recording quality) |
| `av_sync_offset_ms`      | Manual A/V sync correction, in milliseconds. `0` = no correction (default). See "Fixing audio/video sync" below. |
| `hook_switch_enabled`    | `true` (default) to use the physical receiver switch; `false` for Spacebar-only |
| `hook_switch_gpio_pin`   | BCM GPIO pin number for the primary hook-switch signal (default `17`, per PROJECT_SPEC.md section 4) |
| `recording_mode`         | `quality` (default, deferred background transcode, no audio-dropout risk) or `fast` (live transcode, immediately final). Only matters when `record_input_format` is `mjpeg`. See "Recording modes" below. |
| `theme_dir`              | Path to a theme directory (default `themes/classic-walnut`), relative to the repository root. See "Themed guest UI" below. |
| `audio_playback_device`  | ALSA *playback* device for prompt sounds (e.g. the pickup greeting), e.g. `plughw:CARD=Device,DEV=0`. `null`/omitted uses the system default output. Separate from `audio_device` (the microphone). |

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
  per PROJECT_SPEC.md section 16). `recording_mode` (below) has no effect
  in this case -- copy is already both the fastest and highest-quality option.
- **If only `MJPG`/`YUYV` are listed** (no `H264`): set
  `record_input_format: "mjpeg"`. How the camera's raw MJPEG becomes a
  final H.264 file then depends on `recording_mode` -- see the next section.

Setting `record_input_format` to a value the camera doesn't actually
support (e.g. `h264` on a camera with no H.264 output) will fail to start
recording — this is the single most common cause of "the app won't
record" after swapping cameras.

## Recording modes: `fast` vs `quality`

Only relevant when `record_input_format: "mjpeg"` (a camera with no onboard
H.264 -- see above). Controlled by `recording_mode` in config:

- **`quality` (default).** The live recording just copies the camera's raw
  MJPEG (`-c:v copy`, near-zero CPU cost, the same resource profile a
  real-H.264 camera has) alongside the normal AAC audio capture. Nothing
  CPU-heavy runs while the guest is actually recording, so there's no
  audio-dropout risk from encoder contention. Once the guest hangs up, a
  background job (`media/transcode.py`) converts that raw capture into the
  final playable H.264 MP4 -- this can take a real amount of wall-clock
  time (it's re-encoding video), but since nothing is "live" anymore by
  then, however long it takes can never cause an audio glitch. The raw
  file (`<session-id>.raw.mkv`) is kept alongside the final file
  (`<session-id>.mp4`) and is **never deleted automatically** -- it's the
  backup copy, and the only copy left if a transcode ever fails.
- **`fast`.** Transcodes live instead (real CPU cost while recording,
  competes with audio capture -- this is what caused patchy/breaking-up
  audio on this project's MJPEG-only camera). The file is immediately
  final the moment the guest hangs up, with no background processing step
  or queue. Worth choosing over `quality` only if a busy event risks
  guests recording faster than the background transcode queue can keep up
  (each queued job processes one at a time, oldest first).

Check `logs/booth.log` for lines starting with `queued transcode for
session`, `transcoding session`, and `transcode complete for session` (or
`transcode failed for session`, which always names the preserved raw file)
to watch the background queue's progress.

## Themed guest UI

All 6 booth screens (`READY`, `COUNTDOWN`, `RECORDING`, `SAVING`, `SAVED`,
`ERROR`) are rendered by `src/video_guestbook/ui/renderer.py` from a
**theme** — a directory of `theme.json` plus font and image assets, pointed
to by `theme_dir` in config (default: `themes/classic-walnut`). Nothing
visual is hard-coded into the renderer: colors, fonts, copy, and border art
all come from the active theme, so a new event just needs a new theme
directory, not a code change.

This recreates the visual design of a "Guestbook Kiosk" mockup built in
Claude Design (an HTML/CSS/JS prototype) inside the existing OpenCV/ffmpeg
pipeline via Pillow, rather than adopting a browser runtime for the guest UI.
A browser-kiosk implementation (real CSS animations, exact pixel match) was
considered and explicitly deferred: it would need the guest-facing browser
to either share the camera with ffmpeg (conflicts with the one-process-at-
a-time V4L2 constraint this project already had to work around once) or add
a second live video/audio pipeline, and would run a full browser process
alongside ffmpeg on hardware where CPU contention has already caused a real
audio-dropout bug this session. The Pillow renderer reuses the proven
camera/ffmpeg handoff untouched and adds no new real-time processes.

### Making or editing a theme

A theme directory looks like:

```text
themes/classic-walnut/
  theme.json          Colors, fonts, copy, couple names/date, toggles
  fonts/               .ttf files referenced by theme.json (bundled locally
                        so the kiosk never depends on an internet connection
                        or Google Fonts CDN)
  assets/
    frame-floral.png    Decorative border art, drawn behind all screens
    couple-photo.jpg     Optional portrait shown on the READY screen
```

`theme.json` fields:

- `name` — human-readable theme name (logged at startup).
- `couple_names`, `event_date` — shown on most screens.
- `show_couple_photo` — if `true`, `assets.couple_photo` is required.
- `colors` — 10 required `#RRGGBB` keys: `bg_cream`, `ink`, `gold`,
  `gold_dark`, `dark_bar`, `recording_red`, and the 4-color mic meter
  gradient (`meter_green`, `meter_gold`, `meter_amber`, `meter_red`).
- `fonts` — 4 required keys (`script`, `heading`, `heading_medium`, `body`),
  each a path to a `.ttf` file relative to the theme directory.
- `assets` — `frame` (required) and `couple_photo` (required only when
  `show_couple_photo` is `true`), paths relative to the theme directory.
- `text` — the exact copy shown on every screen (prompts, footer bar text,
  tip labels, etc.) — see `themes/classic-walnut/theme.json` for the full
  list of required keys.
- `sounds` — **optional**. `{"pickup": "sounds/pickup.wav"}` plays that clip
  when the receiver is lifted (or Spacebar pressed) and the countdown
  begins. Omit the whole section, or just the `pickup` key, for no sound.
  See "Pickup greeting sound" below.
- `icons` — **optional**. Override any of the `COUNTDOWN` screen's three tip
  icons (`camera`, `mic`, `smile`) with your own transparent PNG instead of
  the built-in vector drawing, e.g.
  `{"icons": {"camera": "assets/icon-camera.png"}}`. Any icon key you don't
  provide keeps the built-in icon. See "Custom tip icons" below.

To make a new theme: copy `themes/classic-walnut/`, edit `theme.json` and
swap the assets, then point `theme_dir` at it in `config/booth.local.json`.
`Theme.load()` validates the whole file up front (missing keys, bad color
formats, missing font/asset files) and raises a clear error naming exactly
what's wrong, before the app opens its window.

### Pickup greeting sound

To play a short spoken prompt ("please leave us a message...") when a guest
lifts the receiver:

1. Drop a `.wav` file in your theme directory, e.g.
   `themes/classic-walnut/sounds/pickup.wav`.
2. Add it to `theme.json`: `"sounds": {"pickup": "sounds/pickup.wav"}`.
3. If your handset's speaker isn't the system's default ALSA playback
   device, set `audio_playback_device` in config (e.g.
   `"plughw:CARD=Device,DEV=0"`) — this is a separate field from
   `audio_device`, which is the *microphone* (capture); leave it unset to
   use the system default output.

Playback is fire-and-forget via `aplay` (`media/audio_playback.py`) — it
starts in the background right when the countdown begins and never blocks
or delays the countdown, recording, or anything else. A missing sound file,
missing `aplay` binary, or bad playback device is logged as a warning
(`logs/booth.log`) and otherwise ignored, never crashes the app.

### Custom tip icons

The `COUNTDOWN` screen's three tips (look at camera / speak clearly / relax
& smile) draw simple built-in vector icons by default. To use your own
artwork instead, add transparent PNGs to your theme's `assets/` folder and
reference them under `icons` in `theme.json`:

```json
"icons": {
  "camera": "assets/icon-camera.png",
  "mic": "assets/icon-mic.png",
  "smile": "assets/icon-smile.png"
}
```

Any subset works — an icon key you omit keeps the built-in drawing for that
tip. Icons are scaled down to fit their slot while preserving aspect ratio
(no cropping), so any reasonable square-ish PNG works.

### Known limitations: RECORDING screen's live preview and mic meter

Only the `RECORDING` screen shows the camera (in a bordered panel, mirrored,
matching the design) — every other screen is fully synthetic branded art.
The `RECORDING` screen's live preview **freezes at the last frame** captured
before ffmpeg takes over the camera (see the camera/ffmpeg handoff note
above) — this is the same pre-existing hardware constraint as the legacy
prototype, not a regression, and it's expected to look static/non-live.
Likewise, the `RECORDING` screen's mic meter is designed to **freeze at the
last reading** from the countdown-time mic check rather than update live,
since ffmpeg also holds the audio device exclusively while recording.

If the mic meter looks completely empty/blank the *entire* time (not just
static-but-lit), that's not this limitation — it means the countdown-time
mic check itself never got a reading. Check `logs/booth.log` for lines
starting with `countdown mic check` around the time you lifted the
receiver; a `could not start` or `stopped early` warning there points at a
real capture problem (wrong `audio_device`, `arecord` missing, device busy)
worth chasing separately from the frozen-vs-live design limitation.

A true live feed and live meter during actual recording would need ffmpeg
to fan out a second low-res preview stream (video) and a way to tap the
audio device without taking it from ffmpeg (audio) while it records — a
real but separate follow-up (see CHANGELOG.md), not attempted here since it
changes the same camera/audio exclusivity architecture that caused a real
CPU-contention audio bug earlier this project, untested on real hardware.

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

## Hook switch (physical receiver)

Wiring, per PROJECT_SPEC.md section 4 (never wire the switch to 5V or 3.3V):

```text
Black wire -> physical pin 9  -> GND
Red wire   -> physical pin 11 -> GPIO17
Green wire -> physical pin 13 -> GPIO27
```

Lifting the receiver starts the countdown (from `READY`); hanging up stops
and saves (from `RECORDING`), or cancels back to `READY` if the receiver
goes back on the hook before the countdown finishes. Spacebar keeps working
identically at the same time -- it's a permanent backup, not just a dev
tool.

- **GPIO17** is the primary signal (stable LOW = lifted, stable HIGH =
  on-hook, using the GPIO's internal pull-up). Debouncing (~150ms) is
  handled by `gpiozero`'s `Button` class, so brief mechanical bounce during
  the switch's travel is filtered out automatically.
- **GPIO27** was observed to stay low regardless of hook state during
  initial testing and is read only for diagnostics (available via
  `HookSwitch.diagnostic_is_active` if you're debugging wiring), never used
  for start/stop logic.
- If the GPIO can't be claimed at startup (not wired, `gpiozero`/`lgpio`
  not installed, pin already in use by something else), the app logs a
  warning and runs Spacebar-only rather than crashing. Check
  `logs/booth.log` for `hook switch` lines.
- `hook_switch_enabled: false` in config disables it entirely (Spacebar
  only), and `hook_switch_gpio_pin` lets you use a different BCM pin if
  your wiring differs.

`scripts/test_hardware.sh` checks that `gpiozero` is importable and that
the configured GPIO pin can be claimed, but that only proves the software
side is ready -- it does **not** simulate lifting the receiver. Confirm the
real thing works by running the app and physically lifting/hanging up.

## Logs

Rotating logs are written to `logs/booth.log` (5 MB per file, 5 backups).
Each recording session logs a unique session ID alongside every message.

## Recordings

Final MP4s are written to `recordings/<timestamp>_<session-id>.mp4`. A
recording is only considered complete after ffmpeg exits and `ffprobe`
confirms the file has both a video and an audio stream and meets the
minimum duration.

In `quality` recording_mode with an MJPEG-only camera, you'll also see
`recordings/<timestamp>_<session-id>.raw.mkv` files -- the raw capture
that gets background-transcoded into the matching `.mp4`. These are kept
permanently (never auto-deleted) as the backup copy; if you ever see a
`.raw.mkv` with no matching `.mp4`, check `logs/booth.log` for a
`transcode failed` line to see why.

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
- Hook switch debouncing/edge detection, mic level classification/mixer gain
  math, recording_mode branching, and the background transcode queue, all
  via monkeypatched subprocess/GPIO calls
- Theme loading/validation (`tests/unit/test_theme.py`) and screen rendering
  (`tests/unit/test_renderer.py`) against the real bundled
  `themes/classic-walnut` theme -- every screen is rendered and checked for
  correct output shape/dtype with no real display or camera required

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
