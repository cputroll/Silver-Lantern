import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import launcher


class DefenderTests(unittest.TestCase):
    def setUp(self):
        for name in ('defender_active', 'defender_exclusion_present'):
            patcher = patch.object(launcher, name, return_value=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_missing_readback_retries_and_logs(self):
        with tempfile.TemporaryDirectory() as d, patch.object(launcher, 'STATE_DIR', Path(d)), patch.object(launcher, 'CONFIG', Path(d) / 'launcher.json'), patch.object(launcher, 'defender_active', return_value=True), patch.object(launcher, 'defender_elevated', return_value=True) as add, patch.object(launcher, 'defender_exclusion_present', side_effect=[False, True]), patch.object(launcher, 'log_message') as log:
            self.assertTrue(launcher.optimize_defender(Path(d) / 'game'))
            self.assertEqual(add.call_count, 2)
            self.assertTrue(any('verified=False' in str(c) for c in log.call_args_list))
            self.assertTrue(any('verified=True' in str(c) for c in log.call_args_list))

    def test_third_party_antivirus_skips_exclusion(self):
        with tempfile.TemporaryDirectory() as d, patch.object(launcher, 'defender_active', return_value=False), patch.object(launcher, 'defender_elevated') as add, patch.object(launcher, 'log_message') as log:
            self.assertFalse(launcher.optimize_defender(Path(d) / 'game'))
            add.assert_not_called()
            self.assertFalse(any('optimizing' in str(c).lower() for c in log.call_args_list))

    def test_failed_permission_is_recorded_and_not_reprompted(self):
        with tempfile.TemporaryDirectory() as d, patch.object(launcher, 'STATE_DIR', Path(d)), patch.object(launcher, 'CONFIG', Path(d) / 'launcher.json'), patch.object(launcher, 'defender_elevated', side_effect=[False, True]) as elevated:
            self.assertFalse(launcher.optimize_defender(Path(d) / 'game'))
            self.assertIn(launcher.defender_config()['defender_state'], ('unknown', 'declined'))
            # A denial is remembered: later runs skip instead of nagging every time.
            self.assertFalse(launcher.optimize_defender(Path(d) / 'game'))
            self.assertEqual(elevated.call_count, 1)

    def test_cleared_config_asks_again(self):
        with tempfile.TemporaryDirectory() as d, patch.object(launcher, 'STATE_DIR', Path(d)), patch.object(launcher, 'CONFIG', Path(d) / 'launcher.json'), patch.object(launcher, 'defender_elevated', side_effect=[False, True]) as elevated:
            self.assertFalse(launcher.optimize_defender(Path(d) / 'game'))
            Path(d, 'launcher.json').unlink()
            self.assertTrue(launcher.optimize_defender(Path(d) / 'game'))
            self.assertEqual(elevated.call_count, 2)

    def test_uac_wait_is_bounded_and_failure_is_nonfatal(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d, patch.object(launcher, 'STATE_DIR', Path(d)), patch.object(launcher, 'CONFIG', Path(d) / 'launcher.json'), patch.object(launcher, 'powershell', side_effect=subprocess.TimeoutExpired('powershell', launcher.DEFENDER_TIMEOUT)) as ps, patch.object(launcher, 'log_message') as lm:
            self.assertFalse(launcher.optimize_defender(Path(d) / 'game'))
            self.assertEqual(ps.call_args.kwargs.get('timeout'), launcher.DEFENDER_TIMEOUT)
            self.assertTrue(any('Continuing without optimizing' in str(c.args[0]) for c in lm.call_args_list))

    def test_existing_exclusion_is_not_forgotten_on_folder_change(self):
        with tempfile.TemporaryDirectory() as d, patch.object(launcher, 'STATE_DIR', Path(d)), patch.object(launcher, 'CONFIG', Path(d) / 'launcher.json'), patch.object(launcher, 'defender_elevated', return_value=True) as elevated:
            first, second = Path(d) / 'first', Path(d) / 'second'
            self.assertTrue(launcher.optimize_defender(first))
            self.assertFalse(launcher.optimize_defender(second))
            self.assertEqual(launcher.defender_config()['defender_path'], str(first.resolve()))
            self.assertEqual(elevated.call_count, 1)

    def test_uninstall_refusal_preserves_game_and_configuration(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            folder = root / 'game'
            folder.mkdir()
            (folder / 'file').write_text('data')
            with patch.object(launcher, 'load_game_folder', return_value=folder), patch.object(launcher, 'STATE_DIR', root / 'state'), patch.object(launcher, 'remove_defender_exclusion', return_value=False), patch('builtins.input', return_value='y'):
                with self.assertRaisesRegex(RuntimeError, 'Could not remove'):
                    launcher.uninstall()
            self.assertTrue((folder / 'file').exists())

    def test_uninstall_removes_recorded_exclusion(self):
        with tempfile.TemporaryDirectory() as d, patch.object(launcher, 'STATE_DIR', Path(d)), patch.object(launcher, 'CONFIG', Path(d) / 'launcher.json'), patch.object(launcher, 'defender_elevated', return_value=True) as elevated:
            folder = Path(d) / 'game'
            launcher.optimize_defender(folder)
            launcher.remove_defender_exclusion()
            self.assertEqual(elevated.call_args.args, (folder.resolve(), False))
