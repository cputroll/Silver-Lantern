"""Time-based brightness and gentle drift for the ready-screen shortcut hints."""

import math

_PERIOD = 5.0
_DRIFT = 1.25
_BASE_ALPHA = 230
_ALPHA_SWING = 25


def glow(t: float, reduced_motion: bool = False) -> tuple[int, float]:
    """Return shortcut highlight alpha and vertical drift for elapsed seconds t.

    When reduced_motion is True (or t is non-finite), returns full steady
    highlight brightness and zero vertical drift.
    """
    if reduced_motion:
        return 255, 0.0
    try:
        if not math.isfinite(t):
            return 255, 0.0
        phase = (t % _PERIOD) / _PERIOD
    except OverflowError:
        return 255, 0.0
    wave = math.sin(math.tau * phase)
    return round(_BASE_ALPHA + _ALPHA_SWING * wave), _DRIFT * wave
