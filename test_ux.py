import errno
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from test_updater import app


class FriendlyTests(unittest.TestCase):
    def short(self, exc, **kw):
        return app.friendly(exc, **kw).short

    def test_error_table(self):
        full = OSError(errno.ENOSPC, 'No space left')
        win = OSError(28, 'disk full'); win.winerror = 112
        locked = OSError(13, 'in use', 'HeyListenBuilder.exe'); locked.winerror = 32
        cases = [
            (full, 'disk full'), (win, 'disk full'),
            (PermissionError(13, 'denied', 'x.part'), 'file in use'), (locked, 'file in use'),
            (ConnectionError('reset'), 'no connection'), (TimeoutError('slow'), 'no connection'),
            (RuntimeError('curl: (28) Operation timed out'), 'no connection'),
            (RuntimeError('HTTP 404'), 'link no longer works'),
            (RuntimeError('no download link on https://x'), 'link no longer works'),
            (RuntimeError('HTTP 503'), 'the site is busy'), (RuntimeError('HTTP 429'), 'the site is busy'),
            (RuntimeError('builder checksum mismatch: a != b'), 'bad download'),
            (ValueError('SHA-256 mismatch: bundle-0.tar'), 'damaged download'),
            (ValueError('weird'), 'unexpected error'),
        ]
        for exc, short in cases:
            with self.subTest(exc=exc):
                self.assertEqual(self.short(exc), short)

    def test_http_status_from_response(self):
        exc = RuntimeError('boom'); exc.response = mock.Mock(status_code=410)
        self.assertEqual(self.short(exc), 'link no longer works')

    def test_resume_wording_only_with_partial_bytes(self):
        self.assertNotIn('already saved', app.friendly(ConnectionError()).action)
        self.assertIn('already saved', app.friendly(ConnectionError(), resumable_bytes=2 ** 30).action)

    def test_user_error_passes_through_and_text_is_ascii(self):
        err = app.UserError('p', 'a')
        self.assertIs(app.friendly(err), err)
        for exc in (OSError(errno.ENOSPC, 'x'), ConnectionError(), ValueError()):
            e = app.friendly(exc)
            (e.problem + e.action).encode('ascii')

    def test_disk_errors_are_not_retried(self):
        self.assertTrue(app._is_local_disk_error(OSError(errno.ENOSPC, 'full')))
        self.assertFalse(app._is_local_disk_error(ConnectionError()))


class ProgressTests(unittest.TestCase):
    def test_rollback_does_not_report_old_transfer_rate(self):
        t = [0.0]
        out = io.StringIO()
        with mock.patch.dict(app.os.environ, {'SILVER_LANTERN_GUI_PROGRESS': '1'}), redirect_stdout(out):
            p = app.Progress('Downloading', 10000000, tty=False, clock=lambda: t[0])
            t[0] = 1
            p.add(2000000)
            t[0] = 16
            p.add(-1000000, received=0)
        self.assertIn('speed=2000000', out.getvalue())
        self.assertTrue(out.getvalue().splitlines()[-1].endswith('done=1000000 total=10000000 speed=0'))

    def test_never_wider_than_terminal(self):
        t = [0.0]
        for width in (40, 80, 120):
            for done in (0, 5 * 2 ** 30, 25 * 2 ** 30):
                with self.subTest(width=width, done=done):
                    p = app.Progress('Game data', 25 * 2 ** 30, tty=False, width=width, clock=lambda: t[0])
                    t[0] += 10; p.done = done; p.speed = 40 * 2 ** 20
                    self.assertLessEqual(len(p.render()), width - 1)

    def test_eta_hidden_first_seconds(self):
        t = [0.0]
        p = app.Progress('x', 100, tty=False, width=120, clock=lambda: t[0])
        p.speed = 10
        self.assertIn('--:--', p.render())
        t[0] = 5
        self.assertIn('0:10 left', p.render())

    def test_plain_output_every_ten_percent(self):
        out = io.StringIO()
        with redirect_stdout(out):
            p = app.Progress('Game data', 100, tty=False)
            for _ in range(100):
                p.add(1)
        self.assertEqual(out.getvalue().count('Game data:'), 10)
        self.assertNotIn('\r', out.getvalue())

    def test_ascii_only(self):
        self.assertEqual(app._ascii('a \u2014 b\u2026 \u201cx\u201d'), 'a - b... "x"')


