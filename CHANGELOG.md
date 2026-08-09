# Changelog

## Unreleased — Milestone 1: Consolidate current Beta

- Initial repository structure created per PROJECT_SPEC.md section 14.
- Added `video_guestbook` Python package:
  - `config.py`: validated JSON booth configuration loading.
  - `state_machine.py`: READY/COUNTDOWN/RECORDING/SAVING/SAVED/ERROR state
    machine with guest-input gating during SAVING.
  - `logging_setup.py`: rotating file + console logging with session IDs.
  - `media/ffmpeg.py`: centralized ffmpeg/ffprobe command construction.
  - `media/recorder.py`: recording session start/stop (graceful `q` stdin
    stop), unique session ID and filename generation.
  - `media/validation.py`: post-recording validation via ffprobe (video +
    audio streams present, minimum duration, non-empty file).
  - `main.py`: full-screen OpenCV MJPEG preview, Spacebar start/stop,
    countdown, and state-driven UI overlay.
- Added `config/booth.default.json` using the stable ALSA device
  `plughw:CARD=Device,DEV=0`.
- Added `scripts/test_hardware.sh` (camera, microphone, ffmpeg/ffprobe,
  output directory checks) and `scripts/install.sh`.
- Added unit tests for state transitions, filename creation, configuration
  validation, and recording validation logic (`tests/unit/`, 60 tests).
- Added README with install/run instructions and manual acceptance tests.

## Unreleased — Fold in the confirmed-working prototype

- Added `legacy/booth.py`: a verbatim backup of the actual working Beta
  prototype, confirmed running on the Raspberry Pi hardware.
- Fixed a camera/ffmpeg resource conflict the fresh Milestone 1 build had
  missed: the USB webcam can only be held open by one process at a time.
  `main.py` now releases the OpenCV preview capture before ffmpeg opens the
  camera to record, and reopens it (with a 10-attempt retry loop) once
  ffmpeg exits, matching `legacy/booth.py`.
- Updated `media/ffmpeg.py`'s recording command to match the proven
  audio-sync flags from the prototype: `-thread_queue_size`,
  `-use_wallclock_as_timestamps` on both inputs, `-af
  aresample=async=1:first_pts=0`, and `-avoid_negative_ts make_zero`. Added
  `tests/unit/test_ffmpeg.py` as a regression guard for these flags.
- Updated `media/recorder.py`'s stop sequence to write `q\n` (matching the
  prototype) and fall back to `SIGINT` if the stdin write fails.
- Fixed a window-flag bug in `main.py` (`cv2.namedWindow` was passed
  `cv2.WND_PROP_FULLSCREEN`, a property enum, instead of a window creation
  flag); now uses `cv2.WINDOW_NORMAL` + `cv2.setWindowProperty(...,
  WND_PROP_FULLSCREEN, ...)` as in the prototype.
- Added `cv2.CAP_V4L2` backend hint and `CAP_PROP_BUFFERSIZE=1` to the
  preview capture for lower latency, matching the prototype.

## Unreleased — Fix hardware script microphone check

