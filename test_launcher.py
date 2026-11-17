import unittest
import launcher
import tempfile
from pathlib import Path
from unittest.mock import patch
import os
import subprocess
from launcher import create_shortcut, cli_command, LauncherUI, tool_update_worker
import sys
from launcher import App, IllegalTransition, State, InputMapper, resolve_folder, legacy_exists, ProgressEvents


class StateTests(unittest.TestCase):
    def test_screenshot_debug_renders_all_screens(self):
        with tempfile.TemporaryDirectory() as d:
            output = Path(d) / 'screen.bmp'
            result = subprocess.run([sys.executable, str(Path(launcher.__file__)), '--screenshot', str(output)], capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
            for name in ('main', 'progress', 'settings'):
                frame = output.with_name('screen-' + name + '.bmp')
                self.assertGreater(frame.stat().st_size, 0)

    def test_tool_worker_invocation_and_success(self):
        events = ProgressEvents()
        with patch('launcher.subprocess.run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = 'SILVERLANTERN_UPDATE_HANDOFF\n'
            tool_update_worker(events, ['--gui'])
        self.assertEqual(run.call_args.args[0], [sys.executable, '--self-update', '--gui'])
        self.assertEqual(events.queue.get_nowait(), ('tool_complete', 0))

    def test_tool_worker_already_current(self):
        events = ProgressEvents()
        with patch('launcher.subprocess.run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = 'Self-update check complete.\n'
            tool_update_worker(events, [])
        self.assertEqual(events.queue.get_nowait(), ('tool_current', 0))

    def test_tool_worker_failure(self):
        events = ProgressEvents()
        with patch('launcher.subprocess.run') as run:
            run.return_value.returncode = 7
            run.return_value.stdout = ''
            tool_update_worker(events, [])
        self.assertEqual(events.queue.get_nowait(), ('error', 'Tool update failed (exit 7).'))

    def test_tool_check_auto_and_manual(self):
        for auto in (True, False):
            with self.subTest(auto=auto), patch.object(LauncherUI, 'check_updates'):
                ui = LauncherUI()
                ui.config['auto_tool_update'] = auto
                ui.events.publish('tool_check', 'v999.0.0')
                with patch.object(ui, 'start_tool_update') as start:
                    ui.drain()
                self.assertEqual(start.called, auto)
                if not auto: self.assertIn('Update now', ui.buttons())

    def test_startup_check_failure_shows_retry_and_rechecks(self):
        with patch.object(LauncherUI, 'check_updates'):
            ui = LauncherUI()
            ui.events.publish('offline', 'Could not reach the update server')
            ui.drain()
            self.assertEqual(ui.screen, 'error')
            self.assertIn('Could not reach the update server', ui.last_error)
            self.assertIn('Retry', ui.buttons())
            ui.focus = ui.buttons().index('Retry')
            with patch.object(ui, 'check_updates') as check, patch.object(ui, 'start_operation') as install:
                ui.activate()
            check.assert_called_once()
            install.assert_not_called()

    def test_equal_tool_version_does_not_handoff(self):
        with patch.object(LauncherUI, 'check_updates'):
            ui = LauncherUI()
            ui.events.publish('tool_check', 'v' + launcher.APP_VERSION)
            with patch.object(ui, 'start_tool_update') as start:
                ui.drain()
            start.assert_not_called()
            self.assertIn('up to date', ui.status.lower())

    def test_newer_tool_version_handoffs_once(self):
        with patch.object(LauncherUI, 'check_updates'):
            ui = LauncherUI()
            ui.events.publish('tool_check', 'v999.0.0')
            ui.events.publish('tool_check', 'v999.0.0')
            with patch.object(ui, 'start_tool_update') as start:
                ui.drain()
            start.assert_called_once()

    def test_tool_error_opens_retry_screen(self):
        with patch.object(LauncherUI, 'check_updates'):
            ui = LauncherUI()
            ui.operation = 'tool'
            ui.events.publish('error', 'failed')
            ui.drain()
            self.assertEqual(ui.screen, 'error')
            self.assertIn('Retry', ui.buttons())
            self.assertIn('View log', ui.buttons())

    def test_tool_success_exits(self):
        with patch.object(LauncherUI, 'check_updates'):
            ui = LauncherUI()
            ui.events.publish('tool_complete', 0)
            ui.drain()
            self.assertFalse(ui.running)

    def test_frozen_cli_uses_same_executable(self):
        with patch.object(sys, 'frozen', True, create=True):
            self.assertEqual(cli_command('--version'), [sys.executable, '--cli', '--version'])

    def test_download_success(self):
        app = App(State.NOT_INSTALLED)
        app.transition(State.DOWNLOADING)
        app.transition(State.READY)
        self.assertEqual(app.state, State.READY)

    def test_illegal_transition(self):
        with self.assertRaises(IllegalTransition):
            App(State.NOT_INSTALLED).transition(State.PLAYING)

    def test_cancel_update(self):
        app = App(State.UPDATE_AVAILABLE)
        app.transition(State.UPDATING)
        app.transition(State.UPDATE_AVAILABLE)
        self.assertEqual(app.state, State.UPDATE_AVAILABLE)

    def test_button_release(self):
        mapper = InputMapper()
        self.assertEqual(mapper.feed({'type': 'button', 'button': 'a', 'down': True}, 0), [])
        self.assertEqual(mapper.feed({'type': 'button', 'button': 'a', 'down': False}, 0), ['accept'])

    def test_deadzone(self):
        self.assertEqual(InputMapper().feed({'type': 'axis', 'x': .2, 'y': .1}, 0), [])

    def test_repeat(self):
        mapper = InputMapper()
        self.assertEqual(mapper.feed({'type': 'axis', 'x': 0, 'y': 1}, 0), ['down'])
        self.assertEqual(mapper.tick(.39), [])
        self.assertEqual(mapper.tick(.41), ['down'])

    def test_default_folder(self):
        self.assertEqual(resolve_folder(Path('primary'), Path('fallback'), lambda p: None)[0], Path('primary'))

    def test_acl_fallback(self):
        def probe(path):
            if path == Path('primary'):
                raise PermissionError()
        self.assertEqual(resolve_folder(Path('primary'), Path('fallback'), probe)[0], Path('fallback'))

    def test_legacy_adoption(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            (folder / '.heylisten-parts').mkdir()
            self.assertTrue(legacy_exists(folder))

    def test_progress_queue(self):
        events = ProgressEvents()
        events.publish('progress', 42)
        self.assertEqual(events.queue.get_nowait(), ('progress', 42))

    @unittest.skipUnless(os.name == 'nt', 'Windows shortcuts require COM')
    def test_shortcut_redirected_desktop(self):
        with tempfile.TemporaryDirectory() as root:
            profile = Path(root)
            (profile / 'Desktop').mkdir()
            folder = profile / "game's folder"
            folder.mkdir()
            (folder / 'HeyListen.exe').touch()
            with patch.dict(os.environ, {'USERPROFILE': str(profile)}):
                create_shortcut(folder)
                self.assertTrue((profile / 'Desktop' / (launcher.GAME_NAME + '.lnk')).is_file())
                create_shortcut(folder, False)
                self.assertFalse((profile / 'Desktop' / (launcher.GAME_NAME + '.lnk')).exists())

    def test_cancellation(self):
        events = ProgressEvents()
        events.cancel()
        self.assertTrue(events.cancelled.is_set())
