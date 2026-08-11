"""Microphone level sampling and classification.

Shared by the standalone `scripts/mic_test.py` dev tool and the guest app's
countdown-time level check (PROJECT_SPEC.md section 6, "Proposed countdown
audio check"). Reads raw PCM directly from `arecord` so no extra Python
audio dependencies (numpy, sounddevice, ...) are required.

Level targets are from PROJECT_SPEC.md section 6:
  Normal speech average: approximately -30 to -12 dBFS
  Peaks: below approximately -6 dBFS
  Clipping danger: near 0 dBFS
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from array import array
from dataclasses import dataclass
from math import log10
from pathlib import Path

QUIET_RMS_DBFS = -40.0  # below this, RMS -> guest is too quiet / too far away
LOUD_PEAK_DBFS = -6.0  # spec: "Peaks: below approximately -6 dBFS"
CLIP_PEAK_DBFS = -1.0  # spec: "Clipping danger: near 0 dBFS"

STATUS_SPEAK_CLOSER = "SPEAK CLOSER"
STATUS_READY = "MICROPHONE READY"
STATUS_TOO_LOUD = "TOO LOUD - MOVE SLIGHTLY AWAY"

SILENCE_DBFS = -96.0  # floor value reported for an all-zero / empty chunk
_SAMPLE_WIDTH_BYTES = 2  # 16-bit PCM (S16_LE)
_FULL_SCALE = 32768.0


class MicrophoneError(RuntimeError):
    """Raised when the microphone capture process can't be started or reads fail."""


def find_arecord() -> str:
    path = shutil.which("arecord")
    if not path:
        raise MicrophoneError("arecord was not found on PATH (install alsa-utils)")
    return path


@dataclass(frozen=True)
class LevelReading:
    timestamp: float
    rms_dbfs: float
    peak_dbfs: float
    clipped: bool
    status: str


def pcm16_to_dbfs(chunk: bytes) -> tuple[float, float]:
    """Return (rms_dbfs, peak_dbfs) for a raw little-endian 16-bit PCM chunk."""
    usable_len = (len(chunk) // _SAMPLE_WIDTH_BYTES) * _SAMPLE_WIDTH_BYTES
    if usable_len == 0:
        return SILENCE_DBFS, SILENCE_DBFS

    samples = array("h")
    samples.frombytes(chunk[:usable_len])
    if len(samples) == 0:
        return SILENCE_DBFS, SILENCE_DBFS

    peak = max(abs(s) for s in samples)
    mean_square = sum(s * s for s in samples) / len(samples)
    rms = mean_square**0.5

    peak_dbfs = 20 * log10(peak / _FULL_SCALE) if peak > 0 else SILENCE_DBFS
    rms_dbfs = 20 * log10(rms / _FULL_SCALE) if rms > 0 else SILENCE_DBFS
    return rms_dbfs, peak_dbfs


def classify_level(rms_dbfs: float, peak_dbfs: float) -> str:
    """Classify a reading using the spec's proposed countdown-check messages."""
    if rms_dbfs < QUIET_RMS_DBFS:
        return STATUS_SPEAK_CLOSER
    if peak_dbfs > LOUD_PEAK_DBFS:
        return STATUS_TOO_LOUD
    return STATUS_READY


# Matches ffmpeg's `ametadata=print` output for astats channel 1 (see
# media/ffmpeg.py's live_level_path support): lines like
# "lavfi.astats.1.Peak_level=-18.063496" or "...=-inf" for total silence.
# Channel 1 is used regardless of audio_channels -- a mono/left-channel
# proxy is plenty for a live UI meter, no need for full multi-channel
# handling here.
_LIVE_LEVEL_RE = re.compile(r"lavfi\.astats\.1\.(Peak|RMS)_level=(-?(?:\d+\.\d+|inf))")


def read_latest_live_level(path: Path) -> LevelReading | None:
    """Parse the most recent complete Peak+RMS pair from a live-level file.

    Returns None if the file doesn't exist yet or doesn't yet contain a
    complete pair (e.g. read mid-write, or before the first chunk has been
    flushed) -- callers should keep showing their last known reading in
    that case, the same graceful-degradation approach used everywhere else
    live hardware feedback can be momentarily unavailable.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    values: dict[str, float] = {}
    for key, raw_value in reversed(_LIVE_LEVEL_RE.findall(text)):
        if key not in values:
            values[key] = float(raw_value)
        if len(values) == 2:
            break
    if len(values) < 2:
        return None

    peak_dbfs = values["Peak"]
    rms_dbfs = values["RMS"]
    return LevelReading(
        timestamp=time.time(),
        rms_dbfs=rms_dbfs,
        peak_dbfs=peak_dbfs,
        clipped=peak_dbfs >= CLIP_PEAK_DBFS,
        status=classify_level(rms_dbfs, peak_dbfs),
    )


class AudioLevelReader:
    """Streams level readings from a microphone via a raw `arecord` capture.

    Usage::

        reader = AudioLevelReader(device, sample_rate, channels)
        reader.start()
        try:
            while running:
                reading = reader.read()  # blocks ~chunk_ms
                ...
        finally:
            reader.stop()

    Or as a context manager: `with AudioLevelReader(...) as reader:`.
    """

    def __init__(
        self,
        device: str,
        sample_rate: int,
        channels: int,
        chunk_ms: int = 100,
    ) -> None:
        self.device = device
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_ms = chunk_ms
        self._chunk_bytes = (
            int(sample_rate * chunk_ms / 1000) * channels * _SAMPLE_WIDTH_BYTES
        )
        self._process: subprocess.Popen | None = None

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        arecord = find_arecord()
        command = [
            arecord,
            "-D",
            self.device,
            "-f",
            "S16_LE",
            "-r",
            str(self.sample_rate),
            "-c",
            str(self.channels),
            "-t",
            "raw",
            "-q",
        ]
        try:
            self._process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
        except OSError as exc:
            raise MicrophoneError(f"Failed to start arecord: {exc}") from exc

    def stop(self) -> None:
        if self._process is None:
            return
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
        self._process = None

    def read(self) -> LevelReading:
        """Block until the next chunk is available and return its reading.

        Raises MicrophoneError if the capture process has stopped producing
        audio (device unplugged, arecord crashed, etc).
        """
        if self._process is None or self._process.stdout is None:
            raise MicrophoneError("AudioLevelReader is not started")

        chunk = self._read_exact(self._chunk_bytes)
        if not chunk:
            stderr = ""
            if self._process.stderr is not None:
                stderr = self._process.stderr.read().decode("utf-8", errors="replace")
            raise MicrophoneError(
                f"arecord stopped producing audio: {stderr.strip() or 'no output'}"
            )

        rms_dbfs, peak_dbfs = pcm16_to_dbfs(chunk)
        return LevelReading(
            timestamp=time.time(),
            rms_dbfs=rms_dbfs,
            peak_dbfs=peak_dbfs,
            clipped=peak_dbfs >= CLIP_PEAK_DBFS,
            status=classify_level(rms_dbfs, peak_dbfs),
        )

    def _read_exact(self, n: int) -> bytes:
        assert self._process is not None and self._process.stdout is not None
        buf = bytearray()
        while len(buf) < n:
            piece = self._process.stdout.read(n - len(buf))
            if not piece:
                break
            buf.extend(piece)
        return bytes(buf)

    def __enter__(self) -> "AudioLevelReader":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()
