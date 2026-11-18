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
    def test_helper_logs_wait_and_relaunch_phases(self):
        with mock.patch.object(app, 'wait_for_parent'), mock.patch.object(app, 'replace_transactionally'), mock.patch.object(app, 'cleanup_stage'), mock.patch.object(app, 'log_update_phase') as phase:
            app._apply_update(Path('target'), Path('replacement'), 1, ['--update-only-internal', 'test'])
        self.assertEqual([call.args[0] for call in phase.call_args_list], ['parent-wait done', 'relaunch'])

    def test_helper_records_cross_process_startup(self):
        with mock.patch.dict('os.environ', {'SILVER_LANTERN_UPDATE_SPAWN_MONOTONIC': '10.0'}), mock.patch.object(app, 'wait_for_parent'), mock.patch.object(app, 'replace_transactionally'), mock.patch.object(app, 'cleanup_stage'), mock.patch.object(app, 'log_update_phase') as phase:
            app._apply_update(Path('target'), Path('replacement'), 1, ['--update-only-internal', 'test'])
        self.assertEqual(phase.call_args_list[0].args, ('helper startup', 10.0))

    def test_update_phase_logs_millisecond_elapsed_time(self):
        with mock.patch.object(app.time, 'monotonic', return_value=12.345), mock.patch.object(app, 'log') as log:
            app.log_update_phase('verification done', 10.0)
        log.assert_called_once_with('Self-update phase=verification done elapsed_ms=2345')

    def test_unsigned_local_update_refused_without_test_seam(self):
        with mock.patch.dict('os.environ', {'SILVER_LANTERN_ALLOW_LOCAL_UPDATE': ''}), mock.patch.object(sys, 'frozen', True, create=True), mock.patch.object(app, '_self_update_started', False):
            with self.assertRaisesRegex(ValueError, 'unsigned local updates are disabled'):
                app.check_self_update(mock.Mock(), {}, [], 'fixture.zip')

    def test_local_update_requires_explicit_test_opt_in(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'old.exe'; target.write_bytes(b'old')
            archive = Path(td) / 'release.zip'; archive.write_bytes(b'fixture')
            with mock.patch.dict('os.environ', {'SILVER_LANTERN_ALLOW_LOCAL_UPDATE': '1'}), mock.patch.object(sys, 'frozen', True, create=True), mock.patch.object(sys, 'executable', str(target)), mock.patch.object(app, '_self_update_started', False), mock.patch.object(app, 'stage_executable', return_value=Path(td) / 'replacement.exe'), mock.patch.object(app.subprocess, 'Popen') as launch:
                with self.assertRaises(SystemExit):
                    app.check_self_update(mock.Mock(), {}, ['--self-update'], str(archive))
                self.assertTrue(launch.called)

    def test_file_replace_waits_for_transient_windows_lock(self):
        locked = OSError('sharing violation')
        locked.winerror = 32
        with mock.patch.object(Path, 'replace', side_effect=[locked, PermissionError('locked'), None]) as replace, \
             mock.patch.object(app.time, 'sleep') as sleep, mock.patch.object(app, 'say') as say:
            app._replace_with_retry(Path('old'), Path('new'))
        self.assertEqual(replace.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])
        self.assertEqual(say.call_count, 2)
        self.assertIn('Waiting for Windows to release a file', say.call_args.args[0])

    def test_file_replace_exhausts_deadline_and_keeps_friendly_error(self):
        clock = [0.0]
        def advance(seconds):
            clock[0] += seconds
        error = PermissionError(13, 'locked', 'Ocarina of Time.exe')
        with mock.patch.object(Path, 'replace', side_effect=error), \
             mock.patch.object(app.time, 'monotonic', side_effect=lambda: clock[0]), \
             mock.patch.object(app.time, 'sleep', side_effect=advance), mock.patch.object(app, 'say'):
            with self.assertRaises(PermissionError) as raised:
                app._replace_with_retry(Path('old'), Path('new'))
        self.assertEqual(clock[0], 60)
        self.assertIn('a file', app.friendly(raised.exception).problem)
        self.assertIn('Wait a minute', app.friendly(raised.exception).action)

    def test_executable_write_waits_past_two_minutes_and_logs(self):
        clock = [0.0]
        error = PermissionError(13, 'locked', 'game.exe.heylisten-new')
        error.winerror = 32
        def attempt(*args, **kwargs):
            if clock[0] < 300:
                raise error
            return mock.MagicMock()
        with mock.patch.object(Path, 'open', side_effect=attempt), mock.patch.object(app.time, 'monotonic', side_effect=lambda: clock[0]), mock.patch.object(app.time, 'sleep', side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)), mock.patch.object(app, 'say') as say, mock.patch.object(app, 'log') as log:
            app._open_with_retry(Path('game.exe.heylisten-new'), 'wb')
        self.assertGreaterEqual(clock[0], 300)
        self.assertTrue(say.called)
        self.assertTrue(any('298.000' in str(c) and '32' in str(c) for c in log.call_args_list))
        self.assertTrue(any('Windows is checking the game files' in str(c) for c in say.call_args_list))

    def test_executable_rename_expiry_has_antivirus_action(self):
        clock = [0.0]
        error = PermissionError(13, 'locked', 'game.exe.heylisten-new')
        with mock.patch.object(Path, 'replace', side_effect=error), mock.patch.object(app.time, 'monotonic', side_effect=lambda: clock[0]), mock.patch.object(app.time, 'sleep', side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)), mock.patch.object(app, 'say'):
            with self.assertRaises(PermissionError) as raised:
                app._replace_with_retry(Path('game.exe.heylisten-new'), Path('game.exe'))
        self.assertEqual(clock[0], 600)
        self.assertEqual(app.friendly(raised.exception).problem, 'Windows is still protecting a game file.')
        self.assertEqual(app.friendly(raised.exception).action, 'Leave this window open - it usually finishes by itself - or allow this app in your antivirus and press Retry.')

    def test_file_retry_cap_is_cumulative_across_calls(self):
        clock = [0.0]
        error = PermissionError(13, 'locked', 'Ocarina of Time.exe')
        app._FILE_LOCK_WAITED.clear()
        with mock.patch.object(app, 'FILE_LOCK_CAP', 6.0), \
             mock.patch.object(Path, 'replace', side_effect=error), \
             mock.patch.object(app.time, 'monotonic', side_effect=lambda: clock[0]), \
             mock.patch.object(app.time, 'sleep', side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)), \
             mock.patch.object(app, 'say'), mock.patch.object(app, 'log') as log:
            with self.assertRaises(PermissionError):
                app._replace_with_retry(Path('old'), Path('cap-cumulative.bin'))
            self.assertEqual(clock[0], 6.0)
            clock[0] = 500.0  # a later call still has per-call budget, but the file already spent its cap
            with self.assertRaises(PermissionError):
                app._replace_with_retry(Path('old'), Path('cap-cumulative.bin'))
            self.assertEqual(clock[0], 500.0)
        self.assertTrue(any('cap exceeded' in str(c) for c in log.call_args_list))

    def test_file_retry_cap_escalates_executable_to_classified_error(self):
        clock = [0.0]
        error = PermissionError(13, 'locked', 'game.exe')
        error.winerror = 32
        app._FILE_LOCK_WAITED.clear()
        with mock.patch.object(app, 'FILE_LOCK_CAP', 5.0), \
             mock.patch.object(Path, 'replace', side_effect=error), \
             mock.patch.object(app.time, 'monotonic', side_effect=lambda: clock[0]), \
             mock.patch.object(app.time, 'sleep', side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)), \
             mock.patch.object(app, 'say'), mock.patch.object(app, 'log'):
            with self.assertRaises(PermissionError) as raised:
                app._replace_with_retry(Path('old'), Path('cap-game.exe'))
        classified = app.friendly(raised.exception)
        self.assertEqual(classified.short, 'file in use')
        self.assertEqual(classified.problem, 'Windows is still protecting a game file.')

    def test_lock_retry_line_logs_path_only_when_holder_unknown(self):
        clock = [0.0]
        error = PermissionError(13, 'locked', 'Ocarina of Time.exe')
        app._FILE_LOCK_WAITED.clear()
        with mock.patch.object(app, '_restart_manager_holders', return_value=None), \
             mock.patch.object(app, 'FILE_LOCK_CAP', 2.0), \
             mock.patch.object(Path, 'replace', side_effect=error), \
             mock.patch.object(app.time, 'monotonic', side_effect=lambda: clock[0]), \
             mock.patch.object(app.time, 'sleep', side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)), \
             mock.patch.object(app, 'say'), mock.patch.object(app, 'log') as log:
            with self.assertRaises(PermissionError):
                app._replace_with_retry(Path('old'), Path('rm-unavailable.bin'))
        lines = [str(c.args[0]) for c in log.call_args_list if c.args and 'File lock retry path=' in str(c.args[0])]
        self.assertTrue(lines)
        self.assertIn('path=rm-unavailable.bin', lines[0])
        self.assertNotIn('holder=', lines[0])

    def test_lock_retry_line_reports_when_no_holder_is_listed(self):
        clock = [0.0]
        error = PermissionError(13, 'locked', 'Ocarina of Time.exe')
        app._FILE_LOCK_WAITED.clear()
        with mock.patch.object(app, '_restart_manager_holders', return_value=[]), \
             mock.patch.object(app, 'FILE_LOCK_CAP', 2.0), \
             mock.patch.object(Path, 'replace', side_effect=error), \
             mock.patch.object(app.time, 'monotonic', side_effect=lambda: clock[0]), \
             mock.patch.object(app.time, 'sleep', side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)), \
             mock.patch.object(app, 'say'), mock.patch.object(app, 'log') as log:
            with self.assertRaises(PermissionError):
                app._replace_with_retry(Path('old'), Path('rm-empty.bin'))
        lines = [str(c.args[0]) for c in log.call_args_list if c.args and 'File lock retry path=' in str(c.args[0])]
        self.assertTrue(lines)
        self.assertIn('holder=none', lines[0])

    def _run_update(self, args):
        release = {'tag_name': 'v999.0.0', 'assets': [
            {'name': app.UPDATE_ASSET, 'browser_download_url': 'zip', 'size': 3},
            {'name': app.SUMS_ASSET, 'browser_download_url': 'sums'}]}
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'app.exe'
            target.write_bytes(b'old')
            def download(s, url, dest, expect, **kw):
                dest.write_bytes(b'new')
            with mock.patch.object(sys, 'frozen', True, create=True), \
                 mock.patch.object(sys, 'executable', str(target)), \
                 mock.patch.object(app, 'latest_app_release', return_value=release) as latest, \
                 mock.patch.object(app, 'fetch_update_checksum', return_value='digest'), \
                 mock.patch.object(app, 'stream_to', side_effect=download), \
                 mock.patch.object(app, 'sha256', return_value='digest'), \
                 mock.patch.object(app, 'stage_executable', return_value=Path(td) / 'new.exe'), \
                 mock.patch.object(app.subprocess, 'Popen') as launch, \
                 mock.patch.object(app, '_self_update_started', False, create=True):
                with self.assertRaises(SystemExit) as result:
                    app.check_self_update(mock.Mock(), {}, args)
                self.assertEqual(result.exception.code, 0)
                app.check_self_update(mock.Mock(), {}, args)
                self.assertEqual(latest.call_count, 1)
                return launch.call_args.args[0]

    def test_normal_update_relaunch_skips_another_check(self):
        command = self._run_update(['--no-launch', '--out', 'folder'])
        self.assertEqual(command[5:], ['--no-launch', '--out', 'folder', '--no-self-update'])

    def test_double_click_update_relaunches_the_window(self):
        command = self._run_update([])
        self.assertEqual(command[5:], ['--gui', '--no-self-update'])

    def test_explicit_self_update_stays_console(self):
        command = self._run_update(['--self-update'])
        self.assertEqual(command[5:], ['--no-self-update'])

    def test_say_survives_windowed_missing_stdout(self):
        with mock.patch.object(sys, 'stdout', None):
            app.say('updating')
            app.warn('still fine')


    def test_update_only_helper_replaces_without_relaunch(self):
        command = ['helper', '--apply-update-internal', 'app.exe', 'new.exe', '123', '--update-only-internal', 'v999.0.0']
        with mock.patch.object(app, 'wait_for_parent'), \
             mock.patch.object(app, 'replace_transactionally'), \
             mock.patch.object(app, 'cleanup_stage'), \
             mock.patch.object(app.subprocess, 'Popen') as launch, \
             mock.patch.object(app, 'log') as log:
            app._apply_update(Path('app.exe'), Path('stage/new.exe'), 123, command[5:])
        launch.assert_not_called()
        log.assert_any_call('Updated to v999.0.0. Run the tool again to continue.')

    def test_archive_falls_back_to_support_executable(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            archive = root / 'update.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('support/SilverLantern.exe', b'executable')
            self.assertEqual(app.stage_executable(archive, root, 'Run Silver Lantern.exe').read_bytes(), b'executable')

    def test_archive_fallback_for_game_named_client(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); archive = root / 'update.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('support/SilverLantern.exe', b'executable')
            self.assertEqual(app.stage_executable(archive, root, 'Ocarina of Time.exe').read_bytes(), b'executable')

    def test_equal_release_never_handoffs(self):
        with mock.patch.object(sys, 'frozen', True, create=True), mock.patch.object(app, '_self_update_started', False), mock.patch.object(app, 'latest_app_release', return_value={'tag_name': 'v' + app.APP_VERSION}), mock.patch.object(app.subprocess, 'Popen') as launch:
            app.check_self_update(mock.Mock(), {}, ['--self-update'])
            launch.assert_not_called()

    def test_self_update_relaunches_with_original_args(self):
        command = self._run_update(['--self-update', '--no-launch'])
        self.assertEqual(command[1], '--apply-update-internal')
        self.assertEqual(command[5:], ['--no-launch', '--no-self-update'])
        with mock.patch.object(app, 'wait_for_parent'), mock.patch.object(app, 'replace_transactionally'), \
             mock.patch.object(app, 'cleanup_stage'), mock.patch.object(app.subprocess, 'Popen') as launch:
            app._apply_update(Path('app.exe'), Path('stage/new.exe'), 123, command[5:])
        launch.assert_called_once_with(['app.exe', '--no-launch', '--no-self-update'], cwd='.')

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

    def test_interrupted_swap_never_removes_entrypoint(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); target = root / 'app.exe'; new = root / 'new.exe'
            target.write_bytes(b'old'); new.write_bytes(b'new')
            original = app.os.replace
            def interrupted(src, dst):
                original(src, dst)
                self.assertTrue(target.exists(), 'entry point disappeared after swap step')
                raise KeyboardInterrupt()
            with mock.patch.object(app.os, 'replace', side_effect=interrupted):
                with self.assertRaises(KeyboardInterrupt):
                    app.replace_transactionally(target, new)
            self.assertIn(target.read_bytes(), (b'old', b'new'))
            app.replace_transactionally(target, new)
            self.assertEqual(target.read_bytes(), b'new')

    def test_transaction_rolls_back(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'app.exe'
            target.write_bytes(b'old')
            new = Path(td) / 'new.exe'
            new.write_bytes(b'new')
            original = app.os.replace
            def fail_second(src, dst):
                if Path(dst) == target:
                    raise OSError('simulated failure')
                original(src, dst)
            with mock.patch.object(app.os, 'replace', side_effect=fail_second):
                with self.assertRaises(OSError):
                    app.replace_transactionally(target, new)
            self.assertEqual(target.read_bytes(), b'old')

    def test_stale_backup_is_recovered_not_fatal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); target = root / 'app.exe'; backup = root / 'app.exe.update-backup'
            target.write_bytes(b'current'); backup.write_bytes(b'older')
            replacement = root / 'new.exe'; replacement.write_bytes(b'new')
            app.replace_transactionally(target, replacement)
            self.assertEqual(target.read_bytes(), b'new')
            self.assertFalse(backup.exists())
            # second recovery shape: target lost, backup holds the only copy
            target.unlink(); backup.write_bytes(b'rescued')
            replacement.write_bytes(b'newer')
            app.replace_transactionally(target, replacement)
            self.assertEqual(target.read_bytes(), b'newer')

        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / 'app.exe'
            target.write_bytes(b'old')
            new = Path(td) / 'new.exe'
            new.write_bytes(b'new')
            original = app.os.replace
            def fail_second(src, dst):
                if Path(dst) == target:
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
            self.assertFalse(target.with_name('app.exe.update-backup').exists())

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
