"""Guest-facing booth application entry point.

Milestone 1 scope only: full-screen MJPEG preview, Spacebar start/stop,
countdown, recording via ffmpeg, save validation, and return to READY.
Hook-switch, themes, admin UI and cloud sync are intentionally not
implemented yet (see PROJECT_SPEC.md section 23).

Camera/ffmpeg handoff: the USB webcam can only be held open by one process
at a time. This mirrors the confirmed working prototype (legacy/booth.py):
the OpenCV preview capture is released just before ffmpeg opens the camera
device for recording, and reopened (with retries) once ffmpeg exits. Do not
read frames from self.capture while a recording is in progress.
"""

from __future__ import annotations

import argparse
import logging
import math
import time
from pathlib import Path

import cv2
import numpy as np

from video_guestbook.config import BoothConfig, ConfigError
from video_guestbook.logging_setup import get_session_adapter, setup_logging
from video_guestbook.media.recorder import Recorder, RecorderError
from video_guestbook.media.validation import validate_recording
from video_guestbook.state_machine import BoothState, StateMachine

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "booth.default.json"

WINDOW_NAME = "Video Guestbook"
KEY_SPACE = 32
KEY_ESCAPE = 27
KEY_Q = ord("q")

SAVED_DISPLAY_SECONDS = 3.0
ERROR_DISPLAY_SECONDS = 4.0

# Camera release/reopen tuning, matched to the confirmed working prototype.
CAMERA_RELEASE_SETTLE_SECONDS = 0.25
CAMERA_REOPEN_ATTEMPTS = 10
CAMERA_REOPEN_RETRY_DELAY_SECONDS = 0.3

# States during which ffmpeg owns the camera device; the preview must not
# touch it and instead shows the last frame captured before recording began.
_CAMERA_RELEASED_STATES = frozenset({BoothState.RECORDING, BoothState.SAVING})

_TEXT_COLOR = (255, 255, 255)
_RECORDING_COLOR = (0, 0, 255)
_ERROR_COLOR = (0, 0, 255)


def _configure_capture(config: BoothConfig) -> cv2.VideoCapture:
    capture = cv2.VideoCapture(config.camera_device, cv2.CAP_V4L2)
    capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    width, height = config.preview_width_height
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    capture.set(cv2.CAP_PROP_FPS, config.preview_fps)
    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return capture


def _blank_frame(config: BoothConfig):
    width, height = config.preview_width_height
    return np.zeros((height, width, 3), dtype=np.uint8)


def _draw_overlay(frame, text_lines: list[str], color=_TEXT_COLOR) -> None:
    y = 40
    for line in text_lines:
        cv2.putText(
            frame, line, (20, y), cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2, cv2.LINE_AA
        )
        y += 40


