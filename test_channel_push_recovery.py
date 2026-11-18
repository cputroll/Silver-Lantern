import subprocess
import unittest
from unittest.mock import patch

from channel_publisher import push_with_reconciliation


class ChannelPushRecoveryTests(unittest.TestCase):
    def test_uncertain_push_is_success_only_if_exact_commit_is_observed(self):
        with patch('channel_publisher.subprocess.run') as run:
            run.side_effect = [subprocess.CompletedProcess([], 0, stdout='local-remote\n'), subprocess.TimeoutExpired('git', 60),
                               subprocess.CompletedProcess([], 0, stdout='a' * 40 + '\trefs/heads/game-channel\n')]
            self.assertEqual(push_with_reconciliation('.', 'origin', 'b' * 40, 'a' * 40), 'published')

    def test_competing_or_unreadable_remote_is_not_reported_as_success(self):
        for response in (subprocess.CompletedProcess([], 0, stdout='def\trefs/heads/game-channel\n'),
                         subprocess.CalledProcessError(1, 'git')):
            with self.subTest(response=response), patch('channel_publisher.subprocess.run') as run:
                run.side_effect = [subprocess.CompletedProcess([], 0, stdout='local-remote\n'), subprocess.TimeoutExpired('git', 60), response]
                with self.assertRaises(RuntimeError):
                    push_with_reconciliation('.', 'origin', 'b' * 40, 'a' * 40)

    def test_normal_success_does_not_need_remote_probe(self):
        with patch('channel_publisher.subprocess.run') as run:
            run.side_effect = [subprocess.CompletedProcess([], 0, stdout='local-remote\n'), subprocess.CompletedProcess([], 0, stdout='')]
            self.assertEqual(push_with_reconciliation('.', 'origin', 'b' * 40, 'a' * 40), 'published')
            self.assertEqual(run.call_count, 2)


if __name__ == '__main__':
    unittest.main()
