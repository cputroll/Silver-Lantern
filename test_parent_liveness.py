import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


class ParentLivenessTests(unittest.TestCase):
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
env=dict(os.environ,SILVER_LANTERN_PARENT_PID=str(os.getpid()))
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
