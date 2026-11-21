import unittest
from pathlib import Path
from unittest.mock import patch

import launcher


def make_ui(**attrs):
    with patch.object(launcher, 'resolve_game_folder', return_value=Path('C:/Games/Ocarina of Time')), \
            patch.object(launcher, 'installed', return_value=True), \
            patch.object(launcher.LauncherUI, 'check_updates'), \
            patch.object(launcher.LauncherUI, 'shortcut_worker'), \
            patch.object(launcher.threading, 'Thread'):
        ui = launcher.LauncherUI()
    for key, value in attrs.items():
        setattr(ui, key, value)
    return ui


class PlayingStateTests(unittest.TestCase):
    def test_external_game_start_sets_playing_and_minimizes(self):
        ui = make_ui()
        self.assertEqual(ui.state, launcher.State.READY)
        ui._external_game_started(r'C:\Games\Ocarina of Time\bin\HeyListen.exe')
        self.assertEqual(ui.state, launcher.State.PLAYING)
        self.assertEqual(ui.status, 'Playing...')
        self.assertEqual(ui.screen, 'main')
        self.assertEqual(ui.focus, 0)
        self.assertIsNotNone(ui.quit_button)
        self.assertEqual(ui._window_requests, ['minimized'])

    def test_external_game_end_publishes_game_done_and_restores(self):
        ui = make_ui()
        ui._external_game_started(r'C:\Games\Ocarina of Time\bin\HeyListen.exe')
        ui._window_requests.clear()
        ui._external_game_ended()
        with patch.object(launcher.LauncherUI, 'check_updates'):
            ui.drain()
        self.assertEqual(ui.state, launcher.State.READY)
        self.assertEqual(ui.status, 'Ready to play.')
        self.assertIsNone(ui.quit_button)
        self.assertEqual(ui._window_requests, ['normal'])

    def test_own_game_end_does_not_fire_external_handler(self):
        ui = make_ui()
        ui._own_game_process = object()
        ui._external_game_ended()
        ui.drain()
        self.assertEqual(ui.state, launcher.State.READY)

    def test_quit_game_gracefully_closes_own_process_without_terminate(self):
        class FakeProcess:
            def __init__(self):
                self.pid = 4242
                self.terminated = False

            def poll(self):
                return None

            def terminate(self):
                self.terminated = True
                raise AssertionError('must not forcefully terminate game process')

        ui = make_ui()
        process = FakeProcess()
        target = r'C:\Games\Ocarina of Time\bin\HeyListen.exe'
        ui._own_game_process = process
        ui._own_game_exe = target
        with patch.object(launcher.game_state, 'close_game', return_value=1) as close_game:
            ui._quit_game()
        close_game.assert_called_once_with(Path(ui.folder), target, pid=4242)
        self.assertFalse(process.terminated)
        self.assertEqual(ui.status, 'Closing game…')

    def test_quit_game_external_gracefully_closes_only_the_exact_game_exe(self):
        ui = make_ui()
        target = Path(r'C:\Games\Ocarina of Time\bin\HeyListen.exe')
        with patch.object(launcher, 'game_play_target', return_value=target), \
                patch.object(launcher.game_state, 'close_game', return_value=1) as close_game:
            ui._quit_game()
        close_game.assert_called_once_with(Path(ui.folder), str(target))
        self.assertEqual(ui.status, 'Closing game…')

    def test_game_done_clears_own_process_and_restores_window(self):
        ui = make_ui()
        ui._own_game_process = object()
        ui._own_game_exe = r'C:\Games\Ocarina of Time\bin\HeyListen.exe'
        ui.events.publish('game_done', None)
        with patch.object(launcher.LauncherUI, 'check_updates'):
            ui.drain()
        self.assertIsNone(ui._own_game_process)
        self.assertIsNone(ui._own_game_exe)
        self.assertEqual(ui._window_requests, ['normal'])

    def test_window_requests_apply_once(self):
        ui = make_ui()
        applied = []
        window = type('W', (), {'minimize': lambda self: applied.append('minimized'),
                                'restore': lambda self: applied.append('restored')})()
        ui._request_window('minimized')
        ui._request_window('minimized')
        while ui._window_requests:
            ui._window_control.apply(ui._window_requests.pop(0), window)
        self.assertEqual(applied, ['minimized'])
        ui._request_window('normal')
        ui._window_control.apply(ui._window_requests.pop(0), window)
        self.assertEqual(applied, ['minimized', 'restored'])

    def test_quit_button_hit_quits_without_activating_rows(self):
        ui = make_ui()
        ui.state = launcher.State.PLAYING
        ui.quit_button = launcher.top_right(launcher.CANVAS[0], launcher.MARGIN)
        left, top, width, height = ui.quit_button.rect
        with patch.object(ui, '_quit_game') as quit_game, patch.object(ui, 'activate') as activate:
            ui.pointer(left + width // 2, top + height // 2, click=True)
        quit_game.assert_called_once()
        activate.assert_not_called()

    def test_quit_button_miss_falls_through_to_rows(self):
        ui = make_ui()
        ui.state = launcher.State.PLAYING
        ui.quit_button = launcher.top_right(launcher.CANVAS[0], launcher.MARGIN)
        ui.hits = [(0, 0, 100, 40, 1)]
        with patch.object(ui, '_quit_game') as quit_game, \
                patch.object(ui, 'activate') as activate:
            ui.pointer(50, 20, click=True)
        quit_game.assert_not_called()
        activate.assert_called_once()

    def test_external_start_during_download_is_ignored(self):
        ui = make_ui()
        ui.state = launcher.State.DOWNLOADING
        ui.operation = 'download'
        ui._external_game_started(r'C:\Games\Ocarina of Time\bin\HeyListen.exe')
        self.assertEqual(ui.state, launcher.State.DOWNLOADING)
        self.assertEqual(ui._window_requests, [])

    def test_watch_poll_gating(self):
        ui = make_ui()
        self.assertTrue(ui._watch_should_poll())  # READY, no operation
        ui.operation = 'check'
        self.assertTrue(ui._watch_should_poll())  # offline check error does not block watching
        ui.operation = None
        ui.state = launcher.State.PLAYING
        self.assertTrue(ui._watch_should_poll())  # external game running: keep polling for exit
        ui.operation = 'check'
        self.assertTrue(ui._watch_should_poll())  # offline check while playing still tracks exit
        ui._own_game_process = object()
        self.assertFalse(ui._watch_should_poll())  # own wait thread handles it
        ui._own_game_process = None
        ui.operation = 'download'
        self.assertFalse(ui._watch_should_poll())
        ui.operation = None
        ui.state = launcher.State.DOWNLOADING
        self.assertFalse(ui._watch_should_poll())

    def test_game_watch_throttles_target_lookup_and_refreshes_on_ttl_or_folder_change(self):
        ui = make_ui()
        target = Path(r'C:\Games\Ocarina of Time\bin\HeyListen.exe')
        clock = [10.0]
        with patch.object(launcher.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(launcher, 'game_play_target', return_value=target) as lookup:
            first = ui._game_watch()
            for _ in range(10):
                clock[0] += 0.1
                self.assertIs(ui._game_watch(), first)
            self.assertEqual(lookup.call_count, 1)
            self.assertEqual(first._exe, str(target))

            # Advance past cache TTL -> refreshes once.
            clock[0] = 16.0
            lookup.return_value = None
            self.assertIsNone(ui._game_watch())
            self.assertEqual(lookup.call_count, 2)
            # Negative lookup is throttled too.
            clock[0] = 16.5
            self.assertIsNone(ui._game_watch())
            self.assertEqual(lookup.call_count, 2)

            # Changing folder invalidates cache immediately.
            ui.folder = Path(r'D:\Games\Ocarina of Time')
            lookup.return_value = Path(r'D:\Games\Ocarina of Time\bin\HeyListen.exe')
            updated = ui._game_watch()
            self.assertEqual(lookup.call_count, 3)
            self.assertEqual(updated._exe, str(lookup.return_value))

    def test_external_game_keeps_tracking_through_exit_even_if_target_disappears_or_offline(self):
        ui = make_ui()
        target = Path(r'C:\Games\Ocarina of Time\bin\HeyListen.exe')
        clock = [0.0]
        with patch.object(launcher.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(launcher.game_state.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(launcher, 'game_play_target', return_value=target) as lookup, \
                patch.object(launcher.game_state, 'running_game', side_effect=[str(target), str(target), None]):
            watch = ui._game_watch()
            watch.poll()
            self.assertEqual(ui.state, launcher.State.PLAYING)
            self.assertEqual(ui._external_game_exe, str(target))
            self.assertEqual(lookup.call_count, 1)

            # Offline event, folder change, and missing play target must NOT drop tracking.
            ui.events.publish('offline', 'No internet')
            ui.drain()
            self.assertEqual(ui.state, launcher.State.PLAYING)
            self.assertEqual(ui.screen, 'main')
            ui.folder = Path(r'D:\OtherFolder')
            lookup.return_value = None
            clock[0] = 10.0

            self.assertTrue(ui._watch_should_poll())
            same_watch = ui._game_watch()
            self.assertIs(same_watch, watch)
            self.assertEqual(lookup.call_count, 1)  # Did not re-lookup while actively tracking
            same_watch.poll()
            self.assertEqual(ui.state, launcher.State.PLAYING)

            # External game exits -> watch emits ended -> game_done restores READY + window.
            clock[0] = 11.5
            ui._window_requests.clear()
            same_watch.poll()
            with patch.object(launcher.LauncherUI, 'check_updates'):
                ui.drain()
            self.assertEqual(ui.state, launcher.State.READY)
            self.assertIsNone(ui._external_game_exe)
            self.assertEqual(ui._window_requests, ['normal'])

    def test_quit_is_reachable_after_taskbar_restore_by_keyboard_and_mouse(self):
        ui = make_ui()
        applied = []
        window = type('W', (), {'minimize': lambda self: applied.append('minimize'),
                                'restore': lambda self: applied.append('restore'),
                                'raise_window': lambda self: applied.append('raise')})()
        target = r'C:\Games\Ocarina of Time\bin\HeyListen.exe'

        ui.tool_update_available = True  # Must not hide Quit while playing
        ui._external_game_started(target)
        while ui._window_requests:
            ui._window_control.apply(ui._window_requests.pop(0), window)
        self.assertEqual(applied, ['minimize'])

        # User clicks taskbar icon to restore the launcher window.
        ui._on_window_restored()
        self.assertEqual(ui._window_control.state, 'normal')
        self.assertEqual(ui._window_requests, [])  # Never re-minimizes after taskbar restore
        self.assertEqual(ui.buttons(), ['Quit game', 'Settings'])
        self.assertEqual(ui.focus, 0)
        self.assertIsNotNone(ui.quit_button)

        # Reachable via keyboard Enter (action 'accept').
        with patch.object(launcher.game_state, 'close_game', return_value=1) as close_game:
            ui.action('accept')
        close_game.assert_called_once_with(Path(ui.folder), target)

        # Reachable via mouse click on top-right Quit button even after navigating to Settings.
        ui.set_screen('settings')
        painter = FakePainter()
        ui.render(painter, 0.016)
        self.assertIsNotNone(ui.quit_button)
        left, top, width, height = ui.quit_button.rect
        with patch.object(launcher.game_state, 'close_game', return_value=1) as close_game:
            ui.pointer(left + width // 2, top + height // 2, click=True)
        close_game.assert_called_once_with(Path(ui.folder), target)

    def test_quit_click_ignored_when_not_playing(self):
        ui = make_ui()
        ui.state = launcher.State.READY
        ui.quit_button = launcher.top_right(launcher.CANVAS[0], launcher.MARGIN)
        left, top, width, height = ui.quit_button.rect
        with patch.object(ui, '_quit_game') as quit_game, patch.object(ui, 'activate') as activate:
            ui.pointer(left + width // 2, top + height // 2, click=True)
        quit_game.assert_not_called()
        activate.assert_not_called()

    def test_start_back_hint_brightens_and_moves_and_respects_reduced_motion(self):
        ui = make_ui()
        ui.state = launcher.State.READY
        ui.reduce_motion = False
        ui.tip_motion_supported = True
        ui.hits = []

        painter = FakePainter()
        with patch.object(launcher.time, 'monotonic', return_value=0.0):
            ui.draw_hero(painter, 0.016)
        t0_combo = next(item for item in painter.texts if item[0] == 'Start + Back')
        t0_copy = next(item for item in painter.texts if item[0] == 'Hold ')
        t0_footer = painter.fills[-1]

        painter.fills.clear()
        painter.texts.clear()
        with patch.object(launcher.time, 'monotonic', return_value=1.25):
            ui.draw_hero(painter, 0.016)
        t1_combo = next(item for item in painter.texts if item[0] == 'Start + Back')
        t1_footer = painter.fills[-1]

        # Start + Back shortcut is brightened (INK) while copy stays muted (INK_DIM).
        self.assertEqual(t0_copy[3], launcher.INK_DIM)
        self.assertEqual(t0_combo[3], launcher.INK)
        self.assertEqual(t1_combo[3], launcher.INK)
        # Animated glow pulses brightness and gently drifts vertically.
        self.assertNotEqual(t0_combo[4], t1_combo[4])
        self.assertNotEqual(t0_combo[2], t1_combo[2])
        self.assertLessEqual(abs(t1_combo[2] - 88.0), 1.25)
        # Bottom footer hints stay static (not bouncing).
        self.assertEqual(t0_footer[1], t1_footer[1])
        self.assertEqual(t0_footer[4], 230)
        self.assertEqual(t1_footer[4], 230)

        # With reduced motion, Start + Back stays completely still and steadily bright.
        ui.reduce_motion = True
        painter.texts.clear()
        with patch.object(launcher.time, 'monotonic', return_value=1.25):
            ui.draw_hero(painter, 0.016)
        reduced_combo = next(item for item in painter.texts if item[0] == 'Start + Back')
        self.assertEqual(reduced_combo[2], 88.0)
        self.assertEqual(reduced_combo[3], launcher.INK)
        self.assertEqual(reduced_combo[4], 255)


class FakePainter:
    def __init__(self):
        self.fills = []
        self.rings = []
        self.texts = []

    def scene(self, scrim=0):
        pass

    def measure(self, style, text):
        return (8 * len(text), 16)

    def height(self, style):
        return 16

    def fit_middle(self, style, text, max_width):
        return text

    def fill(self, x, y, w, h, color, alpha=255, radius=0):
        self.fills.append((x, y, w, h, alpha))

    def ring(self, x, y, w, h, color, alpha=255, radius=0, thickness=2):
        self.rings.append((x, y, w, h, alpha))

    def circle(self, cx, cy, radius, color, alpha=255):
        pass

    def text(self, style, text, x, y, color=launcher.INK, max_width=None, align='left',
             alpha=255, middle=False, subpixel=False):
        self.texts.append((text, x, y, color, alpha))
        return 8 * len(text)


if __name__ == '__main__':
    unittest.main()
