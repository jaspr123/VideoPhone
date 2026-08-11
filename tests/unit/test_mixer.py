import subprocess

import pytest

from video_guestbook.media import mixer as mixer_module
from video_guestbook.media.mixer import (
    MixerError,
    find_capture_control,
    get_capture_percent,
    get_raw_control_info,
    list_capture_controls,
    nudge_capture_gain,
    resolve_card_index,
    set_capture_percent,
)

ARECORD_L_OUTPUT = """\
**** List of CAPTURE Hardware Devices ****
card 3: Device [FDUCE SL40 Audio Device], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
  Subdevice #0: subdevice #0
card 4: webcamproduct [webcamproduct], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
  Subdevice #0: subdevice #0
"""

SCONTROLS_OUTPUT = """\
Simple mixer control 'Mic',0
Simple mixer control 'Mic Boost',0
Simple mixer control 'Auto Gain Control',0
"""


# A combined control with BOTH a Playback (monitoring/sidetone) element AND
# a Capture element, deliberately at DIFFERENT percentages -- this matches
# what was actually observed on real hardware (Playback shown in
# alsamixer's F3 view, Capture in F4) and is the exact scenario that
# revealed the "grabs the first [NN%] in the output" bug.
SGET_OUTPUT = """\
Simple mixer control 'Mic',0
  Capabilities: pvolume pvolume-joined pswitch pswitch-joined cvolume cvolume-joined cswitch cswitch-joined
  Playback channels: Mono
  Capture channels: Mono
  Limits: Playback 0 - 87, Capture 0 - 16
  Mono: Playback 76 [87%] [-10.50dB] [on]
  Mono: Capture 1 [49%] [16.50dB] [on]
"""

SGET_OUTPUT_PLAYBACK_ONLY = """\
Simple mixer control 'Speaker',0
  Capabilities: pvolume pswitch pswitch-joined
  Playback channels: Mono
  Limits: Playback 0 - 87
  Mono: Playback 76 [87%] [-10.50dB] [on]
"""


@pytest.fixture(autouse=True)
def _stub_binaries(monkeypatch):
    monkeypatch.setattr(mixer_module, "find_amixer", lambda: "amixer")
    monkeypatch.setattr(mixer_module, "find_arecord", lambda: "arecord")


def _completed(stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# resolve_card_index
# ---------------------------------------------------------------------------


def test_resolve_card_index_from_card_name(monkeypatch):
    monkeypatch.setattr(
        mixer_module.subprocess, "run", lambda *a, **k: _completed(ARECORD_L_OUTPUT)
    )
    assert resolve_card_index("plughw:CARD=Device,DEV=0") == 3


def test_resolve_card_index_from_numeric_hw(monkeypatch):
    def fail_if_called(*a, **k):
        raise AssertionError("should not shell out for a numeric device string")

    monkeypatch.setattr(mixer_module.subprocess, "run", fail_if_called)
    assert resolve_card_index("plughw:3,0") == 3
    assert resolve_card_index("hw:2,0") == 2


def test_resolve_card_index_unknown_card_name_raises(monkeypatch):
    monkeypatch.setattr(
        mixer_module.subprocess, "run", lambda *a, **k: _completed(ARECORD_L_OUTPUT)
    )
    with pytest.raises(MixerError):
        resolve_card_index("plughw:CARD=NoSuchCard,DEV=0")


def test_resolve_card_index_unparseable_device_raises():
    with pytest.raises(MixerError):
        resolve_card_index("not-a-real-device-string")


# ---------------------------------------------------------------------------
# list_capture_controls / find_capture_control
# ---------------------------------------------------------------------------


def test_list_capture_controls_parses_names(monkeypatch):
    monkeypatch.setattr(
        mixer_module.subprocess, "run", lambda *a, **k: _completed(SCONTROLS_OUTPUT)
    )
    assert list_capture_controls(3) == ["Mic", "Mic Boost", "Auto Gain Control"]


def test_find_capture_control_prefers_mic(monkeypatch):
    monkeypatch.setattr(
        mixer_module.subprocess, "run", lambda *a, **k: _completed(SCONTROLS_OUTPUT)
    )
    assert find_capture_control(3) == "Mic"


def test_find_capture_control_falls_back_to_capture(monkeypatch):
    monkeypatch.setattr(
        mixer_module.subprocess,
        "run",
        lambda *a, **k: _completed("Simple mixer control 'Capture',0\n"),
    )
    assert find_capture_control(3) == "Capture"


def test_find_capture_control_raises_when_none_found(monkeypatch):
    monkeypatch.setattr(
        mixer_module.subprocess,
        "run",
        lambda *a, **k: _completed("Simple mixer control 'Master',0\n"),
    )
    with pytest.raises(MixerError):
        find_capture_control(3)


def test_list_capture_controls_raises_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        mixer_module.subprocess,
        "run",
        lambda *a, **k: _completed("", returncode=1, stderr="no such card"),
    )
    with pytest.raises(MixerError, match="no such card"):
        list_capture_controls(9)


