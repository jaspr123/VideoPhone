"""Recording session management.

Wraps the ffmpeg subprocess: builds a unique session ID and output filename
(architecture rule 8), starts the recording process, and stops it gracefully
by writing 'q' to ffmpeg's stdin (preserving current Beta behavior).
"""

from __future__ import annotations

import logging
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from video_guestbook.config import BoothConfig
from video_guestbook.media.ffmpeg import build_record_command

_FILENAME_TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"
_GRACEFUL_STOP_TIMEOUT_SECONDS = 10
_TERMINATE_TIMEOUT_SECONDS = 5


class RecorderError(RuntimeError):
    """Raised when a recording session cannot be started or stopped cleanly."""


@dataclass(frozen=True)
class RecordingSession:
    session_id: str
    output_path: Path
    started_at: datetime


def generate_session_id(now: datetime | None = None) -> str:
    """Return a unique, sortable session ID: '<timestamp>_<short-uuid>'."""
    now = now or datetime.now(timezone.utc)
    return f"{now.strftime(_FILENAME_TIMESTAMP_FORMAT)}_{uuid.uuid4().hex[:8]}"


def build_output_filename(session_id: str) -> str:
    return f"{session_id}.mp4"


def build_output_path(output_dir: Path, session_id: str) -> Path:
    return output_dir / build_output_filename(session_id)


class Recorder:
    def __init__(self, config: BoothConfig, logger: logging.Logger | None = None) -> None:
        self._config = config
        self._logger = logger or logging.getLogger(__name__)
        self._process: subprocess.Popen | None = None
        self._session: RecordingSession | None = None

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def session(self) -> RecordingSession | None:
        return self._session

    def start(self) -> RecordingSession:
        if self.is_running:
            raise RecorderError("A recording is already in progress")

        self._config.output_dir.mkdir(parents=True, exist_ok=True)
        session_id = generate_session_id()
        output_path = build_output_path(self._config.output_dir, session_id)
        command = build_record_command(self._config, output_path)

        self._logger.info("starting recording session %s: %s", session_id, " ".join(command))
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            raise RecorderError(f"Failed to start ffmpeg: {exc}") from exc

        self._process = process
        self._session = RecordingSession(
            session_id=session_id,
            output_path=output_path,
            started_at=datetime.now(timezone.utc),
        )
        return self._session

    def stop(self) -> Path:
        if self._process is None or self._session is None:
            raise RecorderError("No recording in progress")

        session = self._session
        process = self._process
        self._logger.info("stopping recording session %s", session.session_id)

        if process.poll() is None:
            try:
                if process.stdin is not None:
                    process.stdin.write("q")
                    process.stdin.flush()
                    process.stdin.close()
            except (BrokenPipeError, OSError) as exc:
                self._logger.warning("could not write 'q' to ffmpeg stdin: %s", exc)

            try:
                process.wait(timeout=_GRACEFUL_STOP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                self._logger.warning(
                    "ffmpeg did not exit after 'q', terminating session %s", session.session_id
                )
                process.terminate()
                try:
                    process.wait(timeout=_TERMINATE_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    self._logger.error(
                        "ffmpeg did not terminate, killing session %s", session.session_id
                    )
                    process.kill()
                    process.wait()

        stderr_output = ""
        if process.stderr is not None:
            stderr_output = process.stderr.read()

        return_code = process.returncode
        self._logger.info(
            "ffmpeg exited with code %s for session %s", return_code, session.session_id
        )
        if return_code not in (0, None) and stderr_output:
            self._logger.warning("ffmpeg stderr for session %s: %s", session.session_id, stderr_output)

        self._process = None
        self._session = None
        return session.output_path