class PauseTests(unittest.TestCase):
    def test_no_block_without_tty(self):
        with mock.patch.object(app, 'stdin_is_tty', return_value=False), \
             mock.patch('builtins.input', side_effect=AssertionError('blocked')):
            self.assertFalse(app.pause())

    def test_no_pause_flag(self):
        with mock.patch.object(app, 'stdin_is_tty', return_value=True), \
             mock.patch('builtins.input', side_effect=AssertionError('blocked')):
            self.assertFalse(app.pause(no_pause=True))

    def test_waits_on_tty(self):
        with mock.patch.object(app, 'stdin_is_tty', return_value=True), \
             mock.patch('builtins.input', side_effect=EOFError) as ask:
            self.assertTrue(app.pause())
        ask.assert_called_once()

    def test_error_is_friendly_logged_and_paused(self):
        out = io.StringIO()
        with mock.patch.object(app, 'main', side_effect=ConnectionError('curl: (56) reset')), \
             mock.patch.object(app, '_UI_READY', True), mock.patch.object(app, 'log') as log, \
             mock.patch.object(sys, 'argv', ['app']), \
             mock.patch.object(app, 'stdin_is_tty', return_value=True), \
             mock.patch('builtins.input') as ask, redirect_stdout(out):
            with self.assertRaises(SystemExit) as code:
                app.run()
        self.assertEqual(code.exception.code, 1)
        self.assertIn('Problem:', out.getvalue())
        self.assertIn('internet connection', out.getvalue())
        self.assertNotIn('curl', out.getvalue())
        self.assertIn('Traceback', ''.join(str(c.args[0]) for c in log.call_args_list))
        ask.assert_called_once()

    def test_interrupt_exit_code(self):
        with mock.patch.object(app, 'main', side_effect=KeyboardInterrupt), \
             mock.patch.object(app, '_UI_READY', True), mock.patch.object(app, 'log'), \
             mock.patch.object(sys, 'argv', ['app', '--no-pause']), redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as code:
                app.run()
        self.assertEqual(code.exception.code, 130)


class DiskSpaceTests(unittest.TestCase):
    def test_low_space_raises_user_error(self):
        usage = mock.Mock(free=2 ** 30)
        with tempfile.TemporaryDirectory() as td, mock.patch.object(app.shutil, 'disk_usage', return_value=usage), \
             mock.patch.object(app, 'log'), redirect_stdout(io.StringIO()):
            with self.assertRaises(app.UserError) as err:
                app.check_disk_space(Path(td) / 'missing' / 'game', 33 * 2 ** 30)
        self.assertIn('Not enough free space', err.exception.problem)

    def test_enough_space_passes(self):
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(app.shutil, 'disk_usage', return_value=mock.Mock(free=2 ** 40)), \
             mock.patch.object(app, 'log'), redirect_stdout(io.StringIO()):
            app.check_disk_space(Path(td), 33 * 2 ** 30)


