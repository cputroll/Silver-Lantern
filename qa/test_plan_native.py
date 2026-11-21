import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

def _entry(root, relative, data):
    return {'path': relative, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


class NativePlanParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location('hl_cli', Path('Get-HeyListen.py'))
        cls.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.cli)

    def _plan(self, root, files, native):
        if native:
            return self.cli.plan_game_files(root, files)
        with patch.object(self.cli, '_plan_game_files_native', return_value=None):
            return self.cli.plan_game_files(root, files)

    def _both(self, root, files):
        return self._plan(root, files, False), self._plan(root, files, True)

    def test_clean_corrupt_case_extra_parity(self):
        cases = ('clean', 'corrupt', 'case', 'extra')
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                (root / 'sub').mkdir()
                good = b'abc' * 100
                other = b'xyz' * 50
                (root / 'sub' / 'Exact.bin').write_bytes(good)
                (root / 'plain.dat').write_bytes(other)
                files = [_entry(root, 'sub/Exact.bin', good), _entry(root, 'plain.dat', other)]
                if case == 'corrupt':
                    (root / 'plain.dat').write_bytes(b'xyq' * 50)
                elif case == 'case':
                    (root / 'plain.dat').rename(root / 'Plain.dat')
                elif case == 'extra':
                    (root / 'unexpected.bin').write_bytes(b'zz')
                legacy, native = self._both(root, files)
                self.assertEqual([f['path'] for f in native[0]], [f['path'] for f in legacy[0]], case)
                self.assertEqual(native[1], legacy[1], case)

    def test_fallback_on_relative_root_is_identical(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'a.bin').write_bytes(b'1234')
            files = [_entry(root, 'a.bin', b'1234')]
            cwd = os.getcwd()
            os.chdir(root)
            try:
                legacy = self._plan(Path('.'), files, False)
                native = self._plan(Path('.'), files, True)
            finally:
                os.chdir(cwd)
            self.assertEqual(native, legacy)

    def test_missing_file_is_pending_in_both(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'a.bin').write_bytes(b'1234')
            files = [_entry(root, 'a.bin', b'1234'), _entry(root, 'gone.bin', b'xx')]
            legacy, native = self._both(root, files)
            self.assertEqual(native[0][0]['path'], 'gone.bin')
            self.assertEqual([f['path'] for f in native[0]], [f['path'] for f in legacy[0]])
            self.assertEqual(native[1], legacy[1])

    def _plan_proving_native(self, root, files):
        """Run the planner with the legacy inventory disabled: a pass proves the native path ran."""
        with patch.object(self.cli, 'destination_inventory',
                          side_effect=AssertionError('legacy planner used')):
            return self.cli.plan_game_files(root, files)

    def test_native_completes_for_wrong_size_missing_and_case_renamed(self):
        for case, prepare in (('wrong-size', lambda r, f: (r / f[1]['path']).write_bytes(b'x' * 5)),
                              ('missing', lambda r, f: (r / f[1]['path']).unlink(missing_ok=True)),
                              ('case-rename', lambda r, f: (r / f[1]['path']).rename(r / 'A2.bin'))):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                (root / 'a.bin').write_bytes(b'1234')
                second = {'path': 'a2.bin', 'size': 2, 'sha256': hashlib.sha256(b'ok').hexdigest()}
                (root / 'a2.bin').write_bytes(b'ok')
                files = [_entry(root, 'a.bin', b'1234'), dict(second, size=2)]
                prepare(root, files)
                pending, kept = self._plan_proving_native(root, files)
                self.assertEqual([f['path'] for f in pending], ['a2.bin'], case)
                self.assertEqual(kept, 4, case)

    @unittest.skipUnless(os.name == 'nt', 'requires Windows native inventory')
    def test_native_defers_paths_in_excluded_inventory_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for prefix in ('.HeyListen-Get', '.HeyListen-Parts'):
                with self.subTest(prefix=prefix):
                    item = _entry(root, f'{prefix}/payload.bin', b'abc')
                    self.assertIsNone(self.cli._plan_game_files_native(root, [item], set()))

    def test_native_excludes_running_launcher_without_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'launcher.exe').write_bytes(b'abc')
            files = [_entry(root, 'launcher.exe', b'abc'), _entry(root, 'a.bin', b'1234')]
            (root / 'a.bin').write_bytes(b'1234')
            with patch.object(self.cli, 'running_launcher_paths',
                              return_value={root / 'launcher.exe'}):
                pending, kept = self._plan_proving_native(root, files)
            self.assertEqual((pending, kept), ([], 4))


if __name__ == '__main__':
    unittest.main()
