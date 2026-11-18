import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import qa_pause


class PauseTests(unittest.TestCase):
    def test_qa_pause_signals_and_waits_for_release(self):
        with tempfile.TemporaryDirectory() as td:
            ready, release = Path(td) / 'ready', Path(td) / 'release'
            env = {'SILVER_LANTERN_PAUSE_AT': 'assembly', 'SILVER_LANTERN_PAUSE_READY': str(ready), 'SILVER_LANTERN_PAUSE_RELEASE': str(release)}
            with patch.dict('os.environ', env):
                thread = threading.Thread(target=qa_pause.pause, args=('assembly', True))
                thread.start()
                deadline = time.monotonic() + 3
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(ready.exists())
                self.assertTrue(thread.is_alive())
                release.touch()
                thread.join(3)
                self.assertFalse(thread.is_alive())

    def test_nonmatching_checkpoint_does_not_wait(self):
        with patch.dict('os.environ', {'SILVER_LANTERN_PAUSE_AT': 'assembly'}):
            qa_pause.pause('pre_swap', True)

    def test_timeout_raises(self):
        with tempfile.TemporaryDirectory() as td, patch.dict('os.environ', {'SILVER_LANTERN_PAUSE_AT': 'assembly', 'SILVER_LANTERN_PAUSE_RELEASE': str(Path(td) / 'missing')}), patch('qa_pause.time.monotonic', side_effect=[0, 31]):
            with self.assertRaises(TimeoutError):
                qa_pause.pause('assembly', True)

    def test_production_ignores_pause_environment(self):
        with patch.dict('os.environ', {'SILVER_LANTERN_PAUSE_AT': 'assembly', 'SILVER_LANTERN_PAUSE_RELEASE': 'nonexistent'}):
            qa_pause.pause('assembly', False)
