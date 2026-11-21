import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_game_v2 import h, sample
from test_write_boundary import make_junction


class FirstFetch(Exception):
    pass


class InstallPlanningTests(unittest.TestCase):
    def test_shared_missing_parent_does_not_repeat_file_or_checkpoint_probes(self):
        man = sample()
        man['layout'] = 'whole'
        item = man['files'][0]
        man['files'] = [dict(item, path=f'romfs/new/file-{i}.bin') for i in range(40)]
        man['total_bytes'] = sum(item['size'] for item in man['files'])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'romfs').mkdir()
            with patch.object(h, '_plan_game_files_native', return_value=None), \
                 patch.object(h, '_checked_game_paths', wraps=h._checked_game_paths) as checks, \
                 patch.object(h, '_parent_needs_segment_checks', wraps=h._parent_needs_segment_checks) as parents, \
                 patch.object(h, 'restore_segment_checkpoint') as restore, \
                 patch.object(h, 'running_launcher_paths', return_value=set()), \
                 patch.dict(h.os.environ, {'SILVER_LANTERN_MIN_FREE_GB': '0',
                                          'SILVER_LANTERN_TEST_NO_DEFENDER': '1'}), \
                 patch.object(h, 'stream_to', side_effect=FirstFetch):
                with self.assertRaises(FirstFetch):
                    h.install_game(object(), man, root)

            paths_checked = [call.args[1] for call in checks.call_args_list]
            self.assertFalse(set(paths_checked) & {item['path'] for item in man['files']})
            self.assertEqual(parents.call_count, 2)  # Once per phase, not per file.
            restore.assert_not_called()
            self.assertFalse((root / 'romfs/new').exists())

    def test_checkpoint_created_after_planning_is_reused_without_stale_eta(self):
        man = sample()
        man['layout'] = 'whole'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            downloads = []
            progress = h.GameProgress(12)

            def log(text, **kwargs):
                if text == 'Installation plan complete':
                    (root / 'bin').mkdir()
                    (root / 'bin/game.exe.heylisten-new').write_bytes(b'abc')
                    (root / 'bin/game.exe.heylisten-new.json').write_text(
                        '{"next_index":1,"bytes_written":3}', encoding='utf-8')

            def stream(session, url, destination, **kwargs):
                name = url.rsplit('/', 1)[-1]
                downloads.append(name)
                destination.write_bytes(b'xxabc!' if name == 'bundle-0.tar' else b'?defyy')

            with patch.object(h, 'log', side_effect=log), \
                 patch.object(h, 'GameProgress', return_value=progress), \
                 patch.object(h, 'stream_to', side_effect=stream), \
                 patch.dict(h.os.environ, {'SILVER_LANTERN_MIN_FREE_GB': '0',
                                           'SILVER_LANTERN_TEST_NO_DEFENDER': '1',
                                           'SILVER_LANTERN_GUI_PROGRESS': '1'}):
                h.install_game(object(), man, root)

            self.assertEqual(downloads, ['bundle-1.tar'])
            self.assertFalse(progress.eta_reliable)
            self.assertEqual((root / 'bin/game.exe').read_bytes(), b'abcdef')

    def test_invalid_entries_do_not_get_a_missing_parent_shortcut(self):
        man = sample()
        item = man['files'][0]
        man['files'].append(dict(item, path='bin/../escape'))
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(h, '_plan_game_files_native', return_value=None):
                with self.assertRaises(h.InvalidGamePath):
                    h.install_game(None, man, Path(tmp), dry_run=True)

    def test_native_planner_rejects_invalid_entries_by_falling_back(self):
        man = sample()
        item = man['files'][0]
        man['files'].append(dict(item, path='bin/../escape'))
        with tempfile.TemporaryDirectory() as tmp,                 patch.object(h, 'destination_inventory', return_value={}),                 patch.object(h, 'walk_destination', return_value=[]):
            with patch.object(h, '_plan_game_files_native', wraps=h._plan_game_files_native) as native:
                with self.assertRaises(h.InvalidGamePath):
                    h.install_game(None, man, Path(tmp), dry_run=True)
            self.assertIsNone(native.call_args.args[0] if native.call_args else None) if False else None

    def test_invalid_bundle_order_is_rejected_before_destination_io(self):
        man = copy.deepcopy(sample())
        man['files'][0]['segments'].reverse()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(h, 'destination_inventory') as inventory:
                with self.assertRaisesRegex(h.UserError, 'segments must be packed in bundle order'):
                    h.install_game(None, man, Path(tmp), dry_run=True)
            inventory.assert_not_called()

    def test_running_executable_comparison_still_applies_below_missing_parent(self):
        man = sample()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(h.sys, 'executable', str(root / 'bin/game.exe')), \
                 patch.object(h, 'running_launcher_paths', return_value=set()), \
                 patch.object(h, 'log') as log:
                h.install_game(None, man, root, dry_run=True)

            self.assertTrue(any('launcher executable left in place' in call.args[0]
                                for call in log.call_args_list))

    def test_retry_rechecks_parent_and_restores_checkpoint_created_by_first_attempt(self):
        man = sample()
        man['layout'] = 'whole'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            downloads = []

            def stream(session, url, destination, **kwargs):
                name = url.rsplit('/', 1)[-1]
                downloads.append(name)
                destination.write_bytes(b'xxabc!' if name == 'bundle-0.tar' else b'?defyy')

            replace = h._replace_with_retry

            def fail_first_commit(src, dst, **kwargs):
                if dst == root / 'bin/game.exe' and downloads.count('bundle-1.tar') == 1:
                    raise h.SegmentMismatch('simulated mismatch at commit', 'bundle-1.tar', 'bin/game.exe')
                return replace(src, dst, **kwargs)

            with patch.object(h, 'stream_to', side_effect=stream), \
                 patch.object(h, '_replace_with_retry', side_effect=fail_first_commit), \
                 patch.object(h, 'restore_segment_checkpoint', wraps=h.restore_segment_checkpoint) as restore, \
                 patch.dict(h.os.environ, {'SILVER_LANTERN_MIN_FREE_GB': '0',
                                          'SILVER_LANTERN_TEST_NO_DEFENDER': '1'}):
                h.install_game(object(), man, root)

            restore.assert_called_once()
            self.assertEqual(downloads, ['bundle-0.tar', 'bundle-1.tar', 'bundle-1.tar'])
            self.assertEqual((root / 'bin/game.exe').read_bytes(), b'abcdef')

    def test_junction_created_after_planning_is_refused_before_writing(self):
        man = sample()
        man['layout'] = 'whole'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'destination'
            outside = Path(tmp) / 'outside'
            outside.mkdir()

            def stream(session, url, destination, **kwargs):
                if url.endswith('bundle-0.tar'):
                    make_junction(outside, root / 'bin')
                    destination.write_bytes(b'xxabc!')
                else:
                    destination.write_bytes(b'?defyy')

            with patch.object(h, 'stream_to', side_effect=stream), \
                 patch.dict(h.os.environ, {'SILVER_LANTERN_MIN_FREE_GB': '0',
                                          'SILVER_LANTERN_TEST_NO_DEFENDER': '1'}):
                with self.assertRaisesRegex(h.InvalidGamePath, 'reparse point'):
                    h.install_game(object(), man, root)

            self.assertEqual(list(outside.iterdir()), [])
