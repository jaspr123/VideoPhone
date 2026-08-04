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
