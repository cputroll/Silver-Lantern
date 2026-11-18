"""The experimental guard must remain inert until its safety contract is proven."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from https_push_guard import guarded_push


class Transport:
    destination = 'https://secret-host.example/secret-path/repo'
    REF = 'refs/heads/game-channel'

    def push(self, *args, **kwargs):
        raise AssertionError('push must not run')

    def probe(self):
        raise AssertionError('probe must not run')


class GuardTests(unittest.TestCase):
    def test_unverified_transport_never_creates_evidence_or_pushes(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'marker.json'
            with self.assertRaisesRegex(RuntimeError, 'disabled') as error:
                guarded_push(Transport(), 'a' * 40, expected='b' * 40, marker=marker)
            self.assertFalse(marker.exists())
            self.assertNotIn('secret', str(error.exception))

    def test_existing_evidence_is_never_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'marker.json'
            marker.write_bytes(b'existing evidence')
            with self.assertRaisesRegex(RuntimeError, 'disabled'):
                guarded_push(Transport(), 'a' * 40, expected=None, marker=marker)
            self.assertEqual(marker.read_bytes(), b'existing evidence')

    def test_guard_does_not_call_io_operations(self):
        for operation in ('pathlib.Path.open', 'os.fsync', 'pathlib.Path.unlink'):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory:
                marker = Path(directory) / 'marker.json'
                with patch(operation, side_effect=OSError('injected I/O failure')) as injected:
                    with self.assertRaisesRegex(RuntimeError, 'disabled'):
                        guarded_push(Transport(), 'a' * 40, expected=None, marker=marker)
                    injected.assert_not_called()
                self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()
