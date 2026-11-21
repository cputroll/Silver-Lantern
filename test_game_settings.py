import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import game_settings as gs
from test_game_sync import h


class GameSettingsTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.folder = Path(self.td.name)
        self.ini = self.folder / gs.INI_NAME

    def tearDown(self):
        self.td.cleanup()

    def test_engine_keys_and_specials(self):
        keys = [o.key for o in gs.OPTIONS]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(keys), 47)
        for key in ('output_res', 'dlss', 'resolution', 'upscaling', 'frame_generation', 'frame_pacing', 'nvidia_reflex'):
            self.assertIn(key, gs.BY_KEY)
        for dead in gs.LEGACY_KEYS:
            self.assertNotIn(dead, gs.BY_KEY)
        self.assertEqual(set(o.group for o in gs.OPTIONS), set(gs.GROUPS))

    def test_defaults_when_missing(self):
        values = gs.read_values(self.folder)
        self.assertEqual(values['dlss'], 0)
        self.assertEqual(values['hdr_paper_white'], 200)
        self.assertEqual(values['game_speed'], 1)

    def test_round_trip_keeps_bindings_and_drops_dead_keys(self):
        self.ini.write_text('pad_a=3\nkey_jump=32,0\nframegen=1\npace=1\nreflex=1\ndlss=0\n# note\nmicrophone_device=Mic\ndlss=4\n', encoding='ascii')
        gs.write_values(self.folder, {'dlss': 3, 'frame_generation': 2})
        lines = self.ini.read_text(encoding='ascii').splitlines()
        self.assertEqual(lines[:2], ['pad_a=3', 'key_jump=32,0'])
        self.assertIn('# note', lines)
        self.assertIn('microphone_device=Mic', lines)
        self.assertEqual(lines.count('dlss=3'), 1)
        self.assertFalse([l for l in lines if l.startswith(('framegen=', 'pace=', 'reflex='))])
        self.assertFalse(any(l.startswith('dlss=4') for l in lines))
        values = gs.read_values(self.folder)
        self.assertEqual((values['dlss'], values['frame_generation']), (3, 2))
        self.assertFalse(self.ini.with_name(self.ini.name + '.tmp').exists())

    def test_out_of_range_values_clamp_like_the_engine(self):
        self.ini.write_text('dlss=99\nsharpness=-5\nhdr_peak_brightness=x\noutput_res= 3840x2160\n', encoding='ascii')
        values = gs.read_values(self.folder)
        self.assertEqual(values['dlss'], 5)
        self.assertEqual(values['sharpness'], 0)
        self.assertEqual(values['hdr_peak_brightness'], 1000)
        self.assertEqual(values['output_res'], 2)

    def test_cycle_bounds_wrap(self):
        self.assertEqual(gs.cycle('fullscreen', 1), 0)
        self.assertEqual(gs.cycle('fullscreen', 0, -1), 1)
        self.assertEqual(gs.cycle('sharpness', 100), 0)
        self.assertEqual(gs.cycle('sharpness', 0, -1), 100)
        self.assertEqual(gs.cycle('sharpness', 80), 90)
        self.assertEqual(gs.cycle('frame_generation', 8), 0)
        self.assertEqual(gs.cycle('hdr_peak_brightness', 4000), 400)
        for o in gs.OPTIONS:
            v = o.default
            for _ in range(len(o.values()) + 1):
                v = gs.cycle(o.key, v)
                self.assertTrue(o.low <= v <= o.high, o.key)

    def test_dlss_guarded_without_runtime(self):
        self.assertEqual(gs.allowed('dlss', dlss_ok=False), [0, 3, 4, 5])
        self.assertEqual(gs.cycle('dlss', 0, dlss_ok=False), 3)
        self.assertEqual(gs.cycle('dlss', 3, -1, dlss_ok=False), 0)

    def test_invalid_combos_report_locks(self):
        values = gs.read_values(self.folder)
        self.assertIsNotNone(gs.lock_reason('dlss_model', dict(values, dlss=3)))
        self.assertIsNone(gs.lock_reason('dlss_model', dict(values, dlss=1)))
        self.assertIsNotNone(gs.lock_reason('resolution', dict(values, dlss=2)))
        self.assertIsNotNone(gs.lock_reason('nvidia_reflex', dict(values, frame_generation=1)))
        self.assertIsNotNone(gs.lock_reason('hdr_peak_brightness', dict(values, hdr=0)))

    def test_quality_preset_links(self):
        values = gs.set_value(self.folder, 'quality_preset', 1)
        self.assertEqual((values['resolution'], values['upscaling'], values['sharpness'], values['handheld_profile']), (4, 2, 30, 1))
        values = gs.set_value(self.folder, 'sharpness', 80, values)
        self.assertEqual(values['quality_preset'], 0)
        self.assertEqual(gs.read_values(self.folder)['quality_preset'], 0)

    def test_reset_defaults(self):
        self.ini.write_text('pad_a=3\ndlss=1\nsharpness=80\n', encoding='ascii')
        values = gs.reset_defaults(self.folder)
        self.assertEqual(values, {o.key: o.default for o in gs.OPTIONS})
        self.assertIn('pad_a=3', self.ini.read_text(encoding='ascii'))

    def test_recommended_preset_still_applies(self):
        (self.folder / 'ngx').mkdir()
        (self.folder / 'ngx' / 'nvngx_dlss.dll').write_bytes(b'MZ')
        preset, why = gs.choose_preset(self.folder, detect=lambda: 'NVIDIA GeForce RTX 4080')
        self.assertIs(preset, gs.PRESET_DLSS)
        gs.write_values(self.folder, preset)
        values = gs.read_values(self.folder)
        self.assertEqual((values['dlss'], values['resolution'], values['frame_generation'], values['frame_pacing'], values['nvidia_reflex'], values['fullscreen']), (1, 4, 1, 1, 1, 1))
        preset, _ = gs.choose_preset(self.folder, detect=lambda: 'AMD Radeon RX 7800 XT')
        self.assertIs(preset, gs.PRESET_FSR)
        preset, _ = gs.choose_preset(self.folder, detect=mock.Mock(side_effect=OSError('x')))
        self.assertIs(preset, gs.PRESET_FSR)

    def test_describe_flags_legacy_keys(self):
        self.ini.write_text('framegen=1\ndlss=1\n', encoding='ascii')
        d = gs.describe(self.folder)
        self.assertEqual(d['ignored_legacy_keys'], ['framegen'])
        self.assertEqual(len(d['options']), len(gs.OPTIONS))
        json.dumps(d)

    def test_missing_xbox_mappings_report_engine_defaults(self):
        for preset in (gs.PRESET_DLSS, gs.PRESET_FSR):
            self.assertNotIn('layout_preset', preset)
            self.assertNotIn('button_prompts', preset)
        values = gs.read_values(self.folder)
        self.assertEqual((values['layout_preset'], values['button_prompts']), (0, 0))
        (self.folder / 'hookshot_version.txt').write_text('version=v0.4.3\n', encoding='ascii')
        values_043 = gs.read_values(self.folder)
        self.assertEqual((values_043['layout_preset'], values_043['button_prompts']), (0, 0))

    def test_recommended_preset_preserves_mappings_and_leaves_unset_alone(self):
        (self.folder / 'ngx').mkdir()
        (self.folder / 'ngx' / 'nvngx_dlss.dll').write_bytes(b'MZ')
        self.ini.write_text('pad_a=3\nkey_jump=32,0\nlayout_preset=0\nbutton_prompts=4\ndlss=0\n', encoding='ascii')
        for gpu in ('NVIDIA GeForce RTX 4080', 'AMD Radeon RX 7800 XT'):
            preset, _ = gs.choose_preset(self.folder, detect=lambda g=gpu: g)
            gs.write_values(self.folder, preset)
            values = gs.read_values(self.folder)
            self.assertEqual((values['layout_preset'], values['button_prompts']), (0, 4))
            lines = self.ini.read_text(encoding='ascii').splitlines()
            self.assertIn('pad_a=3', lines)
            self.assertIn('key_jump=32,0', lines)
            self.assertIn('layout_preset=0', lines)
            self.assertIn('button_prompts=4', lines)

        self.ini.write_text('dlss=0\n', encoding='ascii')
        preset, _ = gs.choose_preset(self.folder, detect=lambda: 'AMD Radeon RX 7800 XT')
        gs.write_values(self.folder, preset)
        text = self.ini.read_text(encoding='ascii')
        self.assertNotIn('layout_preset=', text)
        self.assertNotIn('button_prompts=', text)
        self.assertEqual((gs.read_values(self.folder)['layout_preset'], gs.read_values(self.folder)['button_prompts']), (0, 0))

    def test_reset_defaults_preserves_saved_mappings_without_writing_xbox(self):
        self.ini.write_text('pad_a=3\ndlss=1\nsharpness=80\nlayout_preset=0\nbutton_prompts=4\n', encoding='ascii')
        values = gs.reset_defaults(self.folder)
        self.assertEqual((values['dlss'], values['sharpness']), (0, 0))
        self.assertEqual((values['layout_preset'], values['button_prompts']), (0, 4))
        text = self.ini.read_text(encoding='ascii')
        self.assertIn('pad_a=3', text)
        self.assertIn('layout_preset=0', text)
        self.assertIn('button_prompts=4', text)

        self.ini.write_text('fullscreen=1\nlayout_preset=1\nbutton_prompts=2\n', encoding='ascii')
        values = gs.reset_defaults(self.folder)
        self.assertEqual((values['fullscreen'], values['layout_preset'], values['button_prompts']), (0, 1, 2))

        self.ini.write_text('dlss=1\nlayout_preset=3\n', encoding='ascii')
        values = gs.reset_defaults(self.folder)
        self.assertEqual((values['dlss'], values['layout_preset'], values['button_prompts']), (0, 3, 0))
        text = self.ini.read_text(encoding='ascii')
        self.assertIn('layout_preset=3', text)
        self.assertNotIn('button_prompts=', text)

        self.ini.write_text('dlss=1\n', encoding='ascii')
        values = gs.reset_defaults(self.folder)
        self.assertEqual((values['layout_preset'], values['button_prompts']), (0, 0))
        text = self.ini.read_text(encoding='ascii')
        self.assertNotIn('layout_preset=', text)
        self.assertNotIn('button_prompts=', text)

        (self.folder / 'hookshot_version.txt').write_text('version=v0.4.3\n', encoding='ascii')
        self.ini.write_text('dlss_model=4\nhdr_tone_mapper=1\nlayout_preset=2\nbutton_prompts=6\n', encoding='ascii')
        values = gs.reset_defaults(self.folder)
        self.assertEqual((values['hdr_tone_mapper'], values['layout_preset'], values['button_prompts']), (0, 2, 6))
        text = self.ini.read_text(encoding='ascii')
        self.assertNotIn('dlss_model=', text)
        self.assertIn('layout_preset=2', text)
        self.assertIn('button_prompts=6', text)

    def test_fresh_install_seam_writes_xbox_defaults_only_when_unset(self):
        preset = gs.preset_for(self.folder, gs.PRESET_DLSS, fresh_install=True)
        self.assertEqual((preset['layout_preset'], preset['button_prompts']), ('1', '2'))
        self.assertNotIn('layout_preset', gs.PRESET_DLSS)
        self.assertNotIn('button_prompts', gs.PRESET_DLSS)
        gs.write_values(self.folder, preset)
        self.assertEqual((gs.read_values(self.folder)['layout_preset'], gs.read_values(self.folder)['button_prompts']), (1, 2))

        self.ini.unlink()
        gs.apply_xbox_defaults(self.folder)
        self.assertEqual((gs.read_values(self.folder)['layout_preset'], gs.read_values(self.folder)['button_prompts']), (1, 2))

        self.ini.write_text('layout_preset = 0\n# button_prompts=4\nbutton_prompts=\npad_a=3\n', encoding='ascii')
        self.assertEqual(gs.unset_xbox_defaults(self.folder), {'button_prompts': '2'})
        gs.apply_xbox_defaults(self.folder)
        lines = self.ini.read_text(encoding='ascii').splitlines()
        self.assertIn('layout_preset = 0', lines)
        self.assertIn('# button_prompts=4', lines)
        self.assertIn('button_prompts=2', lines)
        self.assertIn('pad_a=3', lines)
        self.assertEqual((gs.read_values(self.folder)['layout_preset'], gs.read_values(self.folder)['button_prompts']), (0, 2))

        original = 'layout_preset=0\nbutton_prompts=0\n# keep formatting\n'
        self.ini.write_text(original, encoding='ascii')
        self.assertEqual(gs.unset_xbox_defaults(self.folder), {})
        fresh = gs.preset_for(self.folder, gs.PRESET_FSR, fresh_install=True)
        self.assertNotIn('layout_preset', fresh)
        self.assertNotIn('button_prompts', fresh)
        gs.apply_xbox_defaults(self.folder)
        self.assertEqual(self.ini.read_text(encoding='ascii'), original)

        (self.folder / 'hookshot_version.txt').write_text('version=v0.4.3\n', encoding='ascii')
        self.ini.write_text('button_prompts=6\n', encoding='ascii')
        fresh_043 = gs.preset_for(self.folder, gs.PRESET_FSR, fresh_install=True)
        self.assertEqual(fresh_043['frame_generation_by'], '0')
        self.assertEqual(fresh_043['layout_preset'], '1')
        self.assertNotIn('button_prompts', fresh_043)
        gs.write_values(self.folder, fresh_043)
        self.assertEqual((gs.read_values(self.folder)['layout_preset'], gs.read_values(self.folder)['button_prompts']), (1, 6))


