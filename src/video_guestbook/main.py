"""Guest-facing booth application entry point.

Full-screen MJPEG preview, hook-switch AND Spacebar start/stop (both work
at once per architecture rule 11), a live-preview "get ready" screen with
tap-to-record, countdown, recording via ffmpeg, save validation, and return
to READY. Themes, admin UI and cloud sync are intentionally not implemented
yet (see PROJECT_SPEC.md section 23).

Camera/ffmpeg handoff: the USB webcam can only be held open by one process
at a time. This mirrors the confirmed working prototype (legacy/booth.py):
the OpenCV preview capture is released just before ffmpeg opens the camera
device for recording, and reopened (with retries) once ffmpeg exits. Do not
read frames from self.capture while a recording is in progress.

PREVIEW and COUNTDOWN both happen before ffmpeg ever touches the camera, so
self.capture stays open and its frames genuinely are live on both those
screens -- see state_machine.py's module docstring for why PREVIEW exists
as its own state (it replaced an earlier design where RECORDING itself
tried to show a live preview via a second ffmpeg output, which caused real
audio-breakup trouble on hardware; see CHANGELOG.md). RECORDING only ever
shows the last frame captured before ffmpeg took over.
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

# Live mic meter during RECORDING (config live_mic_meter_enabled, off by
# default): a second AudioLevelReader on the same device ffmpeg is now
# recording from -- see _start_recording_mic_meter(). Delayed this long
# after ffmpeg's own Popen call before attempting to open the device
# ourselves, so ffmpeg always gets first claim on it; if the ALSA
# device/driver doesn't allow a second concurrent reader, our arecord
# simply fails to open (safe -- the meter just falls back to the last
# countdown reading), rather than risking ffmpeg's own capture losing the
# device to us.
RECORDING_MIC_METER_START_DELAY_SECONDS = 1.0

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

        # PREVIEW auto-advance (config preview_seconds): a guest who never
        # taps the record button (or whose tap the touchscreen missed)
        # still isn't stuck on the live-preview screen forever. Mirrors
        # _countdown_deadline below. The RECORDING screen's live mic meter
        # (config live_mic_meter_enabled) reuses _mic_check_thread/
        # _latest_mic_reading above instead of a field of its own -- see
        # _start_recording_mic_meter().
        self._preview_deadline: float | None = None

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
            self._start_preview()
        elif just_replaced and state == BoothState.RECORDING:
            self.logger.info("hook switch: receiver replaced")
            self._stop_and_save()
        elif just_replaced and state in (BoothState.PREVIEW, BoothState.COUNTDOWN):
            self.logger.info(
                "hook switch: receiver replaced during %s, cancelling", state.value
            )
            self._cancel_to_ready()

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

    def _start_preview(self) -> None:
        """Receiver just lifted: live camera + mic meter, guest checks
        themselves and either taps Record or waits out preview_seconds
        (see tick()'s PREVIEW branch and _on_mouse()). The camera is
        genuinely live here -- ffmpeg hasn't opened it yet -- so the mic
        check can safely start now too and just keep running through
        COUNTDOWN until _start_recording() stops it."""
        self._enter_state(BoothState.PREVIEW)
        self._preview_deadline = time.monotonic() + self.config.preview_seconds
        self._start_mic_check()
        self._play_pickup_greeting()

    def _start_countdown(self) -> None:
        """Guest tapped Record (or preview_seconds ran out): begin the
        numeric countdown. The mic check keeps running from _start_preview()
        uninterrupted -- nothing to start or stop here."""
        self._enter_state(BoothState.COUNTDOWN)
        self._preview_deadline = None
        self._countdown_deadline = time.monotonic() + self.config.countdown_seconds

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

    def _cancel_to_ready(self) -> None:
        """Receiver replaced during PREVIEW or COUNTDOWN, before recording
        started -- spec section 20 explicitly lists "receiver lifted and
        immediately replaced" as a venue-simulation test case. No recording
        was started, so just return to READY without touching the
        recorder."""
        self._stop_mic_check()
        self._preview_deadline = None
        self._countdown_deadline = None
        self._enter_state(BoothState.READY)

    def _start_mic_check(
        self, context: str = "countdown", reset_latest: bool = True, start_delay_seconds: float = 0.0
    ) -> None:
        """Begin sampling mic level in the background via a live arecord.

        Used both during COUNTDOWN (the original, always-safe case -- the
        ALSA device is free at that point) and, when config
        live_mic_meter_enabled is on, again during RECORDING (see
        _start_recording_mic_meter()) where the device is not free and
        this may simply fail to open -- see start_delay_seconds and
        reset_latest below.
        """
        if self._mic_check_thread is not None:
            self._stop_mic_check()  # defensive: should not happen, but don't leak
        with self._mic_readings_lock:
            self._mic_readings = []
            if reset_latest:
                self._latest_mic_reading = None
        self._mic_check_stop_event.clear()
        self._mic_check_thread = threading.Thread(
            target=self._mic_check_loop, args=(context, start_delay_seconds), daemon=True
        )
        self._mic_check_thread.start()

    def _mic_check_loop(self, context: str = "countdown", start_delay_seconds: float = 0.0) -> None:
        if start_delay_seconds > 0 and self._mic_check_stop_event.wait(start_delay_seconds):
            return  # stopped before the delay even elapsed

        reader = AudioLevelReader(
            self.config.audio_device,
            self.config.audio_sample_rate,
            self.config.audio_channels,
            chunk_ms=MIC_CHECK_CHUNK_MS,
        )
        try:
            reader.start()
        except MicrophoneError as exc:
            self.logger.warning("%s mic check could not start: %s", context, exc)
            return
        try:
            while not self._mic_check_stop_event.is_set():
                try:
                    reading = reader.read()
                except MicrophoneError as exc:
                    self.logger.warning("%s mic check stopped early: %s", context, exc)
                    return
                with self._mic_readings_lock:
                    self._mic_readings.append(reading)
                    self._latest_mic_reading = reading
        finally:
            reader.stop()

    def _stop_mic_check(self) -> list[LevelReading]:
        """Stop the background mic-reader thread and return what it collected.

        Blocks until the underlying arecord process has actually exited.
        Called both before ffmpeg opens the audio device to record (so it's
        free) and to stop the RECORDING-phase meter (config
        live_mic_meter_enabled) once the guest hangs up.
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
        self._enter_state(BoothState.RECORDING)
        self._start_recording_mic_meter()

    def _start_recording_mic_meter(self) -> None:
        """Best-effort live mic meter during RECORDING (config
        live_mic_meter_enabled, off by default): a second AudioLevelReader
        on the device ffmpeg is now recording from.

        reset_latest=False deliberately keeps whatever the countdown-time
        check last saw as the displayed value until (if) this reader
        produces a fresh one -- so if the ALSA device doesn't allow a
        second concurrent reader and this never gets a reading, the meter
        stays at the last known-good value instead of going blank for the
        whole recording.
        """
        if not self.config.live_mic_meter_enabled:
            return
        self._start_mic_check(
            context="recording",
            reset_latest=False,
            start_delay_seconds=RECORDING_MIC_METER_START_DELAY_SECONDS,
        )

    def _stop_and_save(self) -> None:
        self._stop_mic_check()  # no-op if live_mic_meter_enabled was off
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
            self._start_preview()
        elif self.state_machine.state == BoothState.PREVIEW:
            self._start_countdown()
        elif self.state_machine.state == BoothState.RECORDING:
            self._stop_and_save()

        return True

    def _on_mouse(self, event: int, x: int, y: int, flags: int, userdata: object) -> None:
        """Touch/click handling for the PREVIEW screen's record button.

        Best-effort (rule 17): if record_button_rect hasn't been set yet
        (no PREVIEW frame rendered yet) this just does nothing rather than
        raising. preview_seconds' auto-advance is the safety net if a
        touchscreen's coordinates don't line up with this rect on a given
        piece of hardware -- see config.py.
        """
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        if self.state_machine.state != BoothState.PREVIEW:
            return
        rect = self.renderer.record_button_rect
        if rect is None:
            return
        x0, y0, x1, y1 = rect
        if x0 <= x <= x1 and y0 <= y <= y1:
            self.logger.info("record button tapped")
            self._start_countdown()

    def tick(self) -> None:
        """Advance time-based transitions (countdown expiry, auto-stop, timeouts)."""
        self._poll_hook_switch()

        state = self.state_machine.state

        if state == BoothState.PREVIEW and self._preview_deadline is not None:
            if time.monotonic() >= self._preview_deadline:
                self.logger.info("preview_seconds elapsed with no tap; auto-advancing")
                self._start_countdown()

        elif state == BoothState.COUNTDOWN and self._countdown_deadline is not None:
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

        Only PREVIEW (live) and RECORDING (frozen) use the camera frame, in
        a bordered panel per the theme; every other screen is fully
        synthetic branded art, matching the "Guestbook Kiosk" design (see
        ui/renderer.py and CHANGELOG.md).
        """
        state = self.state_machine.state
        now = time.monotonic()

        with self._mic_readings_lock:
            latest_mic_reading = self._latest_mic_reading

        if state == BoothState.PREVIEW:
            # frame is genuinely live here -- ffmpeg hasn't opened the
            # camera yet, see the module docstring above.
            return self.renderer.render_preview(
                now,
                frame,
                _mic_fraction(latest_mic_reading),
                bool(latest_mic_reading and latest_mic_reading.clipped),
            )
        elif state == BoothState.COUNTDOWN and self._countdown_deadline is not None:
            remaining = max(0.0, self._countdown_deadline - now)
            mic_status = latest_mic_reading.status if latest_mic_reading is not None else None
            mic_fraction = _mic_fraction(latest_mic_reading) if latest_mic_reading is not None else None
            return self.renderer.render_countdown(
                now, remaining, self.config.countdown_seconds, mic_status, mic_fraction
            )
        elif state == BoothState.RECORDING:
            elapsed = now - self._state_entered_at
            remaining = max(0, self.config.max_recording_seconds - int(elapsed))
            # frame is the last frame captured on the PREVIEW screen, frozen
            # the moment ffmpeg took the camera -- see the module docstring
            # above. latest_mic_reading is live here too when config.
            # live_mic_meter_enabled (see _start_recording_mic_meter()),
            # otherwise it's just whatever the countdown-time check last
            # saw, frozen.
            return self.renderer.render_recording(
                now,
                frame,
                int(elapsed),
                remaining,
                _mic_fraction(latest_mic_reading),
                bool(latest_mic_reading and latest_mic_reading.clipped),
            )
        elif state == BoothState.SAVING:
            return self.renderer.render_saving(now)
        elif state == BoothState.SAVED:
            return self.renderer.render_saved(now, now - self._state_entered_at)
        elif state == BoothState.ERROR:
            return self.renderer.render_error(now, self._last_result_reason)

        return self.renderer.render_ready(now)

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
        cv2.setMouseCallback(WINDOW_NAME, self._on_mouse)
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
