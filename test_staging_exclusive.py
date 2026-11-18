import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_game_sync import h, manifest
from test_game_v2 import V2Tests, sample


class FakeResponse:
    status_code = 200
    headers = {}

    def raise_for_status(self):
        pass

    def close(self):
        pass

    def iter_content(self, size):
        yield b'abc'


class FakeSession:
    def get(self, *args, **kwargs):
        return FakeResponse()


class StagingExclusiveTests(unittest.TestCase):
    def setUp(self):
        disk = patch.object(h.shutil, 'disk_usage', return_value=type('Usage', (), {'free': 100 * 1024 ** 3})())
        disk.start(); self.addCleanup(disk.stop)
        running = patch.object(h, 'running_launcher_paths', return_value=set())
        running.start(); self.addCleanup(running.stop)

    def test_second_exclusive_open_of_same_staging_path_fails(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'game.exe.heylisten-new'
            with h._open_with_retry(path, 'xb') as stream:
                stream.write(b'first')
            with self.assertRaises(FileExistsError):
                h._open_with_retry(path, 'xb')
            self.assertEqual(path.read_bytes(), b'first')

    def test_v2_plain_leftover_is_replaced_never_appended(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            stage = root / 'bin' / 'game.exe.heylisten-new'
            stage.parent.mkdir(parents=True)
            stage.write_bytes(b'STALE-LEFTOVER')
            V2Tests().run_install(root, sample())
            self.assertEqual((root / 'bin' / 'game.exe').read_bytes(), b'abcdef')
            self.assertFalse(stage.exists())

    def test_v1_plain_leftover_is_replaced_never_appended(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            stage = root / 'bin' / 'game.exe.heylisten-new'
            stage.parent.mkdir(parents=True)
            stage.write_bytes(b'STALE-LEFTOVER')
            h.install_game(FakeSession(), manifest(), root)
            self.assertEqual((root / 'bin' / 'game.exe').read_bytes(), b'abc')
            self.assertFalse(stage.exists())

    def test_reparse_leftover_is_refused_and_referent_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / 'game'; root.mkdir()
            sentinel = base / 'sentinel.dat'; sentinel.write_bytes(b'untouched')
            stage = root / 'bin' / 'game.exe.heylisten-new'
            stage.parent.mkdir(parents=True)
            # A plain file occupies the name (so exclusive create fails
            # EEXIST for real); the mock reports the FILE_ATTRIBUTE_REPARSE_
            # POINT bit that a symlink or junction would carry.
            stage.write_bytes(b'planted')
            real_stat = h.os.stat

            def fake_stat(path, *args, **kwargs):
                result = real_stat(path, *args, **kwargs)
                if kwargs.get('follow_symlinks') is False and Path(path) == stage:
                    return SimpleNamespace(
                        st_mode=result.st_mode,
                        st_file_attributes=getattr(result, 'st_file_attributes', 0) | h.FILE_ATTRIBUTE_REPARSE_POINT)
                return result

            with patch.object(h.os, 'stat', side_effect=fake_stat):
                with self.assertRaises(h.UserError) as caught:
                    V2Tests().run_install(root, sample())
            self.assertIn('invalid leftover link', str(caught.exception))
            self.assertIn(str(stage), str(caught.exception))
            self.assertEqual(stage.read_bytes(), b'planted')
            self.assertEqual(sentinel.read_bytes(), b'untouched')

    @unittest.skipUnless(os.name == 'nt', 'directory junctions require Windows')
    def test_staging_inside_junctioned_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / 'game'; root.mkdir()
            outside = base / 'elsewhere'; outside.mkdir()
            try:
                import _winapi
                _winapi.CreateJunction(str(outside), str(root / 'bin'))
            except (OSError, AttributeError) as exc:
                self.skipTest(f'junction creation unavailable: {exc}')
            with self.assertRaises(h.InvalidGamePath):
                V2Tests().run_install(root, sample())
            self.assertFalse(any(outside.iterdir()))
            self.assertTrue((root / 'bin').exists())

    def test_real_staging_symlink_is_refused_without_touching_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / 'game'; root.mkdir()
            sentinel = base / 'sentinel.exe'; sentinel.write_bytes(b'untouched')
            stage = root / 'bin' / 'game.exe.heylisten-new'
            stage.parent.mkdir(parents=True)
            try:
                stage.symlink_to(sentinel)
            except OSError as exc:
                self.skipTest(f'symlink creation unavailable: {exc}')
            with self.assertRaises(h.UserError) as caught:
                V2Tests().run_install(root, sample())
            self.assertIn('invalid leftover link', str(caught.exception))
            self.assertEqual(sentinel.read_bytes(), b'untouched')
            self.assertTrue(stage.is_symlink())


if __name__ == '__main__':
    unittest.main()
