import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import launcher


class DefenderTests(unittest.TestCase):
    def test_failed_permission_remains_unknown_and_next_plan_retries(self):
        with tempfile.TemporaryDirectory() as d, patch.object(launcher, 'STATE_DIR', Path(d)), patch.object(launcher, 'CONFIG', Path(d) / 'launcher.json'), patch.object(launcher, 'defender_elevated', side_effect=[False, True]) as elevated:
            self.assertFalse(launcher.optimize_defender(Path(d) / 'game'))
            self.assertTrue(launcher.optimize_defender(Path(d) / 'game'))
            self.assertEqual(elevated.call_count, 2)
            self.assertTrue(launcher.optimize_defender(Path(d) / 'game'))
            self.assertEqual(elevated.call_count, 2)

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
            self.assertEqual(elevated.call_args.args, (folder, False))
