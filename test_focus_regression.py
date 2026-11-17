import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import launcher


class FocusRegressionTests(unittest.TestCase):
    def test_child_dot_leaders_are_cleaned_but_log_is_raw(self):
        lines = ['Game file list.......... up to date\n', 'Free space on C: . . . . not enough\n']
        process = mock.Mock(stdout=io.StringIO(''.join(lines)))
        process.wait.return_value = 0
        events = mock.Mock()
        events.cancelled.is_set.return_value = False
        with tempfile.TemporaryDirectory() as d, mock.patch.object(launcher, 'STATE_DIR', Path(d)), mock.patch.object(launcher, 'LOG', Path(d) / 'log'), mock.patch.object(launcher.subprocess, 'Popen', return_value=process):
            launcher.worker_command(events, ['install'])
            self.assertEqual((Path(d) / 'log').read_text(), ''.join(lines))
        values = [c.args[1]['text'] for c in events.publish.call_args_list if c.args[0] == 'progress']
        self.assertEqual(values, ['Game file list: up to date', 'Free space on C: not enough'])

    def test_hover_does_not_move_controller_focus(self):
        with mock.patch.object(launcher.LauncherUI, 'check_updates'):
            ui = launcher.LauncherUI()
        ui.state = launcher.State.READY
        ui.hits = [(0, 0, 100, 40, 0), (0, 50, 100, 40, 1)]
        ui.focus = 0
        ui.pointer(10, 60)
        self.assertEqual(ui.focus, 0)
        self.assertEqual(ui.hover, 1)

    def test_focus_ring_follows_navigation_in_all_screens(self):
        with mock.patch.object(launcher.LauncherUI, 'check_updates'):
            ui = launcher.LauncherUI()
        painter = mock.MagicMock()
        painter.height.return_value = 20
        painter.measure.return_value = (20, 20)
        painter.fit_middle.side_effect = lambda role, value, width: str(value)
        painter.s.SDL_Rect.side_effect = lambda *args: args
        for name, setup in ui.screenshot_scenes():
            setup()
            if not ui.buttons():
                continue
            with self.subTest(scene=name):
                mapper = launcher.InputMapper()
                for action in mapper.feed({'type': 'axis', 'x': 0, 'y': 1}, 0):
                    ui.action(action)
                painter.reset_mock()
                ui.render(painter, 0.016)
                target = next((h for h in ui.hits if h[4] == ui.focus), None)
                self.assertIsNotNone(target)
                x, y, w, h, _ = target
                self.assertTrue(any(abs(c.args[0] - (x - launcher.FOCUS_GAP)) < 1 and abs(c.args[1] - (y - launcher.FOCUS_GAP)) < 1 for c in painter.ring.call_args_list), (name, target, painter.ring.call_args_list))
