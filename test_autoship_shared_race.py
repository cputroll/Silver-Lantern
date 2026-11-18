"""Two real publisher scripts racing against local Git and an exclusive fake asset service.

Production holds are bypassed only in temporary script copies. No network access
or updater process is permitted; the fake service creates assets atomically.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import test_autoship_offline_integration as fixture


MOCKS = r'''
function Invoke-RestMethod { return @{ tag_name = '2.0.0' } }
function git { & $env:TEST_GIT @args }
function gh {
    if ($args[0] -ne 'release' -or $args[1] -ne 'view') { throw 'Unexpected gh command' }
    $assets = @(Get-ChildItem -LiteralPath $env:TEST_REMOTE -File | ForEach-Object { @{ name = $_.Name } })
    if ($env:TEST_MODE -eq 'lease' -and $env:TEST_ID -eq 'two') { $assets += @{ name = 'r4-reserved.zip' } }
    $global:LASTEXITCODE = 0
    return (@{ assets = $assets } | ConvertTo-Json -Depth 5 -Compress)
}
function Run([string]$Program, [string[]]$Arguments) {
    if ($Program -eq 'git') {
        if ($env:TEST_MODE -eq 'lease' -and $Arguments[0] -eq 'push' -and $Arguments[-1] -eq 'HEAD:main') {
            if ($env:TEST_ID -eq 'two') {
                $deadline = [DateTime]::UtcNow.AddSeconds(20)
                while (-not (Test-Path (Join-Path $env:TEST_BARRIER 'main-one'))) {
                    if ([DateTime]::UtcNow -gt $deadline) { throw 'main push barrier timeout' }
                    Start-Sleep -Milliseconds 50
                }
                # Test-only bookkeeping isolation: do not let main's manifest conflict
                # prevent exercising the independent channel lease.
                & $env:TEST_GIT fetch origin main
                if ($LASTEXITCODE -ne 0) { throw 'local fetch failed' }
                & $env:TEST_GIT reset --soft FETCH_HEAD
                if ($LASTEXITCODE -ne 0) { throw 'local reset failed' }
                & $env:TEST_GIT commit -m 'Offline second manifest'
                if ($LASTEXITCODE -ne 0) { throw 'local commit failed' }
            }
            & $env:TEST_GIT @Arguments
            if ($LASTEXITCODE -ne 0) { throw "local git failed: $Arguments" }
            if ($env:TEST_ID -eq 'one') { [IO.File]::WriteAllText((Join-Path $env:TEST_BARRIER 'main-one'), 'done') }
        } elseif ($env:TEST_MODE -eq 'lease' -and $Arguments[0] -eq '-C' -and $Arguments[2] -eq 'push') {
            [IO.File]::WriteAllText((Join-Path $env:TEST_BARRIER ('channel-' + $env:TEST_ID)), ($Arguments -join ' '))
            $deadline = [DateTime]::UtcNow.AddSeconds(20)
            while (@(Get-ChildItem -LiteralPath $env:TEST_BARRIER -Filter 'channel-*').Count -lt 2) {
                if ([DateTime]::UtcNow -gt $deadline) { throw 'channel push barrier timeout' }
                Start-Sleep -Milliseconds 50
            }
            if ($env:TEST_ID -eq 'two') {
                while (-not (Test-Path (Join-Path $env:TEST_BARRIER 'channel-winner'))) {
                    if ([DateTime]::UtcNow -gt $deadline) { throw 'channel winner timeout' }
                    Start-Sleep -Milliseconds 50
                }
            }
            $savedPreference = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            $pushOutput = & $env:TEST_GIT @Arguments 2>&1
            $pushExit = $LASTEXITCODE
            $ErrorActionPreference = $savedPreference
            [IO.File]::WriteAllText((Join-Path $env:TEST_BARRIER ('result-' + $env:TEST_ID)), ($pushOutput -join "`n"))
            if ($pushExit -ne 0) { throw "channel lease rejected: $Arguments" }
            [IO.File]::WriteAllText((Join-Path $env:TEST_BARRIER 'channel-winner'), $env:TEST_ID)
        } else {
            & $env:TEST_GIT @Arguments
            if ($LASTEXITCODE -ne 0) { throw "local git failed: $Arguments" }
        }
    } elseif ($Program -eq 'gh' -and $Arguments[1] -eq 'download') {
        $name = $Arguments[[array]::IndexOf($Arguments, '--pattern') + 1]
        $dir = $Arguments[[array]::IndexOf($Arguments, '--dir') + 1]
        Copy-Item -LiteralPath (Join-Path $env:TEST_REMOTE $name) -Destination (Join-Path $dir $name) -Force
    } elseif ($Program -eq 'gh' -and $Arguments[1] -eq 'upload') {
        $source = $Arguments[3]
        if ($env:TEST_MODE -eq 'collision' -and (Split-Path $source -Leaf) -eq 'r4-one.zip') {
            [IO.File]::WriteAllText((Join-Path $env:TEST_BARRIER $env:TEST_ID), 'ready')
            $deadline = [DateTime]::UtcNow.AddSeconds(15)
            while (@(Get-ChildItem -LiteralPath $env:TEST_BARRIER -File).Count -lt 2) {
                if ([DateTime]::UtcNow -gt $deadline) { throw 'race barrier timeout' }
                Start-Sleep -Milliseconds 50
            }
        }
        $dest = Join-Path $env:TEST_REMOTE (Split-Path $source -Leaf)
        $stream = [IO.File]::Open($dest, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try { $bytes = [IO.File]::ReadAllBytes($source); $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Dispose() }
    } elseif ($Program -eq 'python') {
        $dir = $Arguments[[array]::IndexOf($Arguments, '--out') + 1]
        $prefix = $Arguments[[array]::IndexOf($Arguments, '--prefix') + 1]
        $assets = @()
        foreach ($suffix in @('one.zip', 'two.zip')) {
            $name = $prefix + $suffix
            [IO.File]::WriteAllText((Join-Path $dir $name), $env:TEST_ID + $name)
            $file = Get-Item -LiteralPath (Join-Path $dir $name)
            $assets += @{ asset = $name; size = $file.Length; sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToLowerInvariant() }
        }
        $versionFile = Join-Path $env:TEST_GAME 'hookshot_version.txt'
        $manifest = @{ assets = $assets; files = @(@{ path = 'hookshot_version.txt'; sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $versionFile).Hash.ToLowerInvariant() }) }
        [IO.File]::WriteAllText((Join-Path $dir 'game-manifest.json'), ($manifest | ConvertTo-Json -Depth 8 -Compress))
    } else { throw "Unexpected external command: $Program $Arguments" }
}
'''


@unittest.skipUnless(fixture.SHELL and shutil.which('git'), 'PowerShell and Git required')
class SharedPublisherRace(unittest.TestCase):
    def test_two_publishers_cannot_overwrite_assets(self):
        self.run_race('collision')

    def test_distinct_assets_reach_same_tip_and_stale_lease_is_rejected(self):
        self.run_race('lease')

    def run_race(self, mode):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Drop inherited Git settings, then allow only local file transport.
            safe_env = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}
            safe_env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                            GIT_TERMINAL_PROMPT='0', GIT_PROTOCOL_FROM_USER='0',
                            GIT_ALLOW_PROTOCOL='file', HOME=str(root), XDG_CONFIG_HOME=str(root))
            def git(*args, cwd=None):
                return subprocess.run(['git', *map(str, args)], cwd=cwd, env=safe_env, check=True, capture_output=True, text=True, timeout=15).stdout.strip()
            remote, assets, barrier = (root / name for name in ('remote.git', 'assets', 'barrier'))
            assets.mkdir(); barrier.mkdir()
            git('init', '--bare', remote)
            seed = root / 'seed'
            git('init', '-b', 'main', seed)
            git('config', 'user.name', 'Offline Test', cwd=seed)
            git('config', 'user.email', 'offline@example.invalid', cwd=seed)
            (seed / 'game-manifest.json').write_text('{"revision":3}', encoding='utf-8')
            git('add', '.', cwd=seed); git('commit', '-m', 'initial', cwd=seed)
            git('remote', 'add', 'origin', remote, cwd=seed)
            git('push', 'origin', 'HEAD:main', cwd=seed)
            manifest = json.dumps({'revision': 3, 'game_version': '1.0.0', 'assets': [], 'files': []}).encode()
            (assets / 'game-manifest-3.json').write_bytes(manifest)
            (assets / 'game-manifest.json').write_bytes(b'legacy unchanged')
            pointer = {'version': 1, 'revision': 3, 'manifest': 'game-manifest-3.json', 'sha256': hashlib.sha256(manifest).hexdigest()}
            (seed / 'channel.json').write_text(json.dumps(pointer), encoding='utf-8')
            git('add', 'channel.json', cwd=seed); git('commit', '-m', 'channel', cwd=seed)
            git('push', 'origin', 'HEAD:game-channel', cwd=seed)
            source = (fixture.ROOT / 'AutoShip-Game.ps1').read_text(encoding='utf-8-sig')
            self.assertEqual(source.count(fixture.HOLD), 1)
            self.assertEqual(source.count(fixture.UPDATER_HOLD), 1)
            source = source.replace(fixture.HOLD, '        # disposable test copy only')
            source = source.replace(fixture.UPDATER_HOLD, '        # disposable test copy only')
            start = source.index("        Add-Type -Path (Join-Path $repo 'OwnedProcess.cs')")
            end = source.index('        if ($updateExit -ne 10)', start)
            source = source[:start] + '        $updateExit = 10\n' + source[end:]
            start = source.index('function Run('); end = source.index('function Get-AvailableRevision', start)
            source = source[:start] + MOCKS + '\n' + source[end:]
            processes = []
            for name in ('one', 'two'):
                clone = root / name
                git('clone', '--branch', 'main', remote, clone)
                git('config', 'user.name', 'Offline Test', cwd=clone)
                git('config', 'user.email', 'offline@example.invalid', cwd=clone)
                (clone / 'AutoShip-Game.ps1').write_text(source, encoding='utf-8')
                shutil.copyfile(fixture.ROOT / 'OwnedProcessGuard.ps1', clone / 'OwnedProcessGuard.ps1')
                game, public, local = (root / (name + '-' + x) for x in ('game', 'public', 'local'))
                for directory in (game, public, local): directory.mkdir()
                (game / 'hookshot_version.txt').write_text('2.0.0', encoding='utf-8')
                env = safe_env.copy()
                env.update(PUBLIC=str(public), LOCALAPPDATA=str(local), USERPROFILE=str(root), COMPUTERNAME='testhost', SILVERLANTERN_PUBLISH_HOST='testhost', GH_TOKEN='offline-only', TEST_REMOTE=str(assets), TEST_BARRIER=str(barrier), TEST_ID=name, TEST_GAME=str(game), TEST_GIT=shutil.which('git'), TEST_MODE=mode)
                processes.append(subprocess.Popen([fixture.SHELL, '-NoProfile', '-NonInteractive', '-File', str(clone / 'AutoShip-Game.ps1'), '-GameFolder', str(game), '-Force'], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True))
            try:
                results = [(p.returncode, output) for p in processes for output in [p.communicate(timeout=45)[0]]]
            finally:
                for p in processes:
                    if p.poll() is None:
                        p.kill(); p.communicate(timeout=5)
            self.assertEqual(sorted(p.returncode for p in processes), [1, 10], results)
            if mode == 'collision':
                self.assertEqual({p.name for p in barrier.iterdir()}, {'one', 'two'})
                winner = next(name for name in ('one', 'two') if (assets / 'r4-one.zip').read_bytes().startswith(name.encode()))
                self.assertEqual((assets / 'r4-one.zip').read_bytes(), (winner + 'r4-one.zip').encode())
                loser_output = next(output for code, output in results if code == 1)
                self.assertIn('already_exists', loser_output.lower())
            else:
                one, two = ((barrier / ('channel-' + name)).read_text() for name in ('one', 'two'))
                tip = git('--git-dir', remote, 'rev-parse', 'game-channel^')
                self.assertIn('--force-with-lease=refs/heads/game-channel:' + tip, one)
                self.assertIn('--force-with-lease=refs/heads/game-channel:' + tip, two)
                self.assertEqual((barrier / 'channel-winner').read_text(), 'one')
                self.assertIn('channel_lease_rejected', results[1][1])
                self.assertIn('stale info', (barrier / 'result-two').read_text().lower())
                for revision, owner in ((4, 'one'), (5, 'two')):
                    candidate = json.loads((assets / f'game-manifest-{revision}.json').read_bytes())
                    for item in candidate['assets']:
                        payload = (assets / item['asset']).read_bytes()
                        self.assertEqual(payload, (owner + item['asset']).encode())
                        self.assertEqual(hashlib.sha256(payload).hexdigest(), item['sha256'])
            actual = json.loads(git('--git-dir', remote, 'show', 'game-channel:channel.json'))
            self.assertEqual(actual['revision'], 4)
            self.assertEqual(actual['previous']['revision'], 3)
            published = (assets / actual['manifest']).read_bytes()
            self.assertEqual(hashlib.sha256(published).hexdigest(), actual['sha256'])
            for item in json.loads(published)['assets']:
                payload = (assets / item['asset']).read_bytes()
                self.assertEqual(hashlib.sha256(payload).hexdigest(), item['sha256'])
                self.assertEqual(len(payload), item['size'])
            self.assertEqual((assets / 'game-manifest.json').read_bytes(), b'legacy unchanged')
