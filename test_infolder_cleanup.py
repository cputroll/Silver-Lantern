import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import launcher

class CleanupTests(unittest.TestCase):
    def test_cleanup_wait_is_bounded_and_only_targets_running_exe(self):
        with tempfile.TemporaryDirectory() as d, patch.object(launcher.sys, 'frozen', True, create=True), patch.object(launcher.sys, 'executable', str(Path(d) / 'Ocarina of Time.exe')), patch.object(launcher, 'powershell') as run:
            launcher.schedule_owned_cleanup()
        script = run.call_args.args[0]
        self.assertIn('WaitForExit(30000)', script)
        self.assertIn('Ocarina of Time.exe', script)
        self.assertNotIn('-Recurse', script)
        self.assertNotIn('SilverLantern.exe', script)
