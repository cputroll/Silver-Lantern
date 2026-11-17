import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from test_game_sync import h
from test_game_v2 import sample

class WholeDispatch(unittest.TestCase):
    def test_whole_uses_segment_scheduler(self):
        man = sample(); man['layout'] = 'whole'
        with tempfile.TemporaryDirectory() as tmp, patch.object(h, 'check_disk_space'), patch.object(h, 'assemble_segmented') as scheduler:
            h.install_game(object(), man, Path(tmp))
            scheduler.assert_called_once()
