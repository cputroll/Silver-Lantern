"""Track the desired and observed state of the launcher window.

Call WindowController.apply() from the launcher UI loop when the game starts
or ends, passing the SDL window wrapper, and call WindowController.observe()
when SDL reports a user-driven minimize or taskbar restore.
"""

from typing import Literal


WindowState = Literal["normal", "minimized"]


class WindowController:
    def __init__(self):
        self._state: WindowState = "normal"

    @property
    def state(self) -> WindowState:
        return self._state

    def observe(self, state: WindowState) -> WindowState:
        """Record an OS/user window transition such as taskbar restore."""
        self._state = state
        return self._state

    def minimize(self, window):
        minimize = getattr(window, "minimize", None)
        if callable(minimize):
            minimize()

    def restore(self, window):
        restore = getattr(window, "restore", None)
        if callable(restore):
            restore()
        raise_window = getattr(window, "raise_window", None)
        if callable(raise_window):
            raise_window()

    def apply(self, state: WindowState, window) -> WindowState:
        if state == self._state:
            return self._state

        if state == "minimized":
            self.minimize(window)
        else:
            self.restore(window)
        self._state = state
        return state
