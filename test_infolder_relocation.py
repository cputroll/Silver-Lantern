import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import launcher


class RelocationTests(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.TemporaryDirectory()
        self.addCleanup(self.state.cleanup)
        state = Path(self.state.name)
        for name, value in (('STATE_DIR', state), ('LOG', state / 'log')):
            patcher = patch.object(launcher, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_relocates_to_game_folder_with_arguments(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root / 'extracted' / 'SilverLantern.exe'; source.parent.mkdir(); source.write_bytes(b'exe')
            game = root / 'game'
            with patch.object(launcher.sys, 'frozen', True, create=True), patch.object(launcher.sys, 'executable', str(source)), patch.object(launcher.sys, 'argv', ['exe', '--gui']), patch.object(launcher.subprocess, 'Popen') as spawn, patch.object(launcher, 'register_install'):
                self.assertTrue(launcher.relocate_launcher(game))
            target = game / 'Ocarina of Time.exe'
            self.assertEqual(target.read_bytes(), b'exe'); self.assertTrue(source.exists())
            self.assertEqual(spawn.call_args.args[0], [str(target), '--gui'])

    def test_registers_new_destination_when_moving_from_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root / 'Programs' / 'Ocarina of Time' / 'Ocarina of Time.exe'; source.parent.mkdir(parents=True); source.write_bytes(b'exe'); game = root / 'game'
            with patch.dict(launcher.os.environ, {'LOCALAPPDATA': str(root)}), patch.object(launcher.sys, 'frozen', True, create=True), patch.object(launcher.sys, 'executable', str(source)), patch.object(launcher.subprocess, 'Popen'), patch.object(launcher, 'register_install') as register:
                self.assertTrue(launcher.relocate_launcher(game))
                self.assertEqual(register.call_args.kwargs['exe'], game / 'Ocarina of Time.exe')

    def test_existing_destination_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root / 'source.exe'; source.write_bytes(b'exe'); game = root / 'game'; game.mkdir()
            target = game / 'Ocarina of Time.exe'; target.write_bytes(b'user file')
            with patch.dict(launcher.os.environ, {'LOCALAPPDATA': str(root)}), patch.object(launcher.sys, 'frozen', True, create=True), patch.object(launcher.sys, 'executable', str(source)), patch.object(launcher.subprocess, 'Popen'), patch.object(launcher, 'register_install'):
                launcher.relocate_launcher(game)
            self.assertEqual(target.read_bytes(), b'user file')

    def test_both_destinations_occupied_preserves_files_and_launches_copy(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root / 'temp' / 'SilverLantern.exe'; source.parent.mkdir(); source.write_bytes(b'new launcher')
            game = root / 'game'; game.mkdir(); game_target = game / 'Ocarina of Time.exe'; game_target.write_bytes(b'game user data')
            fallback = root / 'Programs' / 'Ocarina of Time'; fallback.mkdir(parents=True); occupied = fallback / 'Ocarina of Time.exe'; occupied.write_bytes(b'other user data')
            with patch.dict(launcher.os.environ, {'LOCALAPPDATA': str(root)}), patch.object(launcher.sys, 'frozen', True, create=True), patch.object(launcher.sys, 'executable', str(source)), patch.object(launcher.sys, 'argv', ['exe', '--gui']), patch.object(launcher, 'STATE_DIR', root / 'state'), patch.object(launcher, 'LOG', root / 'state' / 'log'), patch.object(launcher.subprocess, 'Popen') as spawn, patch.object(launcher, 'register_install') as register:
                self.assertTrue(launcher.relocate_launcher(game))
            target = Path(spawn.call_args.args[0][0])
            self.assertEqual(target.read_bytes(), b'new launcher')
            self.assertEqual(game_target.read_bytes(), b'game user data')
            self.assertEqual(occupied.read_bytes(), b'other user data')
            self.assertEqual(register.call_args.kwargs['exe'], target)

    def test_unique_fallback_does_not_relocate_again(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); fallback = root / 'Programs' / 'Ocarina of Time'; fallback.mkdir(parents=True)
            source = fallback / 'Ocarina of Time-unique.exe'; source.write_bytes(b'exe')
            (fallback / 'Ocarina of Time.exe').write_bytes(b'user data')
            game = root / 'game'; game.mkdir(); (game / 'Ocarina of Time.exe').write_bytes(b'user data')
            with patch.dict(launcher.os.environ, {'LOCALAPPDATA': str(root)}), patch.object(launcher.sys, 'frozen', True, create=True), patch.object(launcher.sys, 'executable', str(source)), patch.object(launcher.subprocess, 'Popen') as spawn:
                self.assertFalse(launcher.relocate_launcher(game))
                spawn.assert_not_called()

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
