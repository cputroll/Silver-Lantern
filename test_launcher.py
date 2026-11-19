import unittest
import launcher
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import os
import subprocess
from launcher import create_shortcut, cli_command, LauncherUI, tool_update_worker
import sys
from launcher import App, IllegalTransition, State, InputMapper, resolve_folder, legacy_exists, ProgressEvents


class TipTests(unittest.TestCase):
    def test_shortcut_tip_tracks_game_setting_without_writing(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            ini = folder / 'hookshot_options.ini'
            for value, key in ((0, 'F1 or Insert'), (1, 'Insert'), (2, 'F1')):
                content = f'menu_shortcut={value}\n'
                ini.write_text(content)
                self.assertEqual(launcher.play_tip(folder), f'While playing, press {key} for settings or to quit.')
                self.assertEqual(ini.read_text(), content)

    def test_hero_tip_moves_without_overlapping_title_or_navigation(self):
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as d, patch.object(LauncherUI, 'check_updates'):
            ui = LauncherUI()
            ui.folder, ui.state, ui.focus = Path(d), State.READY, 0
            ui.reduce_motion = False
            painter = MagicMock()
            painter.height.return_value = 20
            painter.measure.return_value = (20, 20)
            painter.fit_middle.side_effect = lambda role, value, width: value
            frames = []
            for t in (0, .5):
                painter.reset_mock()
                ui.hits = []
                with patch.object(launcher.time, 'monotonic', return_value=t):
                    ui.draw_hero(painter, .016)
                tips = [c for c in painter.text.call_args_list if 'While playing,' in c.args[1]]
                self.assertEqual(len(tips), 1)
                frame = tips[0].args
                self.assertEqual(frame[0], 'body')
                self.assertEqual(frame[4], launcher.INK_DIM)
                frames.append(frame)
                self.assertFalse(any(len(c.args) >= 4 and c.args[1] < 140 for c in painter.fill.call_args_list))
                self.assertFalse(any(len(c.args) >= 4 and c.args[1] < 140 for c in painter.ring.call_args_list))
                self.assertLess(frame[3] + 20, 160)
                keys = [c.args for c in painter.text.call_args_list if c.args[1] == 'F1 or Insert']
                self.assertEqual(len(keys), 1)
                self.assertEqual(keys[0][4], launcher.INK)
                self.assertEqual(keys[0][3], frame[3])
                combo = [c.args for c in painter.text.call_args_list if c.args[1] == 'Start + Back']
                self.assertEqual(len(combo), 1)
                self.assertEqual(combo[0][4], launcher.INK)
                self.assertEqual(combo[0][3], frame[3] + 28)
                self.assertLess(combo[0][3] + 20, 160)
                self.assertTrue(all(y >= 330 for _, y, _, _, _ in ui.hits))
                self.assertIn(0, [h[4] for h in ui.hits])
            self.assertNotEqual(frames[0][3], frames[1][3])

    def test_reduced_motion_disables_bounce(self):
        with patch.object(launcher, 'windows_animations_enabled', return_value=False):
            with patch.object(LauncherUI, 'check_updates'):
                self.assertTrue(LauncherUI().reduce_motion)

    def test_software_renderer_holds_tip_still(self):
        with patch.object(LauncherUI, 'check_updates'):
            ui = LauncherUI()
            ui.state = State.READY
            ui.reduce_motion = False
            ui.tip_motion_supported = False
            ui.hits = []
            painter = MagicMock()
            painter.measure.return_value = (20, 20)
            with patch.object(launcher.time, 'monotonic', return_value=1.25):
                ui.draw_hero(painter, .016)
            tip = next(call.args for call in painter.text.call_args_list if 'While playing,' in call.args[1])
            self.assertEqual(tip[3], 60)

    def test_tip_bounce_is_bounded_and_can_snap(self):
        samples = [launcher.tip_offset(t) for t in (0, .25, .5, .75, 1)]
        self.assertGreater(max(samples), min(samples))
        self.assertTrue(all(-1.25 <= x <= 1.25 for x in samples))
        self.assertTrue(any(x != round(x) for x in samples))
        self.assertEqual(launcher.tip_offset(.25, snap=True), 0)

    def test_vsync_does_not_also_sleep(self):
        sdl = MagicMock()
        sdl.SDL_RENDERER_SOFTWARE = 1
        sdl.SDL_RENDERER_ACCELERATED = 2
        sdl.SDL_RENDERER_PRESENTVSYNC = 4
        launcher.present_frame(sdl, 'renderer', flags=6)
        sdl.SDL_RenderPresent.assert_called_once_with('renderer')
        sdl.SDL_Delay.assert_not_called()
        launcher.present_frame(sdl, 'renderer', flags=5)
        sdl.SDL_Delay.assert_called_once_with(16)

    def test_tip_settings_are_not_read_every_frame(self):
        with patch.object(LauncherUI, 'check_updates'), patch.object(launcher, 'play_tip_parts', return_value=('', 'F1', '')) as read:
            ui = LauncherUI()
            self.assertEqual(ui.tip_parts_at(0)[1], 'F1')
            self.assertEqual(ui.tip_parts_at(.5)[1], 'F1')
            read.assert_called_once_with(ui.folder)
            read.return_value = ('', 'Insert', '')
            self.assertEqual(ui.tip_parts_at(1)[1], 'Insert')
            self.assertEqual(read.call_count, 2)


class StateTests(unittest.TestCase):
    def test_fractional_window_preserves_download_glyph_pixels(self):
        script = r'''
import ctypes as c
import sdl2 as s
import sdl2.sdlttf as t
from launcher import Painter, render_density, CANVAS, ACCENT, ACCENT_INK

assert s.SDL_Init(s.SDL_INIT_VIDEO) == 0 and t.TTF_Init() == 0
window = s.SDL_CreateWindow(b'text test', 0, 0, 1500, 844, s.SDL_WINDOW_HIDDEN)
renderer = s.SDL_CreateRenderer(window, -1, s.SDL_RENDERER_SOFTWARE)
assert window and renderer
painter = Painter(s, t, renderer)
painter.set_density(render_density(1500, 844))
def capture(reference):
    s.SDL_RenderSetLogicalSize(renderer, 0, 0)
    s.SDL_SetRenderDrawColor(renderer, *ACCENT, 255)
    s.SDL_RenderClear(renderer)
    if reference:
        texture, w, h = painter.cache[('cta', 'DOWNLOAD', ACCENT_INK, False)]
        s.SDL_RenderCopy(renderer, texture, None, s.SDL_Rect(0, 0, w, h))
    else:
        s.SDL_RenderSetLogicalSize(renderer, *CANVAS)
        painter.text('cta', 'DOWNLOAD', 0, 0, ACCENT_INK)
        width, height = c.c_int(), c.c_int()
        s.SDL_RenderGetLogicalSize(renderer, c.byref(width), c.byref(height))
        assert (width.value, height.value) == CANVAS, 'Text draw lost logical viewport'
        s.SDL_RenderSetLogicalSize(renderer, 0, 0)
    pixels = (c.c_ubyte * (190 * 42 * 4))()
    assert s.SDL_RenderReadPixels(renderer, s.SDL_Rect(0, 0, 190, 42), s.SDL_PIXELFORMAT_ARGB8888, pixels, 190 * 4) == 0
    return bytes(pixels)
try:
    assert capture(False) == capture(True), 'Static label was resampled'
    s.SDL_SetWindowSize(window, 1603, 810)
    painter.set_density(render_density(1603, 810))
    s.SDL_RenderSetLogicalSize(renderer, *CANVAS)
    s.SDL_SetRenderDrawColor(renderer, *ACCENT, 255)
    s.SDL_RenderClear(renderer)
    clip = s.SDL_Rect(48, 0, 55, 42)
    s.SDL_RenderSetClipRect(renderer, clip)
    painter.text('cta', 'DOWNLOAD', 50, 0, ACCENT_INK)
    restored = s.SDL_Rect()
    s.SDL_RenderGetClipRect(renderer, c.byref(restored))
    assert tuple(getattr(restored, k) for k in ('x','y','w','h')) == (48, 0, 55, 42)
    s.SDL_RenderSetClipRect(renderer, None)
    s.SDL_RenderSetLogicalSize(renderer, 0, 0)
    pixels = (c.c_ubyte * (180 * 42 * 4))()
    assert s.SDL_RenderReadPixels(renderer, s.SDL_Rect(137, 0, 180, 42), s.SDL_PIXELFORMAT_ARGB8888, pixels, 720) == 0
    tex, w, h = painter.cache[('cta', 'DOWNLOAD', ACCENT_INK, False)]
    assert w > 60
    s.SDL_RenderClear(renderer)
    s.SDL_RenderSetClipRect(renderer, s.SDL_Rect(135, 0, 61, 47))
    s.SDL_RenderCopy(renderer, tex, None, s.SDL_Rect(137, 0, w, h))
    reference = type(pixels)()
    assert s.SDL_RenderReadPixels(renderer, s.SDL_Rect(137, 0, 180, 42), s.SDL_PIXELFORMAT_ARGB8888, reference, 720) == 0
    assert bytes(pixels) == bytes(reference), 'Letterboxed clip or label position changed'
finally:
    painter.close()
    s.SDL_DestroyRenderer(renderer)
    s.SDL_DestroyWindow(window)
    t.TTF_Quit()
    s.SDL_Quit()
'''
        result = subprocess.run([sys.executable, '-c', script], cwd=Path(launcher.__file__).parent,
                                env=dict(os.environ, SDL_VIDEODRIVER='dummy'), capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_standalone_version_matches_release_version(self):
        import json
        version = json.loads((Path(launcher.__file__).parent / 'sources.json').read_text(encoding='utf-8'))['tool']['version']
        self.assertEqual(launcher.APP_VERSION, version)

    def test_download_defers_space_check_to_installer_even_on_low_space(self):
        with tempfile.TemporaryDirectory() as d, patch.object(LauncherUI, 'check_updates'):
            ui = LauncherUI()
            ui.folder = Path(d)
            with patch.object(launcher.shutil, 'disk_usage', return_value=type('Usage', (), {'free': 1})()), \
                 patch.object(ui, 'save'), patch.object(launcher.threading, 'Thread') as thread:
                ui.start_operation()
            self.assertEqual(ui.screen, 'progress')
            self.assertEqual(ui.state, State.DOWNLOADING)
            thread.return_value.start.assert_called_once()

    def test_screenshot_debug_renders_all_screens(self):
        with tempfile.TemporaryDirectory() as d:
            output = Path(d) / 'screen.bmp'
            result = subprocess.run([sys.executable, str(Path(launcher.__file__)), '--screenshot', str(output)], capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
            for name in ('main', 'hero-download', 'progress', 'tool-update', 'settings', 'folder', 'update', 'error',
                         'graphics', 'graphics-fsr', 'graphics-actions', 'graphics-picker'):
                frame = output.with_name('screen-' + name + '.bmp')
                # A full 1280x720x32 BMP; an empty or missing frame fails here.
                self.assertGreater(frame.stat().st_size, 1280 * 720 * 3, name)

    def test_screenshot_debug_supports_resized_output(self):
        import struct
        for width, height in ((1296, 759), (640, 360)):
            with self.subTest(size=(width, height)), tempfile.TemporaryDirectory() as d:
                env = dict(os.environ, HEYLISTEN_SCREENSHOT_SIZE=f'{width}x{height}')
                result = subprocess.run([sys.executable, str(Path(launcher.__file__)), '--screenshot', str(Path(d) / 'screen.bmp')], env=env, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
                data = (Path(d) / 'screen-settings-back.bmp').read_bytes()
                self.assertEqual(struct.unpack_from('<ii', data, 18), (width, height))

    def test_settings_rows_stay_above_footer_and_follow_selection(self):
        from unittest.mock import MagicMock
        ui = LauncherUI()
        ui.screen = 'settings'
        ui.snap = True
        painter = MagicMock()
        painter.height.return_value = 20
        painter.fit_middle.side_effect = lambda role, value, width: value
        painter.measure.return_value = (20, 20)
        for index in range(len(ui.buttons())):
            ui.focus = index
            ui.hits = []
            ui.draw_panel(painter, 0)
            self.assertIn(index, [hit[4] for hit in ui.hits])
            self.assertTrue(all(y + h <= 594 for _, y, _, h, _ in ui.hits))
        self.assertEqual(ui.hits[-1][4], len(ui.buttons()) - 1)
        ui.focus = 5  # Hovering a visible row must not shift it under the pointer.
        ui.hits = []
        ui.draw_panel(painter, 0)
        self.assertEqual(ui.hits[0][4], 1)
        for _ in range(8): ui.action('up')
        ui.hits = []
        ui.draw_panel(painter, 0)
        self.assertEqual(ui.hits[0][4], 0)

    def test_worker_failure_includes_exit_code_and_child_diagnostic(self):
        import io
        from unittest.mock import MagicMock
        events = launcher.ProgressEvents()
        proc = MagicMock()
        proc.stdout = io.StringIO('Downloading...\nDisk unavailable\n')
        proc.wait.return_value = 1
        with tempfile.TemporaryDirectory() as d, patch.object(launcher, 'STATE_DIR', Path(d)), patch.object(launcher, 'LOG', Path(d) / 'log'), patch.object(launcher.subprocess, 'Popen', return_value=proc):
            launcher.worker_command(events, [])
            messages = []
            while not events.queue.empty(): messages.append(events.queue.get_nowait())
            error = [value for kind, value in messages if kind == 'error'][0]
            self.assertNotIn('exit 1', str(error))
            self.assertIn('try again', error.lower())
            self.assertNotIn('Disk unavailable', error)
            self.assertIn('Disk unavailable', (Path(d) / 'log').read_text())

    def test_worker_preserves_log_boundary_after_unterminated_prompt(self):
        import io
        from unittest.mock import MagicMock
        proc = MagicMock(stdout=io.StringIO('Press Enter to close this window.'), **{'wait.return_value': 0})
        with tempfile.TemporaryDirectory() as d, patch.object(launcher, 'STATE_DIR', Path(d)), patch.object(launcher, 'LOG', Path(d) / 'log'), patch.object(launcher.subprocess, 'Popen', return_value=proc):
            launcher.worker_command(launcher.ProgressEvents(), [])
            launcher.log_message('Test GUI progress: state=State.READY pct=100')
            self.assertEqual((Path(d) / 'log').read_text().splitlines()[-2:], [
                'Press Enter to close this window.', 'Test GUI progress: state=State.READY pct=100'])

    def test_worker_timestamps_transfer_before_ui_drain(self):
        import io
        events = launcher.ProgressEvents()
        proc = MagicMock(stdout=io.StringIO('HLPROGRESS phase=Downloading done=80 total=100 speed=10485760\n'), **{'wait.return_value': 0})
        with tempfile.TemporaryDirectory() as d, patch.object(launcher, 'STATE_DIR', Path(d)), patch.object(launcher, 'LOG', Path(d) / 'log'), patch.object(launcher.subprocess, 'Popen', return_value=proc), patch.object(launcher.time, 'monotonic', return_value=100):
            launcher.worker_command(events, [])
        kind, value = events.queue.get_nowait()
        self.assertEqual((kind, value.get('rate_received_at')), ('progress', 100))

    def test_worker_failure_without_output_identifies_exit_code(self):
        import io
        from unittest.mock import MagicMock
        events = launcher.ProgressEvents()
        proc = MagicMock(stdout=io.StringIO(''), **{'wait.return_value': 7})
        with tempfile.TemporaryDirectory() as d, patch.object(launcher, 'STATE_DIR', Path(d)), patch.object(launcher, 'LOG', Path(d) / 'log'), patch.object(launcher.subprocess, 'Popen', return_value=proc):
            launcher.worker_command(events, [])
        kind, error = events.queue.get_nowait()
        self.assertEqual(kind, 'error')
        self.assertNotIn('exit 7', error)
        self.assertIn('try again', error.lower())

    def test_worker_failure_surfaces_child_problem_and_action(self):
        import io
        from unittest.mock import MagicMock
        events = launcher.ProgressEvents()
        child = (
            'Preparing install\n'
            '  Problem: Not enough free space on C:. The download\n'
            '  needs 33.2 GB, and 13.4 GB is free.\n'
            '  What to do: Free up about 19.8 GB, then run this again.\n'
            '  Details were saved to: C:\\temp\\HeyListen-Get.log\n'
        )
        proc = MagicMock()
        proc.stdout = io.StringIO(child)
        proc.wait.return_value = 1
        with tempfile.TemporaryDirectory() as d, patch.object(launcher, 'STATE_DIR', Path(d)), patch.object(launcher, 'LOG', Path(d) / 'log'), patch.object(launcher.subprocess, 'Popen', return_value=proc):
            launcher.worker_command(events, [])
            messages = []
            while not events.queue.empty(): messages.append(events.queue.get_nowait())
        error = str([value for kind, value in messages if kind == 'error'][0])
        self.assertNotIn('exit 1', error)
        self.assertIn('Not enough free space on C:', error)
        self.assertIn('needs 33.2 GB', error)          # wrapped continuation is joined
        self.assertIn('Free up about 19.8 GB', error)
        self.assertNotIn('Details were saved to:', error)
        self.assertNotIn('C:\\temp\\HeyListen-Get.log', error)

    def test_worker_log_open_failure_starts_no_worker(self):
        import io
        from unittest.mock import MagicMock
        events = launcher.ProgressEvents()
        with tempfile.TemporaryDirectory() as d, patch.object(launcher, 'STATE_DIR', Path(d)):
            blocked = Path(d) / 'log'
            blocked.mkdir()  # opening a directory for append always fails
            with patch.object(launcher, 'LOG', blocked), patch.object(launcher.subprocess, 'Popen') as popen:
                launcher.worker_command(events, [])
        self.assertFalse(popen.called)
        self.assertIsNone(getattr(events, 'process', None))
        kind, error = events.queue.get_nowait()
        self.assertEqual(kind, 'error')
        self.assertTrue(events.queue.empty())

    def test_worker_failure_after_start_stops_the_worker(self):
        import io
        from unittest.mock import MagicMock
        events = launcher.ProgressEvents()
        proc = MagicMock()
        proc.stdout = io.StringIO('Downloading...\n')
        proc.pid = 4242
        proc.poll.return_value = None
        with tempfile.TemporaryDirectory() as d, patch.object(launcher, 'STATE_DIR', Path(d)), patch.object(launcher, 'LOG', Path(d) / 'log'), patch.object(launcher.subprocess, 'Popen', return_value=proc), patch.object(launcher.subprocess, 'run') as run, patch.object(launcher, 'parse_progress', side_effect=ValueError('progress parser broke')):
            launcher.worker_command(events, [])
        self.assertTrue(events.cancelled.is_set())
        if os.name == 'nt':
            self.assertTrue(run.called)
            self.assertIn('4242', ' '.join(str(part) for part in run.call_args.args[0]))
        kind, error = events.queue.get_nowait()
        self.assertEqual(kind, 'error')
        self.assertNotIn('progress parser broke', error)
        self.assertIn('could not finish', error)

    def test_friendly_gui_text_redacts_paths_and_console_leaders(self):
        text = launcher.friendly_gui_text(r'Problem: C:\Users\friend\file.bin failed ... retry /home/friend/file.bin')
        self.assertNotIn(r'C:\Users', text)
        self.assertNotIn('/home/friend', text)
        self.assertNotIn('...', text)

    def test_friendly_blocks_prefers_the_last_problem(self):
        lines = ['  Problem: first problem', '  What to do: first action',
                 '  Problem: second problem', '  What to do: second action',
                 '  Details were saved to: /tmp/x.log']
        problem, action, details = launcher.friendly_blocks(lines)
        self.assertEqual(problem, 'second problem')
        self.assertEqual(action, 'second action')
        self.assertEqual(details, '/tmp/x.log')

    def test_settings_speed_action_reports_result(self):
        ui = launcher.LauncherUI()
        ui.screen = 'settings'
        ui.focus = 1
        with patch.object(launcher, 'optimize_defender', return_value=True) as optimize:
            ui.activate()
            optimize.assert_called_once_with(ui.folder)
            self.assertEqual(ui.status, 'Added')
        with patch.object(launcher, 'optimize_defender', return_value=False):
            ui.activate()
            self.assertEqual(ui.status, 'Windows declined')
        with patch.object(launcher, 'optimize_defender', side_effect=OSError('unavailable')):
            ui.activate()
            self.assertEqual(ui.status, 'Unavailable')

    def test_backdrop_asset_ships(self):
        self.assertTrue((Path(launcher.__file__).parent / 'assets' / 'ui-backdrop.bmp').is_file())
        release = (Path(launcher.__file__).parent / 'release.ps1').read_text(encoding='utf-8')
        self.assertIn('ui-backdrop.bmp', release)

    def test_no_controller_nag(self):
        source = Path(launcher.__file__).read_text(encoding='utf-8')
        self.assertNotIn('No controller found', source)

    def test_gui_progress_line_parsed(self):
        info = launcher.parse_progress('HLPROGRESS phase=Downloading done=500 total=1000 speed=2048\n')
        self.assertEqual((info['pct'], info['done'], info['total'], info['speed'], info['phase']), (50, 500, 1000, 2048, 'Downloading'))
        self.assertEqual(launcher.parse_progress('HLPROGRESS done=x total=1')['text'] if launcher.parse_progress('HLPROGRESS done=x total=1') else None, None)

    def test_invalid_entry_error_keeps_sentence_boundary(self):
        message = 'Problem: The game package contains an invalid entry: romfs/a file.bin. What to do: Tell the person who provided the game package.'
        rendered = launcher.friendly_gui_text(message)
        self.assertIn('. What to do: Tell', rendered)

    def test_verification_progress_is_bundle_counts_not_speed(self):
        info = launcher.parse_progress('HLVERIFY done=4 total=22\n')
        self.assertEqual(info['phase'], 'Verifying the download...')
        self.assertEqual(info['text'], 'Checking 4 of 22 bundles')
        self.assertEqual(info['speed'], 0)
        self.assertIsNone(info['pct'])
        legacy = launcher.parse_progress('HLPROGRESS phase=Checking done=100 total=100 speed=999')
        self.assertEqual((legacy['pct'], legacy['speed'], legacy['text']), (None, 0, 'Checking game files...'))
        with patch.object(LauncherUI, 'check_updates'):
            ui = LauncherUI()
            ui.events.publish('progress', launcher.parse_progress('HLPROGRESS phase=Downloading done=100 total=100 speed=999'))
            ui.events.publish('progress', info)
            ui.drain()
            self.assertEqual(ui.progress, 'Checking 4 of 22 bundles')
            self.assertEqual(ui.progress_info, info)

    def test_progress_info_reaches_ui(self):
        with patch.object(LauncherUI, 'check_updates'):
            ui = LauncherUI()
            ui.events.publish('progress', launcher.parse_progress('HLPROGRESS phase=Downloading done=63 total=100 speed=10'))
            ui.drain()
            self.assertEqual(ui.progress_info['pct'], 63)
            self.assertEqual(ui.progress, 'Downloading: 63%')

    def test_game_progress_only_uses_monotonic_install_events(self):
        with patch.object(LauncherUI, 'check_updates'), patch.object(LauncherUI, 'save'), patch.object(launcher, 'probe_writable'), patch.object(launcher.threading, 'Thread'):
            for update in (False, True):
                with self.subTest(update=update):
                    ui = LauncherUI()
                    ui.start_operation(update)
                    self.assertEqual(ui.game_progress_pct, 0)
                    for line, expected, phase in (
                        ('Downloading: 77% C:/raw/path', 0, 'PREPARING UPDATE' if update else 'PREPARING INSTALLATION'),
                        ('Checking installation plan: 12345/186614 files checked', 0, 'CHECKING EXISTING FILES'),
                        ('Starting bundle download and installation', 0, 'PREPARING DOWNLOAD'),
                        ('Preparing download: organizing 186614 destination files', 0, 'PREPARING DOWNLOAD'),
                        ('Preparing download: 135566/186614 destination files checked', 0, 'PREPARING DOWNLOAD'),
                        ('HLPROGRESS phase=Downloading done=80 total=100 speed=10485760', 0, 'DOWNLOADING GAME FILES'),
                        ('HLINSTALL done=1 total=0', 0, 'DOWNLOADING GAME FILES'),
                        ('HLINSTALL done=61 total=100', 61, 'DOWNLOADING GAME FILES'),
                        ('HLVERIFY done=1 total=2', 61, 'VERIFYING GAME FILES'),
                        ('HLINSTALL done=30 total=100', 61, 'VERIFYING GAME FILES'),
                        ('HLINSTALL_STAGE name=assembly', 61, 'INSTALLING GAME FILES'),
                        ('HLINSTALL_STAGE name=retry', 61, 'RETRYING INSTALLATION'),
                        ('HLINSTALL_STAGE name=unknown', 61, 'RETRYING INSTALLATION'),
                        ('Connection stalled — retrying…', 61, 'RETRYING CONNECTION'),
                        ('HLINSTALL done=120 total=100', 99, 'RETRYING CONNECTION'),
                    ):
                        ui.events.publish('progress', launcher.parse_progress(line) or {'text': line})
                        ui.drain()
                        self.assertEqual(ui.game_progress_pct, expected, line)
                        painter = MagicMock()
                        with patch.object(ui, 'draw_rows'), patch.object(ui, 'draw_hints'):
                            ui.draw_progress(painter, .016)
                        figures = [c.args[1] for c in painter.text.call_args_list if c.args[0] == 'figure']
                        self.assertEqual(figures, [f'{expected}%'])
                        self.assertEqual(painter.text.call_args_list[0].args[1], phase)
                        labels = [c.args[1] for c in painter.text.call_args_list]
                        self.assertFalse(any('raw/path' in label for label in labels))
                        if line.startswith('Checking installation plan:'):
                            self.assertIn('Checking 12,345 of 186,614 files', labels)
                        if line.startswith('Preparing download: organizing'):
                            self.assertIn('Checking 0 of 186,614 files', labels)
                        if line.startswith('Preparing download: 135566'):
                            self.assertIn('Checking 135,566 of 186,614 files', labels)
                        self.assertEqual([label for label in labels if '/s' in label],
                                         ['10.0 MB/s · About 1 s left to download'] if phase == 'DOWNLOADING GAME FILES' else [])

    def test_game_download_speed_expires_without_fresh_transfer(self):
        with patch.object(LauncherUI, 'check_updates'), patch.object(LauncherUI, 'save'), patch.object(launcher, 'probe_writable'), patch.object(launcher.threading, 'Thread'):
            ui = LauncherUI()
            ui.start_operation(False)
            rate = launcher.parse_progress('HLPROGRESS phase=Downloading done=104857600 total=891289600 speed=10485760')
            ui.events.publish('progress', dict(rate, rate_received_at=100))
            with patch.object(launcher.time, 'monotonic', return_value=104):
                ui.drain()
            def labels_at(now):
                painter = MagicMock()
                with patch.object(launcher.time, 'monotonic', return_value=now), patch.object(ui, 'draw_rows'), patch.object(ui, 'draw_hints'):
                    ui.draw_progress(painter, .016)
                return [call.args[1] for call in painter.text.call_args_list]

            label = '10.0 MB/s · About 1 min 15 s left to download'
            self.assertNotIn(label, labels_at(104))
            ui.events.publish('progress', dict(rate, rate_received_at=105))
            ui.drain()
            self.assertIn(label, labels_at(107))
            retry_rate = launcher.parse_progress('HLPROGRESS phase=Downloading done=104857600 total=891289600 speed=10485760 eta=0')
            ui.events.publish('progress', dict(retry_rate, rate_received_at=107))
            ui.drain()
            self.assertIn('10.0 MB/s', labels_at(107))
            self.assertNotIn(label, labels_at(107))
            ui.events.publish('progress', dict(rate, speed=0, rate_received_at=107))
            ui.drain()
            self.assertNotIn(label, labels_at(107))
            ui.events.publish('progress', dict(rate, done=rate['total'], rate_received_at=107))
            ui.drain()
            self.assertIn('10.0 MB/s', labels_at(107))
            self.assertNotIn(label, labels_at(107))

    def test_tool_progress_ignores_game_install_events(self):
        with patch.object(LauncherUI, 'check_updates'):
            ui = LauncherUI()
            ui.operation = 'tool'
            ui.screen = 'progress'
            ui.progress = 'Updating Silver Lantern…'
            ui.events.publish('progress', launcher.parse_progress('HLINSTALL done=50 total=100') or {})
            ui.drain()
            self.assertIsNone(ui.game_progress_pct)
            self.assertEqual(ui.progress, 'Updating Silver Lantern…')

    def test_cli_emits_gui_progress_when_requested(self):
        import io, importlib.util
        from contextlib import redirect_stdout
        spec = importlib.util.spec_from_file_location('hl_cli', Path(launcher.__file__).parent / 'Get-HeyListen.py')
        cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
        out, t = io.StringIO(), [0.0]
        with patch.dict(os.environ, {'SILVER_LANTERN_GUI_PROGRESS': '1'}), redirect_stdout(out):
            bar = cli.Progress('Downloading', 100, tty=False, clock=lambda: t[0])
            for _ in range(4):
                t[0] += 1; bar.add(25)
        lines = [l for l in out.getvalue().splitlines() if l.startswith('HLPROGRESS ')]
        self.assertTrue(lines)
        self.assertEqual(launcher.parse_progress(lines[-1])['pct'], 100)

    def test_format_helpers(self):
        self.assertEqual(launcher.fmt_bytes(int(33.05 * 1024 ** 3)), '33.0 GB')
        self.assertEqual(launcher.fmt_eta(214), '3 min 34 s')
        self.assertEqual(launcher.fmt_eta(3700), '1 h 01 min')

    def test_tool_worker_invocation_and_success(self):
        events = ProgressEvents()
        with patch('launcher.subprocess.run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = 'SILVERLANTERN_UPDATE_HANDOFF\n'
            tool_update_worker(events, ['--gui'])
        self.assertEqual(run.call_args.args[0], [sys.executable, '--self-update', '--gui'])
        self.assertEqual(events.queue.get_nowait(), ('tool_complete', 0))

    def test_tool_worker_already_current(self):
        events = ProgressEvents()
        with patch('launcher.subprocess.run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = 'Self-update check complete.\n'
            tool_update_worker(events, [])
        self.assertEqual(events.queue.get_nowait(), ('tool_current', 0))

    def test_tool_worker_failure(self):
        events = ProgressEvents()
        with patch('launcher.subprocess.run') as run:
            run.return_value.returncode = 7
            run.return_value.stdout = ''
            tool_update_worker(events, [])
        kind, message = events.queue.get_nowait()
        self.assertEqual(kind, 'error')
        self.assertIn('try again later', message.lower())
        self.assertNotIn('exit 7', message)

    def test_tool_worker_failure_shows_friendly_blocks(self):
        events = ProgressEvents()
        with tempfile.TemporaryDirectory() as d, patch.object(launcher, 'STATE_DIR', Path(d)), patch.object(launcher, 'LOG', Path(d) / 'log'), patch('launcher.subprocess.run') as run:
            run.return_value.returncode = 1
            run.return_value.stdout = 'Problem: Update was damaged.\nWhat to do: Try again later.\nDetails were saved to: C:\\private\\log.txt\n'
            tool_update_worker(events, [])
            kind, message = events.queue.get_nowait()
            self.assertEqual(kind, 'error')
            self.assertIn('Update was damaged', message)
            self.assertIn('Try again later', message)
            self.assertNotIn('C:\\private', message)
            self.assertIn('C:\\private', (Path(d) / 'log').read_text())

    def test_tool_check_auto_and_manual(self):
        for auto in (True, False):
            with self.subTest(auto=auto), patch.object(LauncherUI, 'check_updates'):
                ui = LauncherUI()
                ui.config['auto_tool_update'] = auto
                ui.events.publish('tool_check', 'v999.0.0')
                with patch.object(ui, 'start_tool_update') as start:
                    ui.drain()
                self.assertEqual(start.called, auto)
                if not auto: self.assertIn('Update now', ui.buttons())

    def test_auto_update_deferred_during_game_operation(self):
        for operation in ('game', 'download', 'update'):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as d, patch.object(launcher, 'STATE_DIR', Path(d)), patch.object(launcher, 'LOG', Path(d) / 'test.log'), patch.object(LauncherUI, 'check_updates'):
                ui = LauncherUI()
                ui.operation = operation
                ui.screen = 'main'
                ui.events.publish('tool_check', 'v999.0.0')
                with patch.object(ui, 'start_tool_update') as start:
                    ui.drain()
                start.assert_not_called()
                self.assertTrue(ui.tool_update_available)

    def test_startup_check_failure_shows_retry_and_rechecks(self):
        with patch.object(LauncherUI, 'check_updates'):
            ui = LauncherUI()
            ui.events.publish('offline', 'Could not reach the update server')
            ui.drain()
            self.assertEqual(ui.screen, 'error')
            self.assertIn('Could not reach the update server', ui.last_error)
            self.assertIn('Retry', ui.buttons())
            ui.focus = ui.buttons().index('Retry')
            with patch.object(ui, 'check_updates') as check, patch.object(ui, 'start_operation') as install:
                ui.activate()
            check.assert_called_once()
            install.assert_not_called()

    def test_detected_game_update_starts_automatically_when_idle(self):
        with patch.object(LauncherUI, 'check_updates'), patch.object(launcher, 'installed_manifest_digest', return_value='old'):
            ui = LauncherUI()
            ui.state = State.READY
            ui.screen = 'main'
            ui.operation = None
            ui.events.publish('check', (2, 'new'))
            with patch.object(ui, 'start_operation') as start:
                ui.drain()
            start.assert_called_once_with(True)

    def test_channel_update_uses_measured_manifest_worker_without_pruning(self):
        with patch.object(LauncherUI, 'check_updates'), patch.object(LauncherUI, 'save'), patch.object(launcher, 'probe_writable'), patch.object(launcher, 'installed_manifest_digest', return_value='old'), patch.object(launcher, '_TEST_GAME_MANIFEST', None), patch.object(launcher.threading, 'Thread') as thread:
            ui = LauncherUI()
            ui.folder, ui.state, ui.screen = Path('existing game'), State.READY, 'main'
            ui.events.publish('check', (2, 'new'))
            ui.drain()
            thread.assert_called_once_with(target=launcher.worker_command, args=(ui.events, [
                '--no-self-update', '--install-game', str(ui.folder), '--no-preset',
            ]), daemon=True)
            self.assertEqual((ui.state, ui.operation, ui.game_progress_pct), (State.UPDATING, 'update', 0))
            ui.events.publish('progress', launcher.parse_progress('HLINSTALL done=1 total=2'))
            ui.drain()
            self.assertEqual(ui.game_progress_pct, 50)

    def test_completed_update_clears_operation(self):
        with tempfile.TemporaryDirectory() as d, patch.object(LauncherUI, 'check_updates'), patch.object(launcher.threading, 'Thread'), patch.object(launcher.time, 'monotonic', return_value=10) as clock:
            ui = LauncherUI()
            ui.folder = Path(d)
            (ui.folder / 'hookshot_version.txt').touch()
            (ui.folder / 'bin').mkdir()
            exe = ui.folder / 'bin' / 'HeyListen.exe'
            exe.touch()
            ui.operation = 'update'
            ui.set_screen('progress')
            ui.events.publish('complete', 0)
            ui.drain()
            self.assertEqual((ui.screen, ui.state, ui.game_progress_pct), ('progress', State.READY, 100))
            self.assertEqual(ui.buttons(), [])
            painter = MagicMock()
            ui.draw_progress(painter, .016)
            self.assertEqual([c.args[1] for c in painter.text.call_args_list if c.args[0] == 'figure'], ['100%'])
            clock.return_value = 11
            ui.drain()
            self.assertIsNone(ui.operation)
            self.assertEqual(ui.screen, 'main')
            self.assertIn('Play', ui.buttons())
            with patch.object(launcher.subprocess, 'Popen') as popen:
                ui.activate()
            popen.assert_called_once_with([str(exe)], cwd=str(ui.folder))

    def test_missing_game_target_is_not_success(self):
        with tempfile.TemporaryDirectory() as d, patch.object(LauncherUI, 'check_updates'):
            ui = LauncherUI()
            ui.folder = Path(d)
            ui.operation = 'download'
            ui.set_screen('progress')
            ui.events.publish('complete', 0)
            ui.drain()
            self.assertEqual((ui.screen, ui.state, ui.operation), ('error', State.ERROR, 'download'))
            self.assertIn('Retry', ui.buttons())

    def test_detected_game_update_does_not_interrupt_active_operation(self):
        with patch.object(LauncherUI, 'check_updates'), patch.object(launcher, 'installed_manifest_digest', return_value='old'):
            ui = LauncherUI()
            ui.state = State.READY
            ui.screen = 'main'
            ui.operation = 'game'
            ui.events.publish('check', (2, 'new'))
            with patch.object(ui, 'start_operation') as start:
                ui.drain()
            start.assert_not_called()

    def test_equal_tool_version_does_not_handoff(self):
        with patch.object(LauncherUI, 'check_updates'):
            ui = LauncherUI()
            ui.events.publish('tool_check', 'v' + launcher.APP_VERSION)
            with patch.object(ui, 'start_tool_update') as start:
                ui.drain()
            start.assert_not_called()
            self.assertIn('up to date', ui.status.lower())

    def test_newer_tool_version_handoffs_once(self):
        with patch.object(LauncherUI, 'check_updates'):
            ui = LauncherUI()
            ui.events.publish('tool_check', 'v999.0.0')
            ui.events.publish('tool_check', 'v999.0.0')
            with patch.object(ui, 'start_tool_update') as start:
                ui.drain()
            start.assert_called_once()

    def test_tool_error_opens_retry_screen(self):
        with patch.object(LauncherUI, 'check_updates'):
            ui = LauncherUI()
            ui.operation = 'tool'
            ui.events.publish('error', 'failed')
            ui.drain()
            self.assertEqual(ui.screen, 'error')
            self.assertIn('Retry', ui.buttons())
            self.assertIn('View log', ui.buttons())

    def test_tool_success_exits(self):
        with patch.object(LauncherUI, 'check_updates'):
            ui = LauncherUI()
            ui.events.publish('tool_complete', 0)
            ui.drain()
            self.assertFalse(ui.running)

    def test_frozen_cli_uses_same_executable(self):
        with patch.object(sys, 'frozen', True, create=True):
            self.assertEqual(cli_command('--version'), [sys.executable, '--cli', '--version'])

    def test_download_success(self):
        app = App(State.NOT_INSTALLED)
        app.transition(State.DOWNLOADING)
        app.transition(State.READY)
        self.assertEqual(app.state, State.READY)

    def test_illegal_transition(self):
        with self.assertRaises(IllegalTransition):
            App(State.NOT_INSTALLED).transition(State.PLAYING)

    def test_cancel_update(self):
        app = App(State.UPDATE_AVAILABLE)
        app.transition(State.UPDATING)
        app.transition(State.UPDATE_AVAILABLE)
        self.assertEqual(app.state, State.UPDATE_AVAILABLE)

    def test_button_release(self):
        mapper = InputMapper()
        self.assertEqual(mapper.feed({'type': 'button', 'button': 'a', 'down': True}, 0), [])
        self.assertEqual(mapper.feed({'type': 'button', 'button': 'a', 'down': False}, 0), ['accept'])

    def test_deadzone(self):
        self.assertEqual(InputMapper().feed({'type': 'axis', 'x': .2, 'y': .1}, 0), [])

    def test_repeat(self):
        mapper = InputMapper()
        self.assertEqual(mapper.feed({'type': 'axis', 'x': 0, 'y': 1}, 0), ['down'])
        self.assertEqual(mapper.tick(.39), [])
        self.assertEqual(mapper.tick(.41), ['down'])

    def test_default_folder(self):
        self.assertEqual(resolve_folder(Path('primary'), Path('fallback'), lambda p: None)[0], Path('primary'))

    def test_acl_fallback(self):
        def probe(path):
            if path == Path('primary'):
                raise PermissionError()
        self.assertEqual(resolve_folder(Path('primary'), Path('fallback'), probe)[0], Path('fallback'))

    def test_legacy_adoption(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            (folder / '.heylisten-parts').mkdir()
            self.assertTrue(legacy_exists(folder))

    def test_progress_queue(self):
        events = ProgressEvents()
        events.publish('progress', 42)
        self.assertEqual(events.queue.get_nowait(), ('progress', 42))

    @unittest.skipUnless(os.name == 'nt', 'Windows shortcuts require COM')
    def test_shortcut_redirected_desktop(self):
        with tempfile.TemporaryDirectory() as root:
            profile = Path(root)
            (profile / 'Desktop').mkdir()
            folder = profile / "game's folder"
            folder.mkdir()
            (folder / 'HeyListen.exe').touch()
            with patch.object(launcher, 'shortcut_path', return_value=profile / 'Desktop' / (launcher.GAME_NAME + '.lnk')):
                create_shortcut(folder)
                self.assertTrue((profile / 'Desktop' / (launcher.GAME_NAME + '.lnk')).is_file())
                create_shortcut(folder, False)
                self.assertFalse((profile / 'Desktop' / (launcher.GAME_NAME + '.lnk')).exists())

    def test_cancellation(self):
        events = ProgressEvents()
        events.cancel()
        self.assertTrue(events.cancelled.is_set())
