import io
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

from test_updater import app
import game_channel
import launcher


class RetryDiagnosticsTests(unittest.TestCase):
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
