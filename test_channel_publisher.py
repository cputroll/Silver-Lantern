import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import channel_publisher


class ChannelPublisherTests(unittest.TestCase):
    def test_pointer_rejects_non_monotonic_revision(self):
        old = {'version': 1, 'revision': 3, 'manifest': 'game-manifest-old.json', 'sha256': 'a' * 64}
        with self.assertRaises(ValueError):
            channel_publisher.make_pointer(old, 3, 'game-manifest-new.json', 'b' * 64)

    def test_pointer_allows_forward_rollback_to_old_asset(self):
        old = {'version': 1, 'revision': 3, 'manifest': 'game-manifest-new.json', 'sha256': 'a' * 64}
        result = channel_publisher.make_pointer(old, 4, 'game-manifest-old.json', 'b' * 64)
        self.assertEqual((result['revision'], result['manifest']), (4, 'game-manifest-old.json'))

    def test_competing_git_push_rejects_stale_publisher(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); remote = root / 'remote.git'; one = root / 'one'; two = root / 'two'
            def git(*args, cwd=None, check=True):
                return subprocess.run(['git', *args], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check, timeout=15)
            git('init', '--bare', str(remote))
            git('clone', str(remote), str(one))
            git('config', 'user.email', 'test@example.invalid', cwd=one)
            git('config', 'user.name', 'Test', cwd=one)
            (one / 'channel.json').write_text('{}')
            git('add', 'channel.json', cwd=one); git('commit', '-m', 'initial', cwd=one)
            git('push', 'origin', 'HEAD:refs/heads/game-channel', cwd=one)
            git('clone', '--branch', 'game-channel', str(remote), str(two))
            git('config', 'user.email', 'test@example.invalid', cwd=two)
            git('config', 'user.name', 'Test', cwd=two)
            for repo, value in ((one, 'one'), (two, 'two')):
                (repo / 'channel.json').write_text(value)
                git('add', 'channel.json', cwd=repo); git('commit', '-m', value, cwd=repo)
            channel_publisher.advance_ref(one, 'origin')
            with self.assertRaises(subprocess.CalledProcessError):
                channel_publisher.advance_ref(two, 'origin')

    def test_validate_assets_rejects_mismatch_and_duplicate_names(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'unique.bin').write_bytes(b'abc')
            asset = {'asset': 'unique.bin', 'size': 3, 'sha256': hashlib.sha256(b'abc').hexdigest()}
            channel_publisher.validate_assets(root, [asset])
            with self.assertRaises(ValueError):
                channel_publisher.validate_assets(root, [asset, asset])
            (root / 'unique.bin').write_bytes(b'bad')
            with self.assertRaises(ValueError):
                channel_publisher.validate_assets(root, [asset])

    def test_publish_refuses_to_advance_on_verification_failure(self):
        from unittest.mock import Mock
        upload = Mock()
        verify = Mock(side_effect=ValueError('bad hash'))
        advance = Mock()
        with self.assertRaises(ValueError):
            channel_publisher.publish_verified(['a', 'b'], upload, verify, advance)
        advance.assert_not_called()

    def test_publishes_and_verifies_all_before_advancing(self):
        from unittest.mock import Mock, call
        events = []
        channel_publisher.publish_verified(['a', 'b'], lambda x: events.append(('upload', x)), lambda x: events.append(('verify', x)), lambda: events.append(('advance',)))
        self.assertEqual(events, [('upload', 'a'), ('upload', 'b'), ('verify', 'a'), ('verify', 'b'), ('advance',)])

    def test_publish_rejects_existing_remote_asset_before_upload(self):
        from unittest.mock import Mock
        upload, verify, advance = Mock(), Mock(), Mock()
        with self.assertRaises(FileExistsError):
            channel_publisher.publish_verified(['a'], upload, verify, advance, existing={'a'})
        upload.assert_not_called()
        advance.assert_not_called()

    def test_pointer_keeps_previous_asset_for_rollback(self):
        old = {'version': 1, 'revision': 3, 'manifest': 'game-manifest-old.json', 'sha256': 'a' * 64}
        pointer = channel_publisher.make_pointer(old, 4, 'game-manifest-new.json', 'b' * 64)
        self.assertEqual(pointer['previous'], old)

    def test_pointer_rejects_bad_asset(self):
        with self.assertRaises(ValueError):
            channel_publisher.make_pointer(None, 1, '../bad', 'a' * 64)
