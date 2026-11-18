import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


class ParentLivenessTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows process identity semantics')
    def test_reused_pid_before_first_check_stops_worker(self):
        from unittest.mock import patch
        from test_game_sync import h
        with patch.object(h, '_parent_initialized', False), patch.object(h, '_parent_handle', None), \
             patch.object(h, '_parent_pid', None), patch.dict(os.environ, {
                 'SILVER_LANTERN_PARENT_PID': str(os.getpid()),
                 'SILVER_LANTERN_PARENT_CREATION_TIME': '1',
             }):
            with self.assertRaises(h.ParentExited):
                h.check_parent_alive()

    @unittest.skipUnless(os.name == 'nt', 'Windows process identity semantics')
    def test_missing_creation_time_stops_worker(self):
        from unittest.mock import patch
        from test_game_sync import h
        env = dict(os.environ, SILVER_LANTERN_PARENT_PID=str(os.getpid()))
        env.pop('SILVER_LANTERN_PARENT_CREATION_TIME', None)
        with patch.object(h, '_parent_initialized', False), patch.object(h, '_parent_handle', None), \
             patch.object(h, '_parent_pid', None), patch.dict(os.environ, env, clear=True):
            with self.assertRaises(h.ParentExited):
                h.check_parent_alive()

    @unittest.skipUnless(os.name == 'nt', 'Windows process handle semantics')
    def test_parent_handle_is_not_reopened_after_pid_changes(self):
        from unittest.mock import patch
        from test_game_sync import h
        with patch.object(h, '_parent_initialized', False), patch.object(h, '_parent_handle', None), \
             patch.object(h, '_parent_pid', None), patch.dict(os.environ, __import__('parent_identity').parent_environment()):
            h.check_parent_alive()
            handle = h._parent_handle
            self.assertTrue(handle)
            os.environ['SILVER_LANTERN_PARENT_PID'] = '99999999'
            h.check_parent_alive()
            self.assertEqual(h._parent_handle, handle)
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32')
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(handle)

    def test_locked_retry_checks_parent_before_each_attempt(self):
        from unittest.mock import patch
        from test_game_sync import h
        attempts = []
        def operation():
            attempts.append(1)
            raise PermissionError('locked')
        with patch.object(h, 'check_parent_alive', side_effect=[None, h.ParentExited()]), \
             patch.object(h.time, 'sleep'):
            with self.assertRaises(h.ParentExited):
                h._retry_locked(operation, budget=5)
        self.assertEqual(len(attempts), 1)

    def test_parent_death_during_delayed_segment_copy_preserves_target(self):
        root = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            ready, exit_file = tmp / 'ready', tmp / 'exit'
            game, cache = tmp / 'game', tmp / 'cache'
            game.mkdir(); cache.mkdir()
            target = game / 'file'
            target.write_bytes(b'old')
            (cache / 'bundle').write_bytes(b'new')
            child_code = '''import hashlib,os,sys,time
from pathlib import Path
from test_game_sync import h
h.check_parent_alive()
real_read=h.os.read
ready=Path(sys.argv[3])
def delayed(fd,n):
 data=real_read(fd,n)
 ready.write_text('ready')
 time.sleep(2)
 return data
h.os.read=delayed
item={'path':'file','size':3,'sha256':hashlib.sha256(b'new').hexdigest(),'segments':[{'asset':'bundle','offset':0,'length':3,'sha256':hashlib.sha256(b'new').hexdigest()}]}
try:
 h.assemble_segmented([item], {'bundle':{}}, Path(sys.argv[2]), Path(sys.argv[1]), lambda asset: None, True)
except h.ParentExited:
 Path(sys.argv[4]).write_text('stopped')
'''
            parent_code = '''import os,subprocess,sys,time
from parent_identity import parent_environment
env=parent_environment()
subprocess.Popen([sys.executable,'-c',sys.argv[1],*sys.argv[2:]],env=env)
time.sleep(60)
'''
            parent = subprocess.Popen([sys.executable, '-c', parent_code, child_code, str(game), str(cache), str(ready), str(exit_file)], cwd=root)
            try:
                deadline = time.monotonic() + 15
                while not ready.exists() and parent.poll() is None and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(ready.exists(), 'child did not start copying')
                parent.terminate(); parent.wait(timeout=5)
                deadline = time.monotonic() + 8
                while not exit_file.exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(exit_file.exists(), 'child did not stop after parent exited')
                self.assertEqual(target.read_bytes(), b'old')
            finally:
                if parent.poll() is None:
                    parent.terminate(); parent.wait(timeout=5)

    @unittest.skipUnless(os.name == 'nt', 'Windows locking semantics')
    def test_parent_death_stops_locked_replacement(self):
        root = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ready, exit_file = tmp / 'ready', tmp / 'exit'
            target, staged = tmp / 'target', tmp / 'staged'
            target.write_bytes(b'old')
            staged.write_bytes(b'new')
            child_code = '''import sys,time
from pathlib import Path
from test_game_sync import h
h.check_parent_alive()
Path(sys.argv[3]).write_text('ready')
try:
 h._replace_with_retry(Path(sys.argv[1]),Path(sys.argv[2]),budget=15)
except h.ParentExited:
 Path(sys.argv[4]).write_text('stopped')
'''
            parent_code = '''import os,subprocess,sys,time
from parent_identity import parent_environment
env=parent_environment()
subprocess.Popen([sys.executable,'-c',sys.argv[1],*sys.argv[2:]],env=env)
time.sleep(60)
'''
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            kernel.CreateFileW.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.CreateFileW(str(target), 0x80000000, 1, None, 3, 0, None)
            self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
            parent = subprocess.Popen([sys.executable, '-c', parent_code, child_code, str(staged), str(target), str(ready), str(exit_file)], cwd=root)
            try:
                deadline = time.monotonic() + 15
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(ready.exists())
                time.sleep(.2)
                parent.terminate(); parent.wait(timeout=5)
                deadline = time.monotonic() + 5
                while not exit_file.exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(exit_file.exists(), 'child did not stop while target locked')
            finally:
                kernel.CloseHandle(handle)
                if parent.poll() is None:
                    parent.terminate(); parent.wait(timeout=5)
            self.assertEqual(target.read_bytes(), b'old')

    def test_orphan_stops_at_install_boundary_and_releases_lock(self):
        root = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'game'
            ready = Path(tmp) / 'ready'
            output = Path(tmp) / 'output'
            child_code = '''import os,sys,time
from pathlib import Path
import game_channel
from test_game_sync import h, manifest
folder=Path(sys.argv[1]); ready=Path(sys.argv[2]); out=Path(sys.argv[3])
with out.open('w') as f:
 def main():
  with game_channel.install_lock(folder):
   def stream(session,url,dest,**kw):
    ready.write_text(str(os.getpid()))
    time.sleep(2)
    dest.write_bytes(b'abc')
    if kw.get('progress'): kw['progress'](3)
   h.stream_to=stream
   h.check_disk_space=lambda *a: None
   os.environ['SILVER_LANTERN_TEST_NO_DEFENDER']='1'
   h.install_game(None,manifest(),folder)
 h.main=main
 h.log=lambda text,*a,**k: f.write(str(text)+'\\n') or f.flush()
 h.sys.argv=['test','--no-pause']
 try: h.run()
 except SystemExit as e:
  Path(str(ready)+'.exit').write_text(str(e.code))
  sys.exit(e.code)
'''
            parent_code = '''import os,subprocess,sys,time
from parent_identity import parent_environment
env=parent_environment()
p=subprocess.Popen([sys.executable,'-c',sys.argv[1],*sys.argv[2:]],env=env)
time.sleep(60)
'''
            parent = subprocess.Popen([sys.executable, '-c', parent_code, child_code, str(folder), str(ready), str(output)], cwd=root)
            pid = None
            try:
                deadline = time.monotonic() + 15
                while not ready.exists() and parent.poll() is None and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(ready.exists(), 'child did not acquire lock')
                pid = int(ready.read_text())
                parent.terminate(); parent.wait(timeout=5)
                import game_channel
                deadline = time.monotonic() + 60
                acquired = False
                while time.monotonic() < deadline:
                    try:
                        with game_channel.install_lock(folder, timeout=0): acquired = True
                        break
                    except TimeoutError: time.sleep(.1)
                self.assertTrue(acquired, 'orphan retained install lock')
                exit_file = Path(str(ready) + '.exit')
                while not exit_file.exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(exit_file.exists(), 'orphan did not exit')
                self.assertEqual(exit_file.read_text(), '0')
                text = output.read_text()
                self.assertIn('parent exited; stopping', text)
                self.assertNotIn('Traceback', text)
                restart = '''from pathlib import Path
from test_game_sync import h, manifest
import game_channel,sys
folder=Path(sys.argv[1])
def stream(session,url,dest,**kw):
 dest.write_bytes(b'abc')
 if kw.get('progress'): kw['progress'](3)
h.stream_to=stream
h.check_disk_space=lambda *a: None
with game_channel.install_lock(folder): h.install_game(None,manifest(),folder)
'''
                env = dict(os.environ, SILVER_LANTERN_TEST_NO_DEFENDER='1')
                env.pop('SILVER_LANTERN_PARENT_PID', None)
                fresh = subprocess.run([sys.executable, '-c', restart, str(folder)], cwd=root, env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(fresh.returncode, 0, fresh.stdout + fresh.stderr)
                self.assertNotIn('Traceback', fresh.stdout + fresh.stderr)
                self.assertEqual((folder / 'bin' / 'game.exe').read_bytes(), b'abc')
            finally:
                if parent.poll() is None: parent.terminate(); parent.wait(timeout=5)
                if pid and not Path(str(ready) + '.exit').exists():
                    if os.name == 'nt':
                        subprocess.run(['taskkill', '/PID', str(pid), '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                    else:
                        try: os.kill(pid, 9)
                        except ProcessLookupError: pass
