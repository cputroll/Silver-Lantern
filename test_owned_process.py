"""Offline Windows tests for atomic owned-process launches."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
SHELL = shutil.which('powershell.exe')


@unittest.skipUnless(os.name == 'nt' and SHELL, 'requires Windows PowerShell')
class OwnedProcessTests(unittest.TestCase):
    def run_script(self, script):
        return subprocess.run([SHELL, '-NoProfile', '-NonInteractive', '-Command', "$ErrorActionPreference='Stop'; " + script], cwd=ROOT, capture_output=True, text=True, timeout=20)

    def test_exit_code_and_quoted_arguments(self):
        with tempfile.TemporaryDirectory(prefix='owned process ') as directory:
            target = Path(directory) / 'result.txt'
            child = Path(directory) / 'child.ps1'
            child.write_text('param($p) [IO.File]::WriteAllText($p, "ok"); exit 10')
            script = ("Add-Type -Path './OwnedProcess.cs'; "
                      "$s=(Get-Command powershell.exe).Source; "
                      "$rc=[OwnedProcess]::Run($s,@('-NoProfile','-File','" + str(child).replace("'", "''") + "','" + str(target).replace("'", "''") + "'),10000); "
                      "if($rc -ne 10){exit 1}")
            result = self.run_script(script)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(target.read_text(), 'ok')

    def test_timeout_fails_closed(self):
        result = self.run_script("Add-Type -Path './OwnedProcess.cs'; try { $null=[OwnedProcess]::Run((Get-Command powershell.exe).Source,@('-NoProfile','-Command','Start-Sleep -Seconds 30'),300); exit 1 } catch { if ($_.Exception.ToString() -notmatch 'timed out') { [Console]::Error.WriteLine($_.Exception.ToString()); exit 2 }; exit 0 }")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_executable_fails(self):
        result = self.run_script("Add-Type -Path './OwnedProcess.cs'; try { [OwnedProcess]::Run('Z:\\missing-xyz.exe',@(),1000); exit 1 } catch { if ($_.Exception.ToString() -notmatch 'CreateProcess') { [Console]::Error.WriteLine($_.Exception.ToString()); exit 2 }; exit 0 }")
        self.assertEqual(result.returncode, 0, result.stderr)
