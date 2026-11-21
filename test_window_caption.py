import ctypes
import unittest
from unittest.mock import Mock, patch

import launcher


class WindowCaptionTests(unittest.TestCase):
    def test_windows_caption_is_black_with_white_text(self):
        calls = []

        def set_attribute(hwnd, attribute, value, size):
            calls.append((hwnd, attribute, ctypes.cast(value, ctypes.POINTER(ctypes.c_uint32)).contents.value))
            return 0

        with patch.object(launcher.ctypes, 'windll', Mock(), create=True) as dll:
            dll.dwmapi.DwmSetWindowAttribute.side_effect = set_attribute
            getattr(launcher, '_set_black_caption', lambda hwnd: None)(123)
        self.assertEqual(calls, [(123, 20, 1), (123, 35, 0), (123, 36, 0xffffff)])
