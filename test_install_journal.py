import struct
import os
import tempfile
import unittest


def record(file_id, parent_id, usn, major=2):
    return struct.pack('<IHHQQqqIIIIHH', 64, major, 0, file_id, parent_id,
                       usn, 0, 1, 0, 0, 0, 0, 60) + b'\0' * 4


class JournalTests(unittest.TestCase):
    def test_receipt_requires_exact_identity_and_available_history(self):
        from install_journal import receipt_matches
        receipt = {'manifest': 'manifest-a', 'root': [1, 8], 'journal': 33,
                   'cursor': 100, 'ids': [5, 8]}
        read = lambda _: struct.pack('<q', 300) + record(16, 3, 120)
        self.assertTrue(receipt_matches(receipt, 'manifest-a', (1, 8), (33, 50, 300), read))
        for manifest, root, bounds in [('manifest-b', (1, 8), (33, 50, 300)),
                                       ('manifest-a', (2, 8), (33, 50, 300)),
                                       ('manifest-a', (1, 8), (34, 50, 300)),
                                       ('manifest-a', (1, 8), (33, 101, 300))]:
            self.assertFalse(receipt_matches(receipt, manifest, root, bounds, read))

    @unittest.skipUnless(os.name == 'nt', 'requires Windows')
    def test_verify_rejects_a_synthetic_disk_receipt_at_current_cursor(self):
        from pathlib import Path
        from install_journal import VolumeJournal, verify_baseline
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            info = root.stat()
            with VolumeJournal(root) as journal:
                identity, _, end = journal.bounds()
            forged = {'manifest': 'manifest-a', 'root': [info.st_dev, info.st_ino],
                      'journal': identity, 'cursor': end, 'ids': [info.st_ino]}
            self.assertFalse(verify_baseline(root, 'manifest-a', forged))

    def replay(self, block, *, start=100, end=300):
        import install_journal
        return install_journal.unchanged(lambda _: block, start, end, {5, 8})

    def test_unrelated_record_keeps_baseline(self):
        self.assertTrue(self.replay(struct.pack('<q', 300) + record(16, 3, 120)))

    def test_existing_file_or_child_change_invalidates(self):
        for file_id, parent_id in [(5, 3), (16, 8)]:
            with self.subTest(file_id=file_id):
                self.assertFalse(self.replay(struct.pack('<q', 300) + record(file_id, parent_id, 120)))

    def test_incomplete_or_unsupported_replay_is_not_evidence(self):
        for block in [b'', struct.pack('<q', 100), struct.pack('<q', 300) + b'bad',
                      struct.pack('<q', 300) + record(16, 3, 120, major=9),
                      struct.pack('<q', 300) + record(16, 3, 99)]:
            with self.subTest(block=block):
                self.assertFalse(self.replay(block))

    def test_v3_record_uses_all_128_bits_of_file_and_parent_ids(self):
        from install_journal import unchanged

        high = 1 << 96
        for file_id, parent_id in ((high + 5, 8), (5, high + 8)):
            block = (struct.pack('<qIHH', 300, 80, 3, 0)
                     + file_id.to_bytes(16, 'little') + parent_id.to_bytes(16, 'little')
                     + struct.pack('<q', 120) + b'\0' * 32)
            with self.subTest(file_id=file_id, parent_id=parent_id):
                self.assertFalse(unchanged(lambda _: block, 100, 300, {file_id}))
                self.assertFalse(unchanged(lambda _: block, 100, 300, {parent_id}))
                self.assertTrue(unchanged(lambda _: block, 100, 300,
                                          {file_id ^ high, parent_id ^ high}))

    @unittest.skipUnless(os.name == 'nt', 'requires Windows')
    def test_native_journal_detects_a_new_child(self):
        from pathlib import Path
        from install_journal import VolumeJournal, unchanged
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with VolumeJournal(root) as journal:
                identity, _, start = journal.bounds()
                self.assertTrue(unchanged(journal.reader(identity), start, start, {root.stat().st_ino}))
                (root / 'new.txt').write_bytes(b'new')
                next_identity, first, end = journal.bounds()
                self.assertEqual(next_identity, identity)
                self.assertLessEqual(first, start)
                self.assertGreater(end, start)
                self.assertFalse(unchanged(journal.reader(identity), start, end, {root.stat().st_ino}))

    @unittest.skipUnless(os.name == 'nt', 'requires Windows')
    def test_baseline_blocks_writers_and_refuses_an_existing_writer(self):
        from pathlib import Path
        from install_journal import open_baseline_file
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'file'
            path.write_bytes(b'original')
            with open_baseline_file(path) as verified:
                self.assertEqual(verified.read(), b'original')
                with self.assertRaises(OSError):
                    path.open('r+b')
            with path.open('r+b'):
                with self.assertRaises(OSError):
                    open_baseline_file(path)

    @unittest.skipUnless(os.name == 'nt', 'requires Windows')
    def test_clean_baseline_and_same_size_edit_with_restored_mtime(self):
        import hashlib
        from pathlib import Path
        from install_journal import build_baseline, verify_baseline
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'file'
            path.write_bytes(b'original')
            files = [{'path': 'file', 'size': 8, 'sha256': hashlib.sha256(b'original').hexdigest()}]
            receipt = build_baseline(root, 'manifest-a', files)
            self.assertIsNotNone(receipt)
            self.assertTrue(verify_baseline(root, 'manifest-a', receipt))
            before = path.stat()
            path.write_bytes(b'corrupt!')
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            self.assertFalse(verify_baseline(root, 'manifest-a', receipt))
            self.assertIsNone(build_baseline(root, 'manifest-a', files))

    @unittest.skipUnless(os.name == 'nt', 'requires Windows')
    def test_baseline_rejects_a_surviving_writable_mapping(self):
        import mmap
        from pathlib import Path
        from install_journal import open_baseline_file
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'file'
            path.write_bytes(b'original')
            with path.open('r+b') as stream:
                mapping = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_WRITE)
            with mapping, self.assertRaises(OSError):
                open_baseline_file(path)

    @unittest.skipUnless(os.name == 'nt', 'requires Windows')
    def test_baseline_rejects_hardlink_write_and_directory_rename(self):
        import hashlib
        from pathlib import Path
        from install_journal import build_baseline, verify_baseline
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            root = base / 'game'
            sub = root / 'bin'
            sub.mkdir(parents=True)
            path = sub / 'file'
            path.write_bytes(b'original')
            alias = base / 'alias'
            os.link(path, alias)
            files = [{'path': 'bin/file', 'size': 8, 'sha256': hashlib.sha256(b'original').hexdigest()}]
            receipt = build_baseline(root, 'manifest-a', files)
            self.assertIsNotNone(receipt)
            self.assertTrue(verify_baseline(root, 'manifest-a', receipt))
            alias.write_bytes(b'corrupt!')
            self.assertFalse(verify_baseline(root, 'manifest-a', receipt))
            alias.write_bytes(b'original')
            receipt = build_baseline(root, 'manifest-a', files)
            self.assertIsNotNone(receipt)
            sub.rename(root / 'other')
            self.assertFalse(verify_baseline(root, 'manifest-a', receipt))
