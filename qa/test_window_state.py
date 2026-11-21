import unittest

from window_state import WindowController


class FakeWindow:
    def __init__(self):
        self.calls = []

    def minimize(self):
        self.calls.append("minimize")

    def restore(self):
        self.calls.append("restore")


class RaisableWindow(FakeWindow):
    def raise_window(self):
        self.calls.append("raise")


class WindowControllerTests(unittest.TestCase):
    def test_apply_calls_wrapper_only_when_desired_state_changes(self):
        controller = WindowController()
        window = FakeWindow()

        self.assertEqual(controller.apply("normal", window), "normal")
        self.assertEqual(window.calls, [])
        self.assertEqual(controller.apply("minimized", window), "minimized")
        self.assertEqual(controller.apply("minimized", window), "minimized")
        self.assertEqual(window.calls, ["minimize"])
        self.assertEqual(controller.apply("normal", window), "normal")
        self.assertEqual(controller.apply("normal", window), "normal")
        self.assertEqual(window.calls, ["minimize", "restore"])

    def test_apply_tracks_desired_state_without_wrapper_methods(self):
        controller = WindowController()
        window = FakeWindow()

        self.assertEqual(controller.apply("minimized", object()), "minimized")
        self.assertEqual(controller.apply("minimized", window), "minimized")
        self.assertEqual(window.calls, [])
        self.assertEqual(controller.apply("normal", object()), "normal")
        self.assertEqual(controller.apply("normal", window), "normal")
        self.assertEqual(window.calls, [])

    def test_direct_methods_delegate_to_wrapper(self):
        controller = WindowController()
        window = FakeWindow()

        controller.minimize(window)
        controller.restore(window)

        self.assertEqual(window.calls, ["minimize", "restore"])

    def test_observe_taskbar_restore_and_reminimize_keeps_transitions_accurate(self):
        controller = WindowController()
        window = RaisableWindow()

        controller.apply("minimized", window)
        self.assertEqual(controller.state, "minimized")
        self.assertEqual(window.calls, ["minimize"])

        # User restores the launcher from the Windows taskbar while the game is running.
        controller.observe("normal")
        self.assertEqual(controller.state, "normal")
        # Game exit sees window already restored; does not call restore redundantly.
        controller.apply("normal", window)
        self.assertEqual(window.calls, ["minimize"])
        # Next game launch minimizes again because observed state was normal.
        controller.apply("minimized", window)
        self.assertEqual(window.calls, ["minimize", "minimize"])

        # User restores then manually minimizes again; game exit restores and raises.
        controller.observe("normal")
        controller.observe("minimized")
        controller.apply("normal", window)
        self.assertEqual(window.calls, ["minimize", "minimize", "restore", "raise"])