# ---------------------------------------------------------------------------
# get_raw_control_info / get_capture_percent / set_capture_percent
# ---------------------------------------------------------------------------


def test_get_raw_control_info_returns_full_output_unparsed(monkeypatch):
    monkeypatch.setattr(mixer_module.subprocess, "run", lambda *a, **k: _completed(SGET_OUTPUT))
    raw = get_raw_control_info(3, "Mic")
    assert "Playback 76 [87%]" in raw
    assert "Capture 1 [49%]" in raw


def test_get_raw_control_info_raises_on_failure(monkeypatch):
    monkeypatch.setattr(
        mixer_module.subprocess,
        "run",
        lambda *a, **k: _completed("", returncode=1, stderr="no such control"),
    )
    with pytest.raises(MixerError, match="no such control"):
        get_raw_control_info(3, "Mic")


def test_get_capture_percent_parses_value(monkeypatch):
    monkeypatch.setattr(mixer_module.subprocess, "run", lambda *a, **k: _completed(SGET_OUTPUT))
    assert get_capture_percent(3, "Mic") == 49


def test_get_capture_percent_ignores_playback_line_on_combined_control(monkeypatch):
    """Regression test: must return the Capture value (49%), not the
    Playback/monitoring value (87%) that appears earlier in the same
    `amixer sget` output. This is the exact bug found via real hardware:
    a combined 'Mic' control's Playback line was being read instead of
    Capture, so gain reads/adjustments were silently touching the wrong
    element."""
    monkeypatch.setattr(mixer_module.subprocess, "run", lambda *a, **k: _completed(SGET_OUTPUT))
    percent = get_capture_percent(3, "Mic")
    assert percent == 49
    assert percent != 87


def test_get_capture_percent_raises_when_control_has_no_capture_element(monkeypatch):
    monkeypatch.setattr(
        mixer_module.subprocess, "run", lambda *a, **k: _completed(SGET_OUTPUT_PLAYBACK_ONLY)
    )
    with pytest.raises(MixerError, match="Capture"):
        get_capture_percent(3, "Speaker")


def test_get_capture_percent_raises_when_unparseable(monkeypatch):
    monkeypatch.setattr(mixer_module.subprocess, "run", lambda *a, **k: _completed("nothing useful"))
    with pytest.raises(MixerError):
        get_capture_percent(3, "Mic")


def test_set_capture_percent_clamps_to_valid_range(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return _completed()

    monkeypatch.setattr(mixer_module.subprocess, "run", fake_run)

    set_capture_percent(3, "Mic", 150)
    set_capture_percent(3, "Mic", -20)

    assert calls[0][-1] == "100%"
    assert calls[1][-1] == "0%"


def test_set_capture_percent_raises_on_failure(monkeypatch):
    monkeypatch.setattr(
        mixer_module.subprocess,
        "run",
        lambda *a, **k: _completed("", returncode=1, stderr="invalid control"),
    )
    with pytest.raises(MixerError, match="invalid control"):
        set_capture_percent(3, "Mic", 50)


# ---------------------------------------------------------------------------
# nudge_capture_gain (end to end, all subprocess calls mocked)
# ---------------------------------------------------------------------------


def test_nudge_capture_gain_end_to_end(monkeypatch):
    def fake_run(command, **kwargs):
        joined = " ".join(command)
        if "arecord" in command[0] and "-l" in command:
            return _completed(ARECORD_L_OUTPUT)
        if "scontrols" in command:
            return _completed(SCONTROLS_OUTPUT)
        if "sget" in command:
            return _completed(SGET_OUTPUT)
        if "sset" in command:
            return _completed()
        raise AssertionError(f"unexpected command: {joined}")

    monkeypatch.setattr(mixer_module.subprocess, "run", fake_run)

    result = nudge_capture_gain("plughw:CARD=Device,DEV=0", delta_percent=15)

    assert result.card_index == 3
    assert result.control == "Mic"
    assert result.old_percent == 49
    assert result.new_percent == 64


def test_nudge_capture_gain_clamps_at_100(monkeypatch):
    def fake_run(command, **kwargs):
        if "-l" in command:
            return _completed(ARECORD_L_OUTPUT)
        if "scontrols" in command:
            return _completed(SCONTROLS_OUTPUT)
        if "sget" in command:
            return _completed(SGET_OUTPUT)  # 49%
        if "sset" in command:
            return _completed()
        raise AssertionError("unexpected command")

    monkeypatch.setattr(mixer_module.subprocess, "run", fake_run)

    result = nudge_capture_gain("plughw:CARD=Device,DEV=0", delta_percent=1000)
    assert result.new_percent == 100
