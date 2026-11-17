import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import launcher


class RelocationTests(unittest.TestCase):
    def test_relocates_to_game_folder_with_arguments(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root / 'extracted' / 'SilverLantern.exe'; source.parent.mkdir(); source.write_bytes(b'exe')
            game = root / 'game'
            with patch.object(launcher.sys, 'frozen', True, create=True), patch.object(launcher.sys, 'executable', str(source)), patch.object(launcher.sys, 'argv', ['exe', '--gui']), patch.object(launcher.subprocess, 'Popen') as spawn, patch.object(launcher, 'register_install'):
                self.assertTrue(launcher.relocate_launcher(game))
            target = game / 'Ocarina of Time.exe'
            self.assertEqual(target.read_bytes(), b'exe'); self.assertTrue(source.exists())
            self.assertEqual(spawn.call_args.args[0], [str(target), '--gui'])

    def test_unwritable_game_uses_programs_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root / 'source.exe'; source.write_bytes(b'exe'); game = root / 'game'
            real = launcher.probe_writable
            def probe(folder):
                if folder == game: raise PermissionError('denied')
                real(folder)
            with patch.dict(launcher.os.environ, {'LOCALAPPDATA': str(root)}), patch.object(launcher.sys, 'frozen', True, create=True), patch.object(launcher.sys, 'executable', str(source)), patch.object(launcher, 'probe_writable', side_effect=probe), patch.object(launcher.subprocess, 'Popen'), patch.object(launcher, 'register_install'):
                self.assertTrue(launcher.relocate_launcher(game))
            self.assertTrue((root / 'Programs' / 'Ocarina of Time' / 'Ocarina of Time.exe').exists())
