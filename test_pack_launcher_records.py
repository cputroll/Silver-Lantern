import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('pack_game_records', Path(__file__).with_name('pack-game.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class LauncherRecordsTests(unittest.TestCase):
    def test_recorded_renamed_launcher_excluded_but_changed_game_exe_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'game'
            source.mkdir()
            (source / 'renamed.exe').write_bytes(b'launcher')
            (source / 'game.exe').write_bytes(b'actual game')
            records = source / '.heylisten-get'
            records.mkdir()
            (records / 'launcher-copies.json').write_text(json.dumps({
                'renamed.exe': hashlib.sha256(b'launcher').hexdigest(),
                'game.exe': hashlib.sha256(b'old launcher').hexdigest(),
            }))
            manifest = module.pack(source, root / 'out')
            paths = {entry['path'] for entry in manifest['files']}
            self.assertNotIn('renamed.exe', paths)
            self.assertIn('game.exe', paths)
