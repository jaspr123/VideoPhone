"""Physical receiver hook-switch input (PROJECT_SPEC.md section 4).

Wiring (as documented in the spec):
    Black wire -> physical pin 9  -> GND
    Red wire   -> physical pin 11 -> GPIO17
    Green wire -> physical pin 13 -> GPIO27

GPIO17 is the primary, production receiver-state signal, using the GPIO's
internal pull-up resistor (never wire the switch to 5V or 3.3V):
    stable LOW  -> receiver lifted (off-hook)
    stable HIGH -> receiver on-hook

GPIO27 was observed to stay low regardless of hook state during initial
testing and is treated as diagnostic-only, not used for production
start/stop logic, per the spec's explicit caution ("should be treated as
diagnostic until the switch is re-tested").

Debouncing (~100-200ms per the spec) is handled by gpiozero's Button
bounce_time, so a brief middle/open transition during the mechanical
switch's travel is filtered out at the hardware-polling level rather than
needing extra debounce logic here.
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


class HookSwitchError(RuntimeError):
    """Raised when the hook switch GPIO can't be initialized or read."""


class HookSwitch:
    def __init__(
        self,
        pin: int = HOOK_SWITCH_GPIO_PIN,
        diagnostic_pin: int | None = HOOK_SWITCH_DIAGNOSTIC_GPIO_PIN,
        bounce_time: float = DEBOUNCE_SECONDS,
        logger: logging.Logger | None = None,
        pin_factory=None,
    ) -> None:
        self._logger = logger or logging.getLogger(__name__)

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
        """True when the receiver is off-hook (GPIO17 stable LOW)."""
        try:
            return bool(self._button.is_pressed)
        except Exception as exc:
            raise HookSwitchError(f"could not read hook switch state: {exc}") from exc

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
