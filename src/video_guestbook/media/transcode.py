"""Background deferred-transcode queue for "quality" recording_mode.

The live capture (media/recorder.py + media/ffmpeg.py's
build_record_command) writes a raw MJPEG file at near-zero CPU cost when
needs_deferred_transcode() is true. This module converts that raw file into
the final playable H.264 MP4 on a single background worker thread, so a
guest never waits for it and it never competes with a live recording's
audio capture for CPU (PROJECT_SPEC.md architecture rule 15: long-running
operations must not freeze the UI; rule 14: independent of guest recording).

The raw file is never deleted by this module, success or failure -- it's
preserved as the backup copy until an explicit retention policy says
otherwise (PROJECT_SPEC.md: "Keep a local backup of all event recordings"),
and is the only copy left if a transcode fails.
"""

from __future__ import annotations

import logging
import queue
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from video_guestbook.config import BoothConfig
from video_guestbook.media.ffmpeg import build_transcode_command
from video_guestbook.media.validation import validate_recording


@dataclass(frozen=True)
class TranscodeJob:
    session_id: str
    raw_path: Path
    output_path: Path


class TranscodeQueue:
    """Single background worker draining a FIFO queue of transcode jobs."""

    def __init__(self, config: BoothConfig, logger: logging.Logger | None = None) -> None:
        self._config = config
        self._logger = logger or logging.getLogger(__name__)
        self._queue: "queue.Queue[TranscodeJob | None]" = queue.Queue()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the worker to stop after its current job and wait briefly.

        Does NOT drain/cancel already-queued jobs; it just stops picking up
        new ones once the current one (if any) finishes, bounded by
        timeout. Any jobs still queued are simply left for next startup --
        nothing is lost, they just won't be processed by this instance.
        """
        if self._thread is None:
            return
        self._queue.put(None)
        self._thread.join(timeout=timeout)
        self._thread = None

    def enqueue(self, job: TranscodeJob) -> None:
        self._logger.info(
            "queued transcode for session %s: %s -> %s (pending: %d)",
            job.session_id,
            job.raw_path,
            job.output_path,
            self._queue.qsize() + 1,
        )
        self._queue.put(job)

    def _worker_loop(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                return
            try:
                self._process_job(job)
            except Exception:
                self._logger.exception(
                    "unexpected error transcoding session %s (raw file preserved: %s)",
                    job.session_id,
                    job.raw_path,
                )
            finally:
                self._queue.task_done()

    def _process_job(self, job: TranscodeJob) -> None:
        command = build_transcode_command(job.raw_path, job.output_path, self._config)
        self._logger.info("transcoding session %s: %s", job.session_id, " ".join(command))

        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False)
        except OSError as exc:
            self._logger.error(
                "transcode failed to start for session %s: %s (raw file preserved: %s)",
                job.session_id,
                exc,
                job.raw_path,
            )
            return

        if result.returncode != 0:
            self._logger.error(
                "transcode failed for session %s (exit %s, raw file preserved: %s): %s",
                job.session_id,
                result.returncode,
                job.raw_path,
                result.stderr.strip(),
            )
            return

        validation = validate_recording(job.output_path)
        if not validation.ok:
            self._logger.error(
                "transcoded file for session %s failed validation: %s "
                "(raw file preserved: %s)",
                job.session_id,
                validation.reason,
                job.raw_path,
            )
            return

        self._logger.info(
            "transcode complete for session %s: duration=%.2fs size=%d bytes -> %s",
            job.session_id,
            validation.duration_seconds,
            validation.size_bytes,
            job.output_path,
        )
