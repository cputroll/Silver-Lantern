import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from test_registration_seams import load_launcher


class GuiManifestSeamTests(unittest.TestCase):
    def test_headless_action_requires_dummy_driver_and_fixture(self):
        for dummy, manifest, expected in [('dummy', 'fixture.json', True), ('windows', 'fixture.json', False), ('dummy', '', False)]:
            app = load_launcher({'SDL_VIDEODRIVER': dummy, 'SILVER_LANTERN_GAME_MANIFEST': manifest, 'SILVER_LANTERN_TEST_GUI_INSTALL': '1'})
            self.assertEqual(app._test_gui_install_enabled(), expected)

    def test_channel_check_uses_fixture_when_configured(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            fixture = Path(d) / 'manifest.json'
            fixture.write_text('{"fixture": true}', encoding='utf-8')
            app = load_launcher({'SILVER_LANTERN_GAME_MANIFEST': str(fixture)})
            with patch.dict('sys.modules', {'game_channel': MagicMock()}), patch('importlib.util.spec_from_file_location') as spec:
                parser = MagicMock()
                parser.parse_game_manifest.return_value = {'fixture': True}
                with patch('importlib.util.module_from_spec', return_value=parser):
                    self.assertEqual(app.fetch_channel_manifest(Path(d), MagicMock()), {'fixture': True})

    def test_worker_uses_fixture_only_when_configured(self):
        for value in ('fixture.json', ''):
            with self.subTest(value=value):
                app = load_launcher({'SILVER_LANTERN_GAME_MANIFEST': value})
                instance = SimpleNamespace(folder='isolated', save=MagicMock(), set_screen=MagicMock())
                with patch.object(app, 'probe_writable'), patch.object(app.threading, 'Thread') as thread:
                    app.LauncherUI.start_operation(instance)
                args = thread.call_args.kwargs['args'][1]
                self.assertEqual(args, ['--no-self-update', '--install-game', 'isolated'] + (['--game-manifest', value] if value else []))
