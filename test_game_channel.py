import hashlib
import json
import unittest
from unittest.mock import Mock

import game_channel


class GameChannelTests(unittest.TestCase):
    def pointer(self, content):
        return json.dumps({'version': 1, 'manifest': 'game-manifest-v041.json',
                           'sha256': hashlib.sha256(content).hexdigest()}).encode()

    def test_resolves_verified_immutable_manifest(self):
        content = b'{"version":2}'
        fetch = Mock(side_effect=[self.pointer(content), content])
        self.assertEqual(game_channel.resolve(fetch), content)
        self.assertTrue(fetch.call_args_list[1].args[0].endswith('/game-manifest-v041.json'))

    def test_rejects_stale_pointer_after_newer_observed(self):
        content = b'{"version":2}'
        pointer = json.loads(self.pointer(content))
        pointer['revision'] = 4
        with self.assertRaises(game_channel.StaleChannel):
            game_channel.resolve(Mock(side_effect=[json.dumps(pointer).encode(), content]), minimum_revision=5)

    def test_rejects_missing_revision_when_floor_exists(self):
        content = b'{"version":2}'
        with self.assertRaises(ValueError):
            game_channel.resolve(Mock(side_effect=[self.pointer(content), content]), minimum_revision=1)

    def test_rejects_same_revision_with_different_digest(self):
        content = b'{"version":2}'
        pointer = json.loads(self.pointer(content))
        pointer['revision'] = 5
        with self.assertRaises(ValueError):
            game_channel.resolve(Mock(side_effect=[json.dumps(pointer).encode(), content]), minimum_revision=5, expected_digest='a' * 64)

    def test_channel_outage_with_prior_revision_never_uses_legacy(self):
        fetch = Mock(side_effect=OSError('offline'))
        with self.assertRaises(OSError):
            game_channel.resolve_with_fallback(fetch, minimum_revision=1)
        self.assertEqual(fetch.call_count, 1)

    def test_channel_missing_first_time_uses_legacy(self):
        fetch = Mock(side_effect=[FileNotFoundError('404'), b'legacy'])
        self.assertEqual(game_channel.resolve_with_fallback(fetch), b'legacy')
        self.assertEqual(fetch.call_args.args[0], game_channel.LEGACY_URL)

    def test_channel_corruption_never_uses_legacy(self):
        fetch = Mock(return_value=b'invalid')
        with self.assertRaises(ValueError):
            game_channel.resolve_with_fallback(fetch)
        self.assertEqual(fetch.call_count, 1)

    def test_manifest_asset_missing_does_not_trigger_legacy_fallback(self):
        content = b'valid'
        fetch = Mock(side_effect=[self.pointer(content), FileNotFoundError('asset missing')])
        with self.assertRaises(FileNotFoundError):
            game_channel.resolve_with_fallback(fetch)
        self.assertEqual(fetch.call_count, 2)

    def test_state_round_trip_and_reject_corruption(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self.assertEqual(game_channel.load_state(folder), (0, None))
            game_channel.save_state(folder, 7, 'a' * 64)
            self.assertEqual(game_channel.load_state(folder), (7, 'a' * 64))
            (folder / '.heylisten-get' / 'channel-state.json').write_text('{')
            with self.assertRaises(ValueError):
                game_channel.load_state(folder)

    def test_state_rejects_downgrade_and_same_revision_equivocation(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            game_channel.save_state(folder, 7, 'a' * 64)
            with self.assertRaises(ValueError):
                game_channel.save_state(folder, 6, 'b' * 64)
            with self.assertRaises(ValueError):
                game_channel.save_state(folder, 7, 'b' * 64)
            self.assertEqual(game_channel.load_state(folder), (7, 'a' * 64))

    def test_competing_processes_cannot_regress_state(self):
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            code = "import game_channel,sys,time,pathlib; old=game_channel.load_state; marker=pathlib.Path(sys.argv[1]) / (sys.argv[2]+'.ready'); exec('def delayed(p):\\n r=old(p); marker.touch(); time.sleep(1 if sys.argv[2]==\"10\" else .1); return r'); game_channel.load_state=delayed; game_channel.save_state(sys.argv[1], int(sys.argv[2]), sys.argv[3]*64)"
            low = subprocess.Popen([sys.executable, '-c', code, str(folder), '10', 'b'])
            import time
            deadline = time.monotonic() + 5
            while not (folder / '10.ready').exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue((folder / '10.ready').exists())
            high = subprocess.Popen([sys.executable, '-c', code, str(folder), '20', 'a'])
            high.wait(timeout=15)
            low.wait(timeout=15)
            self.assertEqual(high.returncode, 0)
            self.assertEqual(game_channel.load_state(folder), (20, 'a' * 64))

    def test_rejects_hash_mismatch(self):
        fetch = Mock(side_effect=[self.pointer(b'expected'), b'altered'])
        with self.assertRaises(ValueError):
            game_channel.resolve(fetch)

    def test_rejects_untrusted_asset_name(self):
        pointer = json.dumps({'version': 1, 'manifest': '../evil', 'sha256': 'a' * 64}).encode()
        with self.assertRaises(ValueError):
            game_channel.resolve(Mock(return_value=pointer))
