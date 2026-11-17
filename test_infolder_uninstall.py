import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import launcher


class InFolderUninstallTests(unittest.TestCase):
    def test_generated_settings_removed_with_and_without_saves(self):
        for purge in (False, True):
            with self.subTest(purge=purge), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); game = root / 'game'; saves = game / 'user'; saves.mkdir(parents=True)
                (saves / 'save.dat').write_bytes(b'save')
                (game / launcher.game_settings.INI_NAME).write_text('graphics settings')
                with patch.object(launcher, 'load_game_folder', return_value=game), patch.object(launcher, 'confirm_uninstall', return_value=True), patch.object(launcher, 'remove_defender_exclusion', return_value=True), patch.object(launcher, 'ownership_manifest', return_value=None), patch.object(launcher, 'shortcut_path', return_value=root / 'desktop.lnk'), patch.object(launcher, 'start_menu_path', return_value=root / 'start.lnk'), patch.object(launcher, 'remove_registration'), patch.object(launcher, 'STATE_DIR', root / 'state'), patch.object(launcher, 'LOG', root / 'uninstall.log'), patch.object(launcher, 'schedule_owned_cleanup'):
                    self.assertTrue(launcher.uninstall(purge_saves=purge))
                if purge:
                    self.assertFalse(game.exists())
                else:
                    self.assertEqual(list(game.iterdir()), [saves])
                    self.assertEqual((saves / 'save.dat').read_bytes(), b'save')

    def test_preservation_count_is_logged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); game = root / 'game'; game.mkdir(); (game / 'notes.txt').write_text('mine')
            with patch.object(launcher, 'load_game_folder', return_value=game), patch.object(launcher, 'confirm_uninstall', return_value=True), patch.object(launcher, 'remove_defender_exclusion', return_value=True), patch.object(launcher, 'ownership_manifest', return_value=None), patch.object(launcher, 'shortcut_path', return_value=root / 'desktop.lnk'), patch.object(launcher, 'start_menu_path', return_value=root / 'start.lnk'), patch.object(launcher, 'remove_registration'), patch.object(launcher, 'STATE_DIR', root / 'state'), patch.object(launcher, 'LOG', root / 'uninstall.log'), patch.object(launcher, 'schedule_owned_cleanup'):
                launcher.uninstall()
            self.assertIn('Unknown user files preserved: 1', (root / 'uninstall.log').read_text())

    def test_fixed_item_under_symlink_is_not_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); game = root / 'game'; game.mkdir(); outside = root / 'outside'; outside.mkdir()
            victim = outside / 'SilverLantern.exe'; victim.write_bytes(b'user')
            try:
                (game / 'support').symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest('directory symlinks unavailable')
            with patch.object(launcher, 'load_game_folder', return_value=game), patch.object(launcher, 'confirm_uninstall', return_value=True), patch.object(launcher, 'remove_defender_exclusion', return_value=True), patch.object(launcher, 'ownership_manifest', return_value=None), patch.object(launcher, 'shortcut_path', return_value=root / 'desktop.lnk'), patch.object(launcher, 'start_menu_path', return_value=root / 'start.lnk'), patch.object(launcher, 'remove_registration'), patch.object(launcher, 'STATE_DIR', root / 'state'), patch.object(launcher, 'schedule_owned_cleanup'):
                launcher.uninstall()
            self.assertTrue(victim.exists())

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
