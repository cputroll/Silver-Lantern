import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('hey_sync', Path(__file__).with_name('Get-HeyListen.py'))
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)

def manifest(data=b'abc'):
    digest = hashlib.sha256(data).hexdigest()
    return {'version': 1, 'build': 'game-v1', 'total_bytes': len(data), 'files': [
        {'path': 'bin/game.exe', 'size': len(data), 'sha256': digest,
         'parts': [{'asset': 'g001.p1', 'size': len(data), 'sha256': digest}]}]}

class SyncTests(unittest.TestCase):
    def setUp(self):
        disk = patch.object(h.shutil, 'disk_usage', return_value=type('Usage', (), {'free': 100 * 1024 ** 3})())
        disk.start()
        self.addCleanup(disk.stop)

    def test_manifest_stream_reports_progress_and_retries(self):
        from unittest.mock import Mock
        payload = json.dumps(manifest()).encode()
        response = Mock()
        response.headers = {'content-length': str(len(payload))}
        response.iter_content.return_value = [payload[:10], payload[10:]]
        session = Mock()
        session.get.side_effect = [TimeoutError('slow'), response]
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(h.load_game_manifest(session, 'https://example.test/manifest'), manifest())
        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(session.get.call_args.kwargs['timeout'], (10, 30))
        self.assertIn('Connection stalled - retrying...', out.getvalue())
        self.assertIn('Fetching manifest', out.getvalue())

    def test_install_operation_acquires_lock_before_manifest_fetch(self):
        import game_channel
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as d, game_channel.install_lock(Path(d)):
            args = SimpleNamespace(game_manifest='https://example.test/manifest', dry_run=False, prune=False, keep_assets=False)
            with patch.object(game_channel, 'install_lock', side_effect=TimeoutError('Another installation is in progress')):
                with self.assertRaisesRegex(h.UserError, 'Another game installation'):
                    h.install_from_manifest(None, args, Path(d))

    def test_bad_json(self):
        with self.assertRaises(ValueError): h.parse_game_manifest('{')
    def test_missing_fields(self):
        with self.assertRaises(ValueError): h.parse_game_manifest('{}')
    def test_duplicate_paths(self):
        m = manifest(); m['files'].append(m['files'][0])
        with self.assertRaises(ValueError): h.parse_game_manifest(json.dumps(m))
    def test_part_size_mismatch(self):
        m = manifest(); m['files'][0]['parts'][0]['size'] = 2
        with self.assertRaises(ValueError): h.parse_game_manifest(json.dumps(m))
    def test_identical_skip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'bin/game.exe'; p.parent.mkdir(); p.write_bytes(b'abc')
            with patch.object(h, 'stream_to', side_effect=AssertionError('downloaded')):
                h.install_game(None, manifest(), Path(d))
    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)/'missing'
            h.install_game(None, manifest(), root, dry_run=True, prune=True)
            self.assertFalse(root.exists())
    def test_prune_preserves_user_and_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/'user').mkdir(); (root/'user/save').write_text('x')
            (root/'game-manifest.json').write_text('x'); (root/'old').write_text('x')
            p=root/'bin/game.exe'; p.parent.mkdir(); p.write_bytes(b'abc')
            h.install_game(None, manifest(), root, prune=True)
            self.assertTrue((root/'user/save').exists()); self.assertTrue((root/'game-manifest.json').exists()); self.assertFalse((root/'old').exists())
    def test_aggregate_progress(self):
        out=io.StringIO()
        with redirect_stdout(out):
            progress=h.GameProgress(3); progress.add(1); progress.add(2); progress.finish()
        self.assertIn('3', out.getvalue())
    def test_download_and_assemble(self):
        class Response:
            status_code = 200; headers = {}
            def raise_for_status(self): pass
            def close(self): pass
            def iter_content(self, size): yield b'abc'
        class Session:
            def get(self, *args, **kwargs): return Response()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            h.install_game(Session(), manifest(), root)
            self.assertEqual((root/'bin/game.exe').read_bytes(), b'abc')
    def test_resume_offset_mismatch(self):
        class Response:
            status_code=206; headers={'content-range':'bytes 0-2/3'}
            def raise_for_status(self): pass
            def close(self): pass
        class Session:
            def get(self, *args, **kwargs): return Response()
        with tempfile.TemporaryDirectory() as d:
            dest=Path(d)/'dest'; part=Path(str(dest)+'.part'); part.write_bytes(b'a')
            with self.assertRaises(ValueError): h.stream_to(Session(), 'https://example.test', dest, expect=3, max_retries=1)
            self.assertFalse(part.exists())
