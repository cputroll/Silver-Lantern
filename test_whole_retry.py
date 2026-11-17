import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_game_sync import h

def sha(data): return hashlib.sha256(data).hexdigest()

class WholeRetryTests(unittest.TestCase):
    def test_corrupt_bundle_refetched_once(self):
        good=b'abc'; calls=[]
        man={'version':2,'build':'game-v1','layout':'whole','total_bytes':3,
             'assets':[{'asset':'a','size':3,'sha256':sha(good)}],
             'files':[{'path':'file','size':3,'sha256':sha(good),'segments':[{'asset':'a','offset':0,'length':3,'sha256':sha(good)}]}]}
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            def fake_stream(s,url,dest,**kwargs):
                calls.append(1); dest.write_bytes(good); kwargs['progress'](3)
            real_read=h.os.read
            def read(fd,n):
                data=real_read(fd,n)
                if len(calls)==1 and data: return b'X'+data[1:]
                return data
            with patch.object(h,'stream_to',side_effect=fake_stream), patch.object(h,'check_disk_space'), patch.object(h.os,'read',side_effect=read), patch.dict(h.os.environ,{'SILVER_LANTERN_TEST_NO_DEFENDER':'1'}):
                h.install_game(object(),man,root)
            self.assertEqual(len(calls),2)
            self.assertEqual((root/'file').read_bytes(),good)

    def test_multisegment_restart_preserves_verified_prefix(self):
        blobs={'a':b'abc','b':b'def'}; calls=[]
        man={'version':2,'build':'game-v1','layout':'whole','total_bytes':6,
             'assets':[{'asset':k,'size':3,'sha256':sha(v)} for k,v in blobs.items()],
             'files':[{'path':'file','size':6,'sha256':sha(b'abcdef'),'segments':[{'asset':k,'offset':0,'length':3,'sha256':sha(v)} for k,v in blobs.items()]}]}
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            def stream(s,url,dest,**kwargs):
                name=url.rsplit('/',1)[-1]; calls.append(name); dest.write_bytes(blobs[name]); kwargs['progress'](3)
            real_read=h.os.read
            def read(fd,n):
                data=real_read(fd,n)
                if data == b'def' and calls.count('b') == 1: return b'xef'
                return data
            with patch.object(h,'stream_to',side_effect=stream), patch.object(h,'check_disk_space'), patch.object(h.os,'read',side_effect=read), patch.dict(h.os.environ,{'SILVER_LANTERN_TEST_NO_DEFENDER':'1'}):
                h.install_game(object(),man,root)
            self.assertEqual((root/'file').read_bytes(),b'abcdef')
            self.assertEqual(calls.count('a'),1); self.assertEqual(calls.count('b'),2)

    def test_disk_error_does_not_retry(self):
        data=b'abc'; calls=[]
        man={'version':2,'build':'game-v1','layout':'whole','total_bytes':3,
             'assets':[{'asset':'a','size':3,'sha256':sha(data)}],
             'files':[{'path':'file','size':3,'sha256':sha(data),'segments':[{'asset':'a','offset':0,'length':3,'sha256':sha(data)}]}]}
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            def stream(s,url,dest,**kwargs):
                calls.append(1); dest.write_bytes(data); kwargs['progress'](3)
            with patch.object(h,'stream_to',side_effect=stream), patch.object(h,'check_disk_space'), patch.object(h.os,'read',side_effect=OSError('disk full')), patch.dict(h.os.environ,{'SILVER_LANTERN_TEST_NO_DEFENDER':'1'}):
                with self.assertRaises(OSError): h.install_game(object(),man,root)
            self.assertEqual(len(calls),1); self.assertFalse((root/'file').exists())

    def test_persistent_corruption_fails_after_two_downloads(self):
        good=b'abc'; calls=[]
        man={'version':2,'build':'game-v1','layout':'whole','total_bytes':3,
             'assets':[{'asset':'a','size':3,'sha256':sha(good)}],
             'files':[{'path':'file','size':3,'sha256':sha(good),'segments':[{'asset':'a','offset':0,'length':3,'sha256':sha(good)}]}]}
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            def fake_stream(s,url,dest,**kwargs):
                calls.append(1); dest.write_bytes(good); kwargs['progress'](3)
            real_read=h.os.read
            def read(fd,n):
                data=real_read(fd,n)
                return b'X'+data[1:] if data else data
            with patch.object(h,'stream_to',side_effect=fake_stream), patch.object(h,'check_disk_space'), patch.object(h.os,'read',side_effect=read), patch.dict(h.os.environ,{'SILVER_LANTERN_TEST_NO_DEFENDER':'1'}):
                with self.assertRaises(ValueError): h.install_game(object(),man,root)
            self.assertEqual(len(calls),2)
            self.assertFalse((root/'file').exists())
