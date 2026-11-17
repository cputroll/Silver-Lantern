import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_game_sync import h


class Response:
    def __init__(self, status, data, content_range=''):
        self.status_code = status
        self.data = data
        self.headers = {'content-range': content_range}
    def iter_content(self, size): yield self.data
    def close(self): pass


class ParallelResumeTests(unittest.TestCase):
    def test_resume_skips_completed_chunk(self):
        data = b'abcdefgh'
        calls = []
        class Session:
            def get(self, url, headers, **kwargs):
                calls.append(headers['range'])
                a, b = map(int, headers['range'][6:].split('-'))
                return Response(206, data[a:b+1], f'bytes {a}-{b}/8')
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / 'asset'
            Path(str(dest)+'.part').write_bytes(b'abcdXXXX')
            Path(str(dest)+'.part.json').write_text(json.dumps({'size': 8, 'bounds': [[0,4],[4,8]], 'done': [True,False]}))
            progress = h.Progress('test', 4, tty=False)
            h.parallel_download(Session, 'http://example/', dest, 8, '', progress, 2, chunk_min=1)
            self.assertEqual(calls, ['bytes=4-7'])
            self.assertEqual(dest.read_bytes(), data)
            self.assertEqual(progress.done, 4)
            self.assertFalse(Path(str(dest)+'.part.json').exists())

    def test_layout_mismatch_restarts_all_chunks(self):
        data = b'abcdefgh'
        calls = []
        class Session:
            def get(self, url, headers, **kwargs):
                calls.append(headers['range'])
                a, b = map(int, headers['range'][6:].split('-'))
                return Response(206, data[a:b+1], f'bytes {a}-{b}/8')
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / 'asset'
            Path(str(dest)+'.part').write_bytes(b'XXXXXXXX')
            Path(str(dest)+'.part.json').write_text(json.dumps({'size': 9, 'bounds': [[0,4],[4,8]], 'done': [True,False]}))
            progress = h.Progress('test', 8, tty=False)
            h.parallel_download(Session, 'http://example/', dest, 8, '', progress, 2, chunk_min=1)
            self.assertEqual(set(calls), {'bytes=0-3', 'bytes=4-7'})
            self.assertEqual(dest.read_bytes(), data)
            self.assertEqual(progress.done, 8)

    def test_resumed_ignored_range_counts_only_missing_bytes(self):
        class Session:
            def get(self, *args, **kwargs): return Response(200, b'')
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / 'asset'
            Path(str(dest)+'.part').write_bytes(b'abcdXXXX')
            Path(str(dest)+'.part.json').write_text(json.dumps({'size': 8, 'bounds': [[0,4],[4,8]], 'done': [True,False]}))
            progress = h.Progress('test', 4, tty=False)
            def stream(s, url, target, **kwargs):
                target.write_bytes(b'abcdefgh')
                kwargs['progress'](3)
                kwargs['progress'](5)
            with patch.object(h, 'stream_to', side_effect=stream):
                h.parallel_download(Session, 'http://example/', dest, 8, '', progress, 2, chunk_min=1)
            self.assertEqual(progress.done, 4)

    def test_ignored_range_falls_back(self):
        class Session:
            def get(self, *args, **kwargs): return Response(200, b'')
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / 'asset'
            progress = h.Progress('test', 8, tty=False)
            def stream(s, url, target, **kwargs):
                target.write_bytes(b'abcdefgh')
                kwargs['progress'](8)
            with patch.object(h, 'stream_to', side_effect=stream):
                h.parallel_download(Session, 'http://example/', dest, 8, '', progress, 2, chunk_min=1)
            self.assertEqual(progress.done, 8)
            self.assertEqual(dest.read_bytes(), b'abcdefgh')
