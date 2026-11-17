import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import launcher


class ResolveGameFolderTests(unittest.TestCase):
    def test_bom_config_preserves_configured_destination(self):
        import json
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); config = root / 'launcher.json'; game = root / 'chosen'
            config.write_text(json.dumps({'game_dir': str(game)}), encoding='utf-8-sig')
            with patch.object(launcher, 'CONFIG', config):
                self.assertEqual(launcher.resolve_game_folder(), game)
                self.assertEqual(launcher.defender_config()['game_dir'], str(game))

    def test_configured_folder_wins(self):
        with patch.object(launcher, 'load_game_folder', return_value=Path('custom')), patch.object(launcher, 'probe_writable'):
            self.assertEqual(launcher.resolve_game_folder(), Path('custom'))

    def test_legacy_folder_is_adopted(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(launcher.os.environ, {'USERPROFILE': temp}), patch.object(launcher, 'load_game_folder', return_value=None), patch.object(launcher, 'probe_writable'):
            legacy = Path(temp) / 'Games' / 'HeyListen'; legacy.mkdir(parents=True)
            self.assertEqual(launcher.resolve_game_folder(), legacy)

    def test_both_defaults_unwritable_still_resolve_game_folder(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(launcher.os.environ, {'USERPROFILE': temp}), patch.object(launcher, 'load_game_folder', return_value=None), patch.object(launcher, 'probe_writable', side_effect=PermissionError):
            self.assertEqual(launcher.resolve_game_folder(), Path(temp) / 'Games' / launcher.GAME_NAME)

    def test_unwritable_primary_falls_back(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(launcher.os.environ, {'USERPROFILE': temp}), patch.object(launcher, 'load_game_folder', return_value=None):
            def probe(path):
                if path == Path(r'C:\Games') / launcher.GAME_NAME: raise PermissionError('denied')
            with patch.object(launcher, 'probe_writable', side_effect=probe):
                self.assertEqual(launcher.resolve_game_folder(), Path(temp) / 'Games' / launcher.GAME_NAME)
