"""Exercise the publisher's decision paths in a disposable, fully offline sandbox.

The real hold is checked separately.  For post-hold path coverage only, a copy
of the script has its single hold statement removed; all external commands are
replaced with fail-closed PowerShell functions before that copy is executed.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
HOLD = "        throw 'Publication hold: cross-host single-publisher enforcement and crash recovery require operational approval'"
UPDATER_HOLD = "        throw 'Updater process-tree ownership unavailable; publication blocked'"
SHELL = shutil.which('powershell.exe') or shutil.which('pwsh')


def fixture_powershell_env(env):
    # Hosted CI may export PowerShell 7's module path into a Windows
    # PowerShell 5.1 child. Use the modules belonging to the actual shell.
    return dict(env, PSModulePath=str(Path(SHELL).parent / 'Modules'))

MOCKS = r'''
function Invoke-RestMethod { return @{ tag_name = $env:TEST_LATEST } }
function git {
    $global:LASTEXITCODE = 0
    if ($args[0] -eq 'show') { return $env:TEST_POINTER }
    if ($args[0] -eq 'rev-parse') { return 'oldtip' }
    if ($args[0] -eq '-C' -and $args[2] -eq 'rev-parse') { return ('a' * 40) }
    throw "Unexpected direct git: $args"
}
function gh {
    $global:LASTEXITCODE = 0
    if ($args[0] -eq 'release' -and $args[1] -eq 'view') {
        $assets = @()
        Get-ChildItem -LiteralPath $env:TEST_REMOTE -File | ForEach-Object {
            $assets += @{ name = $_.Name; digest = 'sha256:' + (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant() }
        }
        return (@{ assets = $assets } | ConvertTo-Json -Depth 5 -Compress)
    }
    throw "Unexpected direct gh: $args"
}
function powershell.exe {
    Add-Content -LiteralPath $env:TEST_EVENTS -Value ('updater ' + ($args -join ' '))
    $global:LASTEXITCODE = [int]$env:TEST_UPDATE_EXIT
}
function Run([string]$Program, [string[]]$Arguments) {
    $global:LASTEXITCODE = 0
    Add-Content -LiteralPath $env:TEST_EVENTS -Value ($Program + ' ' + ($Arguments -join ' '))
    if ($Program -eq 'gh' -and $Arguments[1] -eq 'download') {
        $name = $Arguments[[array]::IndexOf($Arguments, '--pattern') + 1]
        $dir = $Arguments[[array]::IndexOf($Arguments, '--dir') + 1]
        Copy-Item -LiteralPath (Join-Path $env:TEST_REMOTE $name) -Destination (Join-Path $dir $name) -Force
    } elseif ($Program -eq 'gh' -and $Arguments[1] -eq 'upload') {
        $source = $Arguments[3]
        Copy-Item -LiteralPath $source -Destination (Join-Path $env:TEST_REMOTE (Split-Path $source -Leaf)) -Force
    } elseif ($Program -eq 'python' -and (Split-Path $Arguments[0] -Leaf) -eq 'channel_publisher.py') {
        if ($Arguments[3] -ne 'oldtip' -or $Arguments[4] -ne ('a' * 40)) { throw 'wrong channel lease or candidate' }
        Copy-Item -LiteralPath (Join-Path $Arguments[1] 'channel.json') -Destination (Join-Path $env:TEST_REMOTE 'channel.json')
    } elseif ($Program -eq 'python' -and (Split-Path $Arguments[0] -Leaf) -eq 'pack-game.py') {
        $dir = $Arguments[[array]::IndexOf($Arguments, '--out') + 1]
        $prefix = $Arguments[[array]::IndexOf($Arguments, '--prefix') + 1]
        $assets = @()
        foreach ($suffix in @('one.zip', 'two.zip')) {
            $name = $prefix + $suffix
            [IO.File]::WriteAllText((Join-Path $dir $name), $name)
            $file = Get-Item -LiteralPath (Join-Path $dir $name)
            $assets += @{ asset = $name; size = $file.Length; sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToLowerInvariant() }
        }
        $versionFile = Join-Path $env:TEST_GAME 'hookshot_version.txt'
        $manifest = @{ assets = $assets; files = @(@{ path = 'hookshot_version.txt'; sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $versionFile).Hash.ToLowerInvariant() }) }
        [IO.File]::WriteAllText((Join-Path $dir 'game-manifest.json'), ($manifest | ConvertTo-Json -Depth 8 -Compress))
    } elseif ($Program -eq 'git') {
        if ($Arguments[0] -eq 'worktree' -and $Arguments[1] -eq 'add') { New-Item -ItemType Directory -Path $Arguments[3] | Out-Null }
    } else { throw "Unexpected command: $Program $Arguments" }
}
'''


@unittest.skipUnless(SHELL, 'PowerShell is required')
class PublisherOfflineIntegration(unittest.TestCase):
    def run_case(self, installed='1.0.0', latest='1.0.0', channel='1.0.0', bypass=True, force=False, update_exit=10, bypass_updater=True):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            game, remote, public, local = (tmp / x for x in ('game', 'remote', 'public', 'local'))
            for path in (game, remote, public, local):
                path.mkdir()
            (game / 'hookshot_version.txt').write_text(installed, encoding='utf-8')
            manifest = json.dumps({'revision': 3, 'game_version': channel, 'assets': [], 'files': []}, separators=(',', ':')).encode()
            import hashlib
            (remote / 'game-manifest-3.json').write_bytes(manifest)
            (remote / 'game-manifest.json').write_text('stale', encoding='utf-8')
            pointer = json.dumps({'version': 1, 'revision': 3, 'manifest': 'game-manifest-3.json', 'sha256': hashlib.sha256(manifest).hexdigest()})
            source = (ROOT / 'AutoShip-Game.ps1').read_text(encoding='utf-8-sig')
            self.assertEqual(source.count(HOLD), 1, 'production hold must remain unconditional')
            self.assertEqual(source.count(UPDATER_HOLD), 1, 'updater hold must remain in production')
            if bypass:
                source = source.replace(HOLD, '        # Test-only bypass in disposable copy.')
            if bypass_updater:
                source = source.replace(UPDATER_HOLD, '        # Test-only bypass in disposable copy.')
            # Never compile or start an owned process in the fixture; retain the real exit check.
            launch_start = source.index("        Add-Type -Path (Join-Path $repo 'OwnedProcess.cs')")
            launch_end = source.index('        if ($updateExit -ne 10)', launch_start)
            launch = source[launch_start:launch_end]
            self.assertIn("'-RequireBuild'", launch)
            source = source[:launch_start] + "        Add-Content -LiteralPath $env:TEST_EVENTS -Value 'updater -RequireBuild'\n        $updateExit = [int]$env:TEST_UPDATE_EXIT\n" + source[launch_end:]
            shutil.copyfile(ROOT / 'OwnedProcessGuard.ps1', tmp / 'OwnedProcessGuard.ps1')
            # Replace only the external-command wrapper; direct invocations are mocked too.
            start = source.index('function Run(')
            end = source.index('function Get-AvailableRevision', start)
            source = source[:start] + MOCKS + '\n' + source[end:]
            script = tmp / 'AutoShip-Game.ps1'
            script.write_text(source, encoding='utf-8')
            shutil.copyfile(ROOT / 'OwnedProcess.cs', tmp / 'OwnedProcess.cs')
            (tmp / 'Update-Game.ps1').write_text("Add-Content -LiteralPath $env:TEST_EVENTS -Value ('updater ' + ($args -join ' '))\nexit ([int]$env:TEST_UPDATE_EXIT)\n", encoding='utf-8')
            events = tmp / 'events.txt'
            env = fixture_powershell_env(os.environ)
            env.update(PUBLIC=str(public), LOCALAPPDATA=str(local), USERPROFILE=str(tmp), COMPUTERNAME='testhost', SILVERLANTERN_PUBLISH_HOST='testhost', GH_TOKEN='offline-only', TEST_LATEST=latest, TEST_POINTER=pointer, TEST_REMOTE=str(remote), TEST_EVENTS=str(events), TEST_GAME=str(game), TEST_UPDATE_EXIT=str(update_exit))
            args = [SHELL, '-NoProfile', '-NonInteractive', '-File', str(script), '-GameFolder', str(game)]
            if force:
                args.append('-Force')
            result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=30)
            calls = events.read_text(encoding='utf-8-sig').splitlines() if events.exists() else []
            files = {p.name: p.read_bytes() for p in remote.iterdir()}
            return result, calls, files, manifest

    def test_real_hold_stops_before_any_external_action(self):
        result, calls, _, _ = self.run_case(bypass=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn('Publication_hold', result.stdout)
        self.assertEqual(calls, [])

    def test_updater_hold_stops_before_launch_or_publication(self):
        result, calls, files, manifest = self.run_case(
            installed='2.0.0', latest='2.0.0', channel='1.0.0', bypass_updater=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('Updater_process-tree_ownership_unavailable', result.stdout)
        self.assertFalse(any(c.startswith(('updater ', 'python ')) or ' upload ' in c or ' push ' in c for c in calls))
        self.assertNotIn('channel.json', files)
        self.assertEqual(files['game-manifest-3.json'], manifest)

    def test_current_channel_leaves_stale_legacy_unchanged(self):
        result, calls, files, manifest = self.run_case()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('status=current', result.stdout)
        self.assertEqual(files['game-manifest.json'], b'stale')
        self.assertEqual(files['game-manifest-3.json'], manifest)
        self.assertTrue(any(' download ' in c and 'game-manifest-3.json' in c for c in calls))
        self.assertFalse(any(' upload ' in c or ' push ' in c for c in calls))

    def test_channel_behind_installed_recovers_by_publishing(self):
        result, calls, files, _ = self.run_case(installed='2.0.0', latest='2.0.0', channel='1.0.0')
        self.assertEqual(result.returncode, 10, result.stdout + result.stderr)
        self.assertIn('status=shipped', result.stdout)
        self.assertTrue(any(c.startswith('updater ') and '-RequireBuild' in c for c in calls))
        pointer = json.loads(files['channel.json'])
        self.assertEqual(pointer['revision'], 4)
        self.assertEqual(pointer['previous']['revision'], 3)
        self.assertEqual(files['game-manifest.json'], b'stale')
        self.assertEqual(pointer['manifest'], 'game-manifest-4.json')
        import hashlib
        self.assertEqual(pointer['sha256'], hashlib.sha256(files[pointer['manifest']]).hexdigest())
        self.assertEqual(json.loads(files[pointer['manifest']])['game_version'], '2.0.0')
        self.assertFalse(any(' upload ' in c and '--clobber' in c for c in calls))
        self.assertTrue(any('channel_publisher.py' in c and 'origin oldtip ' in c for c in calls))

    def test_incomplete_updater_never_publishes(self):
        # 0 is a no-op, 1 is a failure, and 130 represents an interrupted updater.
        for exit_code in (0, 1, 130):
            with self.subTest(update_exit=exit_code):
                result, calls, files, manifest = self.run_case(
                    installed='2.0.0', latest='2.0.0', channel='1.0.0',
                    update_exit=exit_code)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn('update_did_not_complete_a_build', result.stdout)
                self.assertTrue(any(c.startswith('updater ') and '-RequireBuild' in c for c in calls))
                self.assertFalse(any(' upload ' in c or ' push ' in c or c.startswith('python ') for c in calls))
                self.assertNotIn('channel.json', files)
                self.assertEqual(files['game-manifest-3.json'], manifest)
                self.assertEqual(files['game-manifest.json'], b'stale')

    def test_forced_downgrade_stops_before_upload_or_push(self):
        result, calls, _, _ = self.run_case(installed='1.0.0', latest='1.0.0', channel='2.0.0', force=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('regress_authoritative_channel', result.stdout)
        self.assertFalse(any(' upload ' in c or ' push ' in c for c in calls))


if __name__ == '__main__':
    unittest.main()
