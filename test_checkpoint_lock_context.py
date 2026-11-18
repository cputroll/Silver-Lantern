import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qa.simulate_friend import has_crash_traceback
from test_game_sync import h


class CheckpointLockContextTests(unittest.TestCase):
    def test_missing_checkpoint_does_not_leak_context_during_locked_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory) / 'staging'
            temp.write_bytes(b'partial')
            original_unlink = Path.unlink
            attempts = []
            logs = []

            def unlink(path, *args, **kwargs):
                if path == temp and not attempts:
                    attempts.append(1)
                    raise PermissionError(13, 'locked')
                return original_unlink(path, *args, **kwargs)

            with patch.object(Path, 'unlink', unlink), patch.object(h, 'log', side_effect=lambda message: logs.append(str(message))), patch.object(h, 'say'), patch.object(h.time, 'sleep'):
                index, _ = h.restore_segment_checkpoint(temp, Path(directory) / 'missing.json', {'segments': [{}, {}]})

            self.assertEqual(index, 0)
            self.assertFalse(temp.exists())
            self.assertEqual(len(attempts), 1)
            self.assertFalse(has_crash_traceback('\n'.join(logs)), '\n'.join(logs))
            self.assertTrue(any('recoverable-retry:' in line for line in logs))

    def test_unexpected_cleanup_cause_remains_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory) / 'staging'
            temp.write_bytes(b'partial')
            original_unlink = Path.unlink
            attempts = []
            logs = []

            def unlink(path, *args, **kwargs):
                if path == temp and not attempts:
                    attempts.append(1)
                    try:
                        raise ValueError('unexpected cleanup cause')
                    except ValueError as cause:
                        raise PermissionError(13, 'locked') from cause
                return original_unlink(path, *args, **kwargs)

            with patch.object(Path, 'unlink', unlink), patch.object(h, 'log', side_effect=lambda message: logs.append(str(message))), patch.object(h, 'say'), patch.object(h.time, 'sleep'):
                h.restore_segment_checkpoint(temp, Path(directory) / 'missing.json', {'segments': [{}, {}]})

            self.assertTrue(any('unexpected cleanup cause' in line for line in logs))
            self.assertTrue(has_crash_traceback('\n'.join(logs)))


if __name__ == '__main__':
    unittest.main()
