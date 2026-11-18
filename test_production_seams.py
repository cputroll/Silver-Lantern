import importlib.util
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock, MagicMock

from test_registration_seams import load_launcher


class ProductionSeamTests(unittest.TestCase):
    def test_environment_cannot_enable_seams_in_production(self):
        env = {'SILVER_LANTERN_GAME_MANIFEST': 'evil.json',
               'SILVER_LANTERN_TEST_ASSET_URL': 'http://127.0.0.1:1234/',
               'SILVER_LANTERN_ALLOW_LOCAL_UPDATE': '1'}
        with patch.dict(sys.modules, {'build_flags': SimpleNamespace(QA_SEAMS=False)}), patch.dict(os.environ, env):
            launcher = load_launcher(env)
            spec = importlib.util.spec_from_file_location('production_installer', Path(__file__).with_name('Get-HeyListen.py'))
            app = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(app)
            self.assertIsNone(launcher._TEST_GAME_MANIFEST)
            channel = MagicMock()
            channel.fetch_for_folder.return_value = {'pinned': True}
            with patch.dict(sys.modules, {'game_channel': channel, '__main__': app}):
                self.assertEqual(launcher.fetch_channel_manifest(Path('game'), Mock()), {'pinned': True})
            self.assertEqual(channel.fetch_for_folder.call_args.kwargs['legacy_url'], launcher.game_manifest_url())
            self.assertEqual(app.game_asset_base(), app.GAME_ASSET_URL)
            with patch.object(sys, 'frozen', True, create=True):
                with self.assertRaisesRegex(ValueError, 'unsigned local updates are disabled'):
                    app.check_self_update(Mock(), {}, [], 'fixture.zip')
