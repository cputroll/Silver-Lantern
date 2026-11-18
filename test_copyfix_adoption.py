import os
import tempfile
import unittest
from pathlib import Path

import launcher


class AdoptionTests(unittest.TestCase):
    def test_interrupted_first_asset_is_adopted_and_ranked_by_cache_time(self):
        with tempfile.TemporaryDirectory() as directory:
            old, new = Path(directory) / 'old', Path(directory) / 'new'
            for folder in (old, new):
                (folder / '.heylisten-parts').mkdir(parents=True)
                (folder / '.heylisten-parts' / 'bundle.bin.part').write_bytes(b'partial')
            os.utime(old / '.heylisten-parts' / 'bundle.bin.part', (1, 1))
            self.assertEqual(launcher.adopt_existing([old, new]), new)

    def test_empty_cache_and_non_asset_files_are_not_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            cache = folder / '.heylisten-parts'
            cache.mkdir()
            (cache / 'notes.txt').write_text('hello')
            (cache / 'bad name.part').write_bytes(b'x')
            self.assertFalse(launcher.has_game_data(folder))
