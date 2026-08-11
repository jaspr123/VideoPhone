"""Guest-facing booth application entry point.

Full-screen MJPEG preview, hook-switch AND Spacebar start/stop (both work
at once per architecture rule 11), countdown, recording via ffmpeg, save
validation, and return to READY. Themes, admin UI and cloud sync are
intentionally not implemented yet (see PROJECT_SPEC.md section 23).

Camera/ffmpeg handoff: the USB webcam can only be held open by one process
at a time. This mirrors the confirmed working prototype (legacy/booth.py):
the OpenCV preview capture is released just before ffmpeg opens the camera
device for recording, and reopened (with retries) once ffmpeg exits. Do not
read frames from self.capture while a recording is in progress.
"""

from __future__ import annotations

import argparse
import logging
import threading
import time
from pathlib import Path

import cv2

from video_guestbook.config import BoothConfig, ConfigError
from video_guestbook.hardware.hook_switch import HookSwitch, HookSwitchError
from video_guestbook.logging_setup import get_session_adapter, setup_logging
from video_guestbook.media.audio_levels import (
    STATUS_READY,
    STATUS_SPEAK_CLOSER,
    AudioLevelReader,
    LevelReading,
    MicrophoneError,
    classify_level,
    read_latest_live_level,
)
from video_guestbook.media.audio_playback import play_sound_async
from video_guestbook.media.mixer import MixerError, nudge_capture_gain
from video_guestbook.media.recorder import Recorder, RecorderError
from video_guestbook.media.transcode import TranscodeJob, TranscodeQueue
from video_guestbook.media.validation import validate_recording
from video_guestbook.state_machine import BoothState, StateMachine
from video_guestbook.ui.renderer import Renderer
from video_guestbook.ui.theme import Theme, ThemeError

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

# Countdown-time microphone check (PROJECT_SPEC.md section 6, "Proposed
# countdown audio check"): sample level while the guest is getting ready,
# then apply a single, one-shot capture-gain nudge before recording starts.
# Per the spec, gain is NOT adjusted continuously once recording begins.
MIC_CHECK_CHUNK_MS = 100
MIC_GAIN_NUDGE_PERCENT = 15

# States during which ffmpeg owns the camera device; the preview must not
# touch it and instead shows the last frame captured before recording began.
_CAMERA_RELEASED_STATES = frozenset({BoothState.RECORDING, BoothState.SAVING})


def _configure_capture(config: BoothConfig) -> cv2.VideoCapture:
    capture = cv2.VideoCapture(config.camera_device, cv2.CAP_V4L2)
    capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    width, height = config.preview_width_height
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    capture.set(cv2.CAP_PROP_FPS, config.preview_fps)
    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return capture


_METER_FLOOR_DBFS = -60.0
_METER_CEILING_DBFS = 0.0


def _mic_fraction(reading: LevelReading | None) -> float:
    """Normalize a mic level reading's peak dBFS to a 0..1 meter fraction."""
    if reading is None:
        return 0.0
    return max(
        0.0,
        min(1.0, (reading.peak_dbfs - _METER_FLOOR_DBFS) / (_METER_CEILING_DBFS - _METER_FLOOR_DBFS)),
    )


