import json
import ntpath
import os
import threading
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

    @unittest.skipUnless(os.name == 'nt', 'Windows realpath race')
    def test_parallel_parent_creation_during_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entry = 'romfs/Bake/Probe/Scene/BarinadeStage/X0_Y0_Z0_2.bkprb.zs'
            target = root.joinpath(*entry.split('/'))
            original = ntpath._getfinalpathname
            calls = 0
            fired = threading.Event()
            def racing(path):
                nonlocal calls
                if str(path).lower() == str(target).lower():
                    calls += 1
                    if calls == 1:
                        # Create the parents concurrently on the first
                        # resolution of the target; the number of later
                        # resolution attempts is an implementation detail.
                        worker = threading.Thread(target=lambda: (target.parent.mkdir(parents=True, exist_ok=True), fired.set()))
                        worker.start()
                        worker.join(timeout=5)
                        self.assertFalse(worker.is_alive())
                return original(path)
            with patch.object(ntpath, '_getfinalpathname', side_effect=racing):
                self.assertEqual(h.game_path(root, entry), target)
            self.assertTrue(fired.is_set(), 'the concurrent parent creation must actually run')
            self.assertGreaterEqual(calls, 1)

    @unittest.skipUnless(os.name == 'nt', 'Windows extended path handling')
    def test_extended_prefix_does_not_hide_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'root'
            root.mkdir()
            outside = Path(tmp) / 'outside'
            original = Path.resolve
            def resolved(path, *args, **kwargs):
                if path == root / 'romfs' / 'file.bin':
                    return Path('\\\\\\\\?\\\\' + str(outside))
                return original(path, *args, **kwargs)
            with patch.object(Path, 'resolve', resolved):
                with self.assertRaises(h.InvalidGamePath) as caught:
                    h.game_path(root, 'romfs/file.bin')
            self.assertIn('resolved target=', str(caught.exception))
            self.assertIn('resolved root=', str(caught.exception))

    def test_resolution_exception_is_logged_and_propagated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            error = OSError('resolution failed')
            with patch.object(Path, 'resolve', side_effect=error), patch.object(h, 'log') as logger:
                with self.assertRaises(OSError) as caught:
                    h.game_path(root, 'romfs/file.bin')
            self.assertIs(caught.exception, error)
            self.assertIn('resolution failed', logger.call_args.args[0])
            self.assertIn('romfs', logger.call_args.args[0])

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
