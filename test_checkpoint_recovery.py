import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from test_game_sync import h

def sha(data): return hashlib.sha256(data).hexdigest()

class CheckpointRecoveryTests(unittest.TestCase):
    def test_verified_prefix_rebuilds_hash(self):
        item={'segments':[{'length':3,'sha256':sha(b'abc')},{'length':3,'sha256':sha(b'def')}]}
        with tempfile.TemporaryDirectory() as d:
            temp=Path(d)/'file.heylisten-new'; temp.write_bytes(b'abc')
            checkpoint=Path(str(temp)+'.json'); checkpoint.write_text('{"next_index":1,"bytes_written":3}')
            index, hasher=h.restore_segment_checkpoint(temp,checkpoint,item)
            self.assertEqual(index,1); self.assertEqual(hasher.hexdigest(),sha(b'abc'))

    def test_corrupt_prefix_discards_checkpoint(self):
        item={'segments':[{'length':3,'sha256':sha(b'abc')},{'length':3,'sha256':sha(b'def')}]}
        with tempfile.TemporaryDirectory() as d:
            temp=Path(d)/'file.heylisten-new'; temp.write_bytes(b'xbc')
            checkpoint=Path(str(temp)+'.json'); checkpoint.write_text('{"next_index":1,"bytes_written":3}')
            index,_=h.restore_segment_checkpoint(temp,checkpoint,item)
            self.assertEqual(index,0); self.assertFalse(temp.exists()); self.assertFalse(checkpoint.exists())

    def test_locked_temp_is_retried_before_recovery(self):
        item={'segments':[{'length':3,'sha256':sha(b'abc')}]}
        with tempfile.TemporaryDirectory() as d:
            temp=Path(d)/'file.heylisten-new'; temp.write_bytes(b'bad')
            checkpoint=Path(str(temp)+'.json'); checkpoint.write_text('invalid')
            unlink = Path.unlink
            attempts = []
            def locked_once(path, *args, **kwargs):
                if path == temp:
                    attempts.append(path)
                    if len(attempts) == 1:
                        raise PermissionError('locked')
                return unlink(path, *args, **kwargs)
            with mock.patch.object(Path, 'unlink', locked_once), mock.patch.object(h.time, 'sleep'), mock.patch.object(h, 'say') as say:
                index, _ = h.restore_segment_checkpoint(temp, checkpoint, item)
            self.assertEqual(index, 0)
            self.assertEqual(len(attempts), 2)
            self.assertFalse(temp.exists()); self.assertFalse(checkpoint.exists())
            say.assert_any_call('Waiting for Windows to release a file...')

    def test_permanent_lock_preserves_checkpoint_and_reports_friendly_error(self):
        item={'segments':[{'length':3,'sha256':sha(b'abc')}]}
        with tempfile.TemporaryDirectory() as d:
            temp=Path(d)/'file.heylisten-new'; temp.write_bytes(b'bad')
            checkpoint=Path(str(temp)+'.json'); checkpoint.write_text('invalid')
            unlink = Path.unlink
            def locked(path, *args, **kwargs):
                if path == temp: raise PermissionError('locked')
                return unlink(path, *args, **kwargs)
            with mock.patch.object(Path, 'unlink', locked), mock.patch.object(h.time, 'monotonic', side_effect=[0, 61]), mock.patch.object(h, 'main', side_effect=lambda: h.restore_segment_checkpoint(temp, checkpoint, item)), mock.patch.object(h, '_UI_READY', False), mock.patch.object(h, 'say') as say, mock.patch.object(h, 'log') as log:
                with self.assertRaises(SystemExit) as exit_:
                    h.run()
            self.assertEqual(exit_.exception.code, 1)
            self.assertTrue(temp.exists()); self.assertTrue(checkpoint.exists())
            self.assertTrue(any('Traceback' in str(call) for call in log.call_args_list))

    def test_missing_checkpoint_discards_temp(self):
        item={'segments':[{'length':3,'sha256':sha(b'abc')},{'length':3,'sha256':sha(b'def')}]}
        with tempfile.TemporaryDirectory() as d:
            temp=Path(d)/'file.heylisten-new'; temp.write_bytes(b'abc')
            checkpoint=Path(str(temp)+'.json')
            index,_=h.restore_segment_checkpoint(temp,checkpoint,item)
            self.assertEqual(index,0); self.assertFalse(temp.exists())
