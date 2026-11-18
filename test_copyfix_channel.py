import json
import tempfile
import unittest
from pathlib import Path

import game_channel
import launcher


class ChannelErrors(unittest.TestCase):
    def test_failure_types_have_distinct_friendly_messages(self):
        errors = [OSError('network'), TimeoutError('lock'), game_channel.StaleChannel('stale'),
                  game_channel.CorruptState('bad'), game_channel.ChecksumMismatch('bad')]
        messages = [launcher.channel_error_message(error) for error in errors]
        self.assertEqual(len(set(messages)), 5)
        for message in messages:
            self.assertIn('Problem:', message)
            self.assertIn('What to do:', message)

    def test_corrupt_saved_state_has_specific_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / '.heylisten-get' / 'channel-state.json'
            state.parent.mkdir()
            state.write_text('{broken')
            with self.assertRaises(game_channel.CorruptState):
                game_channel.load_state(directory)

    def test_checksum_has_specific_exception(self):
        pointer = json.dumps({'version': 1, 'revision': 1, 'manifest': 'game-manifest-a.json', 'sha256': '0' * 64}).encode()
        with self.assertRaises(game_channel.ChecksumMismatch):
            game_channel.resolve_info(lambda url: b'bad', pointer_data=pointer)
