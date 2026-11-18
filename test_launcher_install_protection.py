import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_game_sync import h
from test_game_v2 import V2Tests, sample


class LauncherInstallProtection(unittest.TestCase):
    def test_running_launcher_skipped_but_other_exe_installs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            running = root / 'launcher.exe'
            running.write_bytes(b'running')
            man = sample()
            first = copy.deepcopy(man['files'][0])
            first['path'] = 'launcher.exe'
            man['files'].insert(0, first)
            man['total_bytes'] *= 2
            with patch.object(h.sys, 'executable', str(running)), patch.object(h, 'running_launcher_paths', return_value=set()), patch.object(h.shutil, 'disk_usage', return_value=type('Usage', (), {'free': 100 * 1024 ** 3})()):
                V2Tests().run_install(root, man)
            self.assertEqual(running.read_bytes(), b'running')
            self.assertEqual((root / 'bin/game.exe').read_bytes(), b'abcdef')

    def test_other_running_copy_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            running = root / 'bin/game.exe'
            running.parent.mkdir()
            running.write_bytes(b'other launcher')
            with patch.object(h, 'running_launcher_paths', return_value={running}), patch.object(h.shutil, 'disk_usage', return_value=type('Usage', (), {'free': 100 * 1024 ** 3})()):
                V2Tests().run_install(root, sample())
            self.assertEqual(running.read_bytes(), b'other launcher')