- Fixed `scripts/test_hardware.sh`: the microphone check grepped for
  `[CARDNAME` in `arecord -l` output, but the short card ID appears right
  after `card N: `, not inside the brackets (that's the longer description).
  The check always failed even when the microphone was present and working.
  Now also supports matching a raw `plughw:N,M` / `hw:N,M` device by card
  index, in addition to the `CARD=NAME` form.

## Unreleased — Configurable A/V sync offset

- Added `av_sync_offset_ms` to booth configuration (default `0`, range
  -5000 to 5000) to correct a constant, device-specific audio/video sync
  offset (PROJECT_SPEC.md section 21, known risk #7) without re-encoding
  video. Positive values delay video via ffmpeg's `-itsoffset`; negative
  values delay audio.
- Documented a calibration procedure (clap test) in the README, and how to
  tell a constant offset apart from progressive drift, which this setting
  does not fix.
- Added unit tests for the new config field and for where `-itsoffset` is
  inserted in the ffmpeg command for positive/negative/zero offsets.

## Unreleased — Software H.264 encode fallback for cameras with no onboard encoder

- Root cause found for "the app won't record" after swapping to a new
  camera (Angetube 4K webcam): `v4l2-ctl --list-formats-ext` confirmed it
  has no H.264 output at all, only MJPG/YUYV. `record_input_format:
  "h264"` with `-c:v copy` fails outright with no H.264 source stream to
  copy.
- `media/ffmpeg.py`'s `build_record_command` now branches on
  `record_input_format`: `"h264"` still copies the camera's stream with no
  re-encode (zero CPU cost, unchanged from before); `"mjpeg"` now
  software-encodes to H.264 via `libx264` (`ultrafast` preset, ~4 Mbps,
  `yuv420p`, constant frame rate) instead of silently producing an
  MJPEG-in-MP4 file. This has not been verified to hold 1280x720@30fps in
  real time on a Pi 4 -- needs on-device testing.
- Changed `config/booth.default.json`'s `record_input_format` to `"mjpeg"`
  to match the currently connected camera.
- Documented how to check camera capability and choose the right
  `record_input_format` in the README ("Camera compatibility" section).
- Added unit tests for both the `copy` and `libx264` encode paths.

## Unreleased — Microphone level tooling and countdown-time gain check

- Added `media/audio_levels.py`: reads raw PCM directly from `arecord` (no
  numpy/sounddevice dependency) and computes RMS/peak dBFS per chunk,
  classified into `MICROPHONE READY` / `SPEAK CLOSER` / `TOO LOUD - MOVE
  SLIGHTLY AWAY` using the targets from PROJECT_SPEC.md section 6.
- Added `media/mixer.py`: best-effort ALSA capture-gain discovery (the
  Mic/Capture control name is resolved at runtime via `amixer scontrols`
  rather than hardcoded, since it differs per USB audio adapter) and a
  one-shot relative gain nudge. Never blocks recording on failure.
- Added `scripts/mic_test.py`: standalone terminal level meter + timestamped
  log file, no camera/video required. Flags clipping and possible dropouts
  in real time. Uses the same level-analysis code as the app, so readings
  match what the app itself sees. Written to help diagnose reported
  feedback/cutting-out issues on the new microphone -- send the generated
  log file for review.
- Wired a countdown-time mic check into `main.py`: samples level in a
  background thread while the countdown runs, shows a live meter + status
  under the countdown number (spec section 8), and applies a single
  ±15% capture-gain nudge right before recording starts based on the
  average level seen -- never continuously during the recording itself
  (spec section 6 explicitly warns against that). The mic-check `arecord`
  process is fully stopped before ffmpeg opens the same ALSA device, same
  handoff pattern as the camera.
- Updated `scripts/test_hardware.sh` to also check `amixer` is installed
  and that a Mic/Capture-like control exists on the configured device.
- Added `tests/unit/test_audio_levels.py` and `tests/unit/test_mixer.py`
  (dBFS math, classification thresholds, mocked `arecord`/`amixer`
  subprocess behavior) -- 31 new tests, none require real hardware.

## Unreleased — Precise mic gain CLI

- Added `scripts/mic_gain.py`: shows/sets the microphone's exact ALSA
  capture percentage via `media/mixer.py`, with an optional `--persist`
  to run `alsactl store`. Written during live gain-tuning on real
  hardware where `alsamixer`'s TUI made it hard to confirm whether a
  change actually took effect between test runs.

## Unreleased — Fix: mixer.py was reading/setting the wrong volume element

- Real-hardware finding: this device's "Mic" ALSA control bundles a
  Playback/monitoring level (shown in `alsamixer`'s `F3` view) and the
  actual Capture level recording uses (shown in `F4`) under one name.
  `amixer sget` prints the Playback line before the Capture line, and
  `media/mixer.py` was matching the *first* `[NN%]` found anywhere in
  that output -- silently reading (and likely also setting, via
  `set_capture_percent`) the wrong element. A screenshot showing Capture
  at `7%` while `mic_test.py` still showed frequent 0 dBFS clipping is
  what surfaced this.
- Fixed `get_capture_percent` (and the new `get_raw_control_info`) to
  anchor specifically to the line containing "Capture" rather than the
  first percentage in the output. Documented in the module docstring so
  it isn't silently reintroduced.
- `set_capture_percent`'s command itself is unchanged (still `amixer sset
  NAME VALUE%` with no direction qualifier) because alsa-utils has no
  reliably version-independent "capture only" flag for `sset` -- this is
  flagged as unverified in its docstring. `mic_gain.py --set` now prints
  the full `amixer sget` output before and after so a human can visually
  confirm the Playback line didn't also move.
- Added regression tests in `test_mixer.py` using a combined
  Playback+Capture fixture with deliberately different percentages, plus
  tests for `get_raw_control_info` and the "no Capture element" case.

## Unreleased — Milestone 2: physical hook-switch integration

- Added `hardware/hook_switch.py`: GPIO17 primary receiver-state signal
  (internal pull-up, active-low) via `gpiozero.Button`, with hardware
  debouncing (~150ms) so brief mechanical bounce during the switch's
  travel is filtered out. GPIO27 is read only for diagnostics
  (`diagnostic_is_active`), matching PROJECT_SPEC.md section 4's caution
  that it "should be treated as diagnostic until the switch is re-tested."
  `gpiozero` is imported lazily and defensively so the module (and the
  rest of the app) still loads/tests fine without it installed.
- Wired into `main.py`: polled once per tick, edge-triggered (lift ->
  start countdown from READY; hang up -> stop and save from RECORDING;
  hang up during COUNTDOWN -> cancel back to READY, per the spec's
  section 20 venue-simulation test case "receiver lifted and immediately
  replaced"). Spacebar keeps working identically and simultaneously
  (architecture rule 11) -- this is not a replacement, both inputs drive
  the same state machine.
- If the GPIO pin can't be claimed at startup (not wired, `gpiozero`/
  backend not installed, pin in use), `open_hook_switch()` catches the
  failure, logs a warning, and the app runs Spacebar-only rather than
  crashing (architecture rule 17).
- Added `hook_switch_enabled` (default `true`) and `hook_switch_gpio_pin`
  (default `17`) to booth configuration, both optional so existing
  configs keep working unchanged.
- Added `gpiozero` and `lgpio` to `requirements.txt`; `install.sh` now
  notes the `python3-lgpio` apt fallback if the pip wheel doesn't build,
  and the `gpio` group requirement.
- `scripts/test_hardware.sh` now checks `gpiozero` is importable and that
  the configured GPIO pin can be claimed (does not simulate an actual
  lift/hang-up -- that still needs a real test on the Pi).
- Added `tests/unit/test_hook_switch.py` (10 tests, using a fake
  `gpiozero.Button` monkeypatched at the module level so these run
  without `gpiozero` installed or real GPIO hardware) and config
  validation tests for the two new fields.

## Unreleased — `recording_mode` setting: eliminate live-encode audio contention

Root cause of the patchy/breaking-up audio on the MJPEG-only camera:
software H.264 encoding during a live recording competes with real-time
audio capture for the Pi's CPU. This adds a real fix (removes the
contention entirely) alongside the existing config-tuning workaround
(lower fps/resolution), as a selectable mode rather than a single
hardcoded behavior.

- Added `recording_mode` config field (`"fast"` | `"quality"`, default
  `"quality"`), only meaningful when `record_input_format` is `"mjpeg"`:
  - `quality`: live capture copies raw MJPEG (`-c:v copy`, near-zero CPU,
    same resource profile a real-H.264 camera has) instead of transcoding
    live. A background job converts it to the final H.264 MP4 after the
    guest hangs up, when there's no real-time deadline to violate.
  - `fast`: today's live-transcode behavior, unchanged. Immediately
    final, but competes with audio capture for CPU. Only worth it if a
    busy event risks the background transcode queue backing up.
- `media/ffmpeg.py`: added `is_live_video_copied()` and
  `needs_deferred_transcode()` as the single source of truth for which
  path applies (shared by the command builder, `recorder.py`, and
  `main.py`), and `build_transcode_command()` for the deferred
  raw-to-final pass (video re-encoded, audio just copied through since
  it was already properly encoded during the live pass).
- `media/recorder.py`: `RecordingSession` now carries `live_output_path`
  (what ffmpeg actually just wrote -- raw `.mkv` or final `.mp4`),
  `final_output_path`, and `needs_transcode`; raw files use a
  `<session-id>.raw.mkv` naming convention (MJPEG doesn't map cleanly
  into MP4). `Recorder.stop()` now returns the full session instead of
  just a path, so callers have everything needed to decide what happens
  next.
- Added `media/transcode.py`: a single-worker background FIFO queue.
  Raw files are never deleted, success or failure, so a message is never
  silently lost -- a failed transcode just leaves the guest's raw
  recording as the only copy, logged clearly with the preserved path.
- `main.py`: starts/stops the transcode queue with the app; after a
  `quality`-mode recording validates successfully, enqueues the
  background job and returns to `SAVED` immediately rather than making
  the guest wait for the transcode.
- Added `tests/unit/test_recorder.py` (raw-vs-final path selection per
  mode, start/stop lifecycle) and `tests/unit/test_transcode.py` (queue
  ordering, success/failure handling, all mocked -- no real ffmpeg or
  threading races), plus `recording_mode` config validation tests and
  ffmpeg command tests for both modes.
