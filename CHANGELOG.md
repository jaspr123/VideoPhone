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
