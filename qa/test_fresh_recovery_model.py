import unittest
from fresh_recovery_model import FakeService, Recovery, Stop


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.s = FakeService()
        self.r = Recovery(self.s)

    def test_interrupted_upload_uses_fresh_revision(self):
        self.s.interrupt_after = 1
        with self.assertRaises(Stop):
            self.r.publish('2.0', b'package')
        self.s.interrupt_after = None
        self.assertEqual(self.r.publish('2.0', b'package'), 2)
        self.assertEqual(self.s.channel['revision'], 2)
        self.assertFalse(self.s.legacy_writes)

    def test_ambiguous_push_committed_is_reconciled(self):
        self.s.push_mode = 'commit_then_error'
        self.assertEqual(self.r.publish('2.0', b'package'), 1)
        self.assertEqual(self.s.channel['revision'], 1)

    def test_ambiguous_push_not_committed_requires_fresh_revision(self):
        self.s.push_mode = 'error'
        with self.assertRaises(Stop):
            self.r.publish('2.0', b'package')
        self.s.push_mode = 'normal'
        self.assertEqual(self.r.publish('2.0', b'package'), 2)

    def test_tip_race_fails_closed(self):
        self.s.push_mode = 'race'
        with self.assertRaises(Stop):
            self.r.publish('2.0', b'package')
        self.assertNotEqual(self.s.channel.get('version'), '2.0')

    def test_untrusted_channel_or_asset_fails_closed(self):
        self.r.publish('2.0', b'package')
        self.s.assets['package-1'] = b'tampered'
        with self.assertRaises(Stop):
            self.r.publish('3.0', b'new')
        self.assertEqual(self.s.channel['revision'], 1)

    def test_no_rollback_or_reuse(self):
        self.r.publish('2.0', b'package')
        with self.assertRaises(Stop):
            self.r.publish('1.0', b'old')
        with self.assertRaises(Stop):
            self.s.upload('package-1', b'other')
        self.assertEqual(self.s.assets['package-1'], b'package')

    def test_equal_version_still_recovers_with_new_revision(self):
        self.r.publish('2.0', b'package')
        self.assertEqual(self.r.publish('2.0', b'package'), 2)


if __name__ == '__main__':
    unittest.main()
