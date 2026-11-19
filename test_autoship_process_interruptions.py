"""Windows-only, offline process interruption tests; never invokes real publishing.

The publication hold is removed only in a temporary script copy. External
commands remain mocked; the updater is a real, disposable PowerShell process.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from test_autoship_offline_integration import HOLD, MOCKS, ROOT, SHELL, fixture_powershell_env


@unittest.skipUnless(os.name == 'nt' and SHELL, 'requires Windows PowerShell')
class PublisherInterruptions(unittest.TestCase):
    def run_interruption(self, kill_parent):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            game, remote, public, local = (tmp / x for x in ('game', 'remote', 'public', 'local'))
            for path in (game, remote, public, local):
                path.mkdir()
            (game / 'hookshot_version.txt').write_text('2.0.0')
            manifest = json.dumps({'revision': 3, 'game_version': '1.0.0', 'assets': [], 'files': []}, separators=(',', ':')).encode()
            (remote / 'game-manifest-3.json').write_bytes(manifest)
            (remote / 'game-manifest.json').write_bytes(b'stale')
            pointer = json.dumps({'version': 1, 'revision': 3, 'manifest': 'game-manifest-3.json', 'sha256': hashlib.sha256(manifest).hexdigest()})
            source = (ROOT / 'AutoShip-Game.ps1').read_text(encoding='utf-8-sig')
            self.assertEqual(source.count(HOLD), 1)
            source = source.replace(HOLD, '        # Isolated test copy only.')
            ownership_hold = "        throw 'Updater process-tree ownership unavailable; publication blocked'"
            self.assertEqual(source.count(ownership_hold), 1)
            source = source.replace(ownership_hold, '        # Isolated test copy only.')
            start = source.index('function Run(')
            end = source.index('function Get-AvailableRevision', start)
            mock_start = MOCKS.index('function powershell.exe {')
            mock_end = MOCKS.index('function Run(', mock_start)
            mocks = MOCKS[:mock_start] + MOCKS[mock_end:]
            source = source[:start] + mocks + '\n' + source[end:]
            self.assertIn('[OwnedProcess]::Run(', source)
            script = tmp / 'AutoShip-Game.ps1'
            script.write_text(source, encoding='utf-8')
            (tmp / 'OwnedProcess.cs').write_bytes((ROOT / 'OwnedProcess.cs').read_bytes())
            (tmp / 'OwnedProcessGuard.ps1').write_bytes((ROOT / 'OwnedProcessGuard.ps1').read_bytes())
            (tmp / 'descendant.ps1').write_text("[IO.File]::WriteAllText($env:TEST_DESCENDANT_PID, [string]$PID)\nStart-Sleep -Seconds 45\n", encoding='utf-8')
            (tmp / 'Update-Game.ps1').write_text("[IO.File]::WriteAllText($env:TEST_CHILD_PID, [string]$PID)\n& $env:TEST_SHELL -NoProfile -NonInteractive -File $env:TEST_DESCENDANT_SCRIPT\nexit 10\n", encoding='utf-8')
            events, pidfile = tmp / 'events.txt', tmp / 'child.pid'
            descendant_file = tmp / 'descendant.pid'
            env = fixture_powershell_env(os.environ)
            env.update(PUBLIC=str(public), LOCALAPPDATA=str(local), USERPROFILE=str(tmp), COMPUTERNAME='testhost', SILVERLANTERN_PUBLISH_HOST='testhost', GH_TOKEN='offline-only', TEST_LATEST='2.0.0', TEST_POINTER=pointer, TEST_REMOTE=str(remote), TEST_EVENTS=str(events), TEST_GAME=str(game), TEST_SHELL=SHELL, TEST_CHILD_PID=str(pidfile), TEST_DESCENDANT_PID=str(descendant_file), TEST_DESCENDANT_SCRIPT=str(tmp / 'descendant.ps1'))
            output = tmp / 'output.txt'
            child_pid = None
            descendant_pid = None
            with output.open('w', encoding='utf-8') as out:
                parent = subprocess.Popen([SHELL, '-NoProfile', '-NonInteractive', '-File', str(script), '-GameFolder', str(game)], env=env, stdout=out, stderr=subprocess.STDOUT)
                try:
                    deadline = time.monotonic() + 20
                    while not pidfile.exists() and parent.poll() is None and time.monotonic() < deadline:
                        time.sleep(.05)
                    self.assertTrue(pidfile.exists(), 'updater did not start: ' + output.read_text(encoding='utf-8', errors='replace'))
                    child_pid = int(pidfile.read_text())
                    if kill_parent:
                        while not descendant_file.exists() and parent.poll() is None and time.monotonic() < deadline:
                            time.sleep(.05)
                        self.assertTrue(descendant_file.exists(), 'updater descendant did not start')
                        descendant_pid = int(descendant_file.read_text())
                        parent.kill()
                        parent.wait(timeout=5)
                        deadline = time.monotonic() + 5
                        while time.monotonic() < deadline and (self.process_alive(child_pid) or self.process_alive(descendant_pid)):
                            time.sleep(.05)
                        self.assertFalse(self.process_alive(child_pid), 'updater survived publisher termination')
                        self.assertFalse(self.process_alive(descendant_pid), 'updater descendant survived publisher termination')
                        self.assertTrue((public / 'SilverLantern-AutoShip-updater-recovery.lock').is_file(), 'crash recovery marker missing')
                    else:
                        while not descendant_file.exists() and parent.poll() is None and time.monotonic() < deadline:
                            time.sleep(.05)
                        self.assertTrue(descendant_file.exists(), 'updater descendant did not start')
                        descendant_pid = int(descendant_file.read_text())
                        subprocess.run(['taskkill', '/PID', str(child_pid), '/F'], capture_output=True, timeout=5, check=True)
                        try:
                            parent.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            self.assertTrue(PublisherInterruptions.process_alive(descendant_pid))
                            self.assertTrue((public / 'SilverLantern-AutoShip-updater-recovery.lock').is_file())
                        else:
                            self.assertNotEqual(parent.returncode, 0)
                            self.assertIn('update_did_not_complete_a_build', output.read_text(encoding='utf-8', errors='replace'))
                    calls = events.read_text(encoding='utf-8-sig').splitlines() if events.exists() else []
                    self.assertFalse(any(' upload ' in c or ' push ' in c or c.startswith('python ') for c in calls), calls)
                    self.assertEqual({p.name: p.read_bytes() for p in remote.iterdir()}, {'game-manifest-3.json': manifest, 'game-manifest.json': b'stale'})
                finally:
                    if parent.poll() is None:
                        parent.kill()
                        parent.wait(timeout=5)
                    if child_pid and self.process_alive(child_pid):
                        subprocess.run(['taskkill', '/PID', str(child_pid), '/T', '/F'], capture_output=True, timeout=5)
                    if descendant_pid is None and descendant_file.exists():
                        descendant_pid = int(descendant_file.read_text())
                    if descendant_pid and self.process_alive(descendant_pid):
                        subprocess.run(['taskkill', '/PID', str(descendant_pid), '/T', '/F'], capture_output=True, timeout=5)

    @staticmethod
    def process_alive(pid):
        result = subprocess.run(['tasklist', '/FI', 'PID eq ' + str(pid), '/FO', 'CSV', '/NH'], capture_output=True, text=True, timeout=5)
        return '"%s"' % pid in result.stdout

    def test_killed_updater_prevents_publication(self):
        self.run_interruption(False)

    def test_killed_publisher_kills_updater_and_descendant(self):
        self.run_interruption(True)


@unittest.skipUnless(os.name == 'nt' and SHELL, 'requires Windows PowerShell')
class OwnedProcessLifetimeTests(unittest.TestCase):
    def run_tree_case(self, root_exits):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            helper = tmp / 'OwnedProcess.cs'
            helper.write_bytes((ROOT / 'OwnedProcess.cs').read_bytes())
            child = tmp / 'child.ps1'
            grandchild = tmp / 'grandchild.ps1'
            pidfile = tmp / 'grandchild.pid'
            grandchild.write_text("[IO.File]::WriteAllText($env:TREE_PID, [string]$PID); Start-Sleep -Seconds 45", encoding='utf-8')
            child.write_text("$p=Start-Process -FilePath $env:TREE_SHELL -ArgumentList @('-NoProfile','-File', ('\"' + $env:TREE_SCRIPT + '\"')) -PassThru; "
                             "while (!(Test-Path -LiteralPath $env:TREE_PID)) { Start-Sleep -Milliseconds 25 }; " +
                             ("exit 10" if root_exits else "Start-Sleep -Seconds 45; exit 10"), encoding='utf-8')
            runner = tmp / 'runner.ps1'
            runner.write_text("$ErrorActionPreference='Stop'; Add-Type -Path $env:TREE_HELPER; "
                              "try { $rc=[OwnedProcess]::Run($env:TREE_SHELL,@('-NoProfile','-File',$env:TREE_CHILD),2000); "
                              "Write-Output ('RETURN=' + $rc) } catch { Write-Output ('ERROR=' + $_.Exception.ToString()) }", encoding='utf-8')
            env = os.environ.copy()
            env.update(TREE_PID=str(pidfile), TREE_SHELL=SHELL, TREE_SCRIPT=str(grandchild), TREE_HELPER=str(helper), TREE_CHILD=str(child))
            try:
                result = subprocess.run([SHELL, '-NoProfile', '-NonInteractive', '-File', str(runner)], env=env, capture_output=True, text=True, timeout=15)
                self.assertTrue(pidfile.exists(), result.stdout + result.stderr)
                pid = int(pidfile.read_text())
                self.assertIn('ERROR=', result.stdout, 'live descendants must not yield successful completion: ' + result.stdout)
                self.assertFalse(PublisherInterruptions.process_alive(pid), 'descendant remained live when helper returned')
            finally:
                if pidfile.exists():
                    pid = int(pidfile.read_text())
                    if PublisherInterruptions.process_alive(pid):
                        subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True, timeout=5)

    def test_root_success_with_live_descendant_fails_closed(self):
        self.run_tree_case(True)

    def test_timeout_reaps_descendant_before_return(self):
        self.run_tree_case(False)

    def test_redirected_output_is_preserved(self):
        script = ("$ErrorActionPreference='Stop'; Add-Type -Path $env:TREE_HELPER; "
                  "$r=[OwnedProcess]::Run($env:TREE_SHELL,@('-NoProfile','-Command',\"[Console]::Out.WriteLine('CHILD_OUT'); [Console]::Error.WriteLine('CHILD_ERR')\"),5000); if($r -ne 0){exit 1}")
        env = os.environ.copy()
        env.update(TREE_HELPER=str(ROOT / 'OwnedProcess.cs'), TREE_SHELL=SHELL)
        result = subprocess.run([SHELL, '-NoProfile', '-NonInteractive', '-Command', script], env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('CHILD_OUT', result.stdout)
        self.assertIn('CHILD_ERR', result.stderr)

    def test_large_redirected_output_does_not_deadlock(self):
        script = ("$ErrorActionPreference='Stop'; Add-Type -Path $env:TREE_HELPER; "
                  "$r=[OwnedProcess]::Run($env:TREE_SHELL,@('-NoProfile','-Command',\"for(`$i=0; `$i -lt 2000; `$i++){ [Console]::Out.WriteLine(('O' * 100)); [Console]::Error.WriteLine(('E' * 100)) }\"),10000); if($r -ne 0){exit 1}")
        env = os.environ.copy()
        env.update(TREE_HELPER=str(ROOT / 'OwnedProcess.cs'), TREE_SHELL=SHELL)
        result = subprocess.run([SHELL, '-NoProfile', '-NonInteractive', '-Command', script], env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr[-1000:])
        self.assertEqual(result.stdout.count('O' * 100), 2000)
        self.assertEqual(result.stderr.count('E' * 100), 2000)

    def test_nested_job_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            nested = tmp / 'nested.ps1'
            nested.write_text("Add-Type -Path $env:TREE_HELPER; $rc=[OwnedProcess]::Run($env:TREE_SHELL,@('-NoProfile','-Command','exit 17'),5000); if($rc -ne 17){exit 2}; exit 0", encoding='utf-8')
            outer = "$ErrorActionPreference='Stop'; Add-Type -Path $env:TREE_HELPER; $rc=[OwnedProcess]::Run($env:TREE_SHELL,@('-NoProfile','-File',$env:TREE_NESTED),10000); if($rc -ne 0){exit 1}"
            env = os.environ.copy()
            env.update(TREE_HELPER=str(ROOT / 'OwnedProcess.cs'), TREE_SHELL=SHELL, TREE_NESTED=str(nested))
            result = subprocess.run([SHELL, '-NoProfile', '-NonInteractive', '-Command', outer], env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
