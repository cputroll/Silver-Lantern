import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import game_settings as gfx


class Graphics043Tests(unittest.TestCase):
    def test_unrelated_edit_preserves_effective_legacy_game_speed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'hookshot_version.txt').write_text('version=v0.4.3\n', encoding='ascii')
            (root / gfx.INI_NAME).write_text('game_speed = 0\ndynamic_resolution=0\n', encoding='ascii')
            self.assertEqual((gfx.read_values(root)['game_speed'], gfx.read_values(root)['resolution']), (1, 1))
            gfx.set_value(root, 'fullscreen', 1)
            self.assertEqual(gfx.read_values(root)['game_speed'], 1)
            gfx.set_value(root, 'game_speed', 0)
            self.assertEqual(gfx.read_values(root)['game_speed'], 0)

    def test_resolution_choices_and_quality_preset_match_engine(self):
        schema = gfx.BY_KEY_043
        self.assertEqual(gfx.allowed('resolution', schema=schema, current={'dlss': 1}), [0, 7, 3, 5, 4, 6])
        self.assertEqual(gfx.allowed('resolution', schema=schema, current={'dlss': 3}), [0, 3, 5, 4, 6])
        self.assertEqual(gfx.allowed('resolution', schema=schema, current={'dlss': 0}), [0, 1, 2, 3, 4])
        self.assertEqual(gfx.display_value(schema['resolution'], 1, {'dlss': 1}, schema), '67% (Quality)')
        self.assertEqual(gfx.display_value(schema['resolution'], 7, {'dlss': 1}, schema), 'DLAA (native)')
        preset = gfx.change({'dlss': 1}, 'quality_preset', 3, schema)
        self.assertEqual((preset['resolution'], preset['frame_pacing'], preset['game_speed']), (3, 1, 1))
        for key, value in (('dlss', 3), ('frame_pacing', 0)):
            self.assertEqual(gfx.change({'quality_preset': 3}, key, value, schema)['quality_preset'], 0)
            self.assertNotIn('quality_preset', gfx.change({'quality_preset': 3}, key, value))

    def test_recommended_auto_uses_builtin_frame_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'hookshot_version.txt').write_text('version=v0.4.3\n', encoding='ascii')
            preset, _ = gfx.choose_preset(root, detect=lambda: 'AMD Radeon')
            gfx.write_values(root, {'frame_generation_by': 2})
            gfx.write_values(root, preset)
            saved = gfx.read_values(root)
            self.assertEqual((saved['frame_generation_by'], saved['frame_generation']), (0, 1))
    def test_edits_keep_migrated_settings_after_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'hookshot_version.txt').write_text('version=v0.4.3\n', encoding='ascii')
            (root / gfx.INI_NAME).write_text('dlss=2\nresolution=4\nhdr=3\n', encoding='ascii')
            gfx.set_value(root, 'dlss', 4)
            gfx.set_value(root, 'hdr_tone_mapper', 0)
            saved = gfx.read_values(root)
            self.assertEqual((saved['dlss'], saved['resolution']), (4, 7))
            self.assertEqual((saved['hdr'], saved['hdr_tone_mapper']), (2, 0))

    def test_cli_uses_installed_schema_for_writes_and_json(self):
        import importlib.util
        import io
        import json
        from contextlib import redirect_stderr, redirect_stdout

        spec = importlib.util.spec_from_file_location('heylisten_cli', Path(__file__).with_name('Get-HeyListen.py'))
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with redirect_stderr(io.StringIO()):
                self.assertEqual(cli.graphics_command(root, ['frame_generation_by=2']), 2)
            (root / 'hookshot_version.txt').write_text('version=v0.4.3\n', encoding='ascii')
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(cli.graphics_command(root, ['frame_generation_by=AMD FSR 3.1 FG']), 0)
            rows = {row['key']: row for row in json.loads(out.getvalue())['options']}
            self.assertEqual(rows['frame_generation_by']['value'], 2)
            self.assertEqual(rows['frame_generation']['allowed'], [0, 2])
            (root / 'ngx').mkdir()
            (root / 'ngx' / 'nvngx_dlss.dll').touch()
            with redirect_stdout(io.StringIO()):
                self.assertEqual(cli.graphics_command(root, ['dlss=1', 'resolution=DLAA (native)']), 0)
            self.assertEqual(gfx.read_values(root)['resolution'], 7)

    def test_large_picker_renders_only_a_window_at_the_selected_index(self):
        from launcher import LauncherUI

        with patch.object(LauncherUI, 'check_updates'):
            ui = LauncherUI()
        ui.screen = 'graphics'
        ui.gfx_schema = gfx.BY_KEY_043
        ui.gfx_picker = 'psychov35_ui_brightness'
        ui.gfx_values = {'psychov35_ui_brightness': 499}
        ui.focus = 419
        painter = MagicMock()
        painter.height.return_value = 12
        with patch.object(ui, 'draw_rows') as draw:
            ui.draw_gfx_picker(painter, 0)
        self.assertEqual(len(draw.call_args.args[1]), 10)
        self.assertEqual(draw.call_args.kwargs['first'], 411)
        self.assertEqual(draw.call_args.args[1][ui.focus - 411], '499 nits')

    def test_reset_leaves_model_to_engine_and_preserves_bindings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'hookshot_version.txt').write_text('version=v0.4.3\n', encoding='ascii')
            ini = root / gfx.INI_NAME
            ini.write_text('dlss_model=4\nkey_custom=9\nhdr_tone_mapper=1\n', encoding='ascii')
            self.assertEqual(gfx.reset_defaults(root)['hdr_tone_mapper'], 0)
            saved = ini.read_text(encoding='ascii')
            self.assertNotIn('dlss_model=', saved)
            self.assertIn('key_custom=9', saved)

    def test_launcher_exposes_new_rows_and_respects_provider_limits(self):
        from launcher import LauncherUI

        with tempfile.TemporaryDirectory() as directory, patch.object(LauncherUI, 'check_updates'):
            root = Path(directory)
            (root / 'hookshot_version.txt').write_text('version=v0.4.3\n', encoding='ascii')
            ini = root / gfx.INI_NAME
            ini.write_text('frame_generation=1\nkey_custom=9\n', encoding='ascii')
            ui = LauncherUI()
            ui.folder = root
            ui.enter_graphics()
            self.assertIn('Frame generation by', ui.buttons())
            self.assertIn('HDR tone mapper', ui.buttons())
            self.assertEqual(ui.gfx_allowed('frame_generation_by'), [0, 2])
            ui.gfx_write('frame_generation_by', 2)
            self.assertEqual(ui.gfx_allowed('frame_generation'), [0, 2])
            self.assertEqual(gfx.read_values(root)['frame_generation'], 2)
            self.assertIn('key_custom=9', ini.read_text(encoding='ascii'))

    def test_legacy_values_are_unchanged_but_new_game_migrates_on_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ini = root / gfx.INI_NAME
            text = 'dlss=2\nresolution=4\ndlss_model=4\nvsync=2\nhdr=3\nkey_custom=9\n'
            ini.write_text(text, encoding='ascii')
            legacy = gfx.read_values(root)
            self.assertEqual((legacy['dlss'], legacy['resolution']), (2, 4))
            self.assertNotIn('frame_generation_by', legacy)
            (root / 'hookshot_version.txt').write_text('version=v0.4.3\n', encoding='ascii')
            current = gfx.read_values(root)
            self.assertEqual((current['dlss'], current['resolution'], current['dlss_model']), (1, 7, 4))
            self.assertEqual((current['vsync'], current['vsync_mode'], current['hdr'], current['hdr_tone_mapper']), (1, 1, 2, 1))
            self.assertEqual(current['frame_generation_by'], 0)
            self.assertEqual(ini.read_text(encoding='ascii'), text)

    def test_explicit_vsync_mode_wins_and_unknown_versions_use_legacy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / gfx.INI_NAME).write_text('vsync=2\nvsync_mode=2\n', encoding='ascii')
            version = root / 'hookshot_version.txt'
            version.write_text('version=v0.4.3\n', encoding='ascii')
            self.assertEqual(gfx.read_values(root)['vsync_mode'], 2)
            version.write_text('version=v9.0.0\n', encoding='ascii')
            self.assertNotIn('vsync_mode', gfx.read_values(root))
