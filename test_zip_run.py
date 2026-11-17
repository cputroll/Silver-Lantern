import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_game_sync import h


class ZipRunTests(unittest.TestCase):
    def test_transient_relaunch_preserves_arguments(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); temp = root / 'temp'; temp.mkdir(); source = temp / 'x' / 'Run Silver Lantern.exe'; source.parent.mkdir(); source.write_bytes(b'exe')
            target = root / 'local' / 'Programs' / 'Silver Lantern' / 'SilverLantern.exe'
            with patch.object(h.sys, 'frozen', True, create=True), patch.object(h.sys, 'executable', str(source)), patch.object(h.sys, 'argv', [str(source), '--version']), patch.dict(h.os.environ, {'TEMP': str(temp), 'LOCALAPPDATA': str(root / 'local')}), patch.object(h.subprocess, 'Popen') as popen:
                self.assertTrue(h.relocate_transient())
                self.assertEqual(target.read_bytes(), b'exe')
                self.assertEqual(popen.call_args.args[0], [str(target), '--version'])

    def test_locked_target_launches_existing(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); temp = root / 'temp'; temp.mkdir(); source = temp / 'app.exe'; source.write_bytes(b'new')
            target = root / 'local' / 'Programs' / 'Silver Lantern' / 'SilverLantern.exe'; target.parent.mkdir(parents=True); target.write_bytes(b'old')
            with patch.object(h.sys, 'frozen', True, create=True), patch.object(h.sys, 'executable', str(source)), patch.object(h.sys, 'argv', [str(source), '--uninstall']), patch.dict(h.os.environ, {'TEMP': str(temp), 'LOCALAPPDATA': str(root / 'local')}), patch.object(h.os, 'replace', side_effect=PermissionError), patch.object(h.subprocess, 'Popen') as popen:
                self.assertTrue(h.relocate_transient())
                self.assertEqual(target.read_bytes(), b'old')
                self.assertEqual(popen.call_args.args[0], [str(target), '--uninstall'])

    def test_missing_local_manifest_does_not_block_zip_launch(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); temp = root / 'temp'; temp.mkdir(); here = temp / 'archive.zip'; here.mkdir()
            with patch.object(h.sys, 'frozen', True, create=True), patch.object(h, 'HERE', here), patch.object(h, 'MANIFEST_LOCAL', here / 'missing'), patch.dict(h.os.environ, {'TEMP': str(temp)}), patch.object(h, '_log_location', return_value=root / 'test.log'), patch.object(h, 'set_title'):
                h.ui_init(plain=True)

    def test_manifest_fallback(self):
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as d:
            response = MagicMock(); response.json.return_value = {'game': {}, 'builder': {}}
            client = MagicMock(); client.get.return_value = response
            with patch.object(h, 'MANIFEST_LOCAL', Path(d)/'missing'), patch.object(h, 'MANIFEST_CACHE', Path(d)/'cache'), patch.object(h, 'session', return_value=client):
                h.load_manifest(None)
            self.assertEqual(client.get.call_args.args[0], 'https://raw.githubusercontent.com/cputroll/Silver-Lantern/main/sources.json')

    def test_non_transient_does_nothing(self):
        with tempfile.TemporaryDirectory() as d, patch.object(h.sys, 'frozen', True, create=True), patch.object(h.sys, 'executable', str(Path(d) / 'app.exe')), patch.dict(h.os.environ, {'TEMP': str(Path(d) / 'other')}), patch.object(h.subprocess, 'Popen') as popen:
            self.assertFalse(h.relocate_transient())
            popen.assert_not_called()
