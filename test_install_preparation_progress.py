import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_game_sync import h


class PreparationProgressTests(unittest.TestCase):
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
