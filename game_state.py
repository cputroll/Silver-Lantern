"""Find game processes, watch running-state changes, and close the exact game gracefully."""

from collections.abc import Callable, Iterator
import ctypes
from ctypes import wintypes
import ntpath
import os
from os import PathLike
import re
import sys
import time


_TH32CS_SNAPPROCESS = 0x00000002
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
_WM_CLOSE = 0x0010
_GW_OWNER = 4

_PROTECTED_EXE_NAMES = frozenset({
    'heylisten-get.exe',
    'silverlantern.exe',
    'run silver lantern.exe',
    'run heylisten.exe',
    'ocarina of time.exe',
})
_PROTECTED_EXE_PATTERN = re.compile(r'^ocarina of time-[^/\\]+\.exe$', re.IGNORECASE)


class _ProcessEntry(ctypes.Structure):
    _fields_ = [
        ('dwSize', wintypes.DWORD),
        ('cntUsage', wintypes.DWORD),
        ('th32ProcessID', wintypes.DWORD),
        ('th32DefaultHeapID', ctypes.c_size_t),
        ('th32ModuleID', wintypes.DWORD),
        ('cntThreads', wintypes.DWORD),
        ('th32ParentProcessID', wintypes.DWORD),
        ('pcPriClassBase', wintypes.LONG),
        ('dwFlags', wintypes.DWORD),
        ('szExeFile', wintypes.WCHAR * 260),
    ]


if sys.platform == 'win32':
    _kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for _step in (_kernel32.Process32FirstW, _kernel32.Process32NextW):
        _step.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ProcessEntry))
        _step.restype = wintypes.BOOL
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    )
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CloseHandle.restype = wintypes.BOOL

    _user32 = ctypes.WinDLL('user32', use_last_error=True)
    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _user32.EnumWindows.argtypes = (_WNDENUMPROC, wintypes.LPARAM)
    _user32.EnumWindows.restype = wintypes.BOOL
    _user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    _user32.IsWindowVisible.restype = wintypes.BOOL
    _user32.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
    _user32.GetWindow.restype = wintypes.HWND
    _user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    _user32.PostMessageW.restype = wintypes.BOOL


def _norm_path(path: str | PathLike[str]) -> str:
    return ntpath.normcase(ntpath.abspath(os.fspath(path)))


def _path_in_folder(path_norm: str, root_norm: str) -> bool:
    parent = ntpath.dirname(path_norm)
    try:
        return ntpath.commonpath((root_norm, parent)) == root_norm
    except ValueError:  # Different drive or share.
        return False


def _is_protected_exe(path_norm: str) -> bool:
    if getattr(sys, 'frozen', False):
        try:
            if path_norm == _norm_path(sys.executable):
                return True
        except OSError:
            pass
    name = ntpath.basename(path_norm).casefold()
    return name in _PROTECTED_EXE_NAMES or bool(_PROTECTED_EXE_PATTERN.match(name))


def _matches_exact_game(image: str, root_norm: str, wanted_norm: str) -> bool:
    image_norm = _norm_path(image)
    return (
        image_norm == wanted_norm
        and _path_in_folder(image_norm, root_norm)
        and not _is_protected_exe(image_norm)
    )


def _process_ids() -> list[int]:
    if sys.platform != 'win32':
        return []
    snapshot = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snapshot == _INVALID_HANDLE_VALUE:
        return []
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        ids = []
        ok = _kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            ids.append(entry.th32ProcessID)
            ok = _kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        return ids
    finally:
        _kernel32.CloseHandle(snapshot)


def _process_image(pid: int) -> str | None:
    if sys.platform != 'win32' or pid <= 0:
        return None
    process = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process:  # Protected process or exited after the snapshot.
        return None
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        ok = _kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size))
    finally:
        _kernel32.CloseHandle(process)
    return buffer.value if ok else None


def _process_records() -> Iterator[tuple[int, str]]:
    ids = _process_ids()
    if not ids:
        return
    for pid in ids:
        image = _process_image(pid)
        if image:
            yield pid, image


def _process_paths() -> Iterator[str]:
    current_pid = os.getpid()
    for pid, path in _process_records():
        if pid != current_pid:
            yield path


def _window_pid(hwnd: int) -> int:
    if sys.platform != 'win32' or not hwnd:
        return 0
    owner_pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(owner_pid))
    return int(owner_pid.value)


