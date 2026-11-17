import unittest
from unittest.mock import patch
from test_game_sync import h


class AssemblyPoolTests(unittest.TestCase):
    def test_worker_count_default_and_bounds(self):
        for value, expected in [(None, 4), ('0', 1), ('5', 5), ('99', 8), ('bad', 4)]:
            with self.subTest(value=value), patch.dict(h.os.environ, {}, clear=True):
                if value is not None:
                    h.os.environ['SILVER_LANTERN_ASSEMBLY_WORKERS'] = value
                self.assertEqual(h.assembly_workers(), expected)
