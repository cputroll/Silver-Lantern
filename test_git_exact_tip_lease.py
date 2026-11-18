"""Offline Git integration checks for exact-tip channel publication leases."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class ExactTipLeaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.remote, self.writer, self.rival = (root / name for name in ('remote.git', 'writer', 'rival'))
        self.env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_TERMINAL_PROMPT='0')
        self.git('init', '--bare', str(self.remote))
        self.git('clone', str(self.remote), str(self.writer))
        self.git('config', 'user.name', 'Lease Test', cwd=self.writer)
        self.git('config', 'user.email', 'lease@example.invalid', cwd=self.writer)
        self.commit(self.writer, 'initial')
        self.initial = self.git('rev-parse', 'HEAD', cwd=self.writer).stdout.strip()
        self.commit(self.writer, 'observed')
        self.observed = self.git('rev-parse', 'HEAD', cwd=self.writer).stdout.strip()
        self.git('push', 'origin', 'HEAD:refs/heads/game-channel', cwd=self.writer)
        self.git('clone', '--branch', 'game-channel', str(self.remote), str(self.rival))
        self.git('config', 'user.name', 'Lease Test', cwd=self.rival)
        self.git('config', 'user.email', 'lease@example.invalid', cwd=self.rival)
        self.commit(self.writer, 'candidate')
        self.candidate = self.git('rev-parse', 'HEAD', cwd=self.writer).stdout.strip()

    def git(self, *args, cwd=None, check=True):
        return subprocess.run(['git', *args], cwd=cwd, env=self.env, text=True,
                              capture_output=True, check=check, timeout=15)

    def commit(self, repo, value):
        (repo / 'channel.json').write_text(value, encoding='utf-8')
        self.git('add', 'channel.json', cwd=repo)
        self.git('commit', '-m', value, cwd=repo)

    def remote_tip(self):
        result = self.git('--git-dir', str(self.remote), 'rev-parse', '--verify',
                          'refs/heads/game-channel', check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    def leased_push(self):
        return self.git('push', f'--force-with-lease=refs/heads/game-channel:{self.observed}',
                        'origin', 'HEAD:refs/heads/game-channel', cwd=self.writer, check=False)

    def test_matching_tip_allows_update(self):
        result = self.leased_push()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.remote_tip(), self.candidate)

    def test_stale_tip_rejects_advanced_deleted_and_rewound_refs(self):
        for change in ('advanced', 'deleted', 'rewound'):
            with self.subTest(change=change):
                # Restore the exact observed state before each independent race.
                self.git('--git-dir', str(self.remote), 'update-ref',
                         'refs/heads/game-channel', self.observed)
                if change == 'advanced':
                    self.commit(self.rival, 'rival')
                    self.git('push', 'origin', 'HEAD:refs/heads/game-channel', cwd=self.rival)
                    expected = self.git('rev-parse', 'HEAD', cwd=self.rival).stdout.strip()
                elif change == 'deleted':
                    self.git('push', 'origin', ':refs/heads/game-channel', cwd=self.rival)
                    expected = None
                else:
                    self.git('push', '--force', 'origin',
                             f'{self.initial}:refs/heads/game-channel', cwd=self.rival)
                    expected = self.initial
                self.assertEqual(self.remote_tip(), expected)
                result = self.leased_push()
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertIn('stale info', result.stderr.lower())
                self.assertEqual(self.remote_tip(), expected)


if __name__ == '__main__':
    unittest.main()
