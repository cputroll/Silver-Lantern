import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_game_sync import h


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sample():
    assets = [b'xxabc!', b'?defyy']
    return {'version': 2, 'build': 'game-v1', 'total_bytes': 6,
            'assets': [{'asset': f'bundle-{i}.tar', 'size': len(data), 'sha256': digest(data)}
                       for i, data in enumerate(assets)],
            'files': [{'path': 'bin/game.exe', 'size': 6, 'sha256': digest(b'abcdef'),
                       'segments': [{'asset': 'bundle-0.tar', 'offset': 2, 'length': 3, 'sha256': digest(b'abc')},
                                    {'asset': 'bundle-1.tar', 'offset': 1, 'length': 3, 'sha256': digest(b'def')}]}]}


class V2Tests(unittest.TestCase):
    def setUp(self):
        disk = patch.object(h.shutil, 'disk_usage', return_value=type('Usage', (), {'free': 100 * 1024 ** 3})())
        disk.start()
        self.addCleanup(disk.stop)

    def validate(self, m):
        return h.parse_game_manifest(json.dumps(m))

    def test_valid(self): self.assertEqual(self.validate(sample())['version'], 2)
    def test_traversal(self):
        m = sample(); m['files'][0]['path'] = '../escape'
        with self.assertRaises(ValueError): self.validate(m)
    def test_length_mismatch(self):
        m = sample(); m['files'][0]['segments'][0]['length'] = 2
        with self.assertRaises(ValueError): self.validate(m)
    def test_out_of_order_segments_rejected(self):
        m = sample()
        m['files'][0]['segments'].reverse()
        with self.assertRaisesRegex(Exception, 'segments must be packed in bundle order'):
            self.validate(m)

    def test_segment_beyond_asset(self):
        m = sample(); m['files'][0]['segments'][0]['offset'] = 5
        with self.assertRaises(ValueError): self.validate(m)
    def test_bad_hashes(self):
        for target in ('asset', 'file', 'segment'):
            m = sample()
            (m['assets'][0] if target == 'asset' else m['files'][0] if target == 'file' else m['files'][0]['segments'][0])['sha256'] = 'bad'
            with self.subTest(target=target), self.assertRaises(ValueError): self.validate(m)
    def test_duplicate_paths(self):
        m = sample(); m['files'].append(copy.deepcopy(m['files'][0])); m['total_bytes'] *= 2
        with self.assertRaises(ValueError): self.validate(m)
    def test_missing_asset(self):
        m = sample(); m['assets'].pop()
        with self.assertRaises(ValueError): self.validate(m)

    def run_install(self, root, m, **kwargs):
        calls = []
        def stream(s, url, dest, **kw):
            name = url.rsplit('/', 1)[-1]; calls.append(name)
            data = {'bundle-0.tar': b'xxabc!', 'bundle-1.tar': b'?defyy'}[name]
            dest.write_bytes(data); kw['progress'](len(data))
        with patch.object(h, 'stream_to', side_effect=stream):
            h.install_game(object(), self.validate(m), root, **kwargs)
        return calls

    def test_assembly_and_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); self.assertEqual(len(self.run_install(root, sample())), 2)
            self.assertEqual((root/'bin/game.exe').read_bytes(), b'abcdef')
            self.assertFalse((root/'.heylisten-parts').exists())
    def test_keep_assets(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); self.run_install(root, sample(), keep_assets=True)
            self.assertTrue((root/'.heylisten-parts/bundle-0.tar').exists())
    def test_segment_hash_mismatch(self):
        m = sample(); m['files'][0]['segments'][0]['sha256'] = digest(b'wrong')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with self.assertRaises(ValueError): self.run_install(root, m)
            self.assertFalse((root/'bin/game.exe').exists())
    def test_only_needed_assets(self):
        m = sample(); m['assets'].append({'asset': 'unused.tar', 'size': 1, 'sha256': digest(b'x')})
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(set(self.run_install(Path(d), m)), {'bundle-0.tar', 'bundle-1.tar'})
    def test_dry_run(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)/'missing'; self.assertEqual(self.run_install(root, sample(), dry_run=True), [])
            self.assertFalse(root.exists())
