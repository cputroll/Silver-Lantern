import os
import subprocess
import sys
import tempfile
import time
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import launcher


@unittest.skipUnless(os.name == 'nt', 'Windows file-handle semantics')
class OwnershipRaceTests(unittest.TestCase):
    def test_concurrent_relocations_preserve_both_targets_and_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / 'game'
            game.mkdir()
            (game / 'Ocarina of Time.exe').write_bytes(b'occupied')
            fallback = root / 'Programs' / 'Ocarina of Time'
            fallback.mkdir(parents=True)
            (fallback / 'Ocarina of Time.exe').write_bytes(b'occupied')
            source = root / 'source.exe'
            source.write_bytes(b'launcher')
            code = '''import launcher, sys, os, time
from pathlib import Path
sys.frozen = True
sys.executable = sys.argv[1]
launcher.register_install = lambda *a, **k: None
launcher.log_message = lambda *a: None
launcher.subprocess.Popen = lambda *a, **k: time.sleep(.4)
launcher.relocate_launcher(Path(sys.argv[2]))
'''
            env = dict(os.environ, LOCALAPPDATA=str(root))
            children = [subprocess.Popen([sys.executable, '-c', code, str(source), str(game)], env=env) for _ in range(2)]
            for child in children:
                self.assertEqual(child.wait(timeout=20), 0)
            records = launcher._launcher_records(fallback)
            siblings = list(fallback.glob('Ocarina of Time-*.exe'))
            self.assertEqual(len(siblings), 2)
            self.assertEqual({p.name for p in siblings}, set(records))

    def test_replacement_during_verification_is_not_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stale = root / 'Ocarina of Time-old.exe'
            stale.write_bytes(b'owned')
            launcher._record_launcher_copy(root, stale)
            original = launcher.hashlib.sha256
            attempted = []
            def hashing(*args, **kwargs):
                digest = original(*args, **kwargs)
                class Proxy:
                    def update(self, block):
                        digest.update(block)
                    def hexdigest(self):
                        try:
                            stale.unlink()
                            stale.write_bytes(b'unowned')
                            attempted.append(True)
                        except OSError:
                            attempted.append(False)
                        return digest.hexdigest()
                return Proxy()
            with patch.object(launcher.hashlib, 'sha256', side_effect=hashing):
                launcher._cleanup_stale_siblings(root, root / 'keep.exe')
            self.assertTrue(attempted)
            if stale.exists():
                self.assertEqual(stale.read_bytes(), b'unowned')
            else:
                self.assertFalse(attempted[0])
