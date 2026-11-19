import tempfile
import unittest
from unittest.mock import Mock, patch
import launcher


class ComboTests(unittest.TestCase):
    def test_continuous_hold_and_rearm(self):
        hold = launcher.MenuCombo()
        self.assertFalse(hold.update(True, False, 0))
        self.assertFalse(hold.update(True, True, 1))
        self.assertFalse(hold.update(True, True, 2.99))
        self.assertTrue(hold.update(True, True, 3))
        self.assertFalse(hold.update(True, True, 10))
        self.assertFalse(hold.update(False, True, 11))
        self.assertFalse(hold.update(True, True, 12))
        self.assertTrue(hold.update(True, True, 14))

    def test_early_release_cancels(self):
        hold = launcher.MenuCombo()
        hold.update(True, True, 0)
        hold.update(False, True, 1)
        self.assertFalse(hold.update(True, True, 2))
        self.assertTrue(hold.update(True, True, 4))

    def test_key_choice(self):
        for setting, key in ((0, 0x2d), (1, 0x2d), (2, 0x70)):
            with patch.object(launcher.game_settings, 'read_values', return_value={'menu_shortcut': setting}):
                self.assertEqual(launcher.menu_combo_key('unused'), key)

    def test_game_exit_and_focus_gate(self):
        process = Mock(pid=42)
        process.poll.return_value = None
        watcher = launcher.ComboWatcher(process, 'unused')
        with patch.object(launcher, 'game_is_foreground', return_value=False), patch.object(launcher, 'send_menu_key') as send:
            watcher.tick({1: (True, True)}, 0)
            watcher.tick({1: (True, True)}, 3)
            send.assert_not_called()
        process.poll.return_value = 0
        self.assertFalse(watcher.tick({1: (True, True)}, 4))

    def test_foreground_hold_sends_once_and_stops_after_exit(self):
        process = Mock(pid=42)
        process.poll.return_value = None
        watcher = launcher.ComboWatcher(process, 'unused')
        with patch.object(launcher, 'game_is_foreground', return_value=True), patch.object(launcher, 'menu_combo_key', return_value=0x70), patch.object(launcher, 'send_menu_key') as send:
            watcher.tick({1: (True, True)}, 0)
            watcher.tick({1: (True, True)}, 2)
            watcher.tick({1: (True, True)}, 4)
            send.assert_called_once_with(42, 0x70)
            process.poll.return_value = 0
            self.assertFalse(watcher.tick({1: (True, True)}, 5))
            send.assert_called_once()

    def test_focus_loss_cancels_hold(self):
        process = Mock(pid=42)
        process.poll.return_value = None
        watcher = launcher.ComboWatcher(process, 'unused')
        with patch.object(launcher, 'game_is_foreground', side_effect=[True, False, True, True]), patch.object(launcher, 'menu_combo_key', return_value=0x2d), patch.object(launcher, 'send_menu_key') as send:
            watcher.tick({1: (True, True)}, 0)
            watcher.tick({1: (True, True)}, 1)
            watcher.tick({1: (True, True)}, 4)
            send.assert_not_called()
            watcher.tick({1: (True, True)}, 6)
            send.assert_called_once_with(42, 0x2d)

    def test_xinput_poll_and_watch_sequence(self):
        process = Mock(pid=42)
        process.poll.side_effect = [None] * 8 + [0]
        samples = iter([0x0010, 0x0030, 0x0030, 0x0030])
        def get_state(index, pointer):
            if index:
                return 1167
            pointer._obj.Gamepad.wButtons = next(samples)
            return 0
        clock = iter([0, 1, 3, 4])
        with patch.object(launcher, 'load_xinput', return_value=get_state), patch.object(launcher, 'game_is_foreground', return_value=True), patch.object(launcher, 'menu_combo_key', return_value=0x2d), patch.object(launcher, 'send_menu_key') as send, patch.object(launcher.time, 'monotonic', side_effect=lambda: next(clock)), patch.object(launcher.time, 'sleep') as sleep:
            launcher.watch_controller_combo(process, 'unused')
        send.assert_called_once_with(42, 0x2d)
        self.assertEqual(sleep.call_count, 4)
        sleep.assert_called_with(0.05)

    def test_launcher_releases_sdl_handles_before_starting_game(self):
        with patch.object(launcher.LauncherUI, 'check_updates'):
            ui = launcher.LauncherUI()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        ui.folder = launcher.Path(folder.name)
        (ui.folder / 'HeyListen.exe').touch()
        ui.buttons = Mock(return_value=['Play'])
        ui.focus = 0
        order = []
        ui.close_controllers = lambda: order.append('close')
        process = Mock()
        def launch(*args, **kwargs):
            order.append('launch')
            return process
        with patch.object(launcher.subprocess, 'Popen', side_effect=launch), patch.object(launcher.threading, 'Thread'):
            ui.activate()
        self.assertEqual(order, ['close', 'launch'])
        self.assertEqual(ui.state, launcher.State.PLAYING)

    def test_none_safe_logging_on_error(self):
        process = Mock()
        process.poll.side_effect = RuntimeError('oops')
        watcher = launcher.ComboWatcher(process, 'unused')
        with patch.object(launcher.sys, 'stdout', None), patch.object(launcher, 'LOG') as log:
            log.parent.mkdir.return_value = None
            self.assertFalse(watcher.tick({}, 0))


if __name__ == '__main__':
    unittest.main()
