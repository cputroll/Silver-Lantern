import unittest
from unittest.mock import patch
from test_game_sync import h


class FixtureAssetSeamTests(unittest.TestCase):
    def test_override_is_loopback_only(self):
        with patch.dict(h.os.environ, {'SILVER_LANTERN_TEST_ASSET_URL': 'http://127.0.0.1:1234/'}):
            self.assertEqual(h.game_asset_base(), 'http://127.0.0.1:1234/')
        with patch.dict(h.os.environ, {'SILVER_LANTERN_TEST_ASSET_URL': 'https://example.com/'}):
            with self.assertRaises(ValueError):
                h.game_asset_base()
        with patch.dict(h.os.environ, {'SILVER_LANTERN_TEST_ASSET_URL': ''}):
            self.assertEqual(h.game_asset_base(), h.GAME_ASSET_URL)
