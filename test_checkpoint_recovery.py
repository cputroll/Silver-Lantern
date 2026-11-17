import hashlib
import tempfile
import unittest
from pathlib import Path
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

    def test_missing_checkpoint_discards_temp(self):
        item={'segments':[{'length':3,'sha256':sha(b'abc')},{'length':3,'sha256':sha(b'def')}]}
        with tempfile.TemporaryDirectory() as d:
            temp=Path(d)/'file.heylisten-new'; temp.write_bytes(b'abc')
            checkpoint=Path(str(temp)+'.json')
            index,_=h.restore_segment_checkpoint(temp,checkpoint,item)
            self.assertEqual(index,0); self.assertFalse(temp.exists())
