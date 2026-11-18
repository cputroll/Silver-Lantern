import tempfile
import unittest
from pathlib import Path
from qa.simulate_friend import fixture_manifest, run_check


class SimulatorTests(unittest.TestCase):
    def test_traceback_classifier_and_log_window(self):
        from qa.simulate_friend import has_crash_traceback, log_window
        self.assertFalse(has_crash_traceback('[12:00:00] recoverable-retry: Traceback (most recent call last):\nrecoverable-retry: PermissionError: locked\n'))
        self.assertTrue(has_crash_traceback('[12:00:00] Traceback (most recent call last):\nPermissionError: crash\n'))
        self.assertTrue(has_crash_traceback('recoverable-retry: Traceback (most recent call last):\n[12:00:01] Traceback (most recent call last):\n'))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'test.log'
            p.write_text('Traceback old\n', encoding='utf-8')
            offset = p.stat().st_size
            with p.open('a', encoding='utf-8') as f: f.write('safe\n')
            self.assertFalse(has_crash_traceback(log_window(p, offset)))
            with p.open('a', encoding='utf-8') as f: f.write('Traceback new\n')
            self.assertTrue(has_crash_traceback(log_window(p, offset)))

    def test_recycled_pid_is_not_a_live_tracked_process(self):
        from qa.simulate_friend import live_identities
        self.assertEqual(live_identities({(12, 100)}, {12: 200}), set())

    def test_reused_parent_cannot_adopt_older_unrelated_process(self):
        from qa.simulate_friend import descendant_identities
        rows = [{'ProcessId': 1, 'ParentProcessId': 0, 'Created': 200}, {'ProcessId': 2, 'ParentProcessId': 1, 'Created': 100}, {'ProcessId': 3, 'ParentProcessId': 1, 'Created': 300}]
        self.assertEqual(descendant_identities(rows, 1), {(1, 200), (3, 300)})

    def test_real_live_child_is_reported(self):
        from qa.simulate_friend import live_identities
        self.assertEqual(live_identities({(12, 100)}, {12: 100}), {(12, 100)})

    @unittest.skipUnless(__import__('os').name == 'nt', 'Windows process identity test')
    def test_real_child_identity_is_live_until_exit(self):
        import subprocess
        import sys
        from qa.simulate_friend import process_creation_time, live_identities
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            created = process_creation_time(child.pid)
            self.assertIsNotNone(created)
            self.assertEqual(live_identities({(child.pid, created)}, {child.pid: process_creation_time(child.pid)}), {(child.pid, created)})
        finally:
            child.terminate(); child.wait(timeout=10)
        self.assertIsNone(process_creation_time(child.pid))

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
