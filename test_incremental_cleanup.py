import copy
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from test_game_sync import h
from test_game_v2 import digest


def fixture():
    return {'version': 2, 'build': 'game-v1', 'total_bytes': 3,
            'assets': [{'asset': 'a', 'size': 2, 'sha256': digest(b'ab')},
                       {'asset': 'b', 'size': 1, 'sha256': digest(b'c')}],
            'files': [
                {'path': 'one', 'size': 1, 'sha256': digest(b'a'),
                 'segments': [{'asset': 'a', 'offset': 0, 'length': 1, 'sha256': digest(b'a')}]},
                {'path': 'two', 'size': 1, 'sha256': digest(b'b'),
                 'segments': [{'asset': 'a', 'offset': 1, 'length': 1, 'sha256': digest(b'b')}]},
                {'path': 'three', 'size': 1, 'sha256': digest(b'c'),
                 'segments': [{'asset': 'b', 'offset': 0, 'length': 1, 'sha256': digest(b'c')}]}]}


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        disk = patch.object(h.shutil, 'disk_usage', return_value=type('Usage', (), {'free': 100 * 1024 ** 3})())
        disk.start()
        self.addCleanup(disk.stop)

    def test_shared_bundle_lifetime_and_overlapping_download(self):
        import threading
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            b_started = threading.Event()
            read_started = threading.Event()
            real_read, real_unlink = h.os.read, Path.unlink
            seen = []
            def read(fd, size):
                if not b_started.is_set():
                    read_started.set()
                    if not b_started.wait(5):
                        raise AssertionError('second download did not overlap assembly')
                return real_read(fd, size)
            def unlink(path, *args, **kwargs):
                if path == root / '.heylisten-parts' / 'a' and path.exists():
                    self.assertEqual((root / 'one').read_bytes(), b'a')
                    self.assertEqual((root / 'two').read_bytes(), b'b')
                return real_unlink(path, *args, **kwargs)
            def stream(s, url, dest, **kw):
                name = url.rsplit('/', 1)[-1]
                if name == 'b':
                    self.assertTrue(read_started.wait(5))
                    self.assertTrue((root / '.heylisten-parts' / 'a').exists())
                    b_started.set()
                seen.append(name)
                self.assertLessEqual(len(list((root / '.heylisten-parts').iterdir())), 2)
                data = {'a': b'ab', 'b': b'c'}[name]
                dest.write_bytes(data)
                kw['progress'](len(data))
            with patch.object(h, 'stream_to', side_effect=stream), patch.object(h.os, 'read', side_effect=read), patch.object(Path, 'unlink', unlink), redirect_stdout(io.StringIO()):
                h.install_game(object(), fixture(), root)
            self.assertEqual(seen, ['a', 'b'])
            self.assertEqual((root / 'three').read_bytes(), b'c')
            self.assertFalse((root / '.heylisten-parts').exists())

    def test_completed_files_need_no_disk_preflight(self):
        with tempfile.TemporaryDirectory() as td, redirect_stdout(io.StringIO()):
            root = Path(td)
            for name, data in [('one', b'a'), ('two', b'b'), ('three', b'c')]:
                (root / name).write_bytes(data)
            cache = root / '.heylisten-parts'
            cache.mkdir()
            (cache / 'preserve').write_bytes(b'cached data')
            with patch.object(h, 'check_disk_space') as check, patch.object(h, 'stream_to') as stream:
                h.install_game(object(), fixture(), root)
            check.assert_not_called()
            stream.assert_not_called()
            self.assertEqual((cache / 'preserve').read_bytes(), b'cached data')

    def test_pending_install_preserves_unrelated_cache(self):
        with tempfile.TemporaryDirectory() as td, redirect_stdout(io.StringIO()):
            root = Path(td)
            cache = root / '.heylisten-parts'
            cache.mkdir()
            (cache / 'preserve').write_bytes(b'cached data')
            def stream(s, url, dest, **kw):
                data = {'a': b'ab', 'b': b'c'}[url.rsplit('/', 1)[-1]]
                dest.write_bytes(data)
                kw['progress'](len(data))
            with patch.object(h, 'stream_to', side_effect=stream):
                h.install_game(object(), fixture(), root)
            self.assertEqual((cache / 'preserve').read_bytes(), b'cached data')

    def test_pending_files_still_require_disk_preflight(self):
        with tempfile.TemporaryDirectory() as td, redirect_stdout(io.StringIO()), \
             patch.object(h.shutil, 'disk_usage', return_value=type('Usage', (), {'free': 1})()), \
             patch.object(h, 'stream_to') as stream:
            with self.assertRaises(h.UserError):
                h.install_game(object(), fixture(), Path(td))
            stream.assert_not_called()

    def test_preflight_override_zero_disables_check(self):
        with tempfile.TemporaryDirectory() as td, patch.dict('os.environ', {'SILVER_LANTERN_MIN_FREE_GB': '0'}), patch.object(h, 'check_disk_space') as check, patch.object(h, 'stream_to', side_effect=RuntimeError('stop')), redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                h.install_game(object(), fixture(), Path(td))
            check.assert_not_called()

    def test_preflight_override_custom_threshold(self):
        with tempfile.TemporaryDirectory() as td, patch.dict('os.environ', {'SILVER_LANTERN_MIN_FREE_GB': '2'}), patch.object(h, 'check_disk_space') as check, patch.object(h, 'stream_to', side_effect=RuntimeError('stop')), redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                h.install_game(object(), fixture(), Path(td))
            self.assertEqual(check.call_args.args[1], 2 * 1024 ** 3)

    def test_plan_and_threshold(self):
        with tempfile.TemporaryDirectory() as td:
            out = io.StringIO()
            with patch.object(h, 'check_disk_space') as check, patch.object(h, 'stream_to', side_effect=RuntimeError('stop')), redirect_stdout(out):
                with self.assertRaises(RuntimeError):
                    h.install_game(object(), fixture(), Path(td))
            self.assertEqual(check.call_args.args[1], 44 * 1024 ** 3)
            self.assertIn('Needs about 44 GB free; the game itself takes about 33 GB', out.getvalue())
            self.assertIn('resume', out.getvalue().lower())
            self.assertIn('bundle', out.getvalue().lower())

    def test_keep_assets_preserves_bundles(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            def stream(s, url, dest, **kw):
                data = {'a': b'ab', 'b': b'c'}[url.rsplit('/', 1)[-1]]
                dest.write_bytes(data); kw['progress'](len(data))
            with patch.object(h, 'stream_to', side_effect=stream), redirect_stdout(io.StringIO()):
                h.install_game(object(), fixture(), root, keep_assets=True)
            self.assertTrue((root / '.heylisten-parts' / 'a').exists())
            self.assertTrue((root / '.heylisten-parts' / 'b').exists())
