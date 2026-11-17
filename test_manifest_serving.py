import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import launcher


class ManifestServingTests(unittest.TestCase):
    def test_autoship_publishes_through_atomic_channel(self):
        script = (Path(__file__).parent / 'AutoShip-Game.ps1').read_text(encoding='utf-8')
        self.assertNotIn('Publishing disabled', script, 'publisher must not be disabled')
        # Bundles and the revisioned manifest are immutable: uploaded without --clobber.
        bundle_upload = "Run 'gh' @('release', 'upload', 'game-v1', (Join-Path $work $asset.asset), '--repo', 'cputroll/Silver-Lantern')"
        self.assertIn(bundle_upload, script)
        manifest_upload = "Run 'gh' @('release', 'upload', 'game-v1', $manifestFile, '--repo', 'cputroll/Silver-Lantern')"
        self.assertIn(manifest_upload, script)
        # The channel pointer advances by fast-forward push before the legacy fixed
        # manifest is replaced, so clients never see a partial or inverted switch.
        channel_push = "Run 'git' @('-C', $channelWork, 'push', 'origin', 'HEAD:game-channel')"
        legacy_upload = "Run 'gh' @('release', 'upload', 'game-v1', $legacy, '--clobber', '--repo', 'cputroll/Silver-Lantern')"
        self.assertIn(channel_push, script)
        self.assertIn(legacy_upload, script)
        self.assertLess(script.index(channel_push), script.index(legacy_upload))
        # The pointer retains the previous revision for rollback.
        self.assertIn('previous', script)

    def test_configured_game_manifest_url_and_fallback(self):
        with tempfile.TemporaryDirectory() as d, patch.object(launcher, 'BASE', Path(d)):
            (Path(d) / 'sources.json').write_text(json.dumps({'game': {'game_manifest_url': 'https://example.com/game.json'}}))
            self.assertEqual(launcher.game_manifest_url(), 'https://example.com/game.json')
            (Path(d) / 'sources.json').unlink()
            self.assertIn('/releases/download/game-v1/game-manifest.json', launcher.game_manifest_url())
