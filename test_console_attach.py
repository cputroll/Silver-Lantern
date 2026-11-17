import unittest
from unittest import mock
from test_game_sync import h


class ConsoleAttachTests(unittest.TestCase):
    def test_pipe_is_preserved(self):
        stream = mock.MagicMock()
        stream.fileno.return_value = 3
        with mock.patch.object(h.sys, 'stdout', stream), mock.patch.object(h, '_attach_parent_console') as attach:
            h.ensure_cli_console()
            attach.assert_not_called()

    def test_inherited_pipe_skips_attach(self):
        with mock.patch.object(h.sys, 'stdout', None), mock.patch.object(h, '_restore_inherited_pipes', return_value=True), mock.patch.object(h, '_attach_parent_console') as attach:
            h.ensure_cli_console()
            attach.assert_not_called()

    def test_attach_failure_does_not_rebind(self):
        with mock.patch.object(h.sys, 'stdout', None), mock.patch.object(h, '_restore_inherited_pipes', return_value=False), mock.patch.object(h, '_attach_parent_console', return_value=False), mock.patch.object(h, '_rebind_console_streams') as rebind:
            h.ensure_cli_console()
            rebind.assert_not_called()

    def test_attach_success_rebinds(self):
        with mock.patch.object(h.sys, 'stdout', None), mock.patch.object(h, '_restore_inherited_pipes', return_value=False), mock.patch.object(h, '_attach_parent_console', return_value=True), mock.patch.object(h, '_rebind_console_streams') as rebind:
            h.ensure_cli_console()
            rebind.assert_called_once()
