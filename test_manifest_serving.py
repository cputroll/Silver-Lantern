import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import launcher


class ManifestServingTests(unittest.TestCase):
    def test_autoship_only_clobbers_the_manifest(self):
        script = (Path(__file__).parent / 'AutoShip-Game.ps1').read_text(encoding='utf-8')
        upload_lines = [line for line in script.splitlines() if "'release', 'upload'" in line]
        self.assertEqual(len(upload_lines), 2)
        self.assertNotIn('--clobber', upload_lines[0])
        self.assertIn('$repoManifest', upload_lines[1])
        self.assertIn('--clobber', upload_lines[1])
        self.assertIn('Bundle asset already exists', script)

    def test_configured_game_manifest_url_and_fallback(self):
        with tempfile.TemporaryDirectory() as d, patch.object(launcher, 'BASE', Path(d)):
            (Path(d) / 'sources.json').write_text(json.dumps({'game': {'game_manifest_url': 'https://example.com/game.json'}}))
            self.assertEqual(launcher.game_manifest_url(), 'https://example.com/game.json')
            (Path(d) / 'sources.json').unlink()
            self.assertIn('/releases/download/game-v1/game-manifest.json', launcher.game_manifest_url())
