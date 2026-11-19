import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_game_sync import h
from test_write_boundary import make_junction


class PreparationProgressTests(unittest.TestCase):
    def test_preparation_skips_missing_parents_without_listing_existing_ones(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'existing').mkdir()
            pending = [{'path': f'{parent}/{number}', 'segments': [{'asset': 'a'}]}
                       for parent in ('existing', 'absent/child') for number in range(20)]
            with patch.object(h, 'restore_segment_checkpoint', wraps=h.restore_segment_checkpoint) as restore, \
                 patch.object(h, 'game_path', wraps=h.game_path) as validate, \
                 patch.object(h.os, 'scandir', wraps=h.os.scandir) as scan:
                with self.assertRaisesRegex(RuntimeError, 'stop before assembly'):
                    h.assemble_segmented(pending, {'a': {}}, root, root,
                                         lambda part: (_ for _ in ()).throw(RuntimeError('stop before assembly')),
                                         False, soft_retry=True)
            scan.assert_not_called()
            self.assertEqual(restore.call_count, 20)
            self.assertEqual(validate.call_count, 20)

    def test_preparation_never_scans_junction_ancestors(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            root, outside = base / 'root', base / 'outside'
            root.mkdir()
            (outside / 'child').mkdir(parents=True)
            link = root / 'link'
            if os.name == 'nt':
                make_junction(outside, link)
            else:
                link.symlink_to(outside, target_is_directory=True)
            pending = [{'path': 'link/child/file', 'segments': [{'asset': 'a'}]}]
            with patch.object(h.os, 'scandir', wraps=h.os.scandir) as scan:
                with self.assertRaisesRegex(h.InvalidGamePath, 'reparse point'):
                    h.assemble_segmented(pending, {'a': {}}, root, root,
                                         lambda part: self.fail('fetched before preparation rejected junction'),
                                         False, soft_retry=True)
                scan.assert_not_called()

    def test_preparation_rejects_a_final_target_reparse_before_fetch(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            root, outside = base / 'root', base / 'outside'
            root.mkdir()
            outside.mkdir()
            if os.name == 'nt':
                make_junction(outside, root / 'file')
            else:
                (root / 'file').symlink_to(outside, target_is_directory=True)
            pending = [{'path': 'file', 'segments': [{'asset': 'a'}]}]
            with self.assertRaisesRegex(h.InvalidGamePath, 'reparse point'):
                h.assemble_segmented(pending, {'a': {}}, root, root,
                                     lambda part: self.fail('fetched before preparation rejected junction'),
                                     False, soft_retry=True)

    def test_preparation_restores_checkpoint_and_discards_orphan_in_same_parent(self):
        def digest(data):
            return hashlib.sha256(data).hexdigest()

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cache = root / 'cache'
            cache.mkdir()
            temp = root / 'resume.heylisten-new'
            temp.write_bytes(b'a')
            (root / 'resume.heylisten-new.json').write_text(json.dumps({'next_index': 1, 'bytes_written': 1}))
            orphan = root / 'orphan.heylisten-new'
            orphan.write_bytes(b'old')
            first = {'asset': 'a', 'offset': 0, 'length': 1, 'sha256': digest(b'a')}
            second = {'asset': 'b', 'offset': 0, 'length': 1, 'sha256': digest(b'b')}
            pending = [{'path': 'resume', 'size': 2, 'sha256': digest(b'ab'), 'segments': [first, second]},
                       {'path': 'orphan', 'size': 1, 'sha256': digest(b'b'), 'segments': [second]}]

            def fetch(asset):
                self.assertEqual(asset, 'b')  # The verified prefix is not fetched again.
                (cache / asset).write_bytes(b'b')

            h.assemble_segmented(pending, {'a': 'a', 'b': 'b'}, cache, root, fetch, False, soft_retry=True)
            self.assertEqual((root / 'resume').read_bytes(), b'ab')
            self.assertEqual((root / 'orphan').read_bytes(), b'b')
            self.assertFalse(orphan.exists())

    def test_preparation_reports_progress_during_scan(self):
        item = {'path': 'file', 'size': 1, 'sha256': '0' * 64,
                'segments': [{'asset': 'a', 'offset': 0, 'length': 1, 'sha256': '0' * 64}]}
        messages = []
        with tempfile.TemporaryDirectory() as d, \
             patch.object(h.time, 'monotonic', side_effect=[0, 6]), \
             patch.object(h, 'log', side_effect=lambda text, **kwargs: messages.append(text)):
            with self.assertRaisesRegex(RuntimeError, 'stop'):
                h.assemble_segmented([item], {'a': {'asset': 'a'}}, Path(d), Path(d),
                                     lambda part: (_ for _ in ()).throw(RuntimeError('stop')), False)
        self.assertIn('Preparing download: 0/1 destination files checked', messages)

    def test_network_timeout_is_retried_and_raised(self):
        from unittest.mock import Mock
        session = Mock()
        session.get.side_effect = TimeoutError('network stalled')
        with tempfile.TemporaryDirectory() as d, patch.object(h.time, 'sleep'):
            with self.assertRaises(TimeoutError):
                h.stream_to(session, 'https://example.invalid/a', Path(d) / 'a')
        self.assertEqual(session.get.call_count, 2)
        self.assertTrue(all(call.kwargs['timeout'] == (10, 30) for call in session.get.call_args_list))

    def test_preparation_announced_before_fetch_and_install_completes(self):
        data = b'abc'
        digest = hashlib.sha256(data).hexdigest()
        manifest = {'version': 2, 'build': 'game-v1', 'layout': 'whole', 'total_bytes': 3,
                    'assets': [{'asset': 'a', 'size': 3, 'sha256': digest}],
                    'files': [{'path': 'nested/file', 'size': 3, 'sha256': digest,
                               'segments': [{'asset': 'a', 'offset': 0, 'length': 3, 'sha256': digest}]}]}
        events = []
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            def stream(session, url, dest, **kwargs):
                events.append('fetch')
                dest.write_bytes(data)
                kwargs['progress'](len(data))
            with patch.object(h, 'stream_to', side_effect=stream), patch.object(h, 'check_disk_space'), \
                 patch.object(h, 'log', side_effect=lambda text, **kwargs: events.append(str(text))), \
                 patch.dict(h.os.environ, {'SILVER_LANTERN_TEST_NO_DEFENDER': '1'}):
                h.install_game(object(), manifest, root)
            self.assertEqual((root / 'nested/file').read_bytes(), data)
        self.assertTrue(any('Preparing' in event for event in events[:events.index('fetch')]), events)
        self.assertTrue(any('Preparation complete' in event for event in events[:events.index('fetch')]), events)
