import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import launcher


@unittest.skipUnless(os.name == 'nt', 'Windows file-handle semantics')
class OwnershipRaceTests(unittest.TestCase):
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
