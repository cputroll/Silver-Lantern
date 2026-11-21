import hashlib
import importlib.util
import io
import json
import tempfile
import threading
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
    def test_cli_play_target_respects_manifest_over_stale_setup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'HeyListen.exe').write_bytes(b'stale setup')
            cache = root / '.heylisten-get'
            cache.mkdir()
            (cache / 'game-manifest.json').write_text(
                json.dumps({'files': [{'path': 'bin/HeyListen.exe'}]}), encoding='utf-8')
            self.assertIsNone(h.game_launcher(root))
            target = root / 'bin' / 'HeyListen.exe'
            target.parent.mkdir()
            target.write_bytes(b'game')
            self.assertEqual(h.game_launcher(root), target)

    def setUp(self):
        disk = patch.object(h.shutil, 'disk_usage', return_value=type('Usage', (), {'free': 100 * 1024 ** 3})())
        disk.start()
        self.addCleanup(disk.stop)

    def test_planner_does_not_hash_a_case_only_inventory_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'bin').mkdir()
            (root / 'bin' / 'GAME.EXE').write_bytes(b'abc')
            item = manifest()['files'][0]
            with patch.object(h, 'running_launcher_paths', return_value=set()), \
                    patch.object(h, 'sha256', side_effect=AssertionError('case-only match was hashed')):
                self.assertEqual(h.plan_game_files(root, [item], workers=1), ([item], 0))

    def test_planner_excludes_running_launcher_before_hashing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            item = manifest()['files'][0]
            launcher = root / item['path']
            launcher.parent.mkdir()
            launcher.write_bytes(b'abc')
            with patch.object(h, 'running_launcher_paths', return_value={launcher}), \
                    patch.object(h, 'sha256', side_effect=AssertionError('running launcher was hashed')):
                self.assertEqual(h.plan_game_files(root, [item], workers=1), ([], 0))

    def test_existing_scan_overlaps_hashes_and_rejects_same_size_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = manifest()['files'][0]
            second = {**first, 'path': 'bin/second.exe'}
            (root / 'bin').mkdir()
            (root / first['path']).write_bytes(b'abc')
            (root / second['path']).write_bytes(b'bad')
            barrier = threading.Barrier(2, timeout=2)
            original = h.sha256

            def synchronized_hash(path, progress=False):
                try:
                    barrier.wait()
                except threading.BrokenBarrierError:
                    self.fail('existing-file hashes did not overlap')
                return original(path, progress=progress)

            scan = getattr(h, 'plan_game_files', None)
            self.assertIsNotNone(scan, 'expected a separately testable existing-file scan')
            with patch.object(h, 'sha256', side_effect=synchronized_hash):
                pending, kept = scan(root, [first, second], workers=2)
            self.assertEqual(pending, [second])
            self.assertEqual(kept, 3)

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
            with patch.object(game_channel, 'install_lock', side_effect=game_channel.LockTimeout('Another installation is in progress')):
                with self.assertRaises(game_channel.LockTimeout):
                    h.install_from_manifest(None, args, Path(d))

    def test_default_manifest_uses_channel_resolver(self):
        import game_channel
        from types import SimpleNamespace
        args = SimpleNamespace(game_manifest=None, dry_run=False, prune=False, keep_assets=False)
        with tempfile.TemporaryDirectory() as d, patch.object(h, 'stream_session') as session, patch.object(game_channel, 'fetch_for_folder', return_value=manifest()) as fetch, patch.object(h, 'install_game'), patch.object(h, 'keep_awake'):
            h.install_from_manifest(None, args, Path(d))
            fetch.assert_called_once()

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
            if h.os.name == 'nt':
                game = manifest()
                leaf = 'legacy.bin'
                parent = 'p' * (248 - len(str(root)) - len(leaf) - 2)
                relative = f'{parent}/{leaf}'
                game['files'][0]['path'] = relative
                target = root / relative
                self.assertEqual(len(str(target)), 248)
                h.install_game(Session(), game, root)
                self.assertEqual(target.read_bytes(), b'abc')
                self.assertEqual(list(target.parent.iterdir()), [target])
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