class ExitGameHintTests(unittest.TestCase):
    def test_quit_row_is_documented_everywhere(self):
        root = Path(gs.__file__).parent
        self.assertIn('Quit', gs.BY_KEY['menu_shortcut'].desc)
        readme = (root / 'README.md').read_text(encoding='utf-8')
        self.assertIn('To exit the game from that menu', readme)
        doc = (root / 'docs' / 'graphics-options.md').read_text(encoding='utf-8')
        self.assertIn('Exit Game already exists', doc)
        self.assertIn('Quit (A twice), exits the game', (root / 'launcher.py').read_text(encoding='utf-8'))


class GraphicsCliTests(unittest.TestCase):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, 'argv', ['tool', *args]), mock.patch.object(h, 'relocate_transient', return_value=False), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = h.main()
        return code, out.getvalue(), err.getvalue()

    def test_graphics_reads_and_sets(self):
        with tempfile.TemporaryDirectory() as td:
            code, out, _ = self.run_cli('--graphics', td, '--set', 'frame_generation=Auto', '--set', 'sharpness=70')
            self.assertEqual(code, 0)
            data = json.loads(out)
            rows = {r['key']: r for r in data['options']}
            self.assertEqual(rows['frame_generation']['value'], 1)
            self.assertEqual(rows['sharpness']['text'], '70%')
            self.assertIn('frame_generation=1', (Path(td) / gs.INI_NAME).read_text(encoding='ascii'))

    def test_graphics_rejects_bad_values(self):
        with tempfile.TemporaryDirectory() as td:
            for item in ('dlss=9', 'nope=1', 'sharpness=abc', 'sharpness=55'):
                with self.subTest(item=item):
                    code, _, err = self.run_cli('--graphics', td, '--set', item)
                    self.assertEqual(code, 2)
                    self.assertTrue(err)
            self.assertFalse((Path(td) / gs.INI_NAME).exists())


if __name__ == '__main__':
    unittest.main()
