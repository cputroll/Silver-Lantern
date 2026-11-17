import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_updater import app


class FakeResponse:
    def __init__(self, status=200, body=b'', headers=None, fail_after=None):
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self._fail_after = fail_after

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')

    def iter_content(self, _size):
        if self._body:
            yield self._body
        if self._fail_after is not None:
            raise ConnectionError('dropped')

    def close(self):
        pass


class PresetTests(unittest.TestCase):
    def test_apply_preset_merges_and_keeps_unknown_lines(self):
        with tempfile.TemporaryDirectory() as td:
            game = Path(td) / 'missing' / 'game'
            game.mkdir(parents=True)
            ini = game / 'hookshot_options.ini'
            ini.write_text('pad_a=3\ndlss=0\n# comment\nsharpness=10\ndlss=2\nkey_jump=space\n', encoding='ascii')
            with mock.patch.object(app, 'log'), \
                 mock.patch.object(app, 'choose_preset', return_value=(app.PRESET_DLSS, 'test')):
                result = app.apply_preset(game)
            self.assertEqual(result, ini)
            lines = ini.read_text(encoding='ascii').splitlines()
            self.assertEqual(lines[:5], ['pad_a=3', 'dlss=1', '# comment', 'sharpness=80', 'key_jump=space'])
            self.assertEqual(lines.count('dlss=1'), 1)
            for k, v in app.PRESET.items():
                self.assertIn(f'{k}={v}', lines)

    def test_apply_preset_creates_folder(self):
        with tempfile.TemporaryDirectory() as td:
            game = Path(td) / 'new' / 'game'
            with mock.patch.object(app, 'log'), mock.patch.object(app, 'detect_gpu_name', return_value=None):
                ini = app.apply_preset(game)
            self.assertEqual(ini.read_text(encoding='ascii').splitlines(), [f'{k}={v}' for k, v in app.PRESET_FSR.items()])

    def _run_main(self, td, *extra):
        argv = ['app', '--no-self-update', '--no-launch', '--out', td, '--game-dir', str(Path(td) / 'g'), *extra]
        man = {'game': {'title': 't'}, 'tool': {}}
        with mock.patch.object(sys, 'argv', argv), mock.patch.object(app, 'load_manifest', return_value=man), \
             mock.patch.object(app, 'session'), mock.patch.object(app, 'download_builder'), \
             mock.patch.object(app, 'download_game'), mock.patch.object(app, 'log'), \
             mock.patch.object(app, 'apply_preset') as preset:
            self.assertEqual(app.main(), 0)
        return preset

    def test_main_applies_preset_unless_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            self._run_main(td).assert_called_once_with(Path(td) / 'g')
            self._run_main(td, '--no-preset').assert_not_called()


