import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import launcher

class InFolderLinksTests(unittest.TestCase):
    def test_links_and_registry_target_game_launcher(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / 'game'; folder.mkdir(); target = folder / 'Ocarina of Time.exe'
            registry = MagicMock(); registry.REG_SZ = 1; registry.REG_DWORD = 4
            with patch.object(launcher, 'winreg', registry), patch.object(launcher, 'shortcut_path', return_value=Path(d) / 'desktop.lnk'), patch.object(launcher, 'start_menu_path', return_value=Path(d) / 'start.lnk'), patch.object(launcher, 'create_link') as link:
                launcher.register_install(folder)
            self.assertEqual([call.args[1] for call in link.call_args_list], [target, target])
            values = {call.args[1]: call.args[4] for call in registry.SetValueEx.call_args_list}
            self.assertEqual(values['DisplayIcon'], str(target)); self.assertEqual(values['InstallLocation'], str(folder))
            self.assertEqual(values['UninstallString'], f'"{target}" --uninstall')
