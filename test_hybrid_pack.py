import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('pack_game', Path(__file__).with_name('pack-game.py'))
packer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packer)

class HybridPackTests(unittest.TestCase):
    def test_unique_prefix_applies_to_assets_and_segments(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root / 'src'; source.mkdir()
            (source / 'a').write_bytes(b'abc')
            result = packer.pack(source, root / 'out', limit=10240, prefix='v041-unique-')
            name = result['assets'][0]['asset']
            self.assertTrue(name.startswith('v041-unique-'))
            self.assertEqual(result['files'][0]['segments'][0]['asset'], name)
            self.assertTrue((root / 'out' / name).exists())

    def test_bounded_assets_and_split_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = root / 'src'; source.mkdir()
            contents = {'a': b'a' * 50, 'big': b'b' * 25000, 'near': b'n' * 10240, 'z': b'z' * 100}
            for name, data in contents.items(): (source / name).write_bytes(data)
            manifest = packer.pack(source, root / 'out', limit=10240)
            self.assertEqual(manifest['layout'], 'whole')
            self.assertTrue(all(a['size'] <= 10240 and a['asset'].startswith('whole-') for a in manifest['assets']))
            for item in manifest['files']:
                self.assertEqual(len(item['segments']) > 1, item['path'] == 'big')
                pieces = []
                for seg in item['segments']:
                    with (root / 'out' / seg['asset']).open('rb') as f:
                        f.seek(seg['offset']); data = f.read(seg['length'])
                    self.assertEqual(hashlib.sha256(data).hexdigest(), seg['sha256'])
                    pieces.append(data)
                self.assertEqual(b''.join(pieces), contents[item['path']])
                self.assertEqual(hashlib.sha256(contents[item['path']]).hexdigest(), item['sha256'])
