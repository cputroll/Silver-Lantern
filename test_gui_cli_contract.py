import sys
import unittest
from unittest import mock
from test_game_sync import h


class GuiCliContractTests(unittest.TestCase):
    def test_launcher_argument_sets_are_accepted(self):
        cases = [
            ['--self-update', '--gui'],
            ['--cli', '--no-self-update', '--install-game', 'sentinel'],
            ['--cli', '--no-self-update', '--update-game', 'sentinel'],
            ['--uninstall'],
        ]
        for args in cases:
            with self.subTest(args=args), mock.patch.object(sys, 'argv', ['tool', *args]), mock.patch.object(h, 'relocate_transient', return_value=False), mock.patch.object(h, 'self_update_step', return_value=0), mock.patch.object(h, 'run_game_update', return_value=0), mock.patch.object(h, 'install_from_manifest'), mock.patch.object(h, 'apply_preset_step'), mock.patch('launcher.uninstall', return_value=True), mock.patch.object(h, 'session'):
                self.assertEqual(h.main(), 0)

    def test_version_does_not_relocate(self):
        import io
        with mock.patch.object(sys, 'argv', ['tool', '--version']), mock.patch.object(sys, 'frozen', True, create=True), mock.patch('launcher.relocate_launcher') as relocate, mock.patch('sys.stdout', new_callable=io.StringIO):
            with mock.patch.object(h, 'ensure_cli_console'):
                self.assertEqual(h.main(), 0)
        relocate.assert_not_called()

    def test_bare_gui_dispatch(self):
        with mock.patch.object(sys, 'argv', ['tool', '--gui']), mock.patch.object(sys, 'frozen', True, create=True), mock.patch('launcher.resolve_game_folder'), mock.patch('launcher.relocate_launcher', return_value=False), mock.patch('launcher.main', return_value=0) as gui:
            self.assertEqual(h.main(), 0)
            gui.assert_called_once()
