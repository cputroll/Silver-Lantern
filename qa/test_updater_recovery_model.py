"""Offline specification tests; these do not establish Windows process containment."""
import unittest
from updater_recovery_model import Decision, decide


class RecoveryTests(unittest.TestCase):
    def test_matching_positive_attestation_allows_reconcile(self):
        self.assertEqual(decide('run-a', 'run-a', True, True, True), Decision.RECONCILE)

    def test_uncertainty_never_clears_hold(self):
        for marker, receipt, authenticated, empty, serialized in [
            ('run-a', None, True, True, True),
            ('run-a', 'run-b', True, True, True),
            ('run-a', 'run-a', False, True, True),
            ('run-a', 'run-a', True, False, True),
            ('run-a', 'run-a', True, True, False),
            (None, 'run-a', True, True, True),
        ]:
            with self.subTest(marker=marker, receipt=receipt, authenticated=authenticated, empty=empty, serialized=serialized):
                self.assertEqual(decide(marker, receipt, authenticated, empty, serialized), Decision.HOLD)


if __name__ == '__main__':
    unittest.main()