class GpuPresetTests(unittest.TestCase):
    RTX = 'NVIDIA GeForce RTX 4070 Ti'
    GTX = 'NVIDIA GeForce GTX 1660 SUPER'
    AMD = 'AMD Radeon RX 7900 XTX'
    INTEL = 'Intel(R) UHD Graphics 770'

    def test_gpu_name_parsing(self):
        self.assertTrue(app.is_dlss_gpu(self.RTX))
        self.assertTrue(app.is_dlss_gpu('nvidia rtx a4000'))
        self.assertFalse(app.is_dlss_gpu(self.GTX))
        self.assertFalse(app.is_dlss_gpu(self.AMD))
        self.assertFalse(app.is_dlss_gpu(self.INTEL))
        self.assertFalse(app.is_dlss_gpu(''))
        self.assertFalse(app.is_dlss_gpu(None))

    def test_detect_gpu_name_reads_first_line(self):
        done = mock.Mock(returncode=0, stdout=self.RTX + '\r\n')
        with mock.patch.object(app.subprocess, 'run', return_value=done) as run:
            self.assertEqual(app.detect_gpu_name(), self.RTX)
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[0], 'powershell')
        self.assertIn('Win32_VideoController', cmd[-1])
        self.assertIn('AdapterRAM -Descending', cmd[-1])

    def test_detect_gpu_name_empty_or_failed(self):
        for done in (mock.Mock(returncode=0, stdout='  \n'), mock.Mock(returncode=1, stdout=self.RTX)):
            with mock.patch.object(app.subprocess, 'run', return_value=done):
                self.assertIsNone(app.detect_gpu_name())

    def _choose(self, gpu_name, dll):
        with tempfile.TemporaryDirectory() as td:
            game = Path(td)
            if dll:
                (game / 'ngx').mkdir()
                (game / 'ngx' / 'nvngx_dlss.dll').write_bytes(b'MZ')
            done = mock.Mock(returncode=0, stdout=gpu_name + '\n')
            with mock.patch.object(app.subprocess, 'run', return_value=done):
                return app.choose_preset(game)

    def test_decision_matrix(self):
        for gpu in (self.RTX, self.GTX, self.AMD, self.INTEL):
            for dll in (True, False):
                with self.subTest(gpu=gpu, dll=dll):
                    preset, why = self._choose(gpu, dll)
                    if gpu == self.RTX and dll:
                        self.assertIs(preset, app.PRESET_DLSS)
                        self.assertIn('NVIDIA RTX with DLSS build', why)
                        self.assertIn('DLSS Performance', why)
                    else:
                        self.assertIs(preset, app.PRESET_FSR)
                        self.assertIn('No RTX/DLSS build', why)
                        self.assertIn('FSR 3.1 Performance', why)

    def test_preset_values(self):
        self.assertEqual(app.PRESET_DLSS['dlss'], '1')
        self.assertEqual(app.PRESET_DLSS['reflex'], '1')
        self.assertEqual(app.PRESET_FSR['dlss'], '3')
        self.assertEqual(app.PRESET_FSR['reflex'], '0')
        for p in (app.PRESET_DLSS, app.PRESET_FSR):
            self.assertEqual((p['resolution'], p['sharpness'], p['pace'], p['framegen']), ('4', '80', '1', '1'))

    def test_detection_failures_fall_back_to_fsr(self):
        for err in (FileNotFoundError('powershell'), app.subprocess.TimeoutExpired('powershell', 30), OSError('x')):
            with self.subTest(err=type(err).__name__), tempfile.TemporaryDirectory() as td:
                with mock.patch.object(app.subprocess, 'run', side_effect=err):
                    preset, why = app.choose_preset(Path(td))
                self.assertIs(preset, app.PRESET_FSR)
                self.assertIn('FSR 3.1', why)

    def test_apply_preset_writes_chosen_preset_and_logs_reason(self):
        with tempfile.TemporaryDirectory() as td:
            game = Path(td) / 'g'
            (game / 'ngx').mkdir(parents=True)
            (game / 'ngx' / 'nvngx_dlss.dll').write_bytes(b'MZ')
            (game / 'hookshot_options.ini').write_text('pad_a=3\ndlss=0\n', encoding='ascii')
            with mock.patch.object(app, 'detect_gpu_name', return_value=self.RTX), \
                 mock.patch.object(app, 'log') as log:
                ini = app.apply_preset(game)
            lines = ini.read_text(encoding='ascii').splitlines()
            self.assertEqual(lines[:2], ['pad_a=3', 'dlss=1'])
            self.assertIn('reflex=1', lines)
            self.assertIn('DLSS Performance', log.call_args_list[0].args[0])

            with mock.patch.object(app, 'detect_gpu_name', side_effect=RuntimeError('boom')), \
                 mock.patch.object(app, 'log') as log:
                app.apply_preset(game)
            lines = ini.read_text(encoding='ascii').splitlines()
            self.assertEqual(lines[:2], ['pad_a=3', 'dlss=3'])
            self.assertIn('reflex=0', lines)
            self.assertIn('detection failed', log.call_args_list[0].args[0])

    def test_ps1_parses(self):
        ps1 = Path(__file__).with_name('Apply-HeyListen-Preset.ps1')
        cmd = ("$e=$null; $null=[System.Management.Automation.Language.Parser]::ParseFile("
               f"'{ps1}', [ref]$null, [ref]$e); exit $e.Count")
        try:
            r = app.subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', cmd],
                                   capture_output=True, text=True, timeout=60)
        except (FileNotFoundError, app.subprocess.TimeoutExpired):
            self.skipTest('PowerShell not available')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        for name in ('log',):
            p = mock.patch.object(app, name)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(app.time, 'sleep')
        p.start()
        self.addCleanup(p.stop)

    def test_resume_offset_mismatch_restarts_from_zero(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / 'f.bin'
            part = Path(td) / 'f.bin.part'
            part.write_bytes(b'XXXXX')
            calls = []

            def get(url, headers=None, **kw):
                calls.append(dict(headers or {}))
                if 'range' in headers:   # wrong offset: server restarts at 0
                    return FakeResponse(206, b'0123456789', {'content-range': 'bytes 0-9/10'})
                return FakeResponse(200, b'0123456789')

            s = mock.Mock(get=get)
            app.stream_to(s, 'u', dest, expect=10, part=part)
            self.assertEqual(dest.read_bytes(), b'0123456789')
            self.assertIn('range', calls[0])
            self.assertNotIn('range', calls[-1])

    def test_matching_resume_appends(self):
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / 'f.bin'
            part = Path(td) / 'f.bin.part'
            part.write_bytes(b'01234')
            s = mock.Mock(get=lambda url, headers=None, **kw: FakeResponse(206, b'56789', {'content-range': 'bytes 5-9/10'}))
            app.stream_to(s, 'u', dest, expect=10, part=part)
            self.assertEqual(dest.read_bytes(), b'0123456789')

    def test_second_source_never_resumes_first_sources_part(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            (out / 'game.nup.part').write_bytes(b'STALE')    # leftover from an older run
            calls = {'a': [], 'b': []}

            def get(url, headers=None, **kw):
                calls[url].append(dict(headers or {}))
                if url == 'a':
                    if len(calls['a']) == 1:
                        return FakeResponse(200, b'AAAA', fail_after=True)
                    return FakeResponse(500)
                have = headers.get('range')
                if have:   # the old bug: would append B bytes after A bytes
                    start = int(have.split('=')[1].rstrip('-'))
                    return FakeResponse(206, b'B' * (8 - start), {'content-range': f'bytes {start}-7/8'})
                return FakeResponse(200, b'B' * 8)

            man = {'game': {'filename': 'game.nup', 'sources': [
                {'url': 'a', 'size': 10, 'sha256': None},
                {'url': 'b', 'size': 8, 'sha256': None},
            ]}}
            dest = app.download_game(mock.Mock(get=get), man, out, skip_hash=True)
            self.assertEqual(dest.read_bytes(), b'B' * 8)
            self.assertTrue(all('range' not in h for h in calls['b']))
            self.assertFalse((out / 'game.nup.part').exists())
            self.assertEqual(sorted(p.name for p in out.iterdir()), ['game.nup'])


class ReleasePickerTests(unittest.TestCase):
    def _session(self, releases):
        response = mock.Mock()
        response.json.return_value = releases
        return mock.Mock(get=mock.Mock(return_value=response))

    def test_picks_highest_stable_semver_tag(self):
        s = self._session([
            {'tag_name': 'game-v1', 'assets': []},
            {'tag_name': 'v9.0.0', 'draft': True},
            {'tag_name': 'v3.0.0', 'prerelease': True},
            {'tag_name': 'v1.10.0'},
            {'tag_name': 'v1.9.0'},
            {'tag_name': 'v2.0.0-rc.1'},
        ])
        self.assertEqual(app.latest_app_release(s, 'x/y')['tag_name'], 'v1.10.0')
        self.assertIn('/releases?per_page=30', s.get.call_args.args[0])

    def test_non_semver_latest_is_up_to_date_not_error(self):
        s = self._session([{'tag_name': 'game-v1', 'assets': [{'name': 'game.nup'}]}])
        self.assertIsNone(app.latest_app_release(s, 'x/y'))
        with mock.patch.object(sys, 'frozen', True, create=True), mock.patch.object(app, 'log') as log:
            app.check_self_update(s, {}, [])
        self.assertIn('up to date', log.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
