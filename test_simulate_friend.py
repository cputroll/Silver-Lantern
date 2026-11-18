import tempfile
import unittest
from pathlib import Path
from qa.simulate_friend import fixture_manifest, run_check


class SimulatorTests(unittest.TestCase):
    def test_fixture_manifest_contains_small_valid_asset(self):
        with tempfile.TemporaryDirectory() as d:
            manifest = fixture_manifest(Path(d))
            import json
            self.assertLess(manifest.stat().st_size, 10000)
            self.assertTrue(list((Path(d) / 'assets').glob('*.tar')))
            self.assertEqual({item['path'] for item in json.loads(manifest.read_text())['files']}, {'bin/HeyListen.exe', 'hookshot_version.txt'})

    def test_invalid_zip_makes_runner_exit_nonzero(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as d:
            result = subprocess.run([sys.executable, str(Path(__file__).parent / 'qa' / 'simulate_friend.py'), '--zip', str(Path(d) / 'missing.zip'), '--artifacts', str(Path(d) / 'artifacts')], capture_output=True, timeout=20)
            self.assertNotEqual(result.returncode, 0)

    def test_seeded_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            ok = run_check('seeded crash', Path(d), lambda: (_ for _ in ()).throw(RuntimeError('seeded crash')))
            self.assertFalse(ok)
            self.assertIn('seeded crash', (Path(d) / 'seeded crash.log').read_text())
