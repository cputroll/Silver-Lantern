import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import os
import subprocess
from launcher import create_shortcut, cli_command
import sys
from launcher import App, IllegalTransition, State, InputMapper, resolve_folder, legacy_exists, ProgressEvents


class StateTests(unittest.TestCase):
    def test_frozen_cli_uses_same_executable(self):
        with patch.object(sys, 'frozen', True, create=True):
            self.assertEqual(cli_command('--version'), [sys.executable, '--cli', '--version'])

    def test_download_success(self):
        app = App(State.NOT_INSTALLED)
        app.transition(State.DOWNLOADING)
        app.transition(State.READY)
        self.assertEqual(app.state, State.READY)

    def test_illegal_transition(self):
        with self.assertRaises(IllegalTransition):
            App(State.NOT_INSTALLED).transition(State.PLAYING)

    def test_cancel_update(self):
        app = App(State.UPDATE_AVAILABLE)
        app.transition(State.UPDATING)
        app.transition(State.UPDATE_AVAILABLE)
        self.assertEqual(app.state, State.UPDATE_AVAILABLE)

    def test_button_release(self):
        mapper = InputMapper()
        self.assertEqual(mapper.feed({'type': 'button', 'button': 'a', 'down': True}, 0), [])
        self.assertEqual(mapper.feed({'type': 'button', 'button': 'a', 'down': False}, 0), ['accept'])

    def test_deadzone(self):
        self.assertEqual(InputMapper().feed({'type': 'axis', 'x': .2, 'y': .1}, 0), [])

    def test_repeat(self):
        mapper = InputMapper()
        self.assertEqual(mapper.feed({'type': 'axis', 'x': 0, 'y': 1}, 0), ['down'])
        self.assertEqual(mapper.tick(.39), [])
        self.assertEqual(mapper.tick(.41), ['down'])

    def test_default_folder(self):
        self.assertEqual(resolve_folder(Path('primary'), Path('fallback'), lambda p: None)[0], Path('primary'))

    def test_acl_fallback(self):
        def probe(path):
            if path == Path('primary'):
                raise PermissionError()
        self.assertEqual(resolve_folder(Path('primary'), Path('fallback'), probe)[0], Path('fallback'))

    def test_legacy_adoption(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            (folder / '.heylisten-parts').mkdir()
            self.assertTrue(legacy_exists(folder))

    def test_progress_queue(self):
        events = ProgressEvents()
        events.publish('progress', 42)
        self.assertEqual(events.queue.get_nowait(), ('progress', 42))

    @unittest.skipUnless(os.name == 'nt', 'Windows shortcuts require COM')
    def test_shortcut_redirected_desktop(self):
        with tempfile.TemporaryDirectory() as root:
            profile = Path(root)
            (profile / 'Desktop').mkdir()
            folder = profile / "game's folder"
            folder.mkdir()
            (folder / 'HeyListen.exe').touch()
            with patch.dict(os.environ, {'USERPROFILE': str(profile)}):
                create_shortcut(folder)
                self.assertTrue((profile / 'Desktop' / 'Silver Lantern.lnk').is_file())
                create_shortcut(folder, False)
                self.assertFalse((profile / 'Desktop' / 'Silver Lantern.lnk').exists())

    def test_cancellation(self):
        events = ProgressEvents()
        events.cancel()
        self.assertTrue(events.cancelled.is_set())
