import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import channel_publisher as publisher


class RealRemoteRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fetch = self.root / 'fetch.git'
        self.push = self.root / 'push.git'
        self.repo = self.root / 'work'
        for remote in (self.fetch, self.push):
            self.git('init', '--bare', str(remote))
        self.git('init', str(self.repo))
        self.git('config', 'user.name', 'Offline', cwd=self.repo)
        self.git('config', 'user.email', 'offline@example.invalid', cwd=self.repo)
        (self.repo / 'channel.json').write_text('{}', encoding='utf-8')
        self.git('add', 'channel.json', cwd=self.repo)
        self.git('commit', '-m', 'candidate', cwd=self.repo)
        self.candidate = self.git('rev-parse', 'HEAD', cwd=self.repo).stdout.strip()
        self.git('remote', 'add', 'origin', str(self.fetch), cwd=self.repo)
        self.git('remote', 'set-url', '--push', 'origin', str(self.push), cwd=self.repo)

    def git(self, *args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, check=True, timeout=15,
                              env=dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull))

    def ambiguous(self, accept):
        real_run = subprocess.run
        def run(args, **kwargs):
            if args[:2] == ['git', 'push']:
                if accept:
                    real_run(args, **kwargs)
                raise subprocess.TimeoutExpired(args, 60)
            return real_run(args, **kwargs)
        return patch('channel_publisher.subprocess.run', side_effect=run)

    def test_accepted_push_is_reconciled_against_push_not_fetch_url(self):
        with self.ambiguous(True):
            self.assertEqual(publisher.push_with_reconciliation(self.repo, 'origin', '', self.candidate), 'published')
        self.assertEqual(self.git('--git-dir', str(self.push), 'rev-parse', 'refs/heads/game-channel').stdout.strip(), self.candidate)

    def test_candidate_on_fetch_remote_does_not_prove_push(self):
        self.git('push', str(self.fetch), f'{self.candidate}:refs/heads/game-channel', cwd=self.repo)
        with self.ambiguous(False), self.assertRaises(RuntimeError):
            publisher.push_with_reconciliation(self.repo, 'origin', '', self.candidate)

    def test_competing_push_ref_is_unresolved(self):
        self.git('push', str(self.push), f'{self.candidate}:refs/heads/game-channel', cwd=self.repo)
        (self.repo / 'channel.json').write_text('{"next":1}', encoding='utf-8')
        self.git('commit', '-am', 'new candidate', cwd=self.repo)
        candidate = self.git('rev-parse', 'HEAD', cwd=self.repo).stdout.strip()
        with self.ambiguous(False), self.assertRaises(RuntimeError):
            publisher.push_with_reconciliation(self.repo, 'origin', self.candidate, candidate)

    def test_multiple_push_destinations_are_rejected_before_push(self):
        self.git('remote', 'set-url', '--add', '--push', 'origin', str(self.fetch), cwd=self.repo)
        with self.assertRaises(RuntimeError):
            publisher.push_with_reconciliation(self.repo, 'origin', '', self.candidate)


if __name__ == '__main__':
    unittest.main()
