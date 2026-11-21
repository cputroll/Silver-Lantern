import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(os.name == 'nt', 'requires Windows')
class NativeScanTests(unittest.TestCase):
    def test_expected_identity_rejects_identical_replacement(self):
        from install_scan_windows import PlainTree

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / 'file'
            path.write_bytes(b'original')
            with PlainTree(root) as tree:
                with tree.open('file') as stream:
                    identity = tree.identity(stream)
                with tree.open('file', expected_identity=identity) as stream:
                    self.assertEqual(stream.read(), b'original')
                volume, file_id = identity
                high_byte_changed = file_id[:-1] + bytes([file_id[-1] ^ 1])
                for expected in ((volume ^ 1, file_id), (volume, high_byte_changed)):
                    with self.subTest(expected=expected), self.assertRaisesRegex(ValueError, 'File identity changed'):
                        tree.open('file', expected_identity=expected)
                path.rename(root / 'old')
                path.write_bytes(b'original')
                with self.assertRaisesRegex(ValueError, 'File identity changed'):
                    tree.open('file', expected_identity=identity)
                path.unlink()

    def test_open_writer_is_not_mistaken_for_a_missing_file(self):
        from install_scan_windows import PlainTree

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / 'file'
            path.write_bytes(b'original')
            with PlainTree(root) as tree, path.open('r+b'):
                with self.assertRaises(OSError) as conflict:
                    tree.open('file')
                self.assertEqual(conflict.exception.winerror, 32)
                with self.assertRaises(FileNotFoundError):
                    tree.open('missing')

    def test_hash_reads_current_bytes_and_refuses_ambiguous_paths(self):
        from install_scan_windows import PlainTree

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'file').write_bytes(b'changed!')
            with PlainTree(root) as tree:
                with tree.open('file') as stream:
                    self.assertNotEqual(hashlib.file_digest(stream, 'sha256').digest(),
                                        hashlib.sha256(b'original').digest())
                for path in ['file:stream', '../file', 'file.', 'file ', 'file\x00', 'CON.txt']:
                    with self.subTest(path=path), self.assertRaises(OSError):
                        tree.open(path)

    def test_reads_bound_file_and_rejects_junction(self):
        from install_scan_windows import PlainTree

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            root = base / 'game'
            (root / 'bin').mkdir(parents=True)
            (root / 'bin' / 'file').write_bytes(b'expected')
            (base / 'other').mkdir()
            (base / 'other' / 'file').write_bytes(b'outside!')
            with PlainTree(root) as tree:
                with tree.open('bin/file') as stream:
                    self.assertEqual(stream.read(), b'expected')
                subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(root / 'link'), str(base / 'other')],
                               check=True, capture_output=True, timeout=5)
                with self.assertRaises(OSError):
                    tree.open('link/file')

    def test_swapped_child_and_linked_install_root_are_refused(self):
        from install_scan_windows import PlainTree

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            root = base / 'game'
            (root / 'bin').mkdir(parents=True)
            outside = base / 'outside'
            outside.mkdir()
            (outside / 'file').write_bytes(b'outside')
            with PlainTree(root) as tree:
                (root / 'bin').rename(root / 'old')
                subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(root / 'bin'), str(outside)],
                               check=True, capture_output=True, timeout=5)
                with self.assertRaises(OSError):
                    tree.open('bin/file')
            with self.assertRaises(OSError), PlainTree(root / 'bin'):
                self.fail('opened linked root')
