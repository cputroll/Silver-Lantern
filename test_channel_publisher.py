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

    def test_pointer_rejects_bad_asset(self):
        with self.assertRaises(ValueError):
            channel_publisher.make_pointer(None, 1, '../bad', 'a' * 64)
