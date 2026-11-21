import inspect
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch


class GameStateTests(unittest.TestCase):
    def setUp(self):
        import game_state

        self.game_state = game_state

    def test_no_terminate_process_in_module(self):
        source = inspect.getsource(self.game_state)
        self.assertNotIn('TerminateProcess', source)
        self.assertNotIn('PROCESS_TERMINATE', source)
        self.assertFalse(hasattr(self.game_state, 'terminate_running_games'))

    @unittest.skipUnless(sys.platform == 'win32', 'requires Windows Toolhelp')
    def test_detects_spawned_process_in_its_executable_folder(self):
        executable = Path(sys.executable)
        with subprocess.Popen([str(executable), '-c', 'import time;time.sleep(5)']) as child:
            try:
                self.assertIn(child.pid, self.game_state._process_ids())
                folder = str(executable.parent).swapcase()
                self.assertTrue(self.game_state.is_running(folder))
                with patch.object(self.game_state, '_process_ids', return_value=[child.pid]):
                    found = self.game_state.running_game(folder)
                    self.assertIsNotNone(found)
                    self.assertEqual(os.path.normcase(found), os.path.normcase(str(executable)))
            finally:
                if child.poll() is None:
                    child.terminate()
                child.wait(timeout=5)

    def test_running_game_excludes_current_pid_and_launcher_or_workers(self):
        folder = r'C:\Games\Ocarina of Time'
        current = os.getpid()
        records = [
            (current, r'C:\Games\Ocarina of Time\bin\HeyListen.exe'),
            (current + 1, r'C:\Games\Ocarina of Time\Ocarina of Time.exe'),
            (current + 2, r'C:\Games\Ocarina of Time\Ocarina of Time-abc123.exe'),
            (current + 3, r'C:\Games\Ocarina of Time\HeyListen-Get.exe'),
            (current + 4, r'C:\Games\Ocarina of Time\SilverLantern.exe'),
        ]
        with patch.object(self.game_state, '_process_records', return_value=iter(records)):
            self.assertIsNone(self.game_state.running_game(folder))
            self.assertIsNone(
                self.game_state.running_game(folder, r'C:\Games\Ocarina of Time\bin\HeyListen.exe')
            )

    def test_matches_nested_path_but_not_prefix_neighbor(self):
        folder = r'C:\Games\Sacrappy'
        sibling = r'c:\games\sacrappy-backup\bin\HeyListen.exe'
        game = r'c:\GAMES\SACRAPPY\bin\HeyListen.exe'
        with patch.object(self.game_state, '_process_paths', return_value=[sibling]):
            self.assertIsNone(self.game_state.running_game(folder))
            self.assertFalse(self.game_state.is_running(folder))
        with patch.object(self.game_state, '_process_paths', return_value=[sibling, game]):
            self.assertEqual(self.game_state.running_game(folder), game)

    def test_close_game_validates_pid_and_image_and_never_closes_launcher_or_workers(self):
        folder = r'C:\Games\Ocarina of Time'
        game_exe = r'C:\Games\Ocarina of Time\bin\HeyListen.exe'
        current = os.getpid()
        game_pid = current + 100
        launcher_pid = current + 101
        worker_pid = current + 102
        other_pid = current + 103

        windows = [
            (10, current, True, False),       # self: must never close
            (11, launcher_pid, True, False),  # launcher in same folder: must never close
            (12, worker_pid, True, False),    # worker in same folder: must never close
            (13, other_pid, True, False),     # sibling folder game: must never close
            (14, game_pid, False, False),     # hidden helper window: skipped
            (15, game_pid, True, True),       # owned popup: skipped when unowned main window exists
            (16, game_pid, True, False),      # exact game main window: close!
        ]
        images = {
            current: game_exe,
            launcher_pid: r'C:\Games\Ocarina of Time\Ocarina of Time.exe',
            worker_pid: r'C:\Games\Ocarina of Time\HeyListen-Get.exe',
            other_pid: r'C:\Games\Ocarina of Time-backup\bin\HeyListen.exe',
            game_pid: game_exe.swapcase(),
        }
        hwnd_pids = {hwnd: pid for hwnd, pid, _, _ in windows}

        with patch.object(self.game_state, '_enum_top_level_windows', return_value=windows), \
                patch.object(self.game_state, '_process_image', side_effect=images.get), \
                patch.object(self.game_state, '_window_pid', side_effect=hwnd_pids.get), \
                patch.object(self.game_state, '_post_close', return_value=True) as post_close:
            self.assertEqual(self.game_state.close_game(folder, game_exe), 1)
            post_close.assert_called_once_with(16)

    def test_close_game_rejects_missing_exe_protected_exe_self_pid_and_toctou_swap(self):
        folder = r'C:\Games\Ocarina of Time'
        game_exe = r'C:\Games\Ocarina of Time\bin\HeyListen.exe'
        current = os.getpid()
        target_pid = current + 200

        with patch.object(self.game_state, '_post_close', return_value=True) as post_close:
            self.assertEqual(self.game_state.close_game(folder, None), 0)
            self.assertEqual(self.game_state.close_game(folder, ''), 0)
            self.assertEqual(self.game_state.close_game(folder, game_exe, pid=current), 0)
            self.assertEqual(
                self.game_state.close_game(folder, r'C:\Games\Ocarina of Time\Ocarina of Time.exe'),
                0,
            )
            self.assertEqual(
                self.game_state.close_game(folder, r'C:\Games\Ocarina of Time\HeyListen-Get.exe'),
                0,
            )
            self.assertEqual(
                self.game_state.close_game(folder, r'C:\Other\HeyListen.exe'),
                0,
            )
            post_close.assert_not_called()

        # If the HWND owner PID or process image changes between enum and post, refuse to post WM_CLOSE.
        windows = [(42, target_pid, True, False)]
        with patch.object(self.game_state, '_enum_top_level_windows', return_value=windows), \
                patch.object(self.game_state, '_process_image', side_effect=[game_exe, r'C:\Windows\notepad.exe']), \
                patch.object(self.game_state, '_window_pid', return_value=target_pid), \
                patch.object(self.game_state, '_post_close', return_value=True) as post_close:
            self.assertEqual(self.game_state.close_game(folder, game_exe, pid=target_pid), 0)
            post_close.assert_not_called()

        with patch.object(self.game_state, '_enum_top_level_windows', return_value=windows), \
                patch.object(self.game_state, '_process_image', return_value=game_exe), \
                patch.object(self.game_state, '_window_pid', return_value=target_pid + 1), \
                patch.object(self.game_state, '_post_close', return_value=True) as post_close:
            self.assertEqual(self.game_state.close_game(folder, game_exe, pid=target_pid), 0)
            post_close.assert_not_called()

    @unittest.skipUnless(sys.platform == 'win32', 'requires Win32 WM_CLOSE')
    def test_close_game_gracefully_closes_real_win32_window_by_pid_and_image(self):
        script = textwrap.dedent('''
            import ctypes
            from ctypes import wintypes
            import pathlib
            import sys

            ready_path = pathlib.Path(sys.argv[1])
            user32 = ctypes.WinDLL('user32', use_last_error=True)
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            LRESULT = ctypes.c_ssize_t
            WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

            user32.DefWindowProcW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
            user32.DefWindowProcW.restype = LRESULT
            user32.RegisterClassW.restype = wintypes.ATOM
            user32.CreateWindowExW.argtypes = (
                wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
            )
            user32.CreateWindowExW.restype = wintypes.HWND
            user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
            user32.DestroyWindow.argtypes = (wintypes.HWND,)
            user32.PostQuitMessage.argtypes = (ctypes.c_int,)
            user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
            user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
            user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
            kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
            kernel32.GetModuleHandleW.restype = wintypes.HMODULE

            class WNDCLASSW(ctypes.Structure):
                _fields_ = [
                    ('style', wintypes.UINT),
                    ('lpfnWndProc', WNDPROC),
                    ('cbClsExtra', ctypes.c_int),
                    ('cbWndExtra', ctypes.c_int),
                    ('hInstance', wintypes.HINSTANCE),
                    ('hIcon', wintypes.HICON),
                    ('hCursor', wintypes.HANDLE),
                    ('hbrBackground', wintypes.HBRUSH),
                    ('lpszMenuName', wintypes.LPCWSTR),
                    ('lpszClassName', wintypes.LPCWSTR),
                ]

            closed_gracefully = False

            def wndproc(hwnd, msg, wparam, lparam):
                global closed_gracefully
                if msg == 0x0010:  # WM_CLOSE
                    closed_gracefully = True
                    user32.DestroyWindow(hwnd)
                    user32.PostQuitMessage(0)
                    return 0
                return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

            proc = WNDPROC(wndproc)
            hinst = kernel32.GetModuleHandleW(None)
            cls = WNDCLASSW()
            cls.lpfnWndProc = proc
            cls.hInstance = hinst
            cls.lpszClassName = 'HeyListenGracefulCloseQA'
            assert user32.RegisterClassW(ctypes.byref(cls))
            hwnd = user32.CreateWindowExW(
                0, cls.lpszClassName, 'GracefulCloseQA', 0x00CF0000,
                0, 0, 120, 80, None, None, hinst, None,
            )
            assert hwnd
            user32.ShowWindow(hwnd, 7)  # SW_SHOWMINNOACTIVE: visible (WS_VISIBLE) without stealing focus
            ready_path.write_text(str(hwnd), encoding='utf-8')
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            sys.exit(0 if closed_gracefully else 2)
        ''')
        executable = Path(sys.executable)
        with tempfile.TemporaryDirectory() as tmp:
            ready = Path(tmp) / 'ready.txt'
            target = subprocess.Popen([str(executable), '-c', script, str(ready)])
            bystander = subprocess.Popen([str(executable), '-c', 'import time; time.sleep(10)'])
            try:
                deadline = time.monotonic() + 5.0
                while not ready.exists() and time.monotonic() < deadline:
                    self.assertIsNone(target.poll())
                    time.sleep(0.05)
                self.assertTrue(ready.exists(), 'child test window did not initialize in time')
                sent = self.game_state.close_game(executable.parent, executable, pid=target.pid)
                self.assertGreaterEqual(sent, 1)
                self.assertEqual(target.wait(timeout=5), 0)
                self.assertIsNone(bystander.poll())
            finally:
                for proc in (target, bystander):
                    if proc.poll() is None:
                        proc.terminate()
                        proc.wait(timeout=5)

    def test_watcher_checks_once_per_second_and_emits_only_transitions(self):
        events = []
        exe = r'C:\Games\Sacrappy\bin\HeyListen.exe'
        watch = self.game_state.GameWatch('folder',
                                          lambda path: events.append(('started', path)),
                                          lambda: events.append(('ended',)))
        with patch.object(self.game_state.time, 'monotonic', side_effect=[0, .5, 1, 2, 3, 4, 5]), \
                patch.object(self.game_state, 'running_game',
                             side_effect=[None, exe, exe.swapcase(), None, None, exe]) as detect:
            for _ in range(7):
                watch.poll()
        self.assertEqual(detect.call_count, 6)
        self.assertEqual(events, [('started', exe), ('ended',), ('started', exe)])
