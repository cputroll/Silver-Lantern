import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_game_sync import h


def digest(b): return hashlib.sha256(b).hexdigest()


class SegmentSchedulerTests(unittest.TestCase):
    def test_four_bundles_single_file_under_two_bundle_cap(self):
        data = [bytes([97+i])*10 for i in range(4)]
        man = {'version': 2, 'build': 'game-v1', 'total_bytes': 40,
               'assets': [{'asset': str(i), 'size': 10, 'sha256': digest(b)} for i,b in enumerate(data)],
               'files': [{'path': 'large', 'size': 40, 'sha256': digest(b''.join(data)),
                          'segments': [{'asset': str(i), 'offset': 0, 'length': 10, 'sha256': digest(b)} for i,b in enumerate(data)]}]}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            def stream(s, url, dest, **kw):
                cache = root / '.heylisten-parts'
                self.assertLessEqual(len(list(cache.iterdir())), 1)
                b = data[int(url.rsplit('/', 1)[-1])]; dest.write_bytes(b); kw['progress'](len(b))
            with patch.object(h, 'stream_to', side_effect=stream), patch.object(h, 'check_disk_space'):
                h.install_game(object(), man, root)
            self.assertEqual((root/'large').read_bytes(), b''.join(data))

    def test_poisoned_segment_cleans_up_and_preserves_error(self):
        data = b'bad segment'
        man = {'version': 2, 'build': 'game-v1', 'total_bytes': len(data),
               'assets': [{'asset': 'a', 'size': len(data), 'sha256': digest(data)}],
               'files': [{'path': 'file', 'size': len(data), 'sha256': digest(data),
                          'segments': [{'asset': 'a', 'offset': 0, 'length': len(data), 'sha256': digest(b'wrong')}]}]}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            def stream(s, url, dest, **kw):
                dest.write_bytes(data)
                kw['progress'](len(data))
            with patch.object(h, 'stream_to', side_effect=stream), patch.object(h, 'check_disk_space'):
                with self.assertRaisesRegex(ValueError, 'segment SHA-256 mismatch'):
                    h.install_game(object(), man, root)
            self.assertFalse((root / 'file').exists())
            self.assertFalse(any((root / '.heylisten-parts').iterdir()))

    def test_valve_reports_unconsumable_segments(self):
        data = b'x'
        # Asset order intentionally contradicts segment order; call the scheduler
        # directly so the manifest validator cannot reject it first.
        assets = {str(i): {'asset': str(i), 'size': 1, 'sha256': digest(data)} for i in range(4)}
        item = {'path': 'file', 'size': 4, 'sha256': digest(data * 4),
                'segments': [{'asset': str(i), 'offset': 0, 'length': 1, 'sha256': digest(data)} for i in (3, 2, 1, 0)]}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            def fetch(asset):
                (root / asset['asset']).write_bytes(data)
            with patch.object(h, 'assembly_workers', return_value=4):
                with self.assertRaisesRegex(h.UserError, 'cannot make progress'):
                    h.assemble_segmented([item], assets, root, root, fetch, False)
            self.assertFalse((root / 'file.heylisten-new').exists())
            self.assertFalse(any(root.iterdir()))

    def test_abort_does_not_delete_untouched_existing_target(self):
        data = b'x'
        man = {'version': 2, 'build': 'game-v1', 'total_bytes': 2,
               'assets': [{'asset': 'a', 'size': 1, 'sha256': digest(data)}],
               'files': [{'path': 'old', 'size': 1, 'sha256': digest(data),
                          'segments': [{'asset': 'a', 'offset': 0, 'length': 1, 'sha256': digest(data)}]}]}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'old').write_bytes(b'old content')
            with patch.object(h, 'stream_to', side_effect=RuntimeError('download failed')), patch.object(h, 'check_disk_space'):
                with self.assertRaisesRegex(RuntimeError, 'download failed'):
                    h.install_game(object(), man, root)
            self.assertEqual((root / 'old').read_bytes(), b'old content')

    def test_one_segment_is_written_directly(self):
        data = b'direct data'
        man = {'version': 2, 'build': 'game-v1', 'total_bytes': len(data),
               'assets': [{'asset': 'a', 'size': len(data), 'sha256': digest(data)}],
               'files': [{'path': 'file', 'size': len(data), 'sha256': digest(data),
                          'segments': [{'asset': 'a', 'offset': 0, 'length': len(data), 'sha256': digest(data)}]}]}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            def stream(s, url, dest, **kw):
                dest.write_bytes(data)
                kw['progress'](len(data))
            with patch.object(h, 'stream_to', side_effect=stream), patch.object(h, 'check_disk_space'), patch.object(Path, 'replace', side_effect=AssertionError('unexpected temp replace')):
                h.install_game(object(), man, root)
            self.assertEqual((root / 'file').read_bytes(), data)
