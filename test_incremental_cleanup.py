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
    def test_shared_bundle_removed_only_after_last_file_before_next_fetch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            events = []
            def stream(s, url, dest, **kw):
                name = url.rsplit('/', 1)[-1]
                events.append(name)
                if name == 'b':
                    self.assertEqual((root / 'one').read_bytes(), b'a')
                    self.assertEqual((root / 'two').read_bytes(), b'b')
                    self.assertFalse((root / '.heylisten-parts' / 'a').exists())
                data = {'a': b'ab', 'b': b'c'}[name]
                dest.write_bytes(data)
                kw['progress'](len(data))
            with patch.object(h, 'stream_to', side_effect=stream), redirect_stdout(io.StringIO()):
                h.install_game(object(), fixture(), root)
            self.assertEqual(events, ['a', 'b'])
            self.assertEqual((root / 'three').read_bytes(), b'c')

    def test_plan_and_threshold(self):
        with tempfile.TemporaryDirectory() as td:
            out = io.StringIO()
            with patch.object(h, 'check_disk_space') as check, patch.object(h, 'stream_to', side_effect=RuntimeError('stop')), redirect_stdout(out):
                with self.assertRaises(RuntimeError):
                    h.install_game(object(), fixture(), Path(td))
            self.assertEqual(check.call_args.args[1], 40 * 1024 ** 3)
            self.assertIn('Needs about 40 GB free; the game itself takes about 33 GB', out.getvalue())
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
