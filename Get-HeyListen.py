#!/usr/bin/env python3
"""
HeyListen Get - one-click installer for the prebuilt game (or HeyListen + the game dump).

  * Reads a manifest (sources.json, or the same file at a URL) that lists where the game
    comes from. Edit that manifest and every run picks the new links up - no code change.
  * Fetches the latest HeyListen release from GitHub and keeps HeyListenBuilder.exe current.
  * Downloads the Ocarina of Time (Switch 2) .nup, resumable, size/hash verified.
  * Seeds a Performance preset (DLSS on NVIDIA RTX + DLSS builds, else FSR 3.1) and can launch the Builder.

Usage:
  python Get-HeyListen.py
  python Get-HeyListen.py --manifest https://raw.githubusercontent.com/OWNER/REPO/main/sources.json
  python Get-HeyListen.py --builder-only | --game-only | --skip-hash | --force
  python Get-HeyListen.py --out D:\\Games --game-dir D:\\Games\\HeyListen --no-launch

Requires: pip install curl_cffi
"""
from __future__ import annotations

import argparse
import atexit
import contextlib
import errno
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import traceback
import zipfile
import threading
from concurrent.futures import ThreadPoolExecutor
from collections import deque
from pathlib import Path
from urllib.parse import quote, urlparse

try:
    import build_flags
except ModuleNotFoundError as exc:
    if exc.name != 'build_flags' or getattr(sys, 'frozen', False):
        raise
    from types import SimpleNamespace
    build_flags = SimpleNamespace(QA_SEAMS=True)

try:
    from curl_cffi import requests
except ImportError:
    sys.exit("Missing dependency. Run:  python -m pip install curl_cffi")

HERE = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
APP_VERSION = "1.6.9"
MAX_UPDATE_BYTES = 150 * 1024 * 1024
MANIFEST_LOCAL = HERE / "sources.json"
MANIFEST_CACHE = HERE / "sources.cache.json"
VERSION_FILE = HERE / "VERSION"
UPDATE_ASSET = "HeyListen-Get.zip"
SUMS_ASSET = "SHA256SUMS.txt"

import game_settings  # noqa: E402  the one reader/writer of hookshot_options.ini (keys as the engine reads them)

PRESET_DLSS = game_settings.PRESET_DLSS  # dlss=1 NVIDIA DLSS, resolution=4 (50%), nvidia_reflex=1, ...
PRESET_FSR = game_settings.PRESET_FSR    # dlss=3 AMD FSR 3.1 (every build), nvidia_reflex=0 (NVIDIA-only)
PRESET = PRESET_DLSS    # kept for backwards compatibility
DLSS_DLL = Path("ngx") / "nvngx_dlss.dll"
GPU_QUERY = ("Get-CimInstance Win32_VideoController | Sort-Object AdapterRAM -Descending | "
             "Select-Object -First 1 -ExpandProperty Name")


LOG_NAME = "HeyListen-Get.log"
LOG_PATH: Path | None = None
LOG_MAX = 2 * 1024 * 1024


def log(msg: str, echo: bool = False) -> None:
    """Technical record: appended to the log file only (never the screen)."""
    line = time.strftime("[%H:%M:%S] ") + str(msg)
    if LOG_PATH is not None:
        try:
            with open(LOG_PATH, "a", encoding="utf-8", errors="replace") as f:
                f.write(line + "\n")
        except OSError:
            pass
    if echo:
        say(str(msg))


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ---------------------------------------------------------------- ui
# Screen output for people; log() above is the technical record (file only).
_ANSI = False
_PLAIN = False
_OPEN_STEP = False
_QUICKEDIT_SAVED: int | None = None
STEP_COL = 34
TONES = {"green": "32", "yellow": "33", "red": "31", "bold": "1", "dim": "2"}


def _ascii(text: str) -> str:
    text = (str(text).replace("\u2014", "-").replace("\u2013", "-").replace("\u2026", "...")
            .replace("\u201c", '"').replace("\u201d", '"').replace("\u2019", "'").replace("\u2018", "'"))
    return text.encode("ascii", "replace").decode("ascii")


def _paint(text: str, tone: str | None) -> str:
    if tone and _ANSI and tone in TONES:
        return f"\x1b[{TONES[tone]}m{text}\x1b[0m"
    return text


def _write(text: str) -> None:
    if sys.stdout is None:
        return
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except (OSError, ValueError):
        pass


def _end_step() -> None:
    global _OPEN_STEP
    if _OPEN_STEP:
        _OPEN_STEP = False
        _write("\n")


def say(text: str = "", tone: str | None = None) -> None:
    _end_step()
    _write("  " + _paint(_ascii(text), tone) + "\n" if text else "\n")


def warn(text: str) -> None:
    say(text, "yellow")


def step(label: str) -> None:
    global _OPEN_STEP
    _end_step()
    _write("  " + _ascii(label).ljust(STEP_COL - 2, ".") + " ")
    _OPEN_STEP = True
    log(f"step: {label}")


def done(status: str, tone: str | None = "green") -> None:
    global _OPEN_STEP
    if not _OPEN_STEP:
        say(status, tone)
        return
    _OPEN_STEP = False
    _write(_paint(_ascii(status), tone) + "\n")
    log(f"  -> {status}")


def term_width() -> int:
    try:
        return max(20, shutil.get_terminal_size((80, 24)).columns)
    except (OSError, ValueError):
        return 80


def is_tty() -> bool:
    try:
        return sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def set_title(text: str) -> None:
    if os.name != "nt" or not is_tty():
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW(_ascii(text))
    except Exception:  # noqa: BLE001
        pass


