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
                self.assertTrue(any('Windows is checking the game files' in str(c) for c in say.call_args_list))
            finally:
                timer.cancel(); proc.wait(timeout=5)

    def test_deadline_reports_friendly_error_without_traceback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); target = root / 'Ocarina of Time.exe'; target.write_bytes(b'old')
            proc = self.lock(target)
            try:
                retry = h._retry_locked
                with patch.object(h, '_retry_locked', side_effect=lambda operation, budget=60, executable=False, path=None: retry(operation, budget=0, executable=executable, path=path)), patch.object(h, 'main', side_effect=lambda: self.assemble(root)), patch.object(h, 'log') as log, patch.object(h, 'say') as say, patch.object(h, '_UI_READY', False), patch.object(h.sys, 'argv', ['test', '--no-pause']):
                    with self.assertRaises(SystemExit) as result:
                        h.run()
                self.assertEqual(result.exception.code, 1)
                self.assertIn('Traceback', str(log.call_args_list))
            finally:
                proc.terminate(); proc.wait(timeout=5)
            self.assertEqual(target.read_bytes(), b'old')

    def clocked_replace(self, src, target, cap):
        """Run one _replace_with_retry on a fake clock; return its log calls."""
        clock = [0.0]
        with patch.object(h.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(h.time, 'sleep', side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)), \
             patch.object(h, 'say'), patch.object(h, 'log') as log, \
             patch.object(h, 'FILE_LOCK_CAP', cap):
            with self.assertRaises(PermissionError):
                h._replace_with_retry(src, target)
        return log.call_args_list

    def lock_lines(self, calls):
        return [str(c.args[0]) for c in calls if c.args and 'File lock retry path=' in str(c.args[0])]

    def test_lock_retry_line_names_external_holder_process(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / 'Ocarina of Time.exe'; target.write_bytes(b'old')
            src = root / 'staged.exe'; src.write_bytes(b'new')
            h._FILE_LOCK_WAITED.clear()
            proc = self.lock(target)
            try:
                calls = self.clocked_replace(src, target, 3.0)
            finally:
                proc.terminate(); proc.wait(timeout=5)
            lines = self.lock_lines(calls)
            self.assertTrue(lines)
            self.assertTrue(any(f'path={target}' in line for line in lines), lines)
            self.assertTrue(any(f'[{proc.pid}]' in line for line in lines), lines)
            self.assertTrue(any('cap exceeded' in str(c.args[0]) for c in calls if c.args), calls)

    def test_lock_retry_line_marks_our_own_process_as_self(self):
        import ctypes
        from ctypes import wintypes
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / 'Ocarina of Time.exe'; target.write_bytes(b'old')
            src = root / 'staged.exe'; src.write_bytes(b'new')
            h._FILE_LOCK_WAITED.clear()
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            kernel.CreateFileW.restype = wintypes.HANDLE
            handle = kernel.CreateFileW(str(target), 0x80000000, 0, None, 3, 0x80, None)
            self.assertNotEqual(handle, ctypes.c_void_p(-1).value, 'exclusive open failed')
            try:
                calls = self.clocked_replace(src, target, 3.0)
            finally:
                kernel.CloseHandle(handle)
            lines = self.lock_lines(calls)
            self.assertTrue(lines)
            self.assertTrue(any('holder=self' in line for line in lines), lines)

    def test_cumulative_cap_escalates_to_classified_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); target = root / 'Ocarina of Time.exe'; target.write_bytes(b'old')
            h._FILE_LOCK_WAITED.clear()
            proc = self.lock(target)
            try:
                with patch.object(h, 'FILE_LOCK_CAP', 0.0), patch.object(h, 'main', side_effect=lambda: self.assemble(root)), patch.object(h, 'log') as log, patch.object(h, 'say') as say, patch.object(h, '_UI_READY', False), patch.object(h.sys, 'argv', ['test', '--no-pause']):
                    with self.assertRaises(SystemExit) as result:
                        h.run()
                self.assertEqual(result.exception.code, 1)
                self.assertTrue(any('cap exceeded' in str(c) for c in log.call_args_list))
                self.assertIn('Traceback', str(log.call_args_list))
            finally:
                proc.terminate(); proc.wait(timeout=5)
            self.assertEqual(target.read_bytes(), b'old')