class BoothApp:
    def __init__(self, config: BoothConfig, theme: Theme, logger: logging.Logger) -> None:
        self.config = config
        self.theme = theme
        self.logger = logger
        self.state_machine = StateMachine(logger=logger)
        self.recorder = Recorder(config, logger=logger)
        self.transcode_queue = TranscodeQueue(config, logger=logger)
        self.renderer = Renderer(theme, *config.preview_width_height)
        self.capture: cv2.VideoCapture | None = None
        self._last_frame = None
        self._countdown_deadline: float | None = None
        self._state_entered_at: float = time.monotonic()
        self._last_result_reason: str = ""
        self._session_log = get_session_adapter(logger, "-")

        self._mic_check_thread: threading.Thread | None = None
        self._mic_check_stop_event = threading.Event()
        self._mic_readings_lock = threading.Lock()
        self._mic_readings: list[LevelReading] = []
        self._latest_mic_reading: LevelReading | None = None

        # Live RECORDING-screen feedback (config live_preview_enabled) --
        # set from RecordingSession when a recording starts, read each
        # render tick during RECORDING. See _read_live_preview_frame() and
        # _read_live_level_reading().
        self._live_preview_path: Path | None = None
        self._live_preview_last_mtime: float | None = None
        self._live_preview_last_frame = None
        self._live_level_path: Path | None = None
        self._live_level_last_reading: LevelReading | None = None

        self.hook_switch: HookSwitch | None = None
        self._hook_switch_was_lifted = False

    def open_hook_switch(self) -> None:
        """Best-effort: missing/failed GPIO must not crash the app (rule 17).

        Spacebar remains fully functional either way (architecture rule 11).
        """
        if not self.config.hook_switch_enabled:
            self.logger.info("hook switch disabled in config; Spacebar only")
            return
        try:
            self.hook_switch = HookSwitch(
                pin=self.config.hook_switch_gpio_pin, logger=self.logger
            )
            self.logger.info(
                "hook switch initialized on GPIO%d", self.config.hook_switch_gpio_pin
            )
        except HookSwitchError as exc:
            self.logger.warning(
                "hook switch unavailable (%s); falling back to Spacebar only", exc
            )
            self.hook_switch = None

    def _poll_hook_switch(self) -> None:
        if self.hook_switch is None:
            return

        try:
            lifted = self.hook_switch.poll(time.monotonic())
        except HookSwitchError as exc:
            self.logger.warning("hook switch read failed, disabling: %s", exc)
            self.hook_switch = None
            return

        just_lifted = lifted and not self._hook_switch_was_lifted
        just_replaced = (not lifted) and self._hook_switch_was_lifted
        self._hook_switch_was_lifted = lifted

        state = self.state_machine.state
        if just_lifted and state == BoothState.READY:
            self.logger.info("hook switch: receiver lifted")
            self._start_countdown()
        elif just_replaced and state == BoothState.RECORDING:
            self.logger.info("hook switch: receiver replaced")
            self._stop_and_save()
        elif just_replaced and state == BoothState.COUNTDOWN:
            self.logger.info("hook switch: receiver replaced during countdown, cancelling")
            self._cancel_countdown()

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
        self._start_mic_check()
        self._play_pickup_greeting()

    def _play_pickup_greeting(self) -> None:
        """Best-effort spoken prompt on pickup (PROJECT_SPEC.md section 6,
        "optional spoken prompt through earpiece"). Fire-and-forget: does
        not delay or block the countdown, and a missing/broken theme sound
        or speaker must never affect recording (rule 17)."""
        pickup_sound = self.theme.sounds.get("pickup")
        if pickup_sound is None:
            return
        play_sound_async(
            pickup_sound, device=self.config.audio_playback_device, logger=self.logger
        )

    def _cancel_countdown(self) -> None:
        """Receiver replaced before the countdown finished -- spec section 20
        explicitly lists "receiver lifted and immediately replaced" as a
        venue-simulation test case. No recording was started, so just
        return to READY without touching the recorder."""
        self._stop_mic_check()
        self._countdown_deadline = None
        self._enter_state(BoothState.READY)

    def _start_mic_check(self) -> None:
        """Begin sampling mic level in the background during the countdown."""
        if self._mic_check_thread is not None:
            self._stop_mic_check()  # defensive: should not happen, but don't leak
        with self._mic_readings_lock:
            self._mic_readings = []
            self._latest_mic_reading = None
        self._mic_check_stop_event.clear()
        self._mic_check_thread = threading.Thread(target=self._mic_check_loop, daemon=True)
        self._mic_check_thread.start()

    def _mic_check_loop(self) -> None:
        reader = AudioLevelReader(
            self.config.audio_device,
            self.config.audio_sample_rate,
            self.config.audio_channels,
            chunk_ms=MIC_CHECK_CHUNK_MS,
        )
        try:
            reader.start()
        except MicrophoneError as exc:
            self.logger.warning("countdown mic check could not start: %s", exc)
            return
        try:
            while not self._mic_check_stop_event.is_set():
                try:
                    reading = reader.read()
                except MicrophoneError as exc:
                    self.logger.warning("countdown mic check stopped early: %s", exc)
                    return
                with self._mic_readings_lock:
                    self._mic_readings.append(reading)
                    self._latest_mic_reading = reading
        finally:
            reader.stop()

    def _stop_mic_check(self) -> list[LevelReading]:
        """Stop the background mic-check thread and return what it collected.

        Blocks until the underlying arecord process has actually exited, so
        the ALSA device is free again before ffmpeg tries to open it.
        """
        self._mic_check_stop_event.set()
        if self._mic_check_thread is not None:
            self._mic_check_thread.join(timeout=3.0)
        self._mic_check_thread = None
        with self._mic_readings_lock:
            return list(self._mic_readings)

    def _apply_mic_gain_nudge(self, readings: list[LevelReading]) -> None:
        """One-shot capture-gain adjustment from the countdown-time check.

        Never raises: a failure here (unknown mixer control, no card found,
        etc) must not block recording, per architecture rule 17.
        """
        if not readings:
            self.logger.debug("no mic readings collected during countdown; skipping gain nudge")
            return

        avg_rms = sum(r.rms_dbfs for r in readings) / len(readings)
        avg_peak = sum(r.peak_dbfs for r in readings) / len(readings)
        status = classify_level(avg_rms, avg_peak)

        if status == STATUS_READY:
            self.logger.info(
                "countdown mic check: %s (avg rms=%.1f peak=%.1f dBFS), no gain change",
                status,
                avg_rms,
                avg_peak,
            )
            return

        delta = MIC_GAIN_NUDGE_PERCENT if status == STATUS_SPEAK_CLOSER else -MIC_GAIN_NUDGE_PERCENT
        try:
            result = nudge_capture_gain(self.config.audio_device, delta)
        except MixerError as exc:
            self.logger.warning(
                "countdown mic check: %s (avg rms=%.1f peak=%.1f dBFS) but could not "
                "adjust capture gain: %s",
                status,
                avg_rms,
                avg_peak,
                exc,
            )
            return

        self.logger.info(
            "countdown mic check: %s (avg rms=%.1f peak=%.1f dBFS) -> adjusted '%s' "
            "capture from %d%% to %d%%",
            status,
            avg_rms,
            avg_peak,
            result.control,
            result.old_percent,
            result.new_percent,
        )

    def _start_recording(self) -> None:
        # Stop the countdown-time mic check and act on it before ffmpeg opens
        # the audio device -- one process at a time, same as the camera.
        readings = self._stop_mic_check()
        self._apply_mic_gain_nudge(readings)

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
        self._session_log.info(
            "recording started -> %s%s",
            session.live_output_path,
            " (raw, will transcode after)" if session.needs_transcode else "",
        )
        self._live_preview_path = session.live_preview_path
        self._live_preview_last_mtime = None
        self._live_preview_last_frame = None
        self._live_level_path = session.live_level_path
        self._live_level_last_reading = None
        self._enter_state(BoothState.RECORDING)

    def _stop_and_save(self) -> None:
        self._enter_state(BoothState.SAVING)
        try:
            session = self.recorder.stop()
        except RecorderError as exc:
            self.logger.error("failed to stop recording: %s", exc)
            self._last_result_reason = str(exc)
            self._reopen_camera_with_retry()
            self._enter_state(BoothState.ERROR)
            return

        result = validate_recording(session.live_output_path)
        # ffmpeg has exited either way; the camera is free again.
        self._reopen_camera_with_retry()

        if result.ok:
            self._session_log.info(
                "recording saved: duration=%.2fs size=%d bytes",
                result.duration_seconds,
                result.size_bytes,
            )
            if session.needs_transcode:
                self.transcode_queue.enqueue(
                    TranscodeJob(
                        session_id=session.session_id,
                        raw_path=session.live_output_path,
                        output_path=session.final_output_path,
                    )
                )
                self._session_log.info(
                    "queued for background transcode -> %s", session.final_output_path
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
        self._poll_hook_switch()

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

    def render(self, frame):
        """Build the full themed display frame for the current state.

        Only RECORDING uses the live/frozen camera frame (in a bordered
        panel, per the theme); every other screen is fully synthetic
        branded art, matching the "Guestbook Kiosk" design (see
        ui/renderer.py and CHANGELOG.md).
        """
        state = self.state_machine.state
        now = time.monotonic()

        with self._mic_readings_lock:
            latest_mic_reading = self._latest_mic_reading

        if state == BoothState.COUNTDOWN and self._countdown_deadline is not None:
            remaining = max(0.0, self._countdown_deadline - now)
            mic_status = latest_mic_reading.status if latest_mic_reading is not None else None
            mic_fraction = _mic_fraction(latest_mic_reading) if latest_mic_reading is not None else None
            return self.renderer.render_countdown(
                now, remaining, self.config.countdown_seconds, mic_status, mic_fraction
            )
        elif state == BoothState.RECORDING:
            elapsed = now - self._state_entered_at
            remaining = max(0, self.config.max_recording_seconds - int(elapsed))
            # Live feedback when config.live_preview_enabled (the ffmpeg
            # process recording also writes these -- see media/ffmpeg.py);
            # falls back to the frozen pre-recording frame / last countdown
            # reading when unavailable (disabled, or not written yet).
            live_frame = self._read_live_preview_frame()
            live_level = self._read_live_level_reading() or latest_mic_reading
            return self.renderer.render_recording(
                now,
                live_frame if live_frame is not None else frame,
                int(elapsed),
                remaining,
                _mic_fraction(live_level),
                bool(live_level and live_level.clipped),
            )
        elif state == BoothState.SAVING:
            return self.renderer.render_saving(now)
        elif state == BoothState.SAVED:
            return self.renderer.render_saved(now, now - self._state_entered_at)
        elif state == BoothState.ERROR:
            return self.renderer.render_error(now, self._last_result_reason)

        return self.renderer.render_ready(now)

    def _read_live_preview_frame(self):
        """Latest live preview frame during RECORDING, or None if unavailable.

        Reads the JPEG ffmpeg keeps overwriting (config live_preview_enabled;
        see media/ffmpeg.py). Skips re-decoding when the file's mtime hasn't
        changed since the last read, and falls back to the last
        successfully-decoded frame on a transient read-mid-write failure --
        never raises, never blocks waiting for a fresh frame.
        """
        path = self._live_preview_path
        if path is None:
            return None
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return self._live_preview_last_frame
        if mtime == self._live_preview_last_mtime:
            return self._live_preview_last_frame

        frame = cv2.imread(str(path))
        if frame is None:
            return self._live_preview_last_frame
        self._live_preview_last_mtime = mtime
        self._live_preview_last_frame = frame
        return frame

    def _read_live_level_reading(self) -> LevelReading | None:
        """Latest live mic level during RECORDING, or the last known
        reading if the file isn't there yet / momentarily unreadable."""
        path = self._live_level_path
        if path is None:
            return None
        reading = read_latest_live_level(path)
        if reading is not None:
            self._live_level_last_reading = reading
        return self._live_level_last_reading

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
        self.open_hook_switch()
        self.transcode_queue.start()
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
        cv2.setWindowProperty(WINDOW_NAME, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
        try:
            running = True
            while running:
                frame = self._read_frame()
                self.tick()
                display = self.render(frame)
                cv2.imshow(WINDOW_NAME, display)

                key = cv2.waitKey(1) & 0xFF
                if key != 0xFF:
                    running = self.handle_key(key)
        finally:
            self._stop_mic_check()
            if self.recorder.is_running:
                try:
                    self.recorder.stop()
                except RecorderError:
                    self.logger.exception("failed to stop recorder during shutdown")
            if self.hook_switch is not None:
                self.hook_switch.close()
            self.close_camera()
            cv2.destroyAllWindows()
            self.transcode_queue.stop()


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

    try:
        theme = Theme.load(config.theme_dir)
    except ThemeError as exc:
        print(f"Theme error: {exc}")
        return 1

    logger = setup_logging(config.log_dir)
    logger.info("booth starting with config %s, theme %s", args.config, theme.name)

    app = BoothApp(config, theme, logger)
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
