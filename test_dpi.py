import unittest
from unittest import mock

import launcher


class DpiAwarenessTests(unittest.TestCase):
    def setUp(self):
        self._orig = launcher._DPI_AWARE
        launcher._DPI_AWARE = False
        self.addCleanup(self._restore)

    def _restore(self):
        launcher._DPI_AWARE = self._orig

    def test_requests_per_monitor_v2(self):
        with mock.patch.object(launcher.os, 'name', 'nt'), \
             mock.patch.object(launcher.ctypes, 'windll', create=True) as windll:
            windll.user32.SetProcessDpiAwarenessContext.return_value = 1
            launcher.enable_dpi_awareness()
            windll.user32.SetProcessDpiAwarenessContext.assert_called_once()

    def test_is_idempotent(self):
        with mock.patch.object(launcher.os, 'name', 'nt'), \
             mock.patch.object(launcher.ctypes, 'windll', create=True) as windll:
            windll.user32.SetProcessDpiAwarenessContext.return_value = 1
            launcher.enable_dpi_awareness()
            launcher.enable_dpi_awareness()
            self.assertEqual(windll.user32.SetProcessDpiAwarenessContext.call_count, 1)

    def test_falls_back_to_older_per_monitor_api(self):
        with mock.patch.object(launcher.os, 'name', 'nt'), \
             mock.patch.object(launcher.ctypes, 'windll', create=True) as windll:
            windll.user32.SetProcessDpiAwarenessContext.side_effect = AttributeError
            windll.shcore.SetProcessDpiAwareness.return_value = 0
            launcher.enable_dpi_awareness()
            windll.shcore.SetProcessDpiAwareness.assert_called_once_with(2)

    def test_falls_back_to_system_aware(self):
        with mock.patch.object(launcher.os, 'name', 'nt'), \
             mock.patch.object(launcher.ctypes, 'windll', create=True) as windll:
            windll.user32.SetProcessDpiAwarenessContext.side_effect = AttributeError
            windll.shcore.SetProcessDpiAwareness.side_effect = AttributeError
            launcher.enable_dpi_awareness()
            windll.user32.SetProcessDPIAware.assert_called_once()

    def test_non_windows_is_a_noop(self):
        with mock.patch.object(launcher.os, 'name', 'posix'), \
             mock.patch.object(launcher.ctypes, 'windll', create=True) as windll:
            launcher.enable_dpi_awareness()
            windll.user32.SetProcessDpiAwarenessContext.assert_not_called()


if __name__ == '__main__':
    unittest.main()
