import hashlib
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from test_game_sync import h


@unittest.skipUnless(os.name == 'nt', 'Windows sharing semantics required')
class InstallFileLockTests(unittest.TestCase):
    def lock(self, target):
        ready = target.with_name('lock-ready')
        code = """import ctypes,sys,time
from pathlib import Path
from ctypes import wintypes
k=ctypes.WinDLL('kernel32',use_last_error=True)
k.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
k.CreateFileW.restype=wintypes.HANDLE
handle=k.CreateFileW(sys.argv[1],0x80000000,0,None,3,0,None)
if handle == ctypes.c_void_p(-1).value: raise ctypes.WinError(ctypes.get_last_error())
Path(sys.argv[3]).write_text('ready')
time.sleep(float(sys.argv[2]))
k.CloseHandle.argtypes=[wintypes.HANDLE]
k.CloseHandle(handle)
"""
        proc = subprocess.Popen([sys.executable, '-c', code, str(target), '30', str(ready)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        deadline = time.monotonic() + 5
        while not ready.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(ready.exists(), 'lock process did not become ready')
        return proc

    def assemble(self, root):
        data = b'new game'; digest = hashlib.sha256(data).hexdigest()
        (root / 'bundle').write_bytes(data)
        item = {'path': 'Ocarina of Time.exe', 'size': len(data), 'sha256': digest,
                'segments': [{'asset': 'bundle', 'offset': 0, 'length': len(data), 'sha256': digest}]}
        h.assemble_segmented([item], {'bundle': {'asset': 'bundle'}}, root, root, lambda _: None, True)

    def test_locked_destination_retries_until_released(self):
        import threading
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); target = root / 'Ocarina of Time.exe'; target.write_bytes(b'old')
            proc = self.lock(target)
            timer = threading.Timer(.3, proc.terminate); timer.start()
            try:
                with patch.object(h, 'say') as say:
                    self.assemble(root)
                self.assertEqual(target.read_bytes(), b'new game')
                self.assertTrue(any('Waiting for Windows' in str(c) for c in say.call_args_list))
            finally:
                timer.cancel(); proc.wait(timeout=5)

    def test_deadline_reports_friendly_error_without_traceback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); target = root / 'Ocarina of Time.exe'; target.write_bytes(b'old')
            proc = self.lock(target)
            try:
                retry = h._retry_locked
                with patch.object(h, '_retry_locked', side_effect=lambda operation, budget=60: retry(operation, budget=0)), patch.object(h, 'main', side_effect=lambda: self.assemble(root)), patch.object(h, 'log') as log, patch.object(h, 'say') as say, patch.object(h, '_UI_READY', False), patch.object(h.sys, 'argv', ['test', '--no-pause']):
                    with self.assertRaises(SystemExit) as result:
                        h.run()
                self.assertEqual(result.exception.code, 1)
                self.assertIn('Windows', str(say.call_args_list))
                self.assertNotIn('Traceback', str(log.call_args_list))
            finally:
                proc.terminate(); proc.wait(timeout=5)
            self.assertEqual(target.read_bytes(), b'old')
