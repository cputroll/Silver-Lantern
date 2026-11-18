import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_game_sync import h


class DestinationInventoryTests(unittest.TestCase):
    def test_empty_directory_skips_walk(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(h, 'walk_destination', side_effect=AssertionError('walked')):
                self.assertEqual(h.destination_inventory(Path(directory)), {})

    def test_inventory_refreshes_modified_file_size_even_when_directory_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'game').mkdir()
            target = root / 'game' / 'data'
            target.write_bytes(b'abc')
            self.assertEqual(h.destination_inventory(root)['game/data'], 3)
            target.write_bytes(b'longer')
            self.assertEqual(h.destination_inventory(root)['game/data'], 6)

    def test_inventory_contains_nested_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'a').mkdir()
            (root / 'a' / 'one').write_bytes(b'1')
            (root / 'two').write_bytes(b'22')
            self.assertEqual(h.destination_inventory(root), {'a/one': 1, 'two': 2})

    def test_symlinks_are_not_followed(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            (Path(outside) / 'secret').write_bytes(b'data')
            try:
                (root / 'linked').symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f'symlink creation unavailable: {exc}')
            self.assertEqual(h.destination_inventory(root), {})

    def test_install_scans_destination_only_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'other').write_bytes(b'x')
            manifest = {'version': 2, 'files': [], 'assets': []}
            with patch.object(h, 'walk_destination', wraps=h.walk_destination) as walk:
                h.install_game(None, manifest, root, dry_run=True)
            self.assertEqual(walk.call_count, 1)

    def test_echo_has_no_timestamp_but_log_does(self):
        with tempfile.TemporaryDirectory() as directory:
            logfile = Path(directory) / 'log'
            with patch.object(h, 'LOG_PATH', logfile), patch.object(h, 'say') as say:
                h.log('Hello', echo=True)
            say.assert_called_once_with('Hello')
            self.assertRegex(logfile.read_text(), r'^\[\d\d:\d\d:\d\d\] Hello')
