import io
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

from test_updater import app
import game_channel
import launcher


class RetryDiagnosticsTests(unittest.TestCase):
    def test_recoverable_retry_traceback_is_labeled_on_every_line(self):
        operation = mock.Mock(side_effect=[PermissionError('locked'), None])
        with mock.patch.object(app, 'log') as logger, mock.patch.object(app.time, 'sleep'):
            app._retry_locked(operation)
        traces = [str(c.args[0]) for c in logger.call_args_list if 'Traceback' in str(c.args[0])]
        self.assertEqual(len(traces), 1)
        self.assertTrue(all(line.startswith('recoverable-retry:') for line in traces[0].splitlines()))

    def test_chained_exception_is_not_hidden_by_retry_label(self):
        calls = 0
        def operation():
            nonlocal calls
            calls += 1
            if calls == 1:
                try:
                    raise RuntimeError('unexpected root cause')
                except RuntimeError:
                    raise PermissionError('locked')
        with mock.patch.object(app, 'log') as logger, mock.patch.object(app.time, 'sleep'):
            app._retry_locked(operation)
        messages = [str(c.args[0]) for c in logger.call_args_list]
        self.assertTrue(any('RuntimeError: unexpected root cause' in m and not m.startswith('recoverable-retry:') for m in messages))
        self.assertTrue(any('recoverable-retry:' in m and 'PermissionError: locked' in m and 'RuntimeError' not in m for m in messages))

    def test_terminal_lock_error_is_not_labeled_recoverable(self):
        with mock.patch.object(app, 'log') as logger:
            with self.assertRaises(PermissionError):
                app._retry_locked(mock.Mock(side_effect=PermissionError('locked')), budget=0)
        self.assertFalse(any('recoverable-retry:' in str(c.args[0]) for c in logger.call_args_list))

    def test_expected_parent_exit_is_not_logged_as_crash(self):
        with mock.patch.object(app, 'log') as logger:
            app._log_assembly_exception(app.ParentExited())
        self.assertNotIn('Traceback', str(logger.call_args))
        with mock.patch.object(app, 'log') as logger:
            try:
                raise RuntimeError('real crash')
            except RuntimeError as exc:
                app._log_assembly_exception(exc)
        self.assertIn('Traceback', str(logger.call_args))

    def test_nonretryable_error_traceback_remains_plain(self):
        with mock.patch.object(app, 'log') as logger:
            with self.assertRaises(FileNotFoundError):
                app._retry_locked(mock.Mock(side_effect=FileNotFoundError('missing')))
        self.assertTrue(any(str(c.args[0]).startswith('Traceback') for c in logger.call_args_list))

    def test_lock_timeout_is_not_network_or_generic(self):
        err = game_channel.LockTimeout('PRIVATE LOCK DETAILS')
        friendly = app.friendly(err)
        self.assertEqual(friendly.short, 'installation busy')
        self.assertNotIn('PRIVATE', friendly.problem + friendly.action)

    def test_known_retry_errors_are_safe_and_not_generic(self):
        cases = [PermissionError(13, 'PRIVATE', 'secret.heylisten-new'),
                 PermissionError(13, 'PRIVATE', 'secret.update-backup'),
                 FileNotFoundError(2, 'PRIVATE', 'game-manifest.json'),
                 FileNotFoundError(2, 'PRIVATE', '.heylisten-parts/bundle'),
                 ValueError('SHA-256 mismatch: PRIVATE partial target'),
                 ValueError('short segment: PRIVATE cache'),
                 ValueError('staged update failed verification PRIVATE')]
        for exc in cases:
            with self.subTest(exc=exc):
                result = app.friendly(exc)
                self.assertNotEqual(result.short, 'unexpected error')
                self.assertNotIn('PRIVATE', result.problem + result.action)
                self.assertNotIn('secret.', result.problem + result.action)

    def test_file_lock_traceback_and_early_action(self):
        output = io.StringIO()
        with mock.patch.object(app, 'main', side_effect=PermissionError('PRIVATE DETAILS')), \
             mock.patch.object(app, 'log') as log, mock.patch.object(app, '_UI_READY', False), \
             mock.patch.object(sys, 'argv', ['app', '--no-pause']), redirect_stdout(output):
            with self.assertRaises(SystemExit):
                app.run()
        self.assertIn('PermissionError: PRIVATE DETAILS', '\n'.join(str(c.args[0]) for c in log.call_args_list))
        self.assertIn('Traceback', '\n'.join(str(c.args[0]) for c in log.call_args_list))
        self.assertIn('What to do:', output.getvalue())
        self.assertNotIn('PRIVATE DETAILS', output.getvalue())

    def test_gui_worker_exception_is_logged_not_displayed(self):
        events = mock.Mock()
        with mock.patch.object(launcher, 'cli_command', side_effect=RuntimeError('PRIVATE DETAILS')), \
             mock.patch.object(launcher, 'log_message') as log, \
             mock.patch.object(launcher, 'LOG', mock.MagicMock()):
            launcher.worker_command(events, [])
        message = events.publish.call_args.args[1]
        self.assertNotIn('PRIVATE DETAILS', message)
        self.assertIn('Traceback', str(log.call_args))
        self.assertIn('RuntimeError: PRIVATE DETAILS', str(log.call_args))
