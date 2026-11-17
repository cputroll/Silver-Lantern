import importlib.util
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location('heylisten', Path(__file__).with_name('Get-HeyListen.py'))
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

class UpdaterTests(unittest.TestCase):
    def test_version_is_not_manifest_controlled(self):
        self.assertNotEqual(app.local_version({'tool': {'version': '999.0.0'}}), '999.0.0')

    def test_semantic_versions(self):
        self.assertGreater(app.version_key('v1.10.0'), app.version_key('1.9.0'))
        self.assertLess(app.version_key('1.0.0-rc.1'), app.version_key('1.0.0'))
        with self.assertRaises(ValueError):
            app.version_key('1.0.0garbage')

    def test_checksum_required_and_exact(self):
        with self.assertRaises(ValueError):
            app.parse_update_checksum('')
        with self.assertRaises(ValueError):
            app.parse_update_checksum('a' * 64 + '  other-HeyListen-Get.zip')
        self.assertEqual(app.parse_update_checksum('a' * 64 + '  HeyListen-Get.zip'), 'a' * 64)

    def test_archive_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'x.zip'
            with zipfile.ZipFile(p, 'w') as z:
                z.writestr('../evil.exe', b'evil')
            with self.assertRaises(ValueError):
                app.stage_executable(p, Path(td), 'app.exe')
            self.assertFalse((Path(td).parent / 'evil.exe').exists())

    def test_transaction_rolls_back(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'app.exe'
            target.write_bytes(b'old')
            new = Path(td) / 'new.exe'
            new.write_bytes(b'new')
            original = app.os.replace
            def fail_second(src, dst):
                if Path(dst) == target and Path(src) == new:
                    raise OSError('simulated failure')
                original(src, dst)
            with mock.patch.object(app.os, 'replace', side_effect=fail_second):
                with self.assertRaises(OSError):
                    app.replace_transactionally(target, new)
            self.assertEqual(target.read_bytes(), b'old')

    def test_helper_waits_before_replacing_and_relaunches(self):
        events = []
        with mock.patch.object(app, 'wait_for_parent', side_effect=lambda pid: events.append(('wait', pid))), \
             mock.patch.object(app, 'replace_transactionally', side_effect=lambda *a: events.append(('replace',))), \
             mock.patch.object(app.subprocess, 'Popen', side_effect=lambda *a, **kw: events.append(('launch',))), \
             mock.patch.object(app, 'cleanup_stage'):
            app._apply_update(Path('app.exe'), Path('stage/new.exe'), 123, [])
        self.assertEqual(events, [('wait', 123), ('replace',), ('launch',)])

    def test_backup_cleanup_failure_does_not_prevent_launch(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'app.exe'
            new = Path(td) / 'replacement.exe'
            target.write_bytes(b'old')
            new.write_bytes(b'new')
            with mock.patch.object(app, 'wait_for_parent'), \
                 mock.patch.object(Path, 'unlink', side_effect=PermissionError('locked')), \
                 mock.patch.object(app.subprocess, 'Popen') as launch, \
                 mock.patch.object(app, 'cleanup_stage'):
                app._apply_update(target, new, 123, [])
            launch.assert_called_once()
            self.assertEqual(target.read_bytes(), b'new')
            self.assertEqual(target.with_name('app.exe.update-backup').read_bytes(), b'old')

    def test_checksum_stream_is_bounded_and_closed(self):
        response = mock.Mock()
        response.iter_content.return_value = iter([b'x' * 40000, b'x' * 40000])
        session = mock.Mock()
        session.get.return_value = response
        with self.assertRaises(ValueError):
            app.fetch_update_checksum(session, 'https://example.test/sums')
        response.close.assert_called_once()
        self.assertTrue(session.get.call_args.kwargs['stream'])

    @unittest.skipUnless(app.os.name == 'nt', 'Windows process handle test')
    def test_wait_for_actual_windows_process_exit(self):
        child = app.subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(0.5)'])
        try:
            app.wait_for_parent(child.pid)
            self.assertIsNotNone(child.poll())
        finally:
            if child.poll() is None:
                child.terminate()
            child.wait()

    def test_parent_timeout_prevents_replacement(self):
        with mock.patch.object(app, 'wait_for_parent', side_effect=TimeoutError), \
             mock.patch.object(app, 'replace_transactionally') as replace:
            with self.assertRaises(TimeoutError):
                app._apply_update(Path('app.exe'), Path('new.exe'), 123, [])
            replace.assert_not_called()

if __name__ == '__main__':
    unittest.main()
