import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import launcher


class ManifestServingTests(unittest.TestCase):
    def test_autoship_publishes_through_atomic_channel(self):
        script = (Path(__file__).parent / 'AutoShip-Game.ps1').read_text(encoding='utf-8')
        self.assertIn('SILVERLANTERN_PUBLISH_HOST', script, 'publisher requires an explicitly designated host')
        # Bundles and the revisioned manifest are immutable: uploaded without --clobber.
        bundle_upload = "Run 'gh' @('release', 'upload', 'game-v1', (Join-Path $work $asset.asset), '--repo', 'cputroll/Silver-Lantern')"
        self.assertIn(bundle_upload, script)
        manifest_upload = "Run 'gh' @('release', 'upload', 'game-v1', $manifestFile, '--repo', 'cputroll/Silver-Lantern')"
        self.assertIn(manifest_upload, script)
        # Publish immutable assets before advancing the leased channel; the fixed
        # legacy manifest must not be replaced because it can roll back across hosts.
        channel_push = "'channel_publisher.py'"
        self.assertIn(channel_push, script)
        self.assertLess(script.index(bundle_upload), script.index(channel_push))
        self.assertLess(script.index(manifest_upload), script.index(channel_push))
        self.assertNotRegex(script, r"(?im)^\s*Run\s+'gh'\s+@\('release',\s*'upload'[^\r\n]*(?:\$legacy|game-manifest\.json)")
        # The pointer retains the previous revision for rollback.
        self.assertIn('previous', script)

    def test_configured_game_manifest_url_and_fallback(self):
        with tempfile.TemporaryDirectory() as d, patch.object(launcher, 'BASE', Path(d)):
            (Path(d) / 'sources.json').write_text(json.dumps({'game': {'game_manifest_url': 'https://example.com/game.json'}}))
            self.assertEqual(launcher.game_manifest_url(), 'https://example.com/game.json')
            (Path(d) / 'sources.json').unlink()
            self.assertIn('/releases/download/game-v1/game-manifest.json', launcher.game_manifest_url())
