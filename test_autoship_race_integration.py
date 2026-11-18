"""Offline races at the real AutoShip script's external-command boundary.

Only the disposable script copy bypasses the two production holds. No network
commands are executed; the fixture fails closed on unrecognised commands.
"""
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import test_autoship_offline_integration as fixture


@unittest.skipUnless(fixture.SHELL, 'PowerShell is required')
class PublisherRaceIntegration(unittest.TestCase):
    def run_with_mocks(self, mocks):
        runner = fixture.PublisherOfflineIntegration('runTest')
        with patch.object(fixture, 'MOCKS', mocks):
            return runner.run_case(installed='2.0.0', latest='2.0.0', channel='1.0.0')

    def test_competing_immutable_asset_create_aborts_without_advancing(self):
        old = """        Copy-Item -LiteralPath $source -Destination (Join-Path $env:TEST_REMOTE (Split-Path $source -Leaf)) -Force"""
        new = """        $dest = Join-Path $env:TEST_REMOTE (Split-Path $source -Leaf)
        [IO.File]::WriteAllText($dest, 'competitor-owned')
        throw 'remote asset already exists'"""
        self.assertEqual(fixture.MOCKS.count(old), 1)
        result, calls, files, _ = self.run_with_mocks(fixture.MOCKS.replace(old, new))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('competitor-owned', [v.decode('utf-8') for k, v in files.items() if k.startswith('r4-')])
        self.assertFalse(any(' push ' in c for c in calls))
        self.assertNotIn('channel.json', files)
        self.assertEqual(files['game-manifest.json'], b'stale')

    def test_stale_channel_lease_preserves_competing_pointer(self):
        old = "Copy-Item -LiteralPath (Join-Path $Arguments[1] 'channel.json') -Destination (Join-Path $env:TEST_REMOTE 'channel.json')"
        new = """[IO.File]::WriteAllText((Join-Path $env:TEST_REMOTE 'channel.json'), '{"revision":99}')
        throw 'stale channel lease'"""
        self.assertEqual(fixture.MOCKS.count(old), 1)
        actual_run = subprocess.run
        def inspect_evidence(*args, **kwargs):
            result = actual_run(*args, **kwargs)
            state = Path(kwargs['env']['LOCALAPPDATA']) / 'SilverLantern'
            evidence = list(state.glob('silvership-*/channel-push-evidence.json'))
            self.assertEqual(len(evidence), 1)
            record = json.loads(evidence[0].read_text(encoding='utf-8'))
            self.assertEqual(record['candidate'], 'a' * 40)
            self.assertEqual(record['expected'], 'oldtip')
            self.assertTrue((Path(record['worktree']) / 'channel.json').exists())
            return result
        with patch.object(fixture.subprocess, 'run', side_effect=inspect_evidence):
            result, calls, files, _ = self.run_with_mocks(fixture.MOCKS.replace(old, new))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(files['channel.json'])['revision'], 99)
        self.assertTrue(any('channel_publisher.py' in c and 'oldtip' in c for c in calls))
        self.assertEqual(files['game-manifest.json'], b'stale')

    def test_interrupted_upload_leaves_pointer_unchanged_and_partial_assets(self):
        old = """        Copy-Item -LiteralPath $source -Destination (Join-Path $env:TEST_REMOTE (Split-Path $source -Leaf)) -Force"""
        new = """        $dest = Join-Path $env:TEST_REMOTE (Split-Path $source -Leaf)
        if ((Split-Path $source -Leaf) -eq 'r4-two.zip') { throw 'injected upload failure' }
        if (Test-Path -LiteralPath $dest) { throw 'immutable collision' }
        Copy-Item -LiteralPath $source -Destination $dest"""
        self.assertEqual(fixture.MOCKS.count(old), 1)
        actual_run = subprocess.run
        first = []
        def run_twice(*args, **kwargs):
            initial = actual_run(*args, **kwargs)
            first.append(initial)
            remote = Path(kwargs['env']['TEST_REMOTE'])
            self.assertIn('r4-one.zip', {p.name for p in remote.iterdir()})
            self.assertFalse((remote / 'channel.json').exists())
            return actual_run(*args, **kwargs)
        with patch.object(fixture.subprocess, 'run', side_effect=run_twice):
            result, calls, files, _ = self.run_with_mocks(fixture.MOCKS.replace(old, new))
        self.assertNotEqual(first[0].returncode, 0)
        self.assertEqual(result.returncode, 10, result.stdout + result.stderr)
        self.assertIn('r4-one.zip', files)
        self.assertEqual(json.loads(files['channel.json'])['revision'], 5)
        self.assertEqual(files['game-manifest.json'], b'stale')
