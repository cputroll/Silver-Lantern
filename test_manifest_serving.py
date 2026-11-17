import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import launcher


class ManifestServingTests(unittest.TestCase):
    def test_legacy_autoship_publication_is_blocked(self):
        script = (Path(__file__).parent / 'AutoShip-Game.ps1').read_text(encoding='utf-8')
        self.assertIn("throw 'Publishing disabled: atomic game-channel publisher", script)
        self.assertLess(script.index("throw 'Publishing disabled:"), script.index("Run 'gh' @('release', 'upload'"))

    def test_configured_game_manifest_url_and_fallback(self):
        with tempfile.TemporaryDirectory() as d, patch.object(launcher, 'BASE', Path(d)):
            (Path(d) / 'sources.json').write_text(json.dumps({'game': {'game_manifest_url': 'https://example.com/game.json'}}))
            self.assertEqual(launcher.game_manifest_url(), 'https://example.com/game.json')
            (Path(d) / 'sources.json').unlink()
            self.assertIn('/releases/download/game-v1/game-manifest.json', launcher.game_manifest_url())
