import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_game_sync import h

def digest(b): return hashlib.sha256(b).hexdigest()

class StartupMemoTests(unittest.TestCase):
    def test_scheduler_creates_shared_parent_once(self):
        data=b'x'*30
        files=[{'path':f'deep/shared/f{i}', 'size':1,'sha256':digest(b'x'), 'segments':[{'asset':'bundle','offset':i,'length':1,'sha256':digest(b'x')}]} for i in range(30)]
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'game'; cache=Path(d)/'cache'; cache.mkdir(); (cache/'bundle').write_bytes(data)
            mkdir=Path.mkdir; calls=[]
            def counted(path,*args,**kwargs):
                calls.append(str(path)); return mkdir(path,*args,**kwargs)
            with patch.object(Path,'mkdir',counted):
                h.assemble_segmented(files,{'bundle':{'asset':'bundle'}},cache,root,lambda a:None,True)
            self.assertLessEqual(calls.count(str(root/'deep'/'shared')),2)
            self.assertTrue(all((root/'deep'/'shared'/f'f{i}').read_bytes()==b'x' for i in range(30)))
