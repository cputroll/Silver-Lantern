import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import launcher
from test_game_sync import h


class ManifestOwnershipTests(unittest.TestCase):
    def test_cached_manifest_preferred(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); cache = root / '.heylisten-get' / 'game-manifest.json'
            cache.parent.mkdir(); cache.write_text(json.dumps({'files': [{'path': 'game.bin', 'size': 3}]}))
            with patch.object(launcher.urllib.request, 'urlopen') as fetch:
                self.assertEqual(launcher.ownership_manifest(root)['files'][0]['size'], 3)
                fetch.assert_not_called()

    def test_missing_cache_fetches_manifest(self):
        with tempfile.TemporaryDirectory() as d, patch.object(launcher, 'fetch_ownership_manifest', return_value={'files': []}) as fetch:
            self.assertEqual(launcher.ownership_manifest(Path(d)), {'files': []})
            fetch.assert_called_once()

    def test_fetch_failure_is_conservative(self):
        with tempfile.TemporaryDirectory() as d, patch.object(launcher, 'fetch_ownership_manifest', side_effect=OSError):
            self.assertIsNone(launcher.ownership_manifest(Path(d)))

    def test_size_mismatch_is_not_owned(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / 'game.bin').write_bytes(b'abc'); (root / 'mod.bin').write_bytes(b'x')
            manifest = {'files': [{'path': 'game.bin', 'size': 3}, {'path': 'mod.bin', 'size': 2}]}
            self.assertEqual(launcher.manifest_owned_files(root, manifest), [root / 'game.bin'])

    def test_gui_exposes_cache_refresh(self):
        self.assertTrue(callable(getattr(launcher, 'cache_game_manifest', None)),
                        'GUI update checks must refresh the game manifest cache')

    def test_cache_persists_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); h.cache_game_manifest(root, {'files': []})
            self.assertEqual(json.loads((root / '.heylisten-get' / 'game-manifest.json').read_text()), {'files': []})
