import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qa.benchmark_install_journal import measure_handles


@unittest.skipUnless(os.name == 'nt', 'requires Windows native file handles')
class HandleBudgetTests(unittest.TestCase):
    def test_retains_more_than_crt_descriptor_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            payload = root / 'fixture.bin'
            payload.write_bytes(b'x')
            files = [{'path': payload.name, 'size': 1}] * 8200
            with patch('msvcrt.open_osfhandle', side_effect=AssertionError('used CRT descriptor')):
                count, elapsed, wall = measure_handles(root, files, budget=20)
            self.assertEqual(count, len(files))
            self.assertGreaterEqual(wall, elapsed)
            payload.unlink()

    def test_serial_and_parallel_pin_files_and_release_them_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            files = [{'path': f'{i}.bin', 'size': 1} for i in range(40)]
            for file in files:
                (root / file['path']).write_bytes(b'x')
            for workers in (1, 8):
                with self.subTest(workers=workers):
                    with patch('msvcrt.open_osfhandle', side_effect=AssertionError('used CRT descriptor')):
                        count, elapsed, wall = measure_handles(root, files, workers)
                    self.assertEqual(count, len(files))
                    self.assertGreaterEqual(wall, elapsed)
            with self.assertRaisesRegex(ValueError, 'Unexpected file identity or size'):
                measure_handles(root, [dict(files[0], size=3), *files[1:]], 8)
            for file in files:
                (root / file['path']).unlink()
