import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import launcher
from test_game_sync import h


class CrashHotfixTests(unittest.TestCase):
    def test_log_with_no_stdout(self):
        with tempfile.TemporaryDirectory() as d, patch.object(launcher, 'LOG', Path(d) / 'launcher.log'), patch.object(launcher.sys, 'stdout', None):
            launcher.log_message('hello')
            self.assertIn('hello', (Path(d) / 'launcher.log').read_text())

    def test_link_creates_parent_and_retries(self):
        with tempfile.TemporaryDirectory() as d:
            link = Path(d) / 'missing' / 'game.lnk'
            with patch.object(launcher, 'powershell', side_effect=[subprocess.CalledProcessError(1, 'powershell', stderr=b'failure'), None]) as run:
                launcher.create_link(link, Path(d) / 'app.exe', Path(d))
            self.assertTrue(link.parent.is_dir())
            self.assertEqual(run.call_count, 2)

    def test_registration_failures_are_independent(self):
        reg = MagicMock(); reg.CreateKey.side_effect = OSError('registry denied')
        with tempfile.TemporaryDirectory() as d, patch.object(launcher, 'winreg', reg), patch.object(launcher, 'LOG', Path(d) / 'log'), patch.object(launcher, 'create_shortcut', side_effect=OSError('desktop denied')), patch.object(launcher, 'create_link', side_effect=OSError('start denied')):
            launcher.register_install(Path(d))
            text = (Path(d) / 'log').read_text()
            self.assertIn('desktop denied', text); self.assertIn('start denied', text); self.assertIn('registry denied', text)

    def test_registration_exception_does_not_stop_relocation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root / 'source.exe'; source.write_bytes(b'exe')
            with patch.object(launcher.sys, 'frozen', True, create=True), patch.object(launcher.sys, 'executable', str(source)), patch.object(launcher.sys, 'stdout', None), patch.object(launcher.sys, 'stderr', None), patch.object(launcher, 'LOG', root / 'log'), patch.object(launcher, 'register_install', side_effect=OSError('registration failed')), patch.object(launcher.subprocess, 'Popen') as spawn:
                self.assertTrue(launcher.relocate_launcher(root / 'game'))
                spawn.assert_called_once()
                self.assertIn('registration failed', (root / 'log').read_text())

    def test_startup_error_opens_error_screen(self):
        with patch.object(h.sys, 'frozen', True, create=True), patch.object(h.sys, 'argv', ['app']), patch.object(launcher, 'resolve_game_folder', side_effect=OSError('startup failed')), patch.object(launcher, 'show_startup_error', return_value=1) as show:
            self.assertEqual(h.main(), 1)
            self.assertIn('startup failed', str(show.call_args.args[0]))

    def test_error_ui_does_not_repeat_failed_resolution(self):
        with patch.object(launcher, 'resolve_game_folder', side_effect=OSError('failed')), patch.object(launcher.sys, 'stdout', None), patch.object(launcher.sys, 'stderr', None), patch.object(launcher.LauncherUI, 'run') as run:
            self.assertEqual(launcher.show_startup_error(OSError('failed')), 1)
            run.assert_called_once()

    def test_startup_error_retry_retries_startup(self):
        with patch.object(launcher, 'resolve_game_folder', return_value=Path('game')), patch.object(launcher.LauncherUI, 'check_updates'), patch.object(launcher, 'relocate_launcher', return_value=True) as relocate:
            ui = launcher.LauncherUI(startup_error=OSError('failed'))
            ui.operation = 'startup'; ui.set_screen('error'); ui.focus = 0
            ui.activate()
            relocate.assert_called_once()

    def test_error_screen_state(self):
        ui = MagicMock()
        with patch.object(launcher, 'LauncherUI', return_value=ui), patch.object(launcher, 'log_message'):
            launcher.show_startup_error(OSError('failure'))
        ui.set_screen.assert_called_with('error')
        ui.run.assert_called_once()
