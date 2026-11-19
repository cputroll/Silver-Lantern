import hashlib
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_game_sync import h


DATA = b'abc'
DIGEST = hashlib.sha256(DATA).hexdigest()
ASSET = {'asset': 'a', 'size': len(DATA), 'sha256': DIGEST}
SEGMENT = {'asset': 'a', 'offset': 0, 'length': len(DATA), 'sha256': DIGEST}


class InstallWorkProgressTests(unittest.TestCase):
    def run_install(self, version, files, *, gui=True, retry=False, bad_hash=False,
                    cache=False, fail_commit=False, stages=None):
        entries = [{'path': name, 'size': len(DATA), 'sha256': DIGEST,
                    **({'parts': [ASSET]} if version == 1 else {'segments': [SEGMENT]})}
                   for name in files]
        manifest = {'version': version, 'build': 'game-v1', 'files': entries,
                    'total_bytes': len(DATA) * len(files), 'assets': [ASSET]}
        if retry:
            manifest['layout'] = 'whole'
        downloads = []
        real_read = h.os.read
        real_replace = h._replace_with_retry

        def stream(_, url, dest, **kwargs):
            downloads.append(dest.name)
            dest.write_bytes(b'bad' if bad_hash else DATA)
            kwargs['progress'](len(DATA))

        def read(fd, n):
            data = real_read(fd, n)
            return b'bad' if retry and len(downloads) == 1 and data == DATA else data

        def replace(src, dest, *args, **kwargs):
            if fail_commit and dest.name in files:
                raise OSError('test destination replacement failed')
            return real_replace(src, dest, *args, **kwargs)

        env = {'SILVER_LANTERN_GUI_PROGRESS': '1' if gui else '0',
               'SILVER_LANTERN_TEST_NO_DEFENDER': '1'}
        with tempfile.TemporaryDirectory() as tmp, patch.dict(h.os.environ, env), \
                patch.object(h, 'stream_to', side_effect=stream), patch.object(h, 'check_disk_space'), \
                patch.object(h, '_write') as write, patch.object(h.os, 'read', side_effect=read), \
                patch.object(h, '_replace_with_retry', side_effect=replace):
            root = Path(tmp)
            if cache:
                bundle = root / '.heylisten-parts' / 'a'
                bundle.parent.mkdir()
                bundle.write_bytes(DATA)
            if bad_hash or fail_commit:
                with self.assertRaises(ValueError if bad_hash else OSError):
                    h.install_game(object(), manifest, root)
                self.assertFalse(any((root / name).exists() for name in files))
            else:
                h.install_game(object(), manifest, root)
                self.assertEqual([(root / name).read_bytes() for name in files], [DATA] * len(files))
            events = [tuple(map(int, match.groups())) for call in write.call_args_list
                      if (match := re.fullmatch(r'HLINSTALL done=(\d+) total=(\d+)\n', call.args[0]))]
            if stages is not None:
                for call in write.call_args_list:
                    line = call.args[0]
                    if line.startswith('HLVERIFY '):
                        stages.append('verify')
                    elif line.startswith('HLINSTALL_STAGE name='):
                        stages.append(line.strip().split('=', 1)[1])
        return downloads, events

    def assert_work(self, events, total):
        self.assertTrue(events, 'worker emitted no HLINSTALL events')
        self.assertEqual(events[0], (0, total))
        self.assertEqual(events[-1], (total, total))
        self.assertEqual({size for _, size in events}, {total})
        done = [n for n, _ in events]
        self.assertEqual(done, sorted(set(done)))

    def test_v1_shared_cached_bundle_counts_once_and_each_destination_counts(self):
        downloads, events = self.run_install(1, ['first', 'second'], cache=True)
        self.assertEqual(downloads, [])
        self.assert_work(events, 9)

    def test_v2_refetch_does_not_credit_bundle_twice(self):
        downloads, events = self.run_install(2, ['file'], retry=True)
        self.assertEqual(downloads, ['a', 'a'])
        self.assert_work(events, 6)

    def test_hash_failure_never_credits_bundle(self):
        _, events = self.run_install(2, ['file'], bad_hash=True)
        self.assertEqual(events, [(0, 6)])

    def test_failed_replacement_does_not_credit_destination(self):
        _, events = self.run_install(2, ['file'], fail_commit=True)
        self.assertTrue(events)
        self.assertTrue(all(done <= 3 and total == 6 for done, total in events))

    def test_no_work_emits_zero_plan(self):
        _, events = self.run_install(2, [])
        self.assertEqual(events, [(0, 0)])

    def test_restored_prefix_does_not_credit_an_unverified_bundle(self):
        other = {'asset': 'b', 'size': len(DATA), 'sha256': DIGEST}
        manifest = {'version': 2, 'build': 'game-v1', 'layout': 'whole',
                    'total_bytes': 6, 'assets': [ASSET, other],
                    'files': [{'path': 'file', 'size': 6,
                               'sha256': hashlib.sha256(DATA * 2).hexdigest(),
                               'segments': [SEGMENT, dict(SEGMENT, asset='b')]}]}

        def stream(_, url, dest, **kwargs):
            self.assertEqual(dest.name, 'b')
            dest.write_bytes(DATA)
            kwargs['progress'](len(DATA))

        with tempfile.TemporaryDirectory() as tmp, patch.object(h, 'stream_to', side_effect=stream), \
                patch.object(h, 'check_disk_space'), patch.object(h, '_write') as write, \
                patch.dict(h.os.environ, {'SILVER_LANTERN_GUI_PROGRESS': '1',
                                         'SILVER_LANTERN_TEST_NO_DEFENDER': '1'}):
            root = Path(tmp)
            (root / 'file.heylisten-new').write_bytes(DATA)
            (root / 'file.heylisten-new.json').write_text('{"next_index":1,"bytes_written":3}')
            h.install_game(object(), manifest, root)
            self.assertEqual((root / 'file').read_bytes(), DATA * 2)
            records = [call.args[0] for call in write.call_args_list if call.args[0].startswith('HLINSTALL ')]
            self.assertEqual(records[0], 'HLINSTALL done=0 total=12\n')
            self.assertEqual(records[-1], 'HLINSTALL done=9 total=12\n')

    def test_non_gui_has_no_install_events(self):
        stages = []
        _, events = self.run_install(2, ['file'], gui=False, retry=True, stages=stages)
        self.assertEqual(events, [])
        self.assertEqual(stages, [])

    def test_v1_verification_precedes_assembly(self):
        stages = []
        self.run_install(1, ['file'], stages=stages)
        self.assertEqual(stages, ['verify', 'assembly'])

    def test_v2_recovery_reports_interleaved_stages(self):
        stages = []
        _, events = self.run_install(2, ['file'], retry=True, stages=stages)
        self.assertEqual(stages, ['verify', 'assembly', 'retry', 'verify', 'assembly'])
        self.assert_work(events, 6)

    def test_serial_short_read_reports_retry_before_verification(self):
        first, second = Mock(status_code=200), Mock(status_code=206)
        first.iter_content.return_value = [DATA[:1]]
        second.headers = {'content-range': 'bytes 1-2/3'}
        second.iter_content.return_value = [DATA[1:]]
        session = Mock()
        session.get.side_effect = [first, second]
        manifest = {'version': 1, 'files': [{'path': 'file', 'size': 3, 'sha256': DIGEST,
                                             'parts': [ASSET]}]}
        with tempfile.TemporaryDirectory() as tmp, patch.object(h, 'check_disk_space'), \
                patch.object(h, '_write') as write, patch.dict(h.os.environ, {
                    'SILVER_LANTERN_GUI_PROGRESS': '1', 'SILVER_LANTERN_TEST_NO_DEFENDER': '1'}):
            h.install_game(session, manifest, Path(tmp))
            self.assertEqual((Path(tmp) / 'file').read_bytes(), DATA)
            records = [call.args[0].split(' ', 1)[0] if call.args[0].startswith('HLVERIFY ')
                       else call.args[0].strip() for call in write.call_args_list
                       if call.args[0].startswith(('HLVERIFY ', 'HLINSTALL_STAGE '))]
        self.assertEqual(records, ['HLINSTALL_STAGE name=retry', 'HLVERIFY', 'HLINSTALL_STAGE name=assembly'])