def _enum_top_level_windows() -> list[tuple[int, int, bool, bool]]:
    """Return (hwnd, pid, visible, owned) for each top-level desktop window."""
    if sys.platform != 'win32':
        return []
    windows: list[tuple[int, int, bool, bool]] = []

    def _callback(hwnd: int, _lparam: int) -> bool:
        pid = _window_pid(hwnd)
        visible = bool(_user32.IsWindowVisible(wintypes.HWND(hwnd)))
        owned = bool(_user32.GetWindow(wintypes.HWND(hwnd), _GW_OWNER))
        windows.append((int(hwnd), pid, visible, owned))
        return True

    proc = _WNDENUMPROC(_callback)
    _user32.EnumWindows(proc, 0)
    return windows


def _post_close(hwnd: int) -> bool:
    if sys.platform != 'win32' or not hwnd:
        return False
    return bool(_user32.PostMessageW(wintypes.HWND(hwnd), _WM_CLOSE, 0, 0))


def running_game(folder: str | PathLike[str], exe: str | PathLike[str] | None = None) -> str | None:
    """Return the image path of a game process under folder, if visible.

    When exe is given, only that exact image is matched, so launcher and worker
    processes inside the folder never count as the game. The current process is
    always excluded.
    """
    if not folder:
        return None
    root = _norm_path(folder)
    wanted = _norm_path(exe) if exe else None
    if wanted is not None and (not _path_in_folder(wanted, root) or _is_protected_exe(wanted)):
        return None
    for image in _process_paths():
        image_norm = _norm_path(image)
        if wanted is not None:
            if image_norm != wanted:
                continue
        elif _is_protected_exe(image_norm):
            continue
        if _path_in_folder(image_norm, root):
            return image
    return None


def is_running(folder: str | PathLike[str]) -> bool:
    return running_game(folder) is not None


def close_game(folder: str | PathLike[str], exe: str | PathLike[str] | None,
               pid: int | None = None) -> int:
    """Gracefully ask the exact game's top-level window(s) to close via WM_CLOSE.

    Never terminates processes forcefully, requires an exact game executable inside
    folder, validates both each window's owning PID and its live process image path,
    and refuses to close the launcher or worker executables.
    """
    if not folder or not exe:
        return 0
    current_pid = os.getpid()
    if pid is not None and (pid <= 0 or pid == current_pid):
        return 0
    root = _norm_path(folder)
    wanted = _norm_path(exe)
    if not _path_in_folder(wanted, root) or _is_protected_exe(wanted):
        return 0

    candidates: dict[int, list[tuple[int, bool]]] = {}
    for hwnd, win_pid, visible, owned in _enum_top_level_windows():
        if not visible or win_pid <= 0 or win_pid == current_pid:
            continue
        if pid is not None and win_pid != pid:
            continue
        candidates.setdefault(win_pid, []).append((hwnd, owned))

    closed = 0
    for win_pid, windows in candidates.items():
        image = _process_image(win_pid)
        if not image or not _matches_exact_game(image, root, wanted):
            continue
        unowned = [hwnd for hwnd, owned in windows if not owned]
        targets = unowned or [hwnd for hwnd, _owned in windows]
        for hwnd in targets:
            # Re-verify HWND ownership and image immediately before posting WM_CLOSE.
            if _window_pid(hwnd) != win_pid:
                continue
            live_image = _process_image(win_pid)
            if not live_image or not _matches_exact_game(live_image, root, wanted):
                continue
            if _post_close(hwnd):
                closed += 1
    return closed


class GameWatch:
    """Call poll() from the UI loop; callbacks run on that same thread."""

    def __init__(self, folder: str | PathLike[str], started: Callable[[str], None],
                 ended: Callable[[], None], exe: str | PathLike[str] | None = None,
                 initial_running: bool = False):
        self.folder = folder
        self._exe = exe
        self._started = started
        self._ended = ended
        self._next_poll = float('-inf')
        self._running = bool(initial_running)

    @property
    def running(self) -> bool:
        return self._running

    def poll(self) -> None:
        now = time.monotonic()
        if now < self._next_poll:
            return
        self._next_poll = now + 1.0
        exe = running_game(self.folder, self._exe)
        was_running = self._running
        self._running = exe is not None
        if exe is not None and not was_running:
            self._started(exe)
        elif exe is None and was_running:
            self._ended()
