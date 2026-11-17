import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import game_settings as gs
import launcher
from launcher import LauncherUI


class GraphicsScreenTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.folder = Path(self.td.name)
        (self.folder / gs.INI_NAME).write_text('pad_a=3\ndlss=3\nframegen=1\n', encoding='ascii')
        with patch.object(LauncherUI, 'check_updates'):
            self.ui = LauncherUI()
        self.ui.folder = self.folder
        self.ui.set_screen('settings')
        self.ui.focus = self.ui.buttons().index('Graphics')
        self.ui.activate()

    def tearDown(self):
        self.td.cleanup()

    def focus_key(self, key):
        self.ui.focus = len(LauncherUI.GFX_ACTIONS) + [o.key for o in gs.OPTIONS].index(key)

    def values(self):
        return gs.read_values(self.folder)

    def test_settings_opens_graphics_on_first_option(self):
        self.assertEqual(self.ui.screen, 'graphics')
        self.assertEqual(self.ui.gfx_option().key, gs.OPTIONS[0].key)
        self.assertEqual(len(self.ui.buttons()), len(gs.OPTIONS) + len(LauncherUI.GFX_ACTIONS))
        self.assertEqual(self.ui.gfx_values['dlss'], 3)

    def test_a_cycles_short_option_and_persists(self):
        self.focus_key('fullscreen')
        self.ui.action('accept')
        self.assertEqual(self.values()['fullscreen'], 1)
        self.ui.action('accept')
        self.assertEqual(self.values()['fullscreen'], 0)
        text = (self.folder / gs.INI_NAME).read_text(encoding='ascii')
        self.assertIn('pad_a=3', text)
        self.assertNotIn('framegen=', text)

    def test_left_right_adjust_with_bounds(self):
        self.focus_key('sharpness')
        self.ui.action('left')
        self.assertEqual(self.values()['sharpness'], 100)
        self.ui.action('right')
        self.assertEqual(self.values()['sharpness'], 0)

    def test_long_option_opens_picker_and_chooses(self):
        self.focus_key('frame_generation')
        self.ui.action('accept')
        self.assertEqual(self.ui.gfx_picker, 'frame_generation')
        self.assertEqual(self.ui.buttons()[0], 'Off')
        self.ui.action('down'); self.ui.action('down')
        self.ui.action('accept')
        self.assertIsNone(self.ui.gfx_picker)
        self.assertEqual(self.values()['frame_generation'], 2)
        self.assertEqual(self.ui.gfx_option().key, 'frame_generation')

    def test_picker_back_cancels_without_writing(self):
        self.focus_key('frame_generation')
        self.ui.action('accept'); self.ui.action('down'); self.ui.action('back')
        self.assertIsNone(self.ui.gfx_picker)
        self.assertEqual(self.values()['frame_generation'], 0)
        self.ui.action('back')
        self.assertEqual(self.ui.screen, 'settings')
        self.assertEqual(self.ui.buttons()[self.ui.focus], 'Graphics')

    def test_dlss_skipped_without_runtime(self):
        self.ui.gfx_dlss_ok = False
        self.focus_key('dlss')
        self.ui.action('left')
        self.assertEqual(self.values()['dlss'], 0)
        self.ui.action('right')
        self.assertEqual(self.values()['dlss'], 3)

    def test_reset_and_recommended(self):
        self.ui.focus = LauncherUI.GFX_ACTIONS.index('Reset to game defaults')
        self.ui.action('accept')
        self.assertEqual(self.values(), {o.key: o.default for o in gs.OPTIONS})
        self.ui.focus = LauncherUI.GFX_ACTIONS.index('Recommended for this PC')
        with patch.object(gs, 'choose_preset', return_value=(gs.PRESET_FSR, 'x -> FSR 3.1 Performance')), \
             patch.object(launcher.threading, 'Thread') as thread:
            self.ui.action('accept')
            thread.call_args.kwargs['target']()
        self.ui.drain()
        self.assertEqual(self.values()['dlss'], 3)
        self.assertEqual(self.ui.gfx_values['frame_generation'], 1)
        self.assertIn('FSR 3.1 Performance', self.ui.status)

    def test_write_failure_reports_status(self):
        self.focus_key('fullscreen')
        with patch.object(gs, 'write_values', side_effect=OSError('denied')):
            self.ui.action('accept')
        self.assertIn("Couldn't save", self.ui.status)


if __name__ == '__main__':
    unittest.main()
