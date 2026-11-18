import tempfile
import unittest
from pathlib import Path
from test_game_sync import h


class InvalidGamePathTest(unittest.TestCase):
    def test_escape_identifies_entry_and_root_and_is_friendly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError) as caught:
                h.game_path(root, '../outside.exe')
            self.assertIn('../outside.exe', str(caught.exception))
            self.assertIn(str(root), str(caught.exception))
            error = h.friendly(caught.exception)
            self.assertIn('game package contains an invalid entry', error.problem.lower())
            self.assertIn('../outside.exe', error.problem)
