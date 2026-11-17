import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock


def load_launcher(env):
    spec = importlib.util.spec_from_file_location('launcher_seam_test', Path(__file__).parent / 'launcher.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, env, clear=False):
        spec.loader.exec_module(module)
    return module


class RegistrationSeamTests(unittest.TestCase):
    def test_defaults_use_standard_registry_and_known_desktop(self):
        env = {k: '' for k in ('SILVER_LANTERN_REG_ROOT', 'SILVER_LANTERN_DESKTOP_DIR', 'SILVER_LANTERN_STARTMENU_DIR')}
        app = load_launcher(env)
        self.assertEqual(app.UNINSTALL_KEY, r'Software\Microsoft\Windows\CurrentVersion\Uninstall\SilverLantern')
        with patch.object(app, '_known_desktop', return_value=Path('redirected')) as known:
            self.assertEqual(app.shortcut_path().parent, Path('redirected'))
            known.assert_called_once()

    def test_overrides_apply_to_creation_and_removal(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); desktop = root / 'desktop'; start = root / 'start'
            desktop.mkdir(); start.mkdir()
            app = load_launcher({'SILVER_LANTERN_REG_ROOT': r'Software\SilverLanternTest', 'SILVER_LANTERN_DESKTOP_DIR': str(desktop), 'SILVER_LANTERN_STARTMENU_DIR': str(start)})
            self.assertEqual(app.UNINSTALL_KEY, r'Software\SilverLanternTest\SilverLantern')
            registry = MagicMock(); registry.REG_SZ = 1; registry.REG_DWORD = 4
            def make_link(path, target, working): Path(path).write_text(str(target))
            with patch.object(app, 'winreg', registry), patch.object(app, 'create_link', side_effect=make_link):
                app.register_install(root, exe=root / 'Ocarina of Time.exe')
                self.assertTrue(app.shortcut_path().is_file()); self.assertTrue(app.start_menu_path().is_file())
                app.remove_registration()
                registry.DeleteKey.assert_called_once_with(registry.HKEY_CURRENT_USER, app.UNINSTALL_KEY)
            self.assertEqual(app.shortcut_path().parent, desktop)
            self.assertEqual(app.start_menu_path().parent, start)
