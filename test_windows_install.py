import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
import launcher


class WindowsInstallTests(unittest.TestCase):
    def test_labels_and_registry(self):
        self.assertEqual(launcher.shortcut_path().name, launcher.GAME_NAME + '.lnk')
        self.assertEqual(launcher.start_menu_path().name, launcher.GAME_NAME + '.lnk')
        registry = MagicMock()
        registry.REG_SZ = 1; registry.REG_DWORD = 4
        with tempfile.TemporaryDirectory() as d, patch.dict(launcher.os.environ, {'APPDATA': d}), patch.object(launcher, 'winreg', registry, create=True), patch.object(launcher, 'create_link') as link, patch.object(launcher.sys, 'executable', r'C:\App\SilverLantern.exe'):
            launcher.register_install(Path('missing'))
            expected_link = launcher.start_menu_path()
        self.assertEqual(link.call_args.args[0], expected_link)
        values = {call.args[1]: call.args[4] for call in registry.SetValueEx.call_args_list}
        self.assertEqual(values['DisplayName'], launcher.GAME_NAME)
        self.assertEqual(values['UninstallString'], '"C:\\App\\SilverLantern.exe" --uninstall')

    def test_ready_starts_registration_even_without_desktop_shortcut(self):
        with patch.object(launcher.LauncherUI, 'check_updates'), patch.object(launcher, 'installed', return_value=True), patch.object(launcher.threading, 'Thread') as thread:
            ui = launcher.LauncherUI()
            ui.config['shortcut'] = False
            ui.events.publish('complete', None)
            ui.drain()
            self.assertTrue(thread.called)
            self.assertEqual(thread.call_args.kwargs['target'], ui.shortcut_worker)
        with patch.object(launcher, 'register_install') as register, patch.object(launcher, 'create_shortcut') as desktop:
            ui.shortcut_worker()
            register.assert_called_once_with(ui.folder)
            desktop.assert_not_called()

    def test_uninstall_refuses_home_directory(self):
        with patch.object(launcher, 'load_game_folder', return_value=Path.home()), patch('builtins.input', return_value='y'), patch.object(launcher.shutil, 'rmtree') as remove, patch.object(launcher, 'schedule_owned_cleanup') as cleanup:
            with self.assertRaises(RuntimeError):
                launcher.uninstall()
            remove.assert_not_called()
            cleanup.assert_not_called()

    def test_delayed_cleanup_is_nonrecursive_and_waits(self):
        with tempfile.TemporaryDirectory() as d, patch.object(launcher.sys, 'frozen', True, create=True), patch.object(launcher.sys, 'executable', str(Path(d) / 'SilverLantern.exe')), patch.object(launcher, 'powershell') as run:
            launcher.schedule_owned_cleanup()
        script = run.call_args.args[0]
        self.assertIn('WaitForExit()', script)
        self.assertIn('SilverLantern.exe', script)
        self.assertIn('[System.IO.Directory]::Delete', script)
        self.assertNotIn('-Recurse', script)
        self.assertFalse(run.call_args.kwargs['wait'])

    def test_refusal_and_keep_saves(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); game = root / 'game'; (game / 'user').mkdir(parents=True)
            (game / 'user' / 'save').write_text('save'); (game / 'game.bin').write_text('game')
            with patch.object(launcher, 'load_game_folder', return_value=game), patch.object(launcher, 'remove_registration') as reg, patch.object(launcher, 'schedule_owned_cleanup') as cleanup, patch.object(launcher, 'STATE_DIR', root / 'state'), patch('builtins.input', return_value='n'):
                self.assertFalse(launcher.uninstall())
                reg.assert_not_called(); cleanup.assert_not_called(); self.assertTrue((game / 'game.bin').exists())
            with patch.object(launcher, 'load_game_folder', return_value=game), patch.object(launcher, 'remove_registration'), patch.object(launcher, 'schedule_owned_cleanup') as cleanup, patch.object(launcher, 'STATE_DIR', root / 'state'), patch.object(launcher, 'shortcut_path', return_value=root / 'a.lnk'), patch.object(launcher, 'start_menu_path', return_value=root / 'b.lnk'), patch('builtins.input', return_value='y'):
                self.assertTrue(launcher.uninstall(keep_saves=True))
                self.assertTrue((game / 'user' / 'save').exists()); self.assertFalse((game / 'game.bin').exists())
                cleanup.assert_called_once()
