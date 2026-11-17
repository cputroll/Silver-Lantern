import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import launcher
from test_game_sync import h


class DesktopFullscreenTests(unittest.TestCase):
    def test_known_folder_api_result(self):
        import ctypes
        from unittest.mock import MagicMock
        shell = MagicMock(); ole = MagicMock(); ole.CoInitializeEx.return_value = 0
        buffer = ctypes.create_unicode_buffer('C:\\Redirected\\Bureau')
        def get_path(guid, flags, token, out):
            ctypes.cast(out, ctypes.POINTER(ctypes.c_wchar_p))[0] = ctypes.cast(buffer, ctypes.c_wchar_p)
            return 0
        shell.SHGetKnownFolderPath.side_effect = get_path
        with patch.object(launcher.ctypes, 'windll', create=True) as dll:
            dll.shell32 = shell; dll.ole32 = ole
            self.assertEqual(str(launcher._known_desktop()), r'C:\Redirected\Bureau')
        ole.CoUninitialize.assert_called_once()
        ole.CoTaskMemFree.assert_called_once()

    def test_redirected_desktop_and_fallback(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(launcher.os.environ, {'USERPROFILE': d}):
            redirected = Path(d) / 'OneDrive' / 'Bureau'
            with patch.object(launcher, '_known_desktop', return_value=redirected):
                self.assertEqual(launcher.shortcut_path().parent, redirected)
            with patch.object(launcher, '_known_desktop', return_value=None):
                self.assertEqual(launcher.shortcut_path().parent, Path(d) / 'Desktop')

    def test_uninstall_removes_redirected_shortcut(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); desktop = root / 'redirected'; desktop.mkdir()
            link = desktop / (launcher.GAME_NAME + '.lnk'); link.write_text('x')
            with patch.object(launcher, '_known_desktop', return_value=desktop), patch.object(launcher, 'load_game_folder', return_value=None), patch.object(launcher, 'remove_defender_exclusion', return_value=True), patch.object(launcher, 'remove_registration'), patch.object(launcher, 'schedule_owned_cleanup'), patch.object(launcher, 'STATE_DIR', root / 'state'), patch.object(launcher, 'start_menu_path', return_value=root / 'start.lnk'), patch('builtins.input', return_value='y'):
                self.assertTrue(launcher.uninstall())
            self.assertFalse(link.exists())

    def test_fullscreen_preset_values(self):
        self.assertEqual(h.PRESET_DLSS['fullscreen'], '1')
        self.assertEqual(h.PRESET_FSR['fullscreen'], '1')
