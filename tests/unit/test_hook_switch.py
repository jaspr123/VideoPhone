import pytest

from video_guestbook.hardware import hook_switch as hook_switch_module
from video_guestbook.hardware.hook_switch import HookSwitch, HookSwitchError


class FakeButton:
    def __init__(self, pin, pull_up=None, bounce_time=None, pin_factory=None):
        self.pin = pin
        self.pull_up = pull_up
        self.bounce_time = bounce_time
        self.is_pressed = False
        self.closed = False

    def close(self):
        self.closed = True


class RaisingCloseButton(FakeButton):
    def close(self):
        raise RuntimeError("close failed")


class RaisingReadButton:
    def __init__(self, pin, **kwargs):
        self.pin = pin

    @property
    def is_pressed(self):
        raise RuntimeError("read failed")

    def close(self):
        pass


@pytest.fixture
def button_registry(monkeypatch):
    created: dict[int, FakeButton] = {}

    def factory(pin, **kwargs):
        button = FakeButton(pin, **kwargs)
        created[pin] = button
        return button

    monkeypatch.setattr(hook_switch_module, "Button", factory)
    return created


def test_raises_when_gpiozero_unavailable(monkeypatch):
    monkeypatch.setattr(hook_switch_module, "Button", None)
    with pytest.raises(HookSwitchError, match="gpiozero"):
        HookSwitch(pin=17, diagnostic_pin=None)


def test_raises_when_primary_pin_init_fails(monkeypatch):
    def factory(pin, **kwargs):
        raise RuntimeError("pin busy")

    monkeypatch.setattr(hook_switch_module, "Button", factory)
    with pytest.raises(HookSwitchError, match="pin busy"):
        HookSwitch(pin=17, diagnostic_pin=None)


def test_constructs_with_pull_up_and_bounce_time(button_registry):
    HookSwitch(pin=17, diagnostic_pin=None, bounce_time=0.2)
    assert button_registry[17].pull_up is True
    assert button_registry[17].bounce_time == 0.2


def test_is_lifted_reflects_primary_button_pressed(button_registry):
    switch = HookSwitch(pin=17, diagnostic_pin=None)

    button_registry[17].is_pressed = True
    assert switch.is_lifted is True

    button_registry[17].is_pressed = False
    assert switch.is_lifted is False


def test_is_lifted_wraps_read_errors(monkeypatch):
    monkeypatch.setattr(hook_switch_module, "Button", RaisingReadButton)
    switch = HookSwitch(pin=17, diagnostic_pin=None)
    with pytest.raises(HookSwitchError, match="read failed"):
        _ = switch.is_lifted


def test_diagnostic_pin_none_disables_diagnostic(button_registry):
    switch = HookSwitch(pin=17, diagnostic_pin=None)
    assert switch.diagnostic_is_active is None
    assert 27 not in button_registry


def test_diagnostic_pin_reflects_second_button(button_registry):
    switch = HookSwitch(pin=17, diagnostic_pin=27)
    button_registry[27].is_pressed = True
    assert switch.diagnostic_is_active is True


def test_diagnostic_pin_init_failure_is_non_fatal(monkeypatch):
    def factory(pin, **kwargs):
        if pin == 27:
            raise RuntimeError("diagnostic pin busy")
        return FakeButton(pin, **kwargs)

    monkeypatch.setattr(hook_switch_module, "Button", factory)

    switch = HookSwitch(pin=17, diagnostic_pin=27)  # must not raise
    assert switch.diagnostic_is_active is None


def test_close_closes_both_buttons(button_registry):
    switch = HookSwitch(pin=17, diagnostic_pin=27)
    switch.close()
    assert button_registry[17].closed is True
    assert button_registry[27].closed is True


def test_close_is_resilient_to_button_close_errors(monkeypatch):
    monkeypatch.setattr(hook_switch_module, "Button", RaisingCloseButton)
    switch = HookSwitch(pin=17, diagnostic_pin=27)
    switch.close()  # must not raise