def fmt_eta(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class Progress:
    """One redrawn line: bar, percent, sizes, smoothed speed and ETA."""

    def __init__(self, label: str, total: int | None, base: int = 0, tty: bool | None = None,
                 width: int | None = None, clock=time.monotonic):
        self.label = label
        self.total = total or 0
        self.done = base
        self.clock = clock
        self.t0 = clock()
        self.last_draw = -1.0
        self.last_t = self.t0
        self.last_done = base
        self.speed = 0.0
        self.tty = is_tty() if tty is None else tty
        self.width = width
        self.prev_len = 0
        self.next_pct = 10
        self.lock = threading.Lock()
        self.finished = False

    def add(self, amount: int) -> None:
        with self.lock:
            self._set(self.done + amount)

    def update(self, done: int) -> None:
        with self.lock:
            self._set(done)

    def _set(self, done: int) -> None:
        self.done = done
        now = self.clock()
        dt = now - self.last_t
        if dt >= 0.25:
            inst = (self.done - self.last_done) / dt
            self.speed = inst if self.speed == 0 else 0.2 * inst + 0.8 * self.speed
            self.last_t, self.last_done = now, self.done
        if self.tty:
            if now - self.last_draw >= 0.25:
                self.last_draw = now
                self._draw(self.render(now))
        elif os.environ.get('SILVER_LANTERN_GUI_PROGRESS') == '1':
            # Machine-readable feed for the launcher window: bytes, total and smoothed speed.
            if now - self.last_draw >= 0.5 or (self.total and self.done >= self.total):
                self.last_draw = now
                _write(f"HLPROGRESS phase={self.label.replace(' ', '_')} done={self.done} total={self.total} speed={int(self.speed)}\n")
        elif self.total:
            pct = self.done * 100 // self.total
            if pct >= self.next_pct:
                self.next_pct = (pct // 10 + 1) * 10
                _end_step()
                _write(f"  {self.label}: {pct}% ({human(self.done)} of {human(self.total)})\n")
        if self.total:
            set_title(f"Silver Lantern - {self.done * 100 // self.total}% downloaded")

    def render(self, now: float | None = None) -> str:
        now = self.clock() if now is None else now
        width = (self.width or term_width()) - 1
        pct = f"{self.done * 100 // self.total:3d}%" if self.total else "  ?%"
        size = f"{human(self.done)} / {human(self.total)}" if self.total else human(self.done)
        speed = f"{human(self.speed)}/s" if self.speed else ""
        if now - self.t0 < 3 or not self.speed or not self.total:
            eta = "--:-- left"
        else:
            eta = fmt_eta((self.total - self.done) / self.speed) + " left"
        fields = {"size": size, "speed": speed}
        bar_w = 30
        for drop in (None, "bar", "speed", "size"):
            if drop == "bar":
                bar_w = 0
            elif drop:
                fields[drop] = ""
            bar = ""
            if bar_w and self.total:
                fill = min(bar_w, self.done * bar_w // self.total)
                bar = "[" + "#" * fill + "-" * (bar_w - fill) + "]"
            text = "  " + "  ".join(x for x in (bar, pct, fields["size"], fields["speed"], eta) if x)
            if len(text) <= width:
                return text
        return text[:max(0, width)]

    def _draw(self, text: str) -> None:
        global _OPEN_STEP
        if _OPEN_STEP:
            _OPEN_STEP = False
            _write("\n")
        if _ANSI:
            _write("\r" + text + "\x1b[K")
        else:
            _write("\r" + text.ljust(self.prev_len))
        self.prev_len = len(text)

    def note(self, text: str) -> None:
        log(f"  {self.label}: {text}")
        if self.tty:
            self._draw(("  " + _ascii(text))[: (self.width or term_width()) - 1])

    def finish(self) -> None:
        with self.lock:
            if self.finished:
                return
            self.finished = True
            if self.tty and self.prev_len:
                if _ANSI:
                    _write("\r\x1b[K")
                else:
                    _write("\r" + " " * self.prev_len + "\r")
            set_title("Silver Lantern")


def _log_location() -> Path:
    for folder in (HERE, Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SilverLantern"):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            p = folder / LOG_NAME
            with open(p, "a", encoding="utf-8"):
                pass
            return p
        except OSError:
            continue
    return HERE / LOG_NAME


def _trim_log(p: Path) -> None:
    try:
        if p.stat().st_size > LOG_MAX:
            with open(p, "rb") as f:
                f.seek(-LOG_MAX, os.SEEK_END)
                tail = f.read()
            p.write_bytes(tail[tail.find(b"\n") + 1:])
    except OSError:
        pass


def _enable_vt() -> bool:
    if os.name != "nt":
        return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not k.GetConsoleMode(h, ctypes.byref(mode)):
            return False
        return bool(k.SetConsoleMode(h, mode.value | 0x4))
    except Exception:  # noqa: BLE001
        return False


def _quickedit_off() -> None:
    """Clicking the window must not freeze the download (QuickEdit pauses all output)."""
    global _QUICKEDIT_SAVED
    if os.name != "nt" or _QUICKEDIT_SAVED is not None:
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-10)
        mode = ctypes.c_uint32()
        if k.GetConsoleMode(h, ctypes.byref(mode)):
            _QUICKEDIT_SAVED = mode.value
            k.SetConsoleMode(h, (mode.value | 0x80) & ~0x40)
            atexit.register(_quickedit_restore)
    except Exception:  # noqa: BLE001
        pass


def _quickedit_restore() -> None:
    global _QUICKEDIT_SAVED
    if _QUICKEDIT_SAVED is None:
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.SetConsoleMode(k.GetStdHandle(-10), _QUICKEDIT_SAVED)
    except Exception:  # noqa: BLE001
        pass
    _QUICKEDIT_SAVED = None


def ui_init(plain: bool = False) -> None:
    global _ANSI, _PLAIN, LOG_PATH
    _PLAIN = plain
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    LOG_PATH = _log_location()
    _trim_log(LOG_PATH)
    log(f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} v{APP_VERSION} argv={sys.argv[1:]!r} =====")
    tty = is_tty()
    _ANSI = bool(tty and not plain and not os.environ.get("NO_COLOR") and _enable_vt())
    if tty:
        _quickedit_off()
    set_title("Silver Lantern")


def stdin_is_tty() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def pause(no_pause: bool = False) -> bool:
    """Keep a double-clicked window open. Returns True if it waited."""
    _end_step()
    _quickedit_restore()
    if no_pause or not stdin_is_tty():
        return False
    try:
        input("\n  Press Enter to close this window.")
    except (EOFError, KeyboardInterrupt):
        pass
    return True


class UserError(Exception):
    """A problem explained for people: what happened and what to do."""

    def __init__(self, problem: str, action: str, short: str = "", resumable_bytes: int = 0):
        super().__init__(problem)
        self.problem = problem
        self.action = action
        self.short = short
        self.resumable_bytes = resumable_bytes


NET_CURL_CODES = {6, 7, 28, 35, 52, 56}


def _http_status(exc: BaseException) -> int | None:
    resp = getattr(exc, "response", None)
    code = getattr(resp, "status_code", None) or getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    m = re.search(r"\bHTTP(?: Error)?\s*([1-5][0-9]{2})\b", str(exc))
    return int(m.group(1)) if m else None


def _curl_code(exc: BaseException) -> int | None:
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    m = re.search(r"curl: \((\d+)\)", str(exc))
    return int(m.group(1)) if m else None


def _is_network(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    names = {c.__name__ for c in type(exc).__mro__}
    if names & {"ConnectionError", "Timeout", "ConnectTimeout", "ReadTimeout", "DNSError"}:
        return True
    return _curl_code(exc) in NET_CURL_CODES


def _is_local_disk_error(exc: BaseException) -> bool:
    """Disk full / locked file: retrying the download can't help."""
    if not isinstance(exc, OSError) or _is_network(exc):
        return False
    winerror = getattr(exc, "winerror", None)
    return (winerror in (112, 39, 32, 33, 5) or exc.errno == errno.ENOSPC
            or isinstance(exc, PermissionError))


def friendly(exc: BaseException, resumable_bytes: int = 0) -> UserError:
    """Map a raw exception to a UserError (problem / what to do / short form)."""
    if isinstance(exc, UserError):
        return exc
    resumes = " It resumes where it left off." if resumable_bytes else ""
    if resumable_bytes:
        resumes = f" It picks up where it left off ({human(resumable_bytes)} is already saved)."
    if isinstance(exc, KeyboardInterrupt):
        return UserError("Stopped.", "Run this again any time. It picks up where it left off.", "stopped")
    winerror = getattr(exc, "winerror", None)
    if isinstance(exc, OSError) and (winerror in (112, 39) or exc.errno == errno.ENOSPC):
        return UserError("The disk ran out of space.", "Free up space and run this again." + resumes,
                         "disk full", resumable_bytes)
    if isinstance(exc, PermissionError) or (isinstance(exc, OSError) and winerror in (32, 33, 5)):
        name = Path(getattr(exc, "filename", None) or "a file").name
        return UserError(f"Windows wouldn't let this tool use {name}. Usually antivirus is still scanning it.",
                         "Wait a minute and run this again. If it keeps happening, allow "
                         "SilverLantern.exe in your antivirus.", "file in use", resumable_bytes)
    status = _http_status(exc)
    if status in (403, 404, 410) or "no download link on" in str(exc):
        return UserError("All download links are currently unavailable.",
                         "Try again later. The links update automatically once the tool's author fixes them.",
                         "link no longer works", resumable_bytes)
    if status is not None and (status == 429 or 500 <= status < 600):
        return UserError("The download site is busy right now.", "Wait about 10 minutes and run this again.",
                         "the site is busy", resumable_bytes)
    if _is_network(exc):
        return UserError("The internet connection was lost (or isn't working).",
                         "Check your connection and run this again." + (resumes or ""),
                         "no connection", resumable_bytes)
    text = str(exc).lower()
    if "builder checksum" in text or "has no " in text:
        return UserError("The HeyListen Builder download didn't check out.",
                         "Run this again. If it repeats, tell the person who sent you this tool.", "bad download")
    if "checksum mismatch" in text or "sha-256 mismatch" in text or "size mismatch" in text:
        return UserError("The download was damaged and has been deleted.",
                         "Run this again to download a fresh copy.", "damaged download")
    return UserError("Something unexpected went wrong.",
                     "Run this again. If it repeats, send the log file to the person who gave you this tool.",
                     "unexpected error", resumable_bytes)


def show_error(err: UserError) -> None:
    _end_step()
    wrap = lambda t: textwrap.fill(_ascii(t), 74, initial_indent="  ", subsequent_indent="  ")
    _write("\n  " + _paint("Problem:", "red") + " " + wrap(err.problem).lstrip() + "\n\n")
    _write(wrap("What to do: " + err.action) + "\n\n")
    if LOG_PATH is not None:
        say(f"Details were saved to: {LOG_PATH}", "dim")


class ParentExited(BaseException):
    """Cooperative stop after the GUI that started this child exits."""


_parent_handle = None
_parent_pid = None
_parent_initialized = False


def init_parent_monitor():
    global _parent_handle, _parent_pid, _parent_initialized
    if _parent_initialized:
        return
    _parent_initialized = True
    value = os.environ.get('SILVER_LANTERN_PARENT_PID')
    if not value:
        return
    _parent_pid = int(value)
    if _parent_pid <= 0:
        raise ValueError('invalid parent PID')
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        _parent_handle = kernel.OpenProcess(0x00100000 | 0x1000, False, _parent_pid)
        if not _parent_handle:
            raise ParentExited()
        try:
            import parent_identity
            expected = int(os.environ['SILVER_LANTERN_PARENT_CREATION_TIME'])
            if expected <= 0 or parent_identity.creation_time(_parent_handle) != expected:
                raise ParentExited()
        except (KeyError, ValueError, OSError, ParentExited):
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(_parent_handle)
            _parent_handle = None
            raise ParentExited() from None


def check_parent_alive():
    init_parent_monitor()
    if _parent_pid is None:
        return
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        if kernel.WaitForSingleObject(_parent_handle, 0) != 0x102:
            raise ParentExited()
    else:
        try:
            os.kill(_parent_pid, 0)
        except ProcessLookupError:
            raise ParentExited() from None


def _retry_locked(operation, budget=60.0):
    """Retry a file operation while Windows or antivirus holds the file."""
    delays = (1, 2, 5, 10)
    deadline = time.monotonic() + budget
    attempt = 0
    while True:
        check_parent_alive()
        try:
            return operation()
        except OSError as exc:
            if not isinstance(exc, PermissionError) and getattr(exc, 'winerror', None) not in (32, 33):
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            say('Waiting for Windows to release a file...')
            time.sleep(min(delays[min(attempt, len(delays) - 1)], remaining))
            attempt += 1


def _replace_with_retry(src: Path, dst: Path, budget: float = 60.0) -> None:
    _retry_locked(lambda: Path(src).replace(dst), budget)


def _open_with_retry(path: Path, mode: str):
    def open_if_alive():
        check_parent_alive()
        return path.open(mode)
    return _retry_locked(open_if_alive)


@contextlib.contextmanager
def keep_awake():
    """Stop the PC sleeping mid-download (ES_CONTINUOUS | ES_SYSTEM_REQUIRED)."""
    k = None
    if os.name == "nt":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.SetThreadExecutionState(0x80000000 | 0x00000001)
        except Exception:  # noqa: BLE001
            k = None
    try:
        yield
    finally:
        if k is not None:
            try:
                k.SetThreadExecutionState(0x80000000)
            except Exception:  # noqa: BLE001
                pass


def check_disk_space(folder: Path, need: int, label: str = "Free space") -> None:
    """Raise UserError when `folder`'s drive can't hold `need` more bytes."""
    probe = Path(folder)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        free = shutil.disk_usage(probe).free
    except OSError as e:
        log(f"disk space check skipped: {e}")
        return
    drive = (probe.drive or str(probe.anchor) or str(probe)).rstrip("\\/")
    step(f"{label} on {drive}")
    if free < need:
        done("not enough", "yellow")
        gap = need - free
        raise UserError(
            f"Not enough free space on {drive}. The download needs {human(need)}, and {human(free)} is free.",
            f"Free up about {human(gap)} (empty the Recycle Bin, or delete old files from Downloads), "
            "then run this again.", "disk full")
    done(f"{human(free)} free, {human(need)} needed", None)


def session() -> "requests.Session":
    return requests.Session(impersonate="chrome", timeout=(10, 60))


def stream_session(source=None) -> "requests.Session":
    from curl_cffi import CurlOpt
    options = {}
    if source is not None:
        options = {key: getattr(source, key) for key in ('headers', 'cookies', 'auth', 'proxies', 'verify', 'trust_env', 'impersonate') if hasattr(source, key)}
    options.setdefault('impersonate', 'chrome')
    return requests.Session(**options, timeout=(10, 30), curl_options={
        CurlOpt.CONNECTTIMEOUT_MS: 10000, CurlOpt.TIMEOUT_MS: 0,
        CurlOpt.LOW_SPEED_LIMIT: 1024, CurlOpt.LOW_SPEED_TIME: 30})


def checksum_session(source=None):
    from curl_cffi import CurlOpt
    options = {}
    if source is not None:
        options = {key: getattr(source, key) for key in ('headers', 'cookies', 'auth', 'proxies', 'verify', 'trust_env', 'impersonate') if hasattr(source, key)}
    options.setdefault('impersonate', 'chrome')
    return requests.Session(**options, curl_options={CurlOpt.CONNECTTIMEOUT_MS: 10000,
                            CurlOpt.TIMEOUT_MS: 60000, CurlOpt.MAXFILESIZE_LARGE: 65536})


def metadata_get(client, url, **kwargs):
    kwargs['timeout'] = (10, 60)
    for attempt in range(2):
        check_parent_alive()
        try:
            return client.get(url, **kwargs)
        except Exception:
            if attempt:
                raise
            say('Connection stalled — retrying…')


# ---------------------------------------------------------------- manifest
_MANIFEST_ORIGIN: str | None = None     # "url", "file" or "cache" after load_manifest()


def load_manifest(manifest_arg: str | None) -> dict:
    """Local file, a URL, or the last good copy. A dead link never stops the run."""
    candidates: list[tuple[str, str]] = []       # (label, kind)
    if manifest_arg:
        kind = "url" if manifest_arg.startswith(("http://", "https://")) else "file"
        candidates.append((manifest_arg, kind))
    if not manifest_arg and not MANIFEST_LOCAL.exists():
        candidates.append(('https://raw.githubusercontent.com/cputroll/Silver-Lantern/main/sources.json', 'url'))
    if MANIFEST_LOCAL.exists():
        try:
            local = json.loads(MANIFEST_LOCAL.read_text(encoding="utf-8-sig"))
            url = local.get("tool", {}).get("manifest_url")
            if url:
                candidates.insert(0 if not manifest_arg else 1, (url, "url"))
        except (OSError, ValueError, AttributeError, TypeError) as e:
            log(f"  local manifest metadata unusable: {e}")
        candidates.append((str(MANIFEST_LOCAL), "file"))
    if MANIFEST_CACHE.exists():
        candidates.append((str(MANIFEST_CACHE), "cache"))

    last_err = None
    for label, kind in candidates:
        try:
            if kind == "file":
                data = json.loads(Path(label).read_text(encoding="utf-8-sig"))
            elif kind == "cache":
                data = json.loads(Path(label).read_text(encoding="utf-8-sig"))
            else:
                r = metadata_get(session(), label, headers={"accept": "application/json"})
                r.raise_for_status()
                data = r.json()
            if "game" not in data or "builder" not in data:
                raise ValueError("manifest lacks 'game' or 'builder'")
            try:
                MANIFEST_CACHE.write_text(json.dumps(data, indent=2), encoding="utf-8")
            except OSError as e:
                log(f"  manifest cache not updated: {e}")
            if kind == "url":
                log(f"Manifest: {label} (v{data.get('tool', {}).get('version', '?')})")
            global _MANIFEST_ORIGIN
            _MANIFEST_ORIGIN = kind
            return data
        except Exception as e:  # noqa: BLE001
            last_err = e
            log(f"  manifest {label} unusable: {e}")
    raise RuntimeError(f"no usable manifest ({last_err})")


# ---------------------------------------------------------------- builder
def latest_release(s: "requests.Session", repo: str) -> dict:
    r = metadata_get(s, f"https://api.github.com/repos/{repo}/releases/latest",
                     headers={"accept": "application/vnd.github+json"})
    r.raise_for_status()
    return r.json()


def download_builder(s: "requests.Session", man: dict, out: Path, force: bool = False) -> Path:
    repo = man["builder"]["repo"]
    asset_name = man["builder"].get("asset", "HeyListenBuilder.exe")
    sums_name = man["builder"].get("sums_asset", "SHA256SUMS.txt")
    rel = latest_release(s, repo)
    tag = rel["tag_name"]
    state_file = out / ".heylisten-state.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    builder = out / asset_name

    assets = {a["name"]: a for a in rel["assets"]}
    if asset_name not in assets:
        raise RuntimeError(f"{repo} {tag} has no {asset_name}")

    if sums_name not in assets:
        raise RuntimeError(f"{repo} {tag} has no {sums_name}")
    response = metadata_get(s, assets[sums_name]["browser_download_url"])
    response.raise_for_status()
    matches = []
    for line in response.text.splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+[*]?(.+)", line.strip())
        if match and match.group(2) == asset_name:
            matches.append(match.group(1).lower())
    if len(matches) != 1:
        raise ValueError("missing or ambiguous builder checksum")
    want = matches[0]
    have = sha256(builder) if builder.exists() else None

    if not force and state.get("release") == tag and have and have == want:
        log(f"Builder already current: {tag}")
        step("HeyListen Builder")
        done(f"up to date ({tag})")
        return builder

    size = assets[asset_name]["size"]
    log(f"Downloading {asset_name} ({tag}, {human(size)})")
    step("HeyListen Builder")
    done(f"downloading {human(size)}", None)
    tmp = builder.with_suffix(builder.suffix + ".part")
    bar = Progress("HeyListen Builder", size)
    try:
        stream_to(s, assets[asset_name]["browser_download_url"], tmp, expect=size, progress=bar.add)
    finally:
        bar.finish()
    got = sha256(tmp)
    if want and got != want:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"builder checksum mismatch: {got} != {want}")
    _replace_with_retry(tmp, builder)
    state.update({"release": tag, "builder_sha256": got, "updated": time.strftime("%Y-%m-%dT%H:%M:%S")})
    state_file.write_text(json.dumps(state, indent=2))
    log(f"Builder ready and checksum-verified: {tag}")
    step("HeyListen Builder")
    done(f"ready ({tag})")
    return builder


# ---------------------------------------------------------------- game
def buzz_download_url(s: "requests.Session", page_url: str) -> tuple[str, str]:
    page = metadata_get(s, page_url, headers={"accept": "text/html", "referer": "https://buzzheavier.com/"})
    page.raise_for_status()
    ident = page_url.rstrip("/").rsplit("/", 1)[-1]
    m = re.search(r'hx-get="(/' + re.escape(ident) + r'/download\?t=[^"]+)"', page.text)
    if not m:
        m = re.search(r'hx-get="(/[A-Za-z0-9]+/download\?t=[^"]+)"', page.text)
    if not m:
        raise RuntimeError("no download link on " + page_url)
    return "https://buzzheavier.com" + m.group(1).replace("&amp;", "&"), page_url


def source_urls(s: "requests.Session", src: dict) -> tuple[list[tuple[str, str | None]], int | None]:
    """(url, referer) pairs to fetch in order, plus the expected total size."""
    kind = src.get("kind")
    if src.get("parts"):
        urls = []
        for p in src["parts"]:
            if kind == "buzzheavier":
                urls.append(buzz_download_url(s, p))
            else:
                urls.append((p, None))
        return urls, src.get("size")
    if kind == "buzzheavier":
        return [buzz_download_url(s, src["url"])], src.get("size")
    return [(src["url"], None)], src.get("size")


def content_range_start(r) -> int | None:
    """First byte offset of a 206 response's Content-Range, or None if absent/invalid."""
    m = re.fullmatch(r"\s*bytes\s+([0-9]+)-[0-9]+/(?:[0-9]+|\*)\s*", r.headers.get("content-range") or "")
    return int(m.group(1)) if m else None


def stream_to(s: "requests.Session", url: str, dest: Path, expect: int | None = None,
              referer: str | None = None, max_retries: int = 2, part: Path | None = None,
              progress=None, quiet: bool = False) -> int:
    """Resumable streaming download with an isolated low-speed policy."""
    if isinstance(s, requests.Session):
        with stream_session(s) as client:
            return _stream_to(client, url, dest, expect, referer, max_retries, part, progress, quiet)
    return _stream_to(s, url, dest, expect, referer, max_retries, part, progress, quiet)


def _stream_to(s, url, dest, expect, referer, max_retries, part, progress, quiet):
    """Resumable streaming download into dest (via `part`, default dest + '.part')."""
    part = part or Path(str(dest) + ".part")
    for attempt in range(1, max_retries + 1):
        check_parent_alive()
        have = part.stat().st_size if part.exists() else 0
        if expect is not None and have > expect:
            part.unlink()
            have = 0
        headers = {"accept": "*/*", "referer": referer or "https://buzzheavier.com/"}
        if have:
            headers["range"] = f"bytes={have}-"
        try:
            r = s.get(url, headers=headers, stream=True, timeout=(10, 30))
            if r.status_code == 416 and expect and have >= expect:
                r.close()
                break
            r.raise_for_status()
            if have and r.status_code == 200:      # server ignored Range
                if not quiet:
                    warn("Starting over (the site didn't allow resuming)")
                have = 0
            elif have and r.status_code == 206 and content_range_start(r) == have and not quiet:
                say(f"Resuming at {human(have)}" + (f" ({have * 100 // expect}%)" if expect else ""))
            elif have and content_range_start(r) != have:
                # Never append bytes that do not continue the file on disk.
                r.close()
                part.unlink(missing_ok=True)
                raise ValueError(f"resume offset mismatch (have {have}), restarting part from zero")
            mode = "ab" if have else "wb"
            total = expect
            if not total:
                cr = r.headers.get("content-range", "")
                if "/" in cr:
                    total = int(cr.split("/")[-1])
                elif r.headers.get("content-length"):
                    total = int(r.headers["content-length"]) + have
            done = have
            t0 = time.time()
            last = 0.0
            try:
                with open(part, mode) as f:
                    for chunk in r.iter_content(1024 * 1024):
                        check_parent_alive()
                        if expect is not None and done + len(chunk) > expect:
                            raise ValueError("download exceeds expected size")
                        f.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(len(chunk))
                        now = time.time()
                        if now - last >= 60:
                            last = now
                            pct = f"{done * 100 / total:5.1f}%" if total else "  ?  "
                            rate = (done - have) / max(now - t0, 0.001)
                            log(f"  {pct}  {human(done)} / {human(total) if total else '?'}  ({human(rate)}/s)")
            finally:
                r.close()
            if total and done < total and attempt < max_retries:
                log(f"  short read ({done}/{total}), resuming...")
                say('Connection stalled — retrying…')
                continue
            break
        except Exception as e:  # noqa: BLE001
            if _is_local_disk_error(e) or attempt == max_retries:
                raise   # a full disk or locked file never fixes itself by retrying
            log(f"  network hiccup ({type(e).__name__}: {e}), retry {attempt}/{max_retries} in 3 s")
            say('Connection stalled — retrying…')
            note = getattr(getattr(progress, "__self__", None), "note", None)
            if note:
                note(f"connection dropped, retrying ({attempt} of {max_retries})...")
            time.sleep(3)
    if expect is not None and part.stat().st_size != expect:
        raise RuntimeError(f"size mismatch: {part.stat().st_size} != {expect}")
    _replace_with_retry(part, dest)
    return dest.stat().st_size


PARALLEL_THRESHOLD = 64 * 1024 * 1024
CHUNK_MIN = 16 * 1024 * 1024


def download_streams():
    try:
        return max(1, min(16, int(os.environ.get('SILVER_LANTERN_DL_STREAMS', '6'))))
    except ValueError:
        return 6


def assembly_workers():
    try:
        return max(1, min(8, int(os.environ.get('SILVER_LANTERN_ASSEMBLY_WORKERS', '4'))))
    except ValueError:
        return 4


def chunk_layout(size, streams, chunk_min=CHUNK_MIN):
    width = max((size + streams - 1) // streams, chunk_min)
    return [[start, min(size, start + width)] for start in range(0, size, width)]


def chunk_state(dest, size, streams, chunk_min=CHUNK_MIN):
    part = Path(str(dest) + '.part')
    side = Path(str(dest) + '.part.json')
    bounds = chunk_layout(size, streams, chunk_min)
    try:
        state = json.loads(side.read_text(encoding='utf-8'))
        if (state['size'] != size or state['bounds'] != bounds or
                len(state['done']) != len(bounds) or
                not all(type(x) is bool for x in state['done']) or
                part.stat().st_size != size):
            raise ValueError('invalid chunk layout')
        hashes = state.get('hashes', [])
        with part.open('rb') as stream:
            for i, ((start, end), done) in enumerate(zip(bounds, state['done'])):
                if not done:
                    continue
                if i >= len(hashes) or not isinstance(hashes[i], str):
                    state['done'][i] = False
                    continue
                digest = hashlib.sha256()
                stream.seek(start)
                remaining = end - start
                while remaining:
                    check_parent_alive()
                    data = stream.read(min(1024 * 1024, remaining))
                    if not data:
                        break
                    digest.update(data)
                    remaining -= len(data)
                if remaining or digest.hexdigest() != hashes[i]:
                    state['done'][i] = False
        state['hashes'] = (hashes + [None] * len(bounds))[:len(bounds)]
        return state
    except (OSError, ValueError, KeyError, TypeError):
        return {'size': size, 'bounds': bounds, 'done': [False] * len(bounds), 'hashes': [None] * len(bounds)}


def parallel_download(session_factory, url, dest, size, expected_sha, progress, streams, chunk_min=CHUNK_MIN):
    """Download into disjoint offsets; only fully completed regions are resumable."""
    part = Path(str(dest) + '.part')
    side = Path(str(dest) + '.part.json')
    state = chunk_state(dest, size, streams, chunk_min)
    bounds = state['bounds']
    completed = sum(b - a for (a, b), done in zip(bounds, state['done']) if done)
    lock = threading.Lock()
    local = threading.local()

    def save():
        temp = Path(str(side) + '.tmp')
        temp.write_text(json.dumps(state), encoding='utf-8')
        _replace_with_retry(temp, side)

    def worker(i, response=None, retries_used=0):
        start, end = bounds[i]
        for attempt in range(retries_used, 2):
            check_parent_alive()
            r = None
            pos = start
            try:
                if response is None:
                    if not hasattr(local, 'session'):
                        local.session = session_factory()
                    r = local.session.get(url, headers={'range': f'bytes={start}-{end-1}', 'accept': '*/*'}, stream=True, timeout=(10, 30))
                else:
                    r, response = response, None
                expected_range = f'bytes {start}-{end-1}/{size}'
                if r.status_code != 206 or (r.headers.get('content-range') or '').lower().strip() != expected_range:
                    raise ValueError('invalid chunk Content-Range')
                digest = hashlib.sha256()
                with part.open('r+b') as f:
                    f.seek(start)
                    for data in r.iter_content(1024 * 1024):
                        check_parent_alive()
                        if pos + len(data) > end:
                            raise ValueError('chunk exceeds expected size')
                        f.write(data)
                        digest.update(data)
                        pos += len(data)
                        progress.add(len(data))
                if pos != end:
                    raise ValueError('short chunk')
                with lock:
                    state['hashes'][i] = digest.hexdigest()
                    state['done'][i] = True
                    save()
                return
            except Exception as exc:
                if pos > start:
                    progress.add(start - pos)
                if attempt or _is_local_disk_error(exc):
                    raise
                say('Connection stalled — retrying…')
            finally:
                if r is not None:
                    r.close()

    pending = [i for i, done in enumerate(state['done']) if not done]
    if not pending:
        _replace_with_retry(part, dest)
        side.unlink(missing_ok=True)
        return
    # Probe before allocating or changing the resume state.
    first = pending[0]
    a, b = bounds[first]
    probe_session = session_factory()
    for attempt in range(2):
        check_parent_alive()
        try:
            probe = probe_session.get(url, headers={'range': f'bytes={a}-{b-1}', 'accept': '*/*'}, stream=True, timeout=(10, 30))
            break
        except Exception:
            if attempt:
                raise
            say('Connection stalled — retrying…')
    if probe.status_code == 200:
        probe.close()
        part.unlink(missing_ok=True)
        side.unlink(missing_ok=True)
        # Existing completed regions were excluded from the plan's total.
        remaining_credit = size - completed
        def credit(n):
            nonlocal remaining_credit
            amount = min(n, remaining_credit)
            remaining_credit -= amount
            progress.add(amount)
        stream_to(probe_session, url, dest, expect=size, progress=credit, quiet=True, max_retries=2 - attempt)
        return
    if probe.status_code != 206 or (probe.headers.get('content-range') or '').lower().strip() != f'bytes {a}-{b-1}/{size}':
        probe.close()
        raise ValueError('invalid chunk Content-Range')
    if not any(state['done']) or not part.exists() or part.stat().st_size != size or not side.exists():
        with part.open('wb') as f:
            f.truncate(size)
        state['done'] = [False] * len(bounds)
        save()
    with ThreadPoolExecutor(max_workers=max(1, streams - 1)) as pool:
        futures = [pool.submit(worker, i) for i in pending[1:]]
        worker(first, probe, attempt)
        for future in futures:
            future.result()
    _replace_with_retry(part, dest)
    side.unlink(missing_ok=True)


def source_part_tag(idx: int, src: dict) -> str:
    """Stable per-source suffix so partial files from different sources never mix."""
    ident = json.dumps([src.get("url"), src.get("parts"), src.get("size")], sort_keys=True)
    return f"s{idx}-{hashlib.sha256(ident.encode('utf-8')).hexdigest()[:10]}"


def download_game(s: "requests.Session", man: dict, out: Path, skip_hash: bool = False,
                  force: bool = False) -> Path:
    game = man["game"]
    dest = out / game["filename"]
    sizes = {src.get("size") for src in game["sources"] if src.get("size")}

    if dest.exists() and dest.stat().st_size in sizes and not force:
        log(f"Game already downloaded: {dest.name}")
        step("Game data")
        done("already downloaded")
    else:
        # Older runs shared one dest.part across sources; its origin is unknown.
        Path(str(dest) + ".part").unlink(missing_ok=True)
        first = game["sources"][0] if game["sources"] else {}
        if first.get("size"):
            existing = sum(p.stat().st_size for p in out.glob(dest.name + ".*.part") if p.is_file())
            extra = first["size"] if first.get("parts") else 0
            check_disk_space(out, max(0, first["size"] - existing) + extra + 1024 ** 3)
            say("(The built game needs more space later; keep extra room free.)", "dim")
        last_err = None
        prev_parts: list[Path] = []
        n_src = len(game["sources"])
        for idx, src in enumerate(game["sources"]):
            if idx and last_err is not None:
                warn(f"Copy {idx} didn't work ({friendly(last_err).short}). Trying copy {idx + 1} of {n_src}...")
            tag = source_part_tag(idx, src)
            for stale in prev_parts:            # never resume another source's bytes
                stale.unlink(missing_ok=True)
            prev_parts = [Path(str(dest) + f".{tag}.part")]
            try:
                urls, size = source_urls(s, src)
                log(f"Game source: {src.get('url') or src.get('parts')[0]}  ->  {human(size) if size else '?'}")
                step("Game data")
                done(f"downloading {human(size) if size else ''}"
                     + (f" (copy {idx + 1} of {n_src})" if n_src > 1 else ""), None)
                bar = Progress("Game data", size)
                if len(urls) == 1:
                    have0 = prev_parts[0].stat().st_size if prev_parts[0].exists() else 0
                    bar.done = have0 if size and have0 <= size else 0
                    try:
                        stream_to(s, urls[0][0], dest, expect=size, referer=urls[0][1], part=prev_parts[0],
                                  progress=bar.add)
                    finally:
                        bar.finish()
                else:   # parts joined in order
                    tmp = Path(str(dest) + ".join")
                    prev_parts.append(tmp)
                    with open(tmp, "wb") as f:
                        for i, (u, ref) in enumerate(urls, 1):
                            p = Path(str(dest) + f".p{i}")
                            pp = Path(str(dest) + f".{tag}.p{i}.part")
                            prev_parts.append(pp)
                            try:
                                stream_to(s, u, p, referer=ref, part=pp, progress=bar.add)
                            finally:
                                if i == len(urls):
                                    bar.finish()
                            log(f"  part {i}/{len(urls)} done ({human(p.stat().st_size)})")
                            with open(p, "rb") as pf:
                                while (b := pf.read(8 * 1024 * 1024)):
                                    f.write(b)
                            p.unlink()
                    if size and tmp.stat().st_size != size:
                        raise RuntimeError(f"joined size {tmp.stat().st_size} != {size}")
                    _replace_with_retry(tmp, dest)
                last_err = None
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                dest.unlink(missing_ok=True)
                log(f"  source failed: {e}")
        if last_err:
            raise last_err
        log(f"Game downloaded: {dest}")

    if not skip_hash:
        want = next((src["sha256"] for src in game["sources"]
                     if src.get("sha256") and src.get("size") == dest.stat().st_size), None)
        log("Verifying SHA-256 (a few minutes on a large file)...")
        step("Checking the download")
        tick = {"t": 0.0}

        def show(done_bytes, total):
            now = time.monotonic()
            if is_tty() and total and now - tick["t"] >= 0.25:
                tick["t"] = now
                _write(f"\r  {'Checking the download'.ljust(STEP_COL - 2, '.')} {done_bytes * 100 // total}%"
                       " (a few minutes)\x1b[K" if _ANSI else
                       f"\r  {'Checking the download'.ljust(STEP_COL - 2, '.')} {done_bytes * 100 // total}%   ")

        got = sha256(dest, progress=show)
        if is_tty():
            _write(f"\r  {'Checking the download'.ljust(STEP_COL - 2, '.')} " + " " * 20
                   + f"\r  {'Checking the download'.ljust(STEP_COL - 2, '.')} ")
        if want and got.lower() != want.lower():
            done("damaged", "yellow")
            dest.unlink(missing_ok=True)
            log(f"game checksum mismatch: {got}")
            raise UserError("The download was damaged and has been deleted.",
                            "Run this again to download a fresh copy.", "damaged download")
        done("OK")
        log("Checksum OK - this is the supported build." if want else f"Checksum (re-pack, no pinned hash): {got}")
    return dest


def is_dlss_gpu(name: str | None) -> bool:
    """True for an NVIDIA RTX adapter name (the only GPUs that run DLSS)."""
    n = (name or "").upper()
    return "NVIDIA" in n and "RTX" in n


def detect_gpu_name() -> str | None:
    """Name of the primary GPU (largest adapter RAM), or None if it can't be read."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", GPU_QUERY],
                       capture_output=True, text=True, timeout=30, creationflags=flags)
    name = (r.stdout or "").strip().splitlines()
    return name[0].strip() if r.returncode == 0 and name and name[0].strip() else None


def choose_preset(game_dir: Path) -> tuple[dict, str]:
    """Pick the DLSS or FSR 3.1 preset for this PC + build. Never raises."""
    try:
        gpu = detect_gpu_name()
    except Exception as e:
        return PRESET_FSR, f"GPU detection failed ({e.__class__.__name__}) -> FSR 3.1 Performance"
    if gpu is None:
        return PRESET_FSR, "GPU could not be identified -> FSR 3.1 Performance"
    try:
        has_dll = (Path(game_dir) / DLSS_DLL).is_file()
    except Exception:
        has_dll = False
    if is_dlss_gpu(gpu) and has_dll:
        return PRESET_DLSS, f"NVIDIA RTX with DLSS build ({gpu}) -> DLSS Performance"
    if is_dlss_gpu(gpu):
        return PRESET_FSR, f"No RTX/DLSS build ({gpu}, but {DLSS_DLL} missing) -> FSR 3.1 Performance"
    return PRESET_FSR, f"No RTX/DLSS build ({gpu}) -> FSR 3.1 Performance"


def apply_preset(game_dir: Path) -> Path:
    """Merge the auto-chosen preset into game_dir/hookshot_options.ini, keeping every line we do not own."""
    game_dir = Path(game_dir)
    preset, why = choose_preset(game_dir)
    log(why)
    # Atomic merge; drops the dead framegen/pace/reflex keys older presets wrote.
    ini = game_settings.write_values(game_dir, preset)
    label = "DLSS" if preset is PRESET_DLSS else "FSR 3.1"
    log(f"{label} preset written: {ini}")
    return ini


def sha256(p: Path, progress=False) -> str:
    """SHA-256 of a file. `progress`: True draws a bar; a callable gets (bytes_done, total)."""
    h = hashlib.sha256()
    total = p.stat().st_size if progress else 0
    bar = Progress("Checking", total) if progress is True else None
    callback = progress if callable(progress) else None
    done_bytes = 0
    try:
        with open(p, "rb") as f:
            for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
                check_parent_alive()
                h.update(block)
                if progress:
                    done_bytes += len(block)
                    if bar:
                        bar.update(done_bytes)
                    if callback:
                        callback(done_bytes, total)
    finally:
        if bar:
            bar.finish()
    return h.hexdigest()


# ---------------------------------------------------------------- self-update
def local_version(man: dict | None = None) -> str:
    # Baked into the executable, never derived from a remotely mutable manifest.
    return APP_VERSION


def version_key(tag: str) -> tuple:
    m = re.fullmatch(r"[vV]?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?", tag)
    if not m:
        raise ValueError("invalid semantic version")
    prerelease = m.group(4)
    parts = []
    if prerelease:
        for part in prerelease.split('.'):
            if part.isdigit():
                if len(part) > 1 and part.startswith('0'):
                    raise ValueError("invalid semantic version")
                parts.append((0, int(part)))
            else:
                parts.append((1, part))
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), 0 if prerelease else 1, tuple(parts))


def parse_update_checksum(text: str) -> str:
    matches = []
    for line in text.splitlines():
        match = re.fullmatch(r"([a-fA-F0-9]{64})[ \t]+\*?HeyListen-Get\.zip", line.strip())
        if match:
            matches.append(match.group(1).lower())
    if len(matches) != 1:
        raise ValueError("missing or ambiguous update checksum")
    return matches[0]


def stage_executable(zpath: Path, directory: Path, name: str) -> Path:
    # Never extract archive paths to disk. Reject suspicious paths even if unrelated.
    with zipfile.ZipFile(zpath) as archive:
        entries = archive.infolist()
        if len(entries) > 256:
            raise ValueError("too many archive entries")
        candidates = {}
        for entry in entries:
            path = entry.filename.replace('\\', '/')
            parts = path.split('/')
            if path.startswith('/') or any(part in ('.', '..') or ':' in part for part in parts):
                raise ValueError("unsafe archive path")
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("archive symlink rejected")
            if not entry.is_dir():
                candidates.setdefault(parts[-1].casefold(), []).append(entry)
        selected = None
        for candidate in (name, 'SilverLantern.exe', 'HeyListen-Get.exe'):
            matches = candidates.get(candidate.casefold(), [])
            if matches:
                if len(matches) != 1:
                    raise ValueError("release must contain exactly one bounded executable")
                selected = matches[0]
                break
        if selected is None or not 0 < selected.file_size <= MAX_UPDATE_BYTES:
            raise ValueError("release must contain exactly one bounded executable")
        dest = directory / 'replacement.exe'
        with archive.open(selected) as src, dest.open('xb') as out:
            remaining = MAX_UPDATE_BYTES
            while True:
                block = src.read(min(1024 * 1024, remaining + 1))
                if not block:
                    break
                remaining -= len(block)
                if remaining < 0:
                    raise ValueError("executable too large")
                out.write(block)
        return dest


def log_update_phase(phase: str, started: float) -> None:
    log(f'Self-update phase={phase} elapsed_ms={round((time.monotonic() - started) * 1000)}')


def replace_transactionally(target: Path, replacement: Path) -> None:
    backup = target.with_name(target.name + '.update-backup')
    if backup.exists():
        # An earlier update was interrupted mid-swap. Recover instead of refusing:
        # with the target present the backup is stale; without it, restore the backup.
        if target.exists():
            backup.unlink()
        else:
            _replace_with_retry(backup, target)
    # Copy onto the target filesystem first: os.replace is atomic only within
    # one filesystem, and the old entry point must remain until the swap.
    fd, name = tempfile.mkstemp(prefix=target.name + '.update-', suffix='.tmp', dir=target.parent)
    staged = Path(name)
    try:
        verify_started = time.monotonic()
        with os.fdopen(fd, 'wb') as out, replacement.open('rb') as src:
            while True:
                check_parent_alive()
                block = src.read(1024 * 1024)
                if not block:
                    break
                out.write(block)
            out.flush()
            os.fsync(out.fileno())
        def digest(path):
            h = hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    check_parent_alive()
                    h.update(block)
            return h.digest()
        if not staged.stat().st_size or staged.stat().st_size != replacement.stat().st_size or digest(staged) != digest(replacement):
            raise ValueError('staged update failed verification')
        log_update_phase('verification done', verify_started)
        swap_started = time.monotonic()
        _replace_with_retry(staged, target)
        log_update_phase('swap done', swap_started)
    finally:
        try:
            staged.unlink(missing_ok=True)
        except OSError as e:
            log(f'Update temporary file retained for cleanup: {e}')


def wait_for_parent(pid: int) -> None:
    if os.name != 'nt' or pid <= 0:
        raise RuntimeError("executable updates require a valid Windows parent process")
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # Process already exited.
            return
        raise ctypes.WinError(error)
    try:
        result = kernel.WaitForSingleObject(handle, 30000)
        if result == 0x102:
            raise TimeoutError("timed out waiting for parent process")
        if result != 0:
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(handle)


def cleanup_stage(stage: Path) -> None:
    # Windows locks the running helper; remove everything else, retaining it safely.
    for name in (UPDATE_ASSET, 'replacement.exe'):
        try:
            (stage / name).unlink(missing_ok=True)
        except OSError:
            pass


def _apply_update(target: Path, replacement: Path, parent: int, args: list[str]) -> None:
    handoff = os.environ.get('SILVER_LANTERN_UPDATE_SPAWN_MONOTONIC')
    if handoff:
        try:
            log_update_phase('helper startup', float(handoff))
        except ValueError:
            log('Self-update timing: invalid helper startup timestamp')
    wait_started = time.monotonic()
    wait_for_parent(parent)
    log_update_phase('parent-wait done', wait_started)
    replace_transactionally(target, replacement)
    try:
        relaunch_started = time.monotonic()
        if len(args) == 2 and args[0] == '--update-only-internal':
            log(f'Updated to {args[1]}. Run the tool again to continue.')
        else:
            subprocess.Popen([str(target), *args], cwd=str(target.parent))
        log_update_phase('relaunch', relaunch_started)
    finally:
        cleanup_stage(replacement.parent)


def fetch_update_checksum(s: "requests.Session", url: str) -> str:
    if isinstance(s, requests.Session):
        with checksum_session(s) as client:
            return _fetch_update_checksum(client, url)
    return _fetch_update_checksum(s, url)


def _fetch_update_checksum(s, url):
    for attempt in range(2):
        check_parent_alive()
        response = None
        try:
            response = s.get(url, stream=True, timeout=(10, 60))
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_content(8192):
                check_parent_alive()
                if len(data) + len(chunk) > 65536:
                    raise ValueError("checksum file too large")
                data.extend(chunk)
            return parse_update_checksum(data.decode('utf-8-sig'))
        except ValueError:
            raise
        except Exception:
            if attempt:
                raise
            say('Connection stalled — retrying…')
        finally:
            if response is not None:
                response.close()


APP_TAG_RE = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")


def latest_app_release(s: "requests.Session", repo: str) -> dict | None:
    """Highest published, non-prerelease release tagged exactly vX.Y.Z, or None.

    Other releases on the same repo (e.g. 'game-v1') must never break updates.
    """
    r = metadata_get(s, f"https://api.github.com/repos/{repo}/releases?per_page=30",
                     headers={"accept": "application/vnd.github+json"})
    r.raise_for_status()
    best, best_key = None, None
    for rel in r.json() or []:
        if not isinstance(rel, dict) or rel.get("draft") or rel.get("prerelease"):
            continue
        tag = rel.get("tag_name") or ""
        if not APP_TAG_RE.fullmatch(tag):
            continue
        key = version_key(tag)
        if best_key is None or key > best_key:
            best, best_key = rel, key
    return best


_self_update_started = False


def check_self_update(s: "requests.Session", man: dict, args: list[str],
                      local_zip: str | None = None) -> None:
    global _self_update_started
    if _self_update_started:
        return
    if not getattr(sys, "frozen", False):
        log("Self-update is available only for the packaged executable.")
        return
    if local_zip:
        # Test-only opt-in: never accept unsigned local updates by default.
        if not build_flags.QA_SEAMS or os.environ.get('SILVER_LANTERN_ALLOW_LOCAL_UPDATE') != '1':
            raise ValueError("unsigned local updates are disabled")
        target = Path(sys.executable).resolve()
        stage = Path(tempfile.mkdtemp(prefix='.heylisten-update-', dir=target.parent))
        try:
            download_started = time.monotonic()
            replacement = stage_executable(Path(local_zip).resolve(strict=True), stage, target.name)
            log_update_phase('download done', download_started)
            helper = stage / 'update-helper.exe'
            shutil.copy2(target, helper)
            check_parent_alive()
            helper_env = dict(os.environ)
            helper_env.pop('SILVER_LANTERN_PARENT_PID', None)
            spawn_started = time.monotonic()
            helper_env['SILVER_LANTERN_UPDATE_SPAWN_MONOTONIC'] = str(spawn_started)
            subprocess.Popen([str(helper), '--apply-update-internal', str(target), str(replacement), str(os.getpid()), '--update-only-internal', 'local fixture'], cwd=str(target.parent), env=helper_env)
            log_update_phase('helper spawned', spawn_started)
            _self_update_started = True
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise
        sys.exit(0)
    # Executable updates must not be redirected by the mutable sources manifest.
    rel = latest_app_release(s, "cputroll/Silver-Lantern")
    if rel is None:
        log(f"Self-update: no stable vX.Y.Z release found, up to date ({APP_VERSION})")
        return
    remote = rel.get('tag_name', '')
    if version_key(remote) <= version_key(APP_VERSION):
        log(f"Self-update: up to date ({APP_VERSION})")
        return
    assets = {a['name']: a for a in rel.get('assets', [])}
    if UPDATE_ASSET not in assets or SUMS_ASSET not in assets:
        raise ValueError("release is missing the update or its checksum")
    want = fetch_update_checksum(s, assets[SUMS_ASSET]['browser_download_url'])
    size = assets[UPDATE_ASSET].get('size')
    if not isinstance(size, int) or not 0 < size <= MAX_UPDATE_BYTES:
        raise ValueError("invalid update size")
    # Same filesystem as the executable for atomic rename and rollback.
    target = Path(sys.executable).resolve()
    stage = Path(tempfile.mkdtemp(prefix='.heylisten-update-', dir=target.parent))
    try:
        zp = stage / UPDATE_ASSET
        download_started = time.monotonic()
        stream_to(s, assets[UPDATE_ASSET]['browser_download_url'], zp, expect=size, quiet=True)
        log_update_phase('download done', download_started)
        if zp.stat().st_size != size or sha256(zp) != want:
            raise ValueError("update size or checksum mismatch")
        replacement = stage_executable(zp, stage, target.name)
        helper = stage / 'update-helper.exe'
        shutil.copy2(target, helper)
        gui_relaunch = not args
        if '--self-update' in args:
            rest = [arg for arg in args if arg != '--self-update']
        else:
            rest = list(args)
        if '--no-self-update' not in rest:
            rest.append('--no-self-update')
        if gui_relaunch:
            # Launched by double-click (GUI): the restart must open the app window,
            # not a console-mode run that would die with no stdout attached.
            rest = ['--gui', '--no-self-update']
        check_parent_alive()
        helper_env = dict(os.environ)
        helper_env.pop('SILVER_LANTERN_PARENT_PID', None)
        spawn_started = time.monotonic()
        helper_env['SILVER_LANTERN_UPDATE_SPAWN_MONOTONIC'] = str(spawn_started)
        subprocess.Popen([str(helper), '--apply-update-internal', str(target), str(replacement), str(os.getpid()), *rest], cwd=str(target.parent), env=helper_env)
        log_update_phase('helper spawned', spawn_started)
        _self_update_started = True
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    # The helper relaunches in this same console (no CREATE_NEW_CONSOLE, on purpose).
    if '--self-update' in args:
        if sys.stdout is not None:
            print('SILVERLANTERN_UPDATE_HANDOFF', flush=True)
    done(f"updating to {remote.lstrip('vV')}, restarting...")
    say()
    sys.exit(0)


# ---------------------------------------------------------------- main
GAME_MANIFEST_URL = "https://github.com/cputroll/Silver-Lantern/releases/download/game-v1/game-manifest.json"
GAME_ASSET_URL = "https://github.com/cputroll/Silver-Lantern/releases/download/game-v1/"


def game_asset_base():
    """Allow only a loopback fixture server for isolated test downloads."""
    value = os.environ.get('SILVER_LANTERN_TEST_ASSET_URL') if build_flags.QA_SEAMS else None
    if not value:
        return GAME_ASSET_URL
    parsed = urlparse(value)
    if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('fixture asset URL must be an HTTP 127.0.0.1 endpoint')
    return value.rstrip('/') + '/'


def validate_bundle_order(man):
    if man.get('version') != 2:
        return
    order = {asset['asset']: i for i, asset in enumerate(man['assets'])}
    for item in man['files']:
        segments = item['segments']
        for first, second in zip(segments, segments[1:]):
            if order[first['asset']] >= order[second['asset']]:
                raise UserError(f"{item['path']}: segments must be packed in bundle order ({first['asset']} -> {second['asset']})", 'Use a correctly packed game manifest.', 'manifest')


def parse_game_manifest(text: str) -> dict:
    try:
        man = json.loads(text)
        if not isinstance(man, dict) or man['version'] not in (1, 2) or man['build'] != 'game-v1':
            raise ValueError('unsupported manifest')
        files = man['files']
        if not isinstance(files, list):
            raise ValueError('files must be a list')
        seen = set()
        assets = set()
        def size(value):
            if type(value) is not int or value < 0:
                raise ValueError('invalid size')
            return value
        def digest(value):
            if not isinstance(value, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', value):
                raise ValueError('invalid SHA-256')
            return value.lower()
        asset_map = {}
        if man['version'] == 2:
            if not isinstance(man['assets'], list):
                raise ValueError('assets must be a list')
            for asset in man['assets']:
                name = asset['asset']
                if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', name) or name in ('.', '..') or name.casefold() in assets:
                    raise ValueError('invalid or duplicate asset')
                assets.add(name.casefold())
                size(asset['size']); asset['sha256'] = digest(asset['sha256'])
                asset_map[name] = asset
        for item in files:
            path = item['path']
            if not isinstance(path, str) or not path or '\\' in path or ':' in path:
                raise ValueError('invalid path')
            segments = path.split('/')
            if any(not p or p in ('.', '..') or p.endswith(('.', ' ')) for p in segments):
                raise ValueError('invalid path')
            key = path.casefold()
            if (key in seen or segments[0].casefold() == '.heylisten-parts'
                    or any(p.casefold() == 'user' for p in segments[:-1])):
                raise ValueError('duplicate or reserved path')
            seen.add(key)
            size(item['size']); item['sha256'] = digest(item['sha256'])
            parts = item['parts' if man['version'] == 1 else 'segments']
            if not isinstance(parts, list) or not parts:
                raise ValueError('missing parts')
            for part in parts:
                asset = part['asset']
                if man['version'] == 1:
                    if not isinstance(asset, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', asset) or asset in ('.', '..') or asset.casefold() in assets:
                        raise ValueError('invalid or duplicate asset')
                    assets.add(asset.casefold())
                    size(part['size']); part['sha256'] = digest(part['sha256'])
                else:
                    if not isinstance(asset, str) or asset not in asset_map:
                        raise ValueError('unknown asset')
                    offset = size(part['offset']); length = size(part['length'])
                    part['sha256'] = digest(part['sha256'])
                    if offset + length > asset_map[asset]['size']:
                        raise ValueError('segment exceeds asset')
            if sum(p['size' if man['version'] == 1 else 'length'] for p in parts) != item['size']:
                raise ValueError('part sizes do not match file')
        if size(man['total_bytes']) != sum(f['size'] for f in files):
            raise ValueError('total size mismatch')
        validate_bundle_order(man)
        return man
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('invalid game manifest') from exc


def load_game_manifest(s, source: str) -> dict:
    if urlparse(source).scheme in ('https', 'http'):
        for attempt in range(2):
            check_parent_alive()
            response = None
            try:
                say('Fetching manifest…')
                response = s.get(source, stream=True, timeout=(10, 30))
                response.raise_for_status()
                total = int(response.headers.get('content-length') or 0)
                chunks, received = [], 0
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    check_parent_alive()
                    if chunk:
                        chunks.append(chunk)
                        received += len(chunk)
                        say(f'Fetching manifest… ({received / 1_000_000:.1f} of {total / 1_000_000:.1f} MB)' if total else f'Fetching manifest… ({received / 1_000_000:.1f} MB)')
                text = b''.join(chunks).decode('utf-8-sig')
                break
            except Exception:
                if attempt:
                    raise
                say('Connection stalled — retrying…')
            finally:
                if response is not None:
                    response.close()
    else:
        say('Fetching manifest…')
        text = Path(source).expanduser().read_text(encoding='utf-8')
    say('Planning…')
    return parse_game_manifest(text)


class GameProgress(Progress):
    """One combined bar across every game asset."""

    def __init__(self, total):
        super().__init__("Downloading", total)


def game_path(root: Path, relative: str) -> Path:
    target = root.joinpath(*relative.split('/'))
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('manifest path escapes install directory')
    return target


def walk_destination(root: Path) -> dict:
    """Collect live sizes without following symlinks; later SHA-256 checks verify content.

    This inventory is transient; matching files are checked again by hash.
    As with installation itself, concurrent directory replacement is not atomic.
    """
    result = {}
    stack = [(root, '')]
    while stack:
        directory, prefix = stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                if not prefix and entry.name.casefold() in ('.heylisten-get', '.heylisten-parts'):
                    continue
                relative = prefix + entry.name
                if entry.is_dir(follow_symlinks=False):
                    stack.append((entry.path, relative + '/'))
                elif entry.is_file(follow_symlinks=False):
                    result[relative] = entry.stat(follow_symlinks=False).st_size
    return result


def destination_inventory(root: Path) -> dict:
    if not root.exists():
        return {}
    with os.scandir(root) as entries:
        if not any(entry.name.casefold() not in ('.heylisten-get', '.heylisten-parts') for entry in entries):
            return {}
    return walk_destination(root)


def restore_segment_checkpoint(temp, checkpoint, item):
    """Trust a retained prefix only after checking every segment hash."""
    hasher = hashlib.sha256()
    try:
        info = json.loads(checkpoint.read_text(encoding='utf-8'))
        index = info['next_index']
        if type(index) is not int or not 0 < index < len(item['segments']):
            raise ValueError('invalid checkpoint index')
        size = sum(part['length'] for part in item['segments'][:index])
        if type(info['bytes_written']) is not int or info['bytes_written'] != size or temp.stat().st_size != size:
            raise ValueError('invalid checkpoint length')
        with temp.open('rb') as source:
            for part in item['segments'][:index]:
                segment_hash = hashlib.sha256()
                remaining = part['length']
                while remaining:
                    check_parent_alive()
                    data = source.read(min(remaining, 1024 * 1024))
                    if not data:
                        raise ValueError('short checkpoint')
                    segment_hash.update(data); hasher.update(data); remaining -= len(data)
                if segment_hash.hexdigest() != part['sha256']:
                    raise ValueError('checkpoint hash mismatch')
        return index, hasher
    except (OSError, ValueError, KeyError, TypeError):
        _retry_locked(lambda: temp.unlink(missing_ok=True))
        _retry_locked(lambda: checkpoint.unlink(missing_ok=True))
        return 0, hashlib.sha256()


class SegmentMismatch(ValueError):
    def __init__(self, message, asset, path):
        super().__init__(message)
        self.asset = asset
        self.path = path


def assemble_segmented(pending, assets, cache, root, fetch, keep_assets, soft_retry=False):
    """Download bundles in order while workers consume segments in file order."""
    cond = threading.Condition()
    bundles = deque()
    states = []
    created_parents = set()
    parent_lock = threading.Lock()
    by_asset = {name: [] for name in assets}
    log(f"Preparing download: organizing {len(pending)} destination files", echo=True)
    last_report = time.monotonic()
    for count, item in enumerate(pending, 1):
        check_parent_alive()
        now = time.monotonic()
        if now - last_report >= 5:
            log(f"Preparing download: {count - 1}/{len(pending)} destination files checked", echo=True)
            last_report = now
        target = root.joinpath(*item['path'].split('/'))
        direct = False  # Always stage so cancellation cannot truncate a final target.
        state = {'item': item, 'target': target,
                 'temp': target if direct else target.with_name(target.name + '.heylisten-new'),
                 'direct': direct, 'next': 0, 'started': False, 'complete': False,
                 'hasher': hashlib.sha256(), 'lock': threading.Lock()}
        if soft_retry and not direct:
            game_path(root, item['path'])
            state['next'], state['hasher'] = restore_segment_checkpoint(state['temp'], Path(str(state['temp']) + '.json'), item)
        states.append(state)
        for index, segment in enumerate(item['segments']):
            if index >= state['next']:
                by_asset[segment['asset']].append((state, index, segment))
    names = [name for name in assets if by_asset[name]]
    log(f"Preparation complete: {len(pending)} files, {len(names)} bundles ready", echo=True)
    shared = {'done': False, 'error': None, 'cap': 2, 'slots': 0, 'stalled': 0}

    def fail(exc):
        with cond:
            if shared['error'] is None:
                shared['error'] = exc
                for bundle in bundles:
                    bundle['pending'].clear()
            cond.notify_all()

    def close_fd(fd):
        try:
            os.close(fd)
        except OSError as exc:
            if exc.errno != errno.EBADF:
                raise

    def remove_bundle(bundle):
        for fd in bundle['fds'].values():
            close_fd(fd)
        bundle['fds'].clear()
        if not keep_assets:
            bundle['path'].unlink(missing_ok=True)
            Path(str(bundle['path']) + '.part').unlink(missing_ok=True)
            Path(str(bundle['path']) + '.part.json').unlink(missing_ok=True)
        bundles.remove(bundle)
        shared['slots'] -= 1
        cond.notify_all()

    def worker():
        ident = threading.get_ident()
        try:
            while True:
                check_parent_alive()
                with cond:
                    if shared['error']:
                        return
                    chosen = None
                    for _ in range(len(bundles)):
                        bundle = bundles[0]
                        bundles.rotate(-1)
                        if not bundle['pending']:
                            continue
                        state, index, segment = bundle['pending'][0]
                        if index == state['next'] and state['lock'].acquire(False):
                            bundle['pending'].popleft()
                            chosen = bundle, state, index, segment
                            break
                    if not chosen:
                        if shared['done'] and not bundles:
                            return
                        # Only count a stall when all outstanding work is blocked,
                        # not while another worker is actively writing.
                        active = any(b['refs'] > len(b['pending']) for b in bundles)
                        if not active and (shared['slots'] >= shared['cap'] or (shared['done'] and bundles)):
                            shared['stalled'] += 1
                            if shared['cap'] == 2 and not shared['done']:
                                shared['cap'] = 3
                                cond.notify_all()
                            elif shared['stalled'] >= 100:
                                raise UserError('Segment scheduler cannot make progress.', 'Use a correctly packed game manifest.', 'manifest')
                        cond.wait(0.05)
                        continue
                    shared['stalled'] = 0
                bundle, state, index, segment = chosen
                try:
                    fd = bundle['fds'].get(ident)
                    if fd is None:
                        fd = os.open(bundle['path'], os.O_RDONLY | getattr(os, 'O_BINARY', 0))
                        bundle['fds'][ident] = fd
                    os.lseek(fd, segment['offset'], os.SEEK_SET)
                    remaining = segment['length']
                    segment_hash = hashlib.sha256()
                    check_parent_alive()
                    game_path(root, state['item']['path'])
                    with parent_lock:
                        if state['temp'].parent not in created_parents:
                            state['temp'].parent.mkdir(parents=True, exist_ok=True)
                            created_parents.add(state['temp'].parent)
                    with _open_with_retry(state['temp'], 'wb' if index == 0 else 'ab') as output:
                        state['started'] = True
                        while remaining:
                            check_parent_alive()
                            data = os.read(fd, min(remaining, 1024 * 1024))
                            if not data:
                                raise ValueError(f"short segment: {segment['asset']}")
                            output.write(data)
                            segment_hash.update(data)
                            state['hasher'].update(data)
                            remaining -= len(data)
                    if segment_hash.hexdigest() != segment['sha256']:
                        raise SegmentMismatch(f"segment SHA-256 mismatch: {segment['asset']}", segment['asset'], state['item']['path'])
                    state['next'] += 1
                    if not state['direct'] and state['next'] < len(state['item']['segments']):
                        checkpoint = Path(str(state['temp']) + '.json')
                        staging = Path(str(checkpoint) + '.tmp')
                        staging.write_text(json.dumps({'next_index': state['next'], 'bytes_written': state['temp'].stat().st_size}), encoding='utf-8')
                        _replace_with_retry(staging, checkpoint)
                    if state['next'] == len(state['item']['segments']):
                        if state['temp'].stat().st_size != state['item']['size'] or state['hasher'].hexdigest() != state['item']['sha256']:
                            raise SegmentMismatch(f"assembled SHA-256 mismatch: {state['item']['path']}", segment['asset'], state['item']['path'])
                        if not state['direct']:
                            _replace_with_retry(state['temp'], state['target'])
                            Path(str(state['temp']) + '.json').unlink(missing_ok=True)
                        state['complete'] = True
                    with cond:
                        bundle['refs'] -= 1
                        if not bundle['refs']:
                            remove_bundle(bundle)
                        cond.notify_all()
                finally:
                    state['lock'].release()
        except BaseException as exc:
            fail(exc)
        finally:
            with cond:
                for bundle in bundles:
                    fd = bundle['fds'].pop(ident, None)
                    if fd is not None:
                        close_fd(fd)

    workers = [threading.Thread(target=worker) for _ in range(assembly_workers())]
    for thread in workers:
        thread.start()
    try:
        for name in names:
            check_parent_alive()
            with cond:
                while shared['slots'] >= shared['cap'] and not shared['error']:
                    cond.wait(0.05)
                if shared['error']:
                    break
            fetch(assets[name])
            with cond:
                bundle = {'path': cache / name, 'pending': deque(by_asset[name]),
                          'refs': len(by_asset[name]), 'fds': {}}
                bundles.append(bundle)
                shared['slots'] += 1
                cond.notify_all()
    except BaseException as exc:
        fail(exc)
    finally:
        with cond:
            shared['done'] = True
            cond.notify_all()
        for thread in workers:
            thread.join()
    if shared['error']:
        soft = soft_retry is True and isinstance(shared['error'], SegmentMismatch)
        for state in states:
            if state['started'] and not state['complete']:
                checkpoint = Path(str(state['temp']) + '.json')
                if soft and not state['direct'] and checkpoint.exists():
                    try:
                        length = json.loads(checkpoint.read_text(encoding='utf-8'))['bytes_written']
                        with state['temp'].open('r+b') as output:
                            output.truncate(length)
                    except (OSError, ValueError, KeyError, TypeError):
                        state['temp'].unlink(missing_ok=True)
                        checkpoint.unlink(missing_ok=True)
                else:
                    state['temp'].unlink(missing_ok=True)
                    checkpoint.unlink(missing_ok=True)
        for name in ([shared['error'].asset] if soft else names):
            for suffix in ('', '.part', '.part.json'):
                Path(str(cache / name) + suffix).unlink(missing_ok=True)
        raise shared['error']


def install_game(s, man: dict, root: Path, dry_run: bool = False, prune: bool = False,
                 manifest_path: Path | None = None, keep_assets: bool = False) -> None:
    log('Checking installation plan', echo=True)
    validate_bundle_order(man)
    root = root.expanduser()
    pending = []
    kept = 0
    inventory = destination_inventory(root)
    last_report = time.monotonic()
    for count, item in enumerate(man['files'], 1):
        now = time.monotonic()
        if now - last_report >= 5:
            log(f"Checking installation plan: {count - 1}/{len(man['files'])} files checked", echo=True)
            last_report = now
        relative = item['path']
        if inventory.get(relative) == item['size']:
            target = game_path(root, relative)
            if item['size'] >= 256 * 1024 * 1024:
                log(f"Checking SHA-256: {item['path']} ({human(item['size'])})")
            if sha256(target, progress=item['size'] >= 256 * 1024 * 1024) == item['sha256']:
                kept += item['size']
                continue
        pending.append(item)
    cache = root / '.heylisten-parts'
    v2 = man['version'] == 2
    assets = {a['asset']: a for a in man['assets']} if v2 else {
        p['asset']: p for f in man['files'] for p in f['parts']}
    needed = {p['asset'] for f in pending for p in f['segments' if v2 else 'parts']}
    download_bytes = 0
    uncached_asset_sizes = []
    for name in needed:
        part = assets[name]
        cached = cache / name
        partial = Path(str(cached) + '.part')
        if cached.is_file() and cached.stat().st_size == part['size'] and sha256(cached, progress=True) == part['sha256']:
            continue
        if partial.is_file() and partial.stat().st_size == part['size'] and not Path(str(partial) + '.json').exists() and sha256(partial, progress=True) == part['sha256']:
            continue
        # Count the entire asset: sparse/preallocated chunks may consume space
        # later, and a server may discard a partial when it ignores ranges.
        uncached_asset_sizes.append(part['size'])
        if part['size'] >= PARALLEL_THRESHOLD and download_streams() > 1:
            state = chunk_state(cached, part['size'], download_streams())
            have = sum(b - a for (a, b), done in zip(state['bounds'], state['done']) if done)
        else:
            have = partial.stat().st_size if partial.is_file() and not Path(str(partial) + '.json').exists() else 0
            have = have if have < part['size'] else 0
        download_bytes += part['size'] - have
    log(f"Plan: {len(man['files']) - len(pending)} files present, {len(pending)} to download ({download_bytes / 1024**3:.2f} GB), {kept / 1024**3:.2f} GB kept")
    log('Installation plan complete', echo=True)
    step("Game files")
    if not pending:
        done("up to date")
    else:
        done(f"{len(pending)} to download ({human(download_bytes)})", None)
    if pending:
        say("Bundles are cleaned as files are installed; resume may re-fetch cleaned bundles.")
    if dry_run:
        return
    if pending:
        # Bound additional allocation, not total installation size. Three bundles
        # may overlap in the segmented scheduler; older layouts fetch all assets
        # for one file at a time. Cached verified assets already occupy disk.
        if keep_assets:
            asset_budget = sum(uncached_asset_sizes)
        elif v2:
            asset_budget = sum(sorted(uncached_asset_sizes, reverse=True)[:3])
        else:
            # Shared legacy bundles can remain live across multiple files.
            asset_budget = sum(uncached_asset_sizes)
        need = sum(item['size'] for item in pending) + asset_budget + 1024 ** 3
        override = os.environ.get('SILVER_LANTERN_MIN_FREE_GB')
        if override is not None:
            try:
                minimum_gb = float(override)
                if not 0 <= minimum_gb < float('inf'):
                    raise ValueError
            except ValueError:
                raise UserError('SILVER_LANTERN_MIN_FREE_GB must be a non-negative number.', 'Correct the environment variable and try again.', 'configuration')
            if minimum_gb == 0:
                need = 0
            else:
                need = max(need, int(minimum_gb * 1024 ** 3))
        if need:
            check_disk_space(root, need)
    root.mkdir(parents=True, exist_ok=True)
    if pending:
        if os.name == 'nt' and os.environ.get('SILVER_LANTERN_TEST_NO_DEFENDER') != '1':
            log('Preparing Windows security settings', echo=True)
            import launcher
            launcher.optimize_defender(root)
            log('Windows security preparation complete', echo=True)
        cache.mkdir(parents=True, exist_ok=True)
        progress = GameProgress(download_bytes)
        verified_assets = set()
        def fetch(part):
            check_parent_alive()
            dest = cache / part['asset']
            if part['asset'] in verified_assets and dest.is_file():
                return
            verified_assets.discard(part['asset'])
            if dest.is_file() and dest.stat().st_size == part['size'] and sha256(dest, progress=True) == part['sha256']:
                verified_assets.add(part['asset'])
                return
            dest.unlink(missing_ok=True)
            partial = Path(str(dest) + '.part')
            if partial.is_file() and partial.stat().st_size == part['size'] and not Path(str(partial) + '.json').exists():
                if sha256(partial, progress=True) == part['sha256']:
                    _replace_with_retry(partial, dest)
                    verified_assets.add(part['asset'])
                    return
                partial.unlink()
            url = game_asset_base() + quote(part['asset'])
            if part['size'] >= PARALLEL_THRESHOLD and download_streams() > 1:
                parallel_download(stream_session, url, dest, part['size'], part['sha256'], progress, download_streams())
            else:
                side = Path(str(partial) + '.json')
                if side.exists():
                    partial.unlink(missing_ok=True)
                    side.unlink(missing_ok=True)
                worker = stream_session() if s is None else s
                stream_to(worker, url, dest, expect=part['size'], progress=progress.add, quiet=True)
            if sha256(dest, progress=True) != part['sha256']:
                dest.unlink(missing_ok=True)
                raise ValueError(f"SHA-256 mismatch: {part['asset']}")
            verified_assets.add(part['asset'])
        log('Starting bundle download and installation', echo=True)
        if v2:
            if man.get('layout') == 'whole':
                try:
                    assemble_segmented(pending, assets, cache, root, fetch, keep_assets, soft_retry=True)
                except SegmentMismatch as error:
                    verified_assets.discard(error.asset)
                    remaining = [item for item in pending if not (game_path(root, item['path']).is_file() and sha256(game_path(root, item['path'])) == item['sha256'])]
                    assemble_segmented(remaining, assets, cache, root, fetch, keep_assets, soft_retry='resume')
            else:
                assemble_segmented(pending, assets, cache, root, fetch, keep_assets)
        else:
            remaining_uses = {name: 0 for name in needed}
            for item in pending:
                for name in {part['asset'] for part in item['segments' if v2 else 'parts']}:
                    remaining_uses[name] += 1
            for item in pending:
                check_parent_alive()
                # Fetch only assets this file needs, in manifest order. Shared assets stay
                # until their final pending file is fully assembled and verified.
                names = list(dict.fromkeys(part['asset'] for part in item['segments' if v2 else 'parts']))
                for name in names:
                    fetch(assets[name])
                target = game_path(root, item['path'])
                target.parent.mkdir(parents=True, exist_ok=True)
                temp = target.with_name(target.name + '.heylisten-new')
                try:
                    file_hash = hashlib.sha256()
                    check_parent_alive()
                    with _open_with_retry(temp, 'wb') as output:
                        for part in item['segments' if v2 else 'parts']:
                            with (cache / part['asset']).open('rb') as source:
                                source.seek(part['offset'] if v2 else 0)
                                remaining = part['length'] if v2 else part['size']
                                segment_hash = hashlib.sha256()
                                while remaining:
                                    check_parent_alive()
                                    chunk = source.read(min(remaining, 1024 * 1024))
                                    if not chunk:
                                        raise ValueError(f"short segment: {part['asset']}")
                                    output.write(chunk)
                                    segment_hash.update(chunk)
                                    file_hash.update(chunk)
                                    remaining -= len(chunk)
                                if segment_hash.hexdigest() != part['sha256']:
                                    raise ValueError(f"segment SHA-256 mismatch: {part['asset']}")
                    if temp.stat().st_size != item['size'] or file_hash.hexdigest() != item['sha256']:
                        raise ValueError(f"assembled SHA-256 mismatch: {item['path']}")
                    _replace_with_retry(temp, target)
                finally:
                    temp.unlink(missing_ok=True)
                for name in names:
                    remaining_uses[name] -= 1
                    if remaining_uses[name] == 0 and not keep_assets:
                        (cache / name).unlink(missing_ok=True)
                        Path(str(cache / name) + '.part').unlink(missing_ok=True)
        progress.finish()
        log('Bundle download and installation complete', echo=True)
    if pending and not keep_assets and cache.exists():
        try:
            cache.rmdir()  # Keep unrelated or resumable cached data.
        except OSError:
            pass
    if prune:
        wanted = {f['path'].casefold() for f in man['files']}
        manifest_resolved = manifest_path.resolve() if manifest_path else None
        for path in root.rglob('*'):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(root)
            if (relative.as_posix().casefold() == 'ocarina of time.exe'
                    or relative.parts[0].casefold() == '.heylisten-get'
                    or any(x.casefold() == 'user' for x in relative.parts[:-1])
                    or (keep_assets and relative.parts[0].casefold() == '.heylisten-parts')):
                continue
            if relative.as_posix().casefold() in wanted or path.name.casefold() == 'game-manifest.json' or (manifest_resolved and path.resolve() == manifest_resolved):
                continue
            path.unlink()


def graphics_command(folder: Path, sets: list[str]) -> int:
    """--graphics: apply each KEY=VALUE (by number or by its label), then print every option as JSON."""
    values = game_settings.read_values(folder)
    for item in sets:
        key, _, raw = item.partition('=')
        opt = game_settings.BY_KEY.get(key.strip())
        if opt is None:
            print(f"unknown setting: {key}", file=sys.stderr)
            return 2
        raw = raw.strip()
        labels = [] if opt.ranged else [c.casefold() for c in opt.choices]
        if raw.casefold() in labels:
            value = labels.index(raw.casefold())
        else:
            try:
                value = int(raw)
            except ValueError:
                print(f"bad value for {key}: {raw}", file=sys.stderr)
                return 2
        if value not in game_settings.allowed(opt.key, game_settings.dlss_available(folder)):
            print(f"{key}={raw} is not allowed (min {opt.low}, max {opt.high})", file=sys.stderr)
            return 2
        values = game_settings.set_value(folder, opt.key, value, values)
    print(json.dumps(game_settings.describe(folder), indent=2))
    return 0


def default_game_folder() -> str:
    return str(Path(os.environ.get('USERPROFILE', str(Path.home()))) / 'Games' / 'HeyListen')


UPDATE_LOG = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'HeyListenBuilder' / 'game-update-cli.log'


def parse_update_line(line: str) -> dict:
    if not line.startswith('HLUPDATE '):
        return {}
    payload = line[len('HLUPDATE '):].strip()
    return dict(re.findall(r'([A-Za-z_][\w-]*)=([^\s]+)', payload))


def render_update_status(fields: dict) -> str | None:
    version = lambda value: 'v' + str(value or '?').lstrip('vV')
    old = version(fields.get('installed', fields.get('current')))
    new = version(fields.get('latest', fields.get('available')))
    status = fields.get('status')
    if status in ('current', 'up-to-date'):
        return f'Already up to date ({old}).'
    if status in ('available', 'update-available'):
        return f'Update available: {old} -> {new} (dry run).'
    if status == 'updated':
        return f"Updated {old} -> {new} in {fields.get('total', fields.get('seconds', '?'))}s."
    if status == 'error':
        return f"Update failed: {fields.get('error', fields.get('reason', 'unknown error'))}"
    return None


def update_exit_code(code: int) -> int:
    return 0 if code in (0, 10) else 1


def _update_say(message: str) -> None:
    log(message)
    say(message)


def run_game_update(folder: Path, dry_run: bool = False, force: bool = False, silent: bool = False) -> int:
    script = next((p for p in (HERE / 'Update-Game.ps1', HERE / 'support' / 'Update-Game.ps1', Path.cwd() / 'Update-Game.ps1') if p.is_file()), None)
    if script is None:
        _update_say('Update failed: Update-Game.ps1 not found.')
        return 1
    args = ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script), '-GameFolder', str(folder)]
    if dry_run:
        args.append('-DryRun')
    if force:
        args.append('-Force')
    fields = {}
    shown = False
    try:
        UPDATE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with UPDATE_LOG.open('a', encoding='utf-8', errors='replace') as output:
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', bufsize=1)
            import launcher
            for line in launcher.child_output(proc, 'Game update'):
                output.write(line)
                output.flush()
                parsed = parse_update_line(line)
                fields.update(parsed)
                # The script emits its error reason after status=error.
                if not silent and parsed.get('status') not in (None, 'error'):
                    message = render_update_status(fields)
                    if message:
                        _update_say(message)
                        shown = True
            try:
                code = update_exit_code(proc.wait(timeout=10))
            except subprocess.TimeoutExpired:
                launcher.stop_child(proc)
                raise TimeoutError('Game update did not exit. Please retry and check the log.')
        if silent or (not shown and (fields.get('status') == 'error' or code)):
            message = render_update_status(fields) or 'Update failed: update check failed.'
            _update_say(message)
        return code
    except (OSError, ValueError) as e:
        _update_say('Update failed: ' + str(e))
        return 1


def relocate_transient() -> bool:
    """Move Explorer ZIP launches to a stable per-user executable before startup."""
    if not getattr(sys, 'frozen', False) or not os.environ.get('TEMP'):
        return False
    source = Path(sys.executable).resolve()
    temp = Path(os.environ['TEMP']).resolve()
    if not source.is_relative_to(temp):
        return False
    target = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Programs' / 'Silver Lantern' / 'SilverLantern.exe'
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = None
    try:
        with tempfile.NamedTemporaryFile(prefix='SilverLantern-', suffix='.tmp', dir=target.parent, delete=False) as stream:
            staging = Path(stream.name)
            with source.open('rb') as original:
                while True:
                    check_parent_alive()
                    block = original.read(1024 * 1024)
                    if not block:
                        break
                    stream.write(block)
        _replace_with_retry(staging, target)
    except PermissionError:
        if not target.is_file():
            raise
    finally:
        if staging:
            staging.unlink(missing_ok=True)
    subprocess.Popen([str(target), *sys.argv[1:]], close_fds=False)
    return True


def _attach_parent_console():
    try:
        import ctypes
        return bool(ctypes.windll.kernel32.AttachConsole(-1))
    except (AttributeError, OSError):
        return False


def _rebind_console_streams():
    for name in ('stdout', 'stderr'):
        stream = os.fdopen(os.open('CONOUT$', os.O_WRONLY), 'w', encoding='utf-8', errors='replace', buffering=1)
        setattr(sys, name, stream)


def _restore_inherited_pipes():
    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel.GetStdHandle.restype = wintypes.HANDLE
        kernel.GetFileType.argtypes = [wintypes.HANDLE]
        kernel.GetFileType.restype = wintypes.DWORD
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.DuplicateHandle.restype = wintypes.BOOL
        restored = False
        for name, number in (('stdout', -11), ('stderr', -12)):
            handle = kernel.GetStdHandle(number & 0xffffffff)
            if handle and handle != wintypes.HANDLE(-1).value and kernel.GetFileType(handle) in (1, 3):
                duplicate = wintypes.HANDLE()
                process = kernel.GetCurrentProcess()
                if not kernel.DuplicateHandle(process, handle, process, ctypes.byref(duplicate), 0, False, 2):
                    continue
                fd = msvcrt.open_osfhandle(duplicate.value, os.O_WRONLY)
                setattr(sys, name, os.fdopen(fd, 'w', encoding='utf-8', errors='replace', buffering=1))
                restored = restored or name == 'stdout'
        return restored
    except (AttributeError, OSError, ImportError):
        return False


def ensure_cli_console():
    try:
        if sys.stdout is not None and sys.stdout.fileno() >= 0:
            return
    except (AttributeError, OSError, ValueError):
        pass
    if _restore_inherited_pipes():
        return
    if _attach_parent_console():
        try:
            _rebind_console_streams()
        except OSError:
            pass


def main() -> int:
    if getattr(sys, 'frozen', False) and (not sys.argv[1:] or sys.argv[1:] in (['--gui'], ['--gui', '--no-self-update'])):
        import launcher
        try:
            if launcher.relocate_launcher(launcher.resolve_game_folder()):
                return 0
        except Exception as exc:
            return launcher.show_startup_error(exc)
    cli_requested = '--cli' in sys.argv[1:]
    if cli_requested:
        sys.argv.remove('--cli')
    elif '--screenshot' in sys.argv[1:]:
        import launcher
        return launcher.main()
    elif '--self-test' in sys.argv[1:]:
        import launcher
        app = launcher.App(launcher.State.NOT_INSTALLED)
        app.transition(launcher.State.DOWNLOADING)
        app.transition(launcher.State.READY)
        return 0
    elif getattr(sys, 'frozen', False) and (len(sys.argv) == 1 or sys.argv[1:] in (['--gui'], ['--gui', '--no-self-update'])):
        try:
            import launcher
            return launcher.main()
        except ImportError:
            if sys.argv[1:] == ['--gui']:
                raise
    if len(sys.argv) >= 5 and sys.argv[1] == "--apply-update-internal":
        global LOG_PATH
        LOG_PATH = Path(sys.argv[2]).parent / 'HeyListen-Get.log'
        _apply_update(Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4]), sys.argv[5:])
        return 0
    ap = argparse.ArgumentParser(description="One-click HeyListen + game downloader")
    ap.add_argument('--gui', action='store_true', help=argparse.SUPPRESS)
    ap.add_argument('--internal-trust-check', action='store_true', help=argparse.SUPPRESS)
    ap.add_argument("--manifest", help="sources.json path or URL (default: next to this script)")
    ap.add_argument("--out", default=None,
                    help="folder for the .nup and builder (default: Downloads)")
    ap.add_argument("--builder-only", action="store_true")
    ap.add_argument("--game-only", action="store_true")
    ap.add_argument("--skip-hash", action="store_true", help="skip the 25 GB SHA-256 check")
    ap.add_argument("--force", action="store_true", help="re-download even if present")
    ap.add_argument("--no-launch", action="store_true", help="don't open HeyListenBuilder.exe")
    ap.add_argument("--no-preset", action="store_true", help="don't write the upscaler preset ini")
    ap.add_argument("--game-dir", default=None,
                    help="built game folder for the upscaler preset")
    ap.add_argument("--print-sources", action="store_true", help="show the manifest and exit")
    ap.add_argument("--version", action="store_true", help="print the installed version and exit")
    ap.add_argument("--no-self-update", action="store_true",
                    help="skip the GitHub self-update check")
    ap.add_argument("--self-update", action="store_true",
                    help="run only the self-update check (installs a newer release if there is one)")
    ap.add_argument("--self-update-from", metavar="ZIP",
                    help="install a release zip from a local path (testing)")
    ap.add_argument('--install-game', nargs='?', const=str(Path(os.environ.get('USERPROFILE', str(Path.home()))) / 'Games' / 'HeyListen'), metavar='FOLDER')
    import launcher
    ap.add_argument('--game-manifest', default=None, metavar='PATH_OR_URL')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--keep-assets', action='store_true', help='Keep downloaded game assets after installation')
    ap.add_argument('--prune', action='store_true')
    ap.add_argument('--update-game', nargs='?', const=default_game_folder(), metavar='FOLDER')
    ap.add_argument('--update-dry-run', action='store_true')
    ap.add_argument('--force-update', action='store_true')
    ap.add_argument('--update-on-launch', action='store_true')
    ap.add_argument('--builder-flow', action='store_true',
                    help='old flow: download the game dump + HeyListenBuilder and open the Builder')
    ap.add_argument('--graphics', nargs='?', const=default_game_folder(), metavar='FOLDER',
                    help="print the game's settings (hookshot_options.ini) as JSON and exit")
    ap.add_argument('--set', action='append', default=[], metavar='KEY=VALUE',
                    help='with --graphics: change a setting first (repeatable)')
    ap.add_argument('--uninstall', action='store_true', help='Remove the installed game and launcher')
    ap.add_argument('--keep-saves', action='store_true', help='Compatibility alias; saves are kept by default')
    ap.add_argument('--purge-saves', action='store_true', help='Remove saved games as well when uninstalling')
    ap.add_argument('--no-pause', action='store_true', help="don't wait for Enter before closing")
    ap.add_argument('--plain', action='store_true', help='no colors')
    a = ap.parse_args()
    if getattr(sys, 'frozen', False):
        ensure_cli_console()

    if a.internal_trust_check:
        try:
            check_self_update(None, {}, [], 'nonexistent-fixture.zip')
        except ValueError as exc:
            refused = str(exc) == 'unsigned local updates are disabled'
        else:
            refused = False
        print(json.dumps({'manifest_override': launcher._TEST_GAME_MANIFEST,
                          'asset_url': game_asset_base(), 'local_update_refused': refused}))
        return 0

    if a.uninstall:
        import launcher
        return 0 if launcher.uninstall(purge_saves=a.purge_saves, gui=getattr(sys, 'frozen', False) and not cli_requested) else 1
    if a.update_game is not None:
        return run_game_update(Path(a.update_game).expanduser(), dry_run=a.update_dry_run, force=a.force_update)
    if a.version:
        print(local_version({}))
        return 0
    if a.print_sources:
        print(json.dumps(load_manifest(a.manifest), indent=2))
        return 0
    if a.graphics is not None:
        return graphics_command(Path(a.graphics).expanduser(), a.set)

    global _UI_READY
    _UI_READY = True
    ui_init(a.plain)
    builder_flow = bool(a.builder_flow or a.builder_only or a.game_only or a.manifest or a.out or a.skip_hash)
    say()
    say(f"Silver Lantern {APP_VERSION}", "bold")
    if builder_flow:
        say("Downloads the game data and the HeyListen Builder, then opens the Builder.")
    elif a.install_game is not None:
        say("Installs or updates the game files.")
    else:
        say("Installs (or updates) the game, then starts it.")
    say("-" * 70)
    say()

    s = session()
    if not a.no_self_update:
        code = self_update_step(s, a)
        if code is not None:
            return code
    if a.self_update or a.self_update_from:
        say("Self-update check complete.")
        return 0

    if builder_flow:
        return run_builder_flow(s, a)
    if a.install_game is not None:
        folder = Path(a.install_game).expanduser()
        fresh_install = not (folder / 'hookshot_options.ini').exists()
        install_from_manifest(s, a, folder)
        check_parent_alive()
        if not a.dry_run and not a.no_preset:
            apply_preset_step(folder, fresh_install=fresh_install)
        return 0
    return run_default_flow(s, a)


_UI_READY = False


def self_update_step(s, a) -> int | None:
    """One quiet line. Returns an exit code only when the user asked for --self-update."""
    frozen = getattr(sys, "frozen", False)
    if frozen:
        step("Checking for updates")
    try:
        check_self_update(s, {}, sys.argv[1:], a.self_update_from)
        if frozen:
            done("up to date")
    except Exception as e:  # noqa: BLE001
        log(f"Self-update failed safely: {e}")
        log(traceback.format_exc())
        if frozen or a.self_update or a.self_update_from:
            done("skipped (no connection)" if _is_network(e) else "skipped", "dim")
        if a.self_update or a.self_update_from:
            return 1
    return None


def cache_game_manifest(folder: Path, manifest: dict) -> None:
    cache = folder / '.heylisten-get' / 'game-manifest.json'
    cache.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache.with_suffix('.tmp')
    temporary.write_text(json.dumps(manifest), encoding='utf-8')
    _replace_with_retry(temporary, cache)


def install_from_manifest(s, a, folder: Path) -> None:
    import game_channel
    try:
        with game_channel.install_lock(folder, check_cancel=check_parent_alive):
            check_parent_alive()
            _install_from_manifest_locked(s, a, folder)
    except TimeoutError as exc:
        raise UserError('Another game installation or update is in progress.', 'Wait for it to finish and try again.', 'busy') from exc


def _install_from_manifest_locked(s, a, folder: Path) -> None:
    step("Game file list")
    try:
        with stream_session() as manifest_session:
            if a.game_manifest is None:
                import game_channel
                import launcher
                game_manifest = game_channel.fetch_for_folder(folder, lambda url: game_channel.fetch_bytes(manifest_session, url), lambda data: parse_game_manifest(data.decode('utf-8-sig')), legacy_url=launcher.game_manifest_url())
            else:
                game_manifest = load_game_manifest(manifest_session, a.game_manifest)
    except Exception:
        done("failed", "yellow")
        raise
    check_parent_alive()
    done("up to date")
    local_manifest = None if a.game_manifest is None or urlparse(a.game_manifest).scheme in ('http', 'https') else Path(a.game_manifest).expanduser()
    with keep_awake():
        install_game(None, game_manifest, folder, a.dry_run, a.prune, local_manifest, a.keep_assets)
    if not a.dry_run:
        check_parent_alive()
        cache_game_manifest(folder, game_manifest)


def game_launcher(folder: Path) -> Path:
    top = folder / 'HeyListen.exe'
    return top if top.is_file() else folder / 'bin' / 'HeyListen.exe'


def finish_lines(folder: Path, launched: bool, preset: str | None = None) -> list[str]:
    """The numbered next-steps block shown after a successful install."""
    lines = ["-" * 70, "The game is installed." + (" It is starting now." if launched else ""), "",
             "Your game folder:", f"  {folder}", "", "Next steps:"]
    if launched:
        lines.append("  1. Wait for the game window to open (the first start can take")
        lines.append("     a minute).")
    else:
        lines.append("  1. Start the game: double-click HeyListen.exe in the folder above.")
    lines.append("  2. For game updates later, run this tool again. It only downloads")
    lines.append("     what changed.")
    if preset:
        lines.append(f"  3. Graphics settings were set to {preset}.")
        lines.append("     You can change them in the game's menu.")
    else:
        lines.append("  3. You can change the graphics settings in the game's menu.")
    return lines


def apply_preset_step(folder: Path, fresh_install: bool = False) -> str | None:
    """Apply the GPU preset once (never over the user's own settings). Returns its label."""
    if not fresh_install and (folder / 'hookshot_options.ini').exists():
        log(f"preset skipped: {folder / 'hookshot_options.ini'} already exists")
        return None
    step("Graphics settings")
    try:
        ini = apply_preset(folder)
        dlss = 'dlss=1' in ini.read_text(encoding='ascii', errors='replace').splitlines()
        label = "DLSS Performance" if dlss else "FSR 3.1 Performance"
        done(f"applied ({label})")
        return label
    except Exception as e:  # noqa: BLE001
        log(f"preset failed: {e}")
        log(traceback.format_exc())
        done("skipped", "dim")
        return None


def run_default_flow(s, a) -> int:
    """Double-click path: install the prebuilt game, set graphics, start it."""
    folder = Path(a.game_dir or default_game_folder()).expanduser()
    fresh_install = not (folder / 'hookshot_options.ini').exists()
    install_from_manifest(s, a, folder)
    if a.dry_run:
        return 0
    if not (folder / 'bin' / 'HeyListen.exe').is_file():
        raise UserError("The game files were installed, but HeyListen.exe is missing.",
                        "Run this again. If it repeats, send the log file to the person who gave you this tool.")
    preset = None if a.no_preset else apply_preset_step(folder, fresh_install=fresh_install)
    launched = False
    if not a.no_launch:
        exe = game_launcher(folder)
        log(f"launching {exe}")
        subprocess.Popen([str(exe)], cwd=str(exe.parent))
        launched = True
    say()
    for line in finish_lines(folder, launched, preset):
        say(line)
    if a.update_on_launch:
        try:
            run_game_update(folder, dry_run=True, silent=True)
        except Exception as e:  # noqa: BLE001
            log(('Update failed: ' + str(e)).encode('ascii', 'replace').decode('ascii'))
    return 0


def run_builder_flow(s, a) -> int:
    """Old flow: game dump + HeyListenBuilder, preset after the Builder closes."""
    step("Download links")
    try:
        man = load_manifest(a.manifest)
    except RuntimeError as e:
        done("failed", "yellow")
        log(f"manifest: {e}")
        raise UserError("This tool's files are incomplete.",
                        "Extract the whole zip into a folder again and run SilverLantern.exe from there.")
    done("up to date" if _MANIFEST_ORIGIN == "url" else "using saved copy",
         "green" if _MANIFEST_ORIGIN == "url" else "dim")
    tool = man.get("tool", {})
    log(f"Manifest v{tool.get('version', '?')} - game: {man.get('game', {}).get('title', '?')}")

    out = Path(a.out or (Path.home() / "Downloads")).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    game_dir = Path(a.game_dir or (Path.home() / "Games" / "HeyListen")).expanduser()

    builder = None
    with keep_awake():
        if not a.game_only:
            builder = download_builder(s, man, out, force=a.force)
        game = None
        if not a.builder_only:
            game = download_game(s, man, out, skip_hash=a.skip_hash, force=a.force)

    say()
    say("-" * 70)
    proc = None
    if builder and not a.no_launch:
        say("Everything is downloaded. The HeyListen Builder is opening now.")
        log(f"Opening {builder}")
        proc = subprocess.Popen([str(builder)], cwd=str(builder.parent))
    else:
        say("Everything is downloaded.")
    say()
    say("Your files are in: " + str(out))
    if game is not None:
        say(f"  {Path(str(game)).name}   (the game data)")
    if builder:
        say(f"  {Path(str(builder)).name}   (the Builder)")
    if builder:
        say()
        say("Next steps, in the Builder window:")
        say(f"  1. Choose the game file: {man.get('game', {}).get('filename', 'the .nup file')} in the folder above.")
        say('  2. Tick the box "NVIDIA DLSS" if you have an NVIDIA RTX card.')
        say('  3. Press "Build the game" and wait for it to finish.')
    if proc is not None:
        say()
        say("Leave this window open. When you close the Builder, the graphics settings")
        say("are applied for you.")
        try:
            proc.wait(timeout=24 * 60 * 60)
        except subprocess.TimeoutExpired:
            log('Builder is still open after 24 hours; continuing without waiting.')
            say('Builder is still open. Apply graphics settings after it closes.')
            return 1
        except Exception as e:  # noqa: BLE001
            log(f"builder wait failed: {e}")
    if not a.builder_only and not a.no_preset:
        step("Graphics settings")
        apply_preset(game_dir)
        done("applied")
    if a.update_on_launch:
        try:
            run_game_update(game_dir, dry_run=True, silent=True)
        except Exception as e:  # noqa: BLE001
            log(('Update failed: ' + str(e)).encode('ascii', 'replace').decode('ascii'))
    say()
    say("All done. Enjoy!", "green")
    return 0


def run() -> None:
    """Entry point: friendly errors, full details in the log, keep the window open."""
    no_pause = '--no-pause' in sys.argv
    try:
        check_parent_alive()
        try:
            import launcher
            launcher.enable_dpi_awareness()
        except Exception:
            pass
        code = main()
    except ParentExited:
        log('parent exited; stopping')
        sys.exit(0)
    except KeyboardInterrupt:
        log("interrupted by user")
        say()
        say("Stopped. Run this again any time. It picks up where it left off.", "dim")
        code = 130
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, PermissionError) or (isinstance(exc, OSError) and getattr(exc, 'winerror', None) in (32, 33)):
            log(f'File in use: {exc}')
        else:
            log(traceback.format_exc())
        if _UI_READY:
            show_error(friendly(exc))
        else:
            say("Problem: " + friendly(exc).problem)
        code = 1
    if _UI_READY:
        pause(no_pause)
    sys.exit(code)


if __name__ == "__main__":
    run()