class BoothApp:
    def __init__(self, config: BoothConfig, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger
        self.state_machine = StateMachine(logger=logger)
        self.recorder = Recorder(config, logger=logger)
        self.capture: cv2.VideoCapture | None = None
        self._last_frame = None
        self._countdown_deadline: float | None = None
        self._state_entered_at: float = time.monotonic()
        self._last_result_reason: str = ""
        self._session_log = get_session_adapter(logger, "-")

    def open_camera(self) -> None:
        self.capture = _configure_capture(self.config)
        if not self.capture.isOpened():
            self.capture = None
            raise RuntimeError(f"Could not open camera device {self.config.camera_device}")

    def close_camera(self) -> None:
        if self.capture is not None:
            self.capture.release()
            self.capture = None

    def _reopen_camera_with_retry(self) -> bool:
        """Release and reacquire the camera, retrying while ffmpeg lets it go.

        The v4l2 device can take a moment to become available again after
        ffmpeg exits, so this mirrors the proven prototype's retry loop
        rather than failing on the first attempt.
        """
        self.close_camera()
        for attempt in range(CAMERA_REOPEN_ATTEMPTS):
            try:
                self.open_camera()
                return True
            except RuntimeError as exc:
                self.logger.debug(
                    "camera reopen attempt %d/%d failed: %s",
                    attempt + 1,
                    CAMERA_REOPEN_ATTEMPTS,
                    exc,
                )
                time.sleep(CAMERA_REOPEN_RETRY_DELAY_SECONDS)
        self.logger.error("could not reopen camera after %d attempts", CAMERA_REOPEN_ATTEMPTS)
        return False

    def _enter_state(self, new_state: BoothState) -> None:
        self.state_machine.transition(new_state)
        self._state_entered_at = time.monotonic()

    def _start_countdown(self) -> None:
        self._enter_state(BoothState.COUNTDOWN)
        self._countdown_deadline = time.monotonic() + self.config.countdown_seconds

    def _start_recording(self) -> None:
        # ffmpeg needs exclusive access to the camera device: release the
        # preview capture first and give the driver a moment to settle.
        self.close_camera()
        time.sleep(CAMERA_RELEASE_SETTLE_SECONDS)

        try:
            session = self.recorder.start()
        except RecorderError as exc:
            self.logger.error("failed to start recording: %s", exc)
            self._last_result_reason = str(exc)
            self._reopen_camera_with_retry()
            self._enter_state(BoothState.ERROR)
            return

        self._session_log = get_session_adapter(self.logger, session.session_id)
        self._session_log.info("recording started -> %s", session.output_path)
        self._enter_state(BoothState.RECORDING)

    def _stop_and_save(self) -> None:
        self._enter_state(BoothState.SAVING)
        try:
            output_path = self.recorder.stop()
        except RecorderError as exc:
            self.logger.error("failed to stop recording: %s", exc)
            self._last_result_reason = str(exc)
            self._reopen_camera_with_retry()
            self._enter_state(BoothState.ERROR)
            return

        result = validate_recording(output_path)
        # ffmpeg has exited either way; the camera is free again.
        self._reopen_camera_with_retry()

        if result.ok:
            self._session_log.info(
                "recording saved: duration=%.2fs size=%d bytes",
                result.duration_seconds,
                result.size_bytes,
            )
            self._last_result_reason = "Message saved"
            self._enter_state(BoothState.SAVED)
        else:
            self._session_log.error("recording failed validation: %s", result.reason)
            self._last_result_reason = result.reason
            self._enter_state(BoothState.ERROR)

    def handle_key(self, key: int) -> bool:
        """Returns False if the app should quit."""
        if key in (KEY_Q, KEY_ESCAPE):
            return False

        if key != KEY_SPACE:
            return True

        if not self.state_machine.accepts_guest_input:
            self.logger.debug("ignoring spacebar during %s", self.state_machine.state.value)
            return True

        if self.state_machine.state == BoothState.READY:
            self._start_countdown()
        elif self.state_machine.state == BoothState.RECORDING:
            self._stop_and_save()

        return True

    def tick(self) -> None:
        """Advance time-based transitions (countdown expiry, auto-stop, timeouts)."""
        state = self.state_machine.state

        if state == BoothState.COUNTDOWN and self._countdown_deadline is not None:
            if time.monotonic() >= self._countdown_deadline:
                self._start_recording()

        elif state == BoothState.RECORDING:
            if not self.recorder.is_running:
                self._stop_and_save()

        elif state == BoothState.SAVED:
            if time.monotonic() - self._state_entered_at >= SAVED_DISPLAY_SECONDS:
                self._enter_state(BoothState.READY)

        elif state == BoothState.ERROR:
            if time.monotonic() - self._state_entered_at >= ERROR_DISPLAY_SECONDS:
                self._enter_state(BoothState.READY)

    def render(self, frame) -> None:
        state = self.state_machine.state
        if state == BoothState.READY:
            _draw_overlay(frame, ["READY", "Press SPACE to record"])
        elif state == BoothState.COUNTDOWN and self._countdown_deadline is not None:
            remaining = max(0, math.ceil(self._countdown_deadline - time.monotonic()))
            _draw_overlay(frame, [str(remaining) if remaining > 0 else "GO"])
        elif state == BoothState.RECORDING:
            elapsed = time.monotonic() - self._state_entered_at
            remaining = max(0, self.config.max_recording_seconds - int(elapsed))
            _draw_overlay(
                frame,
                [f"REC  {int(elapsed)}s", f"Press SPACE to stop ({remaining}s left)"],
                color=_RECORDING_COLOR,
            )
        elif state == BoothState.SAVING:
            _draw_overlay(frame, ["SAVING..."])
        elif state == BoothState.SAVED:
            _draw_overlay(frame, ["Message saved", "Thank you!"])
        elif state == BoothState.ERROR:
            _draw_overlay(frame, ["Something went wrong", self._last_result_reason], color=_ERROR_COLOR)

    def _read_frame(self):
        """Return the frame to display, honoring the camera/ffmpeg handoff."""
        if self.state_machine.state in _CAMERA_RELEASED_STATES:
            # ffmpeg owns the camera right now; freeze on the last preview frame.
            return self._last_frame

        if self.capture is not None:
            ok, frame = self.capture.read()
            if ok:
                self._last_frame = frame
            else:
                self.logger.error("camera read failed")
                if not self._reopen_camera_with_retry():
                    if self.state_machine.state != BoothState.ERROR:
                        self._last_result_reason = "camera read failed"
                        self._enter_state(BoothState.ERROR)

        return self._last_frame

    def run(self) -> None:
        self.open_camera()
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
        cv2.setWindowProperty(WINDOW_NAME, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
        try:
            running = True
            while running:
                frame = self._read_frame()
                display = (frame if frame is not None else _blank_frame(self.config)).copy()

                self.tick()
                self.render(display)
                cv2.imshow(WINDOW_NAME, display)

                key = cv2.waitKey(1) & 0xFF
                if key != 0xFF:
                    running = self.handle_key(key)
        finally:
            if self.recorder.is_running:
                try:
                    self.recorder.stop()
                except RecorderError:
                    self.logger.exception("failed to stop recorder during shutdown")
            self.close_camera()
            cv2.destroyAllWindows()


def load_config(config_path: Path) -> BoothConfig:
    return BoothConfig.from_file(config_path, base_dir=PROJECT_ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description="Video guestbook booth application")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to booth configuration JSON file",
    )
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 1

    logger = setup_logging(config.log_dir)
    logger.info("booth starting with config %s", args.config)

    app = BoothApp(config, logger)
    try:
        app.run()
    except Exception:
        logger.exception("unhandled error, exiting")
        return 1
    finally:
        logger.info("booth stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