class DefaultFlowTests(unittest.TestCase):
    def run_main(self, *argv, make_exe=True):
        calls = []
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / 'Games' / 'HeyListen'

            def install(s, man, root, *a, **kw):
                calls.append(('install', Path(root)))
                if make_exe:
                    (Path(root) / 'bin').mkdir(parents=True, exist_ok=True)
                    (Path(root) / 'bin' / 'HeyListen.exe').write_bytes(b'MZ')

            out = io.StringIO()
            with mock.patch.object(sys, 'argv', ['app', '--no-pause', *argv]), \
                 mock.patch.object(app, 'ui_init'), mock.patch.object(app, 'log'), \
                 mock.patch.object(app, 'session'), \
                 mock.patch.object(app, 'default_game_folder', return_value=str(folder)), \
                 mock.patch.object(app, 'check_self_update', side_effect=lambda *a: calls.append(('update',))), \
                 mock.patch('game_channel.fetch_for_folder', side_effect=lambda *a, **k: calls.append(('manifest', app.GAME_MANIFEST_URL)) or {}), \
                 mock.patch.object(app, 'install_game', side_effect=install), \
                 mock.patch.object(app, 'detect_gpu_name', return_value=None), \
                 mock.patch.object(app, 'run_builder_flow', return_value=0) as builder, \
                 mock.patch.object(app, 'run_game_update', return_value=0) as update, \
                 mock.patch.object(app.subprocess, 'Popen') as popen, redirect_stdout(out):
                code = app.main()
            ini = (folder / 'hookshot_options.ini').exists()
        return code, calls, popen, builder, update, out.getvalue(), folder, ini

    def test_no_args_updates_then_installs_then_launches(self):
        code, calls, popen, builder, _, out, folder, ini = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(calls, [('update',), ('manifest', app.GAME_MANIFEST_URL), ('install', folder)])
        builder.assert_not_called()
        popen.assert_called_once()
        self.assertEqual(Path(popen.call_args.args[0][0]), folder / 'bin' / 'HeyListen.exe')
        self.assertTrue(ini)
        self.assertIn('1. ', out); self.assertIn('2. ', out)
        out.encode('ascii')

    def test_no_launch(self):
        code, _, popen, _, _, out, _, _ = self.run_main('--no-launch')
        self.assertEqual(code, 0)
        popen.assert_not_called()
        self.assertIn('double-click HeyListen.exe', out)

    def test_fresh_install_applies_preset_to_shipped_ini(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / 'game'
            def install(s, a, root):
                (root / 'bin').mkdir(parents=True)
                (root / 'bin' / 'HeyListen.exe').write_bytes(b'MZ')
                (root / 'hookshot_options.ini').write_text('resolution=0\n')
            args = mock.Mock(game_dir=str(folder), dry_run=False, no_preset=False, no_launch=True, update_on_launch=False)
            with mock.patch.object(app, 'install_from_manifest', side_effect=install), mock.patch.object(app, 'detect_gpu_name', return_value=None):
                self.assertEqual(app.run_default_flow(None, args), 0)
            self.assertIn('fullscreen=1', (folder / 'hookshot_options.ini').read_text())

    def test_explicit_install_applies_preset_to_shipped_ini(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / 'game'
            def install(s, a, root):
                root.mkdir(parents=True)
                (root / 'hookshot_options.ini').write_text('resolution=0\n')
            with mock.patch.object(sys, 'argv', ['app', '--install-game', str(folder), '--no-self-update', '--no-pause']), mock.patch.object(app, 'ui_init'), mock.patch.object(app, 'session'), mock.patch.object(app, 'install_from_manifest', side_effect=install), mock.patch.object(app, 'detect_gpu_name', return_value=None):
                self.assertEqual(app.main(), 0)
            self.assertIn('fullscreen=1', (folder / 'hookshot_options.ini').read_text())

    def test_existing_settings_are_kept(self):
        with mock.patch.object(app, 'apply_preset') as preset, \
             mock.patch.object(app.Path, 'exists', return_value=True):
            app.apply_preset_step(Path('x'))
        preset.assert_not_called()

    def test_no_preset(self):
        *_, ini = self.run_main('--no-preset')
        self.assertFalse(ini)

    def test_missing_exe_is_an_error(self):
        with self.assertRaises(app.UserError):
            self.run_main(make_exe=False)

    def test_builder_flow_flag(self):
        code, calls, popen, builder, *_ = self.run_main('--builder-flow')
        self.assertEqual(code, 0)
        builder.assert_called_once()
        self.assertEqual(calls, [('update',)])
        popen.assert_not_called()

    def test_update_on_launch(self):
        _, _, _, _, update, _, folder, _ = self.run_main('--update-on-launch', '--no-launch')
        update.assert_called_once_with(folder, dry_run=True, silent=True)

    def test_no_self_update_skips_check(self):
        _, calls, *_ = self.run_main('--no-self-update')
        self.assertNotIn(('update',), calls)

    def test_install_game_flag_does_not_launch(self):
        code, calls, popen, *_ = self.run_main('--install-game', 'D:\\X')
        self.assertEqual(calls[-1], ('install', Path('D:\\X')))
        popen.assert_not_called()

    def test_finish_block(self):
        lines = app.finish_lines(Path('C:/G'), True, 'DLSS Performance')
        text = '\n'.join(lines)
        self.assertIn('starting now', text)
        self.assertIn('DLSS Performance', text)
        self.assertEqual([l.strip()[:2] for l in lines if l.strip()[:2] in ('1.', '2.', '3.')], ['1.', '2.', '3.'])
        self.assertTrue(all(len(l) <= 76 for l in lines))


if __name__ == '__main__':
    unittest.main()
