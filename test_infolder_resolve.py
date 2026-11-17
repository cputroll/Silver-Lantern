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
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); primary, fallback, legacy = root / 'primary', root / 'fallback', root / 'legacy'
            for f in (primary, fallback, legacy): f.mkdir()
            with patch.object(launcher, 'load_game_folder', return_value=None), \
                 patch.object(launcher, 'game_folder_candidates', return_value=(primary, fallback, legacy)), \
                 patch.object(launcher, 'probe_writable'):
                self.assertEqual(launcher.resolve_game_folder(), legacy)

    def test_both_defaults_unwritable_still_resolve_game_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); primary, fallback, legacy = root / 'primary', root / 'fallback', root / 'legacy'
            for f in (primary, fallback): f.mkdir()
            with patch.object(launcher, 'load_game_folder', return_value=None), \
                 patch.object(launcher, 'game_folder_candidates', return_value=(primary, fallback, legacy)), \
                 patch.object(launcher, 'probe_writable', side_effect=PermissionError):
                self.assertEqual(launcher.resolve_game_folder(), fallback)

    def test_unwritable_primary_falls_back(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); primary, fallback, legacy = root / 'primary', root / 'fallback', root / 'legacy'
            for f in (primary, fallback): f.mkdir()
            def probe(path):
                if path == primary: raise PermissionError('denied')
            with patch.object(launcher, 'load_game_folder', return_value=None), \
                 patch.object(launcher, 'game_folder_candidates', return_value=(primary, fallback, legacy)), \
                 patch.object(launcher, 'probe_writable', side_effect=probe):
                self.assertEqual(launcher.resolve_game_folder(), fallback)

    def test_resumes_existing_partial_install_not_a_fresh_folder(self):
        import json
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            primary, fallback, legacy = root / 'primary', root / 'fallback', root / 'legacy'
            for f in (primary, fallback, legacy): f.mkdir()
            cache = primary / '.heylisten-get'; cache.mkdir()
            (cache / 'game-manifest.json').write_text(json.dumps({'files': []}))
            (primary / 'user-save.dat').write_text('keep me')
            with patch.object(launcher, 'load_game_folder', return_value=None), \
                 patch.object(launcher, 'game_folder_candidates', return_value=(primary, fallback, legacy)), \
                 patch.object(launcher, 'probe_writable') as probe:
                self.assertEqual(launcher.resolve_game_folder(), primary)
                probe.assert_not_called()
            self.assertEqual((primary / 'user-save.dat').read_text(), 'keep me')

    def test_partial_download_files_are_adopted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            primary, fallback, legacy = root / 'primary', root / 'fallback', root / 'legacy'
            for f in (primary, fallback, legacy): f.mkdir()
            (primary / 'whole-07.bin.part').write_bytes(b'partial')
            with patch.object(launcher, 'load_game_folder', return_value=None), \
                 patch.object(launcher, 'game_folder_candidates', return_value=(primary, fallback, legacy)):
                self.assertEqual(launcher.resolve_game_folder(), primary)

    def test_adopts_newest_candidate_when_multiple_have_data(self):
        import json, os
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            primary, fallback, legacy = root / 'primary', root / 'fallback', root / 'legacy'
            for f in (primary, fallback, legacy): f.mkdir()
            for f in (primary, fallback):
                cache = f / '.heylisten-get'; cache.mkdir()
                (cache / 'game-manifest.json').write_text(json.dumps({'files': []}))
            os.utime(primary / '.heylisten-get' / 'game-manifest.json', (100, 100))
            os.utime(fallback / '.heylisten-get' / 'game-manifest.json', (200, 200))
            with patch.object(launcher, 'load_game_folder', return_value=None), \
                 patch.object(launcher, 'game_folder_candidates', return_value=(primary, fallback, legacy)):
                self.assertEqual(launcher.resolve_game_folder(), fallback)

    def test_no_data_keeps_fallback_order(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            primary, fallback, legacy = root / 'primary', root / 'fallback', root / 'legacy'
            for f in (primary, fallback, legacy): f.mkdir()
            with patch.object(launcher, 'load_game_folder', return_value=None), \
                 patch.object(launcher, 'game_folder_candidates', return_value=(primary, fallback, legacy)), \
                 patch.object(launcher, 'probe_writable'):
                self.assertEqual(launcher.resolve_game_folder(), legacy)  # legacy exists first
            legacy.rmdir()
            with patch.object(launcher, 'load_game_folder', return_value=None), \
                 patch.object(launcher, 'game_folder_candidates', return_value=(primary, fallback, legacy)), \
                 patch.object(launcher, 'probe_writable'):
                self.assertEqual(launcher.resolve_game_folder(), primary)  # then writable primary
