"""Physical receiver hook-switch input (PROJECT_SPEC.md section 4).

Wiring (as documented in the spec):
    Black wire -> physical pin 9  -> GND
    Red wire   -> physical pin 11 -> GPIO17
    Green wire -> physical pin 13 -> GPIO27

GPIO17 is the primary, production receiver-state signal, using the GPIO's
internal pull-up resistor (never wire the switch to 5V or 3.3V):
    stable LOW  -> receiver lifted (off-hook)   [normally-open switch]
    stable HIGH -> receiver on-hook

That LOW=lifted mapping assumes a normally-open switch (circuit closes,
pulling the pin to GND, when the receiver is lifted). A normally-closed
switch wired the same GND-only way reads the opposite: stable HIGH when
lifted. `invert` (see HookSwitch.__init__) flips is_lifted's
interpretation for that case -- it does not change the wiring rule itself,
never wire either terminal to 5V/3.3V regardless of switch type. Which
one you have, and which GPIO pin it's actually on, is exactly what the
attendant DEVELOPER screen's "Find receiver switch" wizard (main.py's
_start_hook_scan and friends) is for -- confirmed miswiring/wrong-pin
issues on real installs are common enough that this is expected to run at
least once per physical booth setup.

GPIO27 was observed to stay low regardless of hook state during initial
testing and is treated as diagnostic-only, not used for production
start/stop logic, per the spec's explicit caution ("should be treated as
diagnostic until the switch is re-tested").

Debouncing (~100-200ms per the spec) is requested from gpiozero via
Button's bounce_time, but that only debounces gpiozero's own edge-callback
system (when_pressed/when_released) -- it does not debounce plain
`is_pressed` reads, which is all this module's polling-based `is_lifted`
property does. Real hardware testing confirmed this gap in practice: a
lift or hang-up motion would sometimes not register, or would need a
second motion to register, consistent with mechanical switch bounce being
read raw by a tight polling loop. `poll()` below adds its own time-based
software debounce on top of the raw reads for that reason -- callers doing
edge detection should use `poll()`, not `is_lifted` directly.
"""

from __future__ import annotations

import logging

try:
    from gpiozero import Button
except ImportError:  # pragma: no cover - exercised via monkeypatching in tests
    Button = None

HOOK_SWITCH_GPIO_PIN = 17
HOOK_SWITCH_DIAGNOSTIC_GPIO_PIN = 27
DEBOUNCE_SECONDS = 0.15
# Software debounce applied on top of raw is_lifted reads by poll() -- see
# module docstring for why gpiozero's bounce_time isn't sufficient here.
POLL_DEBOUNCE_SECONDS = 0.2


class HookSwitchError(RuntimeError):
    """Raised when the hook switch GPIO can't be initialized or read."""


class HookSwitch:
    def __init__(
        self,
        pin: int = HOOK_SWITCH_GPIO_PIN,
        diagnostic_pin: int | None = HOOK_SWITCH_DIAGNOSTIC_GPIO_PIN,
        bounce_time: float = DEBOUNCE_SECONDS,
        poll_debounce_seconds: float = POLL_DEBOUNCE_SECONDS,
        logger: logging.Logger | None = None,
        pin_factory=None,
        invert: bool = False,
    ) -> None:
        self._logger = logger or logging.getLogger(__name__)
        self._poll_debounce_seconds = poll_debounce_seconds
        self._confirmed_lifted: bool | None = None
        self._pending_lifted: bool | None = None
        self._pending_since: float | None = None
        # See module docstring: False assumes a normally-open switch
        # (raw LOW/is_pressed=True == lifted); True flips that for a
        # normally-closed one, wired the same GND-only way.
        self._invert = invert

        if Button is None:
            raise HookSwitchError(
                "gpiozero is not installed (pip install gpiozero, plus a pin "
                "factory backend such as lgpio)"
            )

        button_kwargs = {"pull_up": True, "bounce_time": bounce_time}
        if pin_factory is not None:
            button_kwargs["pin_factory"] = pin_factory

        try:
            self._button = Button(pin, **button_kwargs)
        except Exception as exc:
            raise HookSwitchError(f"could not initialize GPIO pin {pin}: {exc}") from exc

        self._diagnostic_button = None
        if diagnostic_pin is not None:
            try:
                self._diagnostic_button = Button(diagnostic_pin, **button_kwargs)
            except Exception as exc:
                self._logger.warning(
                    "could not initialize diagnostic GPIO pin %d (non-fatal): %s",
                    diagnostic_pin,
                    exc,
                )
                self._diagnostic_button = None

    @property
    def is_lifted(self) -> bool:
        """Raw, instantaneous read (no debounce) -- for diagnostics.

        Callers doing edge detection (start/stop on lift/hang-up) should
        use poll() instead: a single raw read taken at a fast, tight
        polling rate can catch mechanical switch bounce mid-transition.
        Already accounts for `invert` -- True always means "lifted",
        regardless of the switch's electrical polarity.
        """
        try:
            raw = bool(self._button.is_pressed)
        except Exception as exc:
            raise HookSwitchError(f"could not read hook switch state: {exc}") from exc
        return (not raw) if self._invert else raw

    def poll(self, now: float) -> bool:
        """Debounced hook state, safe to call every frame at any poll rate.

        A raw is_lifted() reading must hold steady for
        poll_debounce_seconds before poll() reports a changed state -- this
        is the actual bounce filter (see module docstring: gpiozero's
        bounce_time does not cover plain is_pressed reads). `now` is the
        caller's clock (time.monotonic()), passed in rather than read here
        so this stays trivially testable without real time passing.
        """
        raw = self.is_lifted

        if self._confirmed_lifted is None:
            self._confirmed_lifted = raw
            return self._confirmed_lifted

        if raw == self._confirmed_lifted:
            self._pending_lifted = None
            self._pending_since = None
            return self._confirmed_lifted

        if raw != self._pending_lifted:
            self._pending_lifted = raw
            self._pending_since = now
        elif now - self._pending_since >= self._poll_debounce_seconds:
            self._confirmed_lifted = raw
            self._pending_lifted = None
            self._pending_since = None

        return self._confirmed_lifted

    @property
    def diagnostic_is_active(self) -> bool | None:
        """GPIO27 raw state, for logging only. None if unavailable."""
        if self._diagnostic_button is None:
            return None
        try:
            return bool(self._diagnostic_button.is_pressed)
        except Exception:
            return None

    def close(self) -> None:
        try:
            self._button.close()
        except Exception:
            self._logger.exception("error closing hook switch GPIO pin")
        if self._diagnostic_button is not None:
            try:
                self._diagnostic_button.close()
            except Exception:
                self._logger.exception("error closing diagnostic GPIO pin")
