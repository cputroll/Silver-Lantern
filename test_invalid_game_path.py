import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from test_game_sync import h, manifest


class InvalidGamePathTest(unittest.TestCase):
    def test_escape_identifies_entry_and_root_and_is_friendly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError) as caught:
                h.game_path(root, '../outside.exe')
            self.assertIn('../outside.exe', str(caught.exception))
            self.assertIn(str(root.resolve()), str(caught.exception))
            error = h.friendly(caught.exception)
            self.assertIn('game package contains an invalid entry', error.problem.lower())
            self.assertIn('../outside.exe', error.problem)
            self.assertTrue(error.problem.endswith('../outside.exe.'))

    def test_realistic_entries_and_escapes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for entry in ['romfs/a file.bin', 'romfs/音楽/épreuve.bin', 'romfs/' + '/'.join(['long directory'] * 24) + '/file.bin']:
                with self.subTest(entry=entry):
                    package = manifest()
                    package['files'][0]['path'] = entry
                    parsed = h.parse_game_manifest(json.dumps(package))
                    self.assertEqual(h.game_path(root, parsed['files'][0]['path']), root.joinpath(*entry.split('/')))
            for entry in ['../escape', '/absolute', 'romfs/../../escape']:
                with self.subTest(entry=entry), self.assertRaises(h.InvalidGamePath):
                    h.game_path(root, entry)

    def test_reparse_ancestor_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(h, '_leftover_kind', side_effect=lambda p: 'reparse' if p == root / 'link' else 'absent'):
                with self.assertRaises(h.InvalidGamePath):
                    h.game_path(root, 'link/file.bin')

    def test_symlink_ancestor_is_refused_even_if_it_points_inside(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'real').mkdir()
            try:
                (root / 'link').symlink_to(root / 'real', target_is_directory=True)
            except OSError:
                self.skipTest('directory symlinks unavailable')
            with self.assertRaises(h.InvalidGamePath):
                h.game_path(root, 'link/file.bin')
