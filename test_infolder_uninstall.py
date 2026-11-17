import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import launcher


class InFolderUninstallTests(unittest.TestCase):
    def test_uninstall_preserves_unknown_modified_and_saves(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'game'; root.mkdir()
            (root / 'owned.bin').write_bytes(b'abc')
            (root / 'modified.bin').write_bytes(b'changed')
            (root / 'notes.txt').write_text('mine')
            (root / 'user').mkdir(); (root / 'user' / 'save.dat').write_bytes(b'save')
            manifest = {'files': [{'path': 'owned.bin', 'size': 3}, {'path': 'modified.bin', 'size': 1}]}
            with patch.object(launcher, 'load_game_folder', return_value=root), patch.object(launcher, 'confirm_uninstall', return_value=True), patch.object(launcher, 'remove_defender_exclusion', return_value=True), patch.object(launcher, 'ownership_manifest', return_value=manifest), patch.object(launcher, 'shortcut_path', return_value=Path(temp) / 'desktop.lnk'), patch.object(launcher, 'start_menu_path', return_value=Path(temp) / 'start.lnk'), patch.object(launcher, 'remove_registration'), patch.object(launcher, 'STATE_DIR', Path(temp) / 'state'), patch.object(launcher, 'schedule_owned_cleanup'):
                self.assertTrue(launcher.uninstall())
            self.assertFalse((root / 'owned.bin').exists())
            self.assertTrue((root / 'modified.bin').exists())
            self.assertTrue((root / 'notes.txt').exists())
            self.assertTrue((root / 'user' / 'save.dat').exists())
