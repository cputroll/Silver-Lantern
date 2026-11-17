import subprocess
import sys
import time
import unittest

import launcher


class ChildWatchdogTests(unittest.TestCase):
    def test_silent_child_is_stopped(self):
        proc = subprocess.Popen([sys.executable, '-u', '-c', 'import time; time.sleep(10)'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(proc.stdout.close)
        self.addCleanup(lambda: proc.kill() if proc.poll() is None else None)
        started = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, 'installation'):
            list(launcher.child_output(proc, 'installation', idle_timeout=0.3))
        self.assertLess(time.monotonic() - started, 4)
        self.assertIsNotNone(proc.poll())

    def test_timeout_names_last_phase(self):
        proc = subprocess.Popen([sys.executable, '-u', '-c', "import time; print('Preparing bundles', flush=True); time.sleep(10)"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(proc.stdout.close)
        self.addCleanup(lambda: proc.kill() if proc.poll() is None else None)
        with self.assertRaisesRegex(TimeoutError, 'Preparing bundles'):
            list(launcher.child_output(proc, 'installation', idle_timeout=.3))

    def test_progressing_child_is_not_stopped(self):
        script = "import time\nfor i in range(6):\n print(i, flush=True)\n time.sleep(.12)"
        proc = subprocess.Popen([sys.executable, '-u', '-c', script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(proc.stdout.close)
        self.addCleanup(lambda: proc.kill() if proc.poll() is None else None)
        self.assertEqual(list(launcher.child_output(proc, 'installation', idle_timeout=.4)), [f'{i}\n' for i in range(6)])
        self.assertEqual(proc.wait(timeout=2), 0)
