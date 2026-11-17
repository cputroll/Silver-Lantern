"""Headless launcher state model; UI integration is not yet implemented."""
from enum import Enum, auto
import queue
import threading
import argparse
import ctypes
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import hashlib
import base64
try:
    import winreg
except ImportError:
    winreg = None
import game_settings

ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
BASE = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
STATE_DIR = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'SilverLantern'
CONFIG = STATE_DIR / 'launcher.json'
LOG = STATE_DIR / 'launcher.log'
# Test-only isolation overrides, read once: registry parent under HKCU and shortcut directories.
# Empty/unset values preserve normal Windows registration and known-folder behavior.
_TEST_REG_ROOT = os.environ.get('SILVER_LANTERN_REG_ROOT') or r'Software\Microsoft\Windows\CurrentVersion\Uninstall'
_TEST_DESKTOP_DIR = os.environ.get('SILVER_LANTERN_DESKTOP_DIR') or None
_TEST_STARTMENU_DIR = os.environ.get('SILVER_LANTERN_STARTMENU_DIR') or None
GAME_NAME = 'Legend of Zelda Ocarina of Time'
MANIFEST_URL = 'https://github.com/cputroll/Silver-Lantern/releases/download/game-v1/game-manifest.json'


def game_manifest_url():
    try:
        value = json.loads((BASE / 'sources.json').read_text(encoding='utf-8-sig')).get('game', {}).get('game_manifest_url')
        if isinstance(value, str) and value.startswith('https://'):
            return value
    except (OSError, ValueError, AttributeError):
        pass
    return MANIFEST_URL


APP_VERSION = getattr(sys.modules.get('__main__'), 'APP_VERSION', '1.6.3')


def play_tip(folder):
    try:
        shortcut = game_settings.read_values(folder)['menu_shortcut']
        keys = {0: 'F1 or Insert', 1: 'Insert', 2: 'F1'}.get(shortcut)
    except OSError:
        keys = None
    first = (f'While playing, press {keys} for settings or to quit.' if keys else
             'While playing, open the game menu for settings or to quit.')
    return first


def windows_animations_enabled():
    try:
        enabled = ctypes.c_int(1)
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
            return bool(enabled.value)
    except (AttributeError, OSError):
        pass
    return True


def create_renderer(s, window, screenshot=False):
    if screenshot:
        return s.SDL_CreateRenderer(window, -1, s.SDL_RENDERER_SOFTWARE)
    renderer = s.SDL_CreateRenderer(window, -1, s.SDL_RENDERER_ACCELERATED | s.SDL_RENDERER_PRESENTVSYNC)
    return renderer or s.SDL_CreateRenderer(window, -1, s.SDL_RENDERER_SOFTWARE)


def tip_offset(t, snap=False):
    return 0 if snap else round(3 * math.sin(t * math.tau / 2))


def cli_command(*args):
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--cli', *map(str, args)]
    return [sys.executable, str(BASE / 'Get-HeyListen.py'), *map(str, args)]


def installed(folder):
    return (folder / 'hookshot_version.txt').exists() and (folder / 'bin' / 'HeyListen.exe').exists()


def probe_writable(folder):
    folder.mkdir(parents=True, exist_ok=True)
    probe = folder / ('.silverlantern-probe-' + str(os.getpid()))
    try:
        with probe.open('wb') as stream:
            stream.write(b'1')
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        probe.unlink(missing_ok=True)


def _known_desktop():
    if os.name != 'nt':
        return None
    try:
        from ctypes import wintypes
        class GUID(ctypes.Structure):
            _fields_ = [('Data1', wintypes.DWORD), ('Data2', wintypes.WORD), ('Data3', wintypes.WORD), ('Data4', ctypes.c_ubyte * 8)]
        desktop = GUID(0xB4BFCC3A, 0xDB1C, 0x4D44, (ctypes.c_ubyte * 8)(0x83, 0x4F, 0x28, 0x4F, 0xB3, 0x87, 0xCD, 0x39))
        ole = ctypes.windll.ole32
        shell = ctypes.windll.shell32
        initialized = ole.CoInitializeEx(None, 2)
        path = ctypes.c_wchar_p()
        try:
            shell.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
            shell.SHGetKnownFolderPath.restype = ctypes.c_long
            if shell.SHGetKnownFolderPath(ctypes.byref(desktop), 0, None, ctypes.byref(path)) == 0 and path.value:
                return Path(path.value)
        finally:
            if path:
                ole.CoTaskMemFree(path)
            if initialized in (0, 1):
                ole.CoUninitialize()
    except (AttributeError, OSError, ValueError):
        pass
    return None


def shortcut_path():
    desktop = Path(_TEST_DESKTOP_DIR) if _TEST_DESKTOP_DIR else (_known_desktop() or Path(os.environ.get('USERPROFILE', str(Path.home()))) / 'Desktop')
    return desktop / (GAME_NAME + '.lnk')


def launcher_target(folder):
    fallback = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Programs' / 'Ocarina of Time' / 'Ocarina of Time.exe'
    if getattr(sys, 'frozen', False) and Path(sys.executable).resolve() == fallback.resolve():
        return fallback
    return Path(folder) / 'Ocarina of Time.exe'


def create_shortcut(folder, enabled=True, exe=None):
    link = shortcut_path()
    if not enabled:
        link.unlink(missing_ok=True)
        return
    target = exe or launcher_target(folder)
    create_link(link, target, target.parent)


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def log_message(message):
    text = str(message)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open('a', encoding='utf-8') as stream:
            stream.write(text + '\n')
    except OSError:
        pass
    try:
        if sys.stdout is not None:
            print(text, file=sys.stdout, flush=True)
    except (OSError, ValueError):
        pass


def powershell(script, wait=True, timeout=None):
    encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
    args = ['powershell', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded]
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    if wait:
        return subprocess.run(args, check=True, creationflags=flags, stderr=subprocess.PIPE, timeout=timeout)
    return subprocess.Popen(args, creationflags=flags, close_fds=True)


DEFENDER_TIMEOUT = 75  # seconds to wait for the UAC consent prompt before giving up


def defender_elevated(folder, enable):
    if os.name != 'nt':
        return False
    cmd = ('Add-MpPreference' if enable else 'Remove-MpPreference') + ' -ExclusionPath ' + ps_quote(folder) + ' -ErrorAction Stop'
    encoded = base64.b64encode(cmd.encode('utf-16le')).decode('ascii')
    script = ("$ErrorActionPreference='Stop'; try { $p=Start-Process powershell -Verb RunAs -Wait -PassThru -WindowStyle Hidden "
              "-ArgumentList '-NoProfile -NonInteractive -EncodedCommand " + encoded + "'; if ($null -eq $p) { exit 1 }; exit $p.ExitCode } catch { exit 1 }")
    try:
        powershell(script, timeout=DEFENDER_TIMEOUT)
        return True
    except (OSError, subprocess.SubprocessError):
        # Timeout, denial, or a failure: the exclusion is only an optimization. Never fatal.
        return False


def defender_config():
    try:
        return json.loads(CONFIG.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError, TypeError):
        return {}


def optimize_defender(folder):
    config = defender_config()
    folder = Path(folder).resolve()
    state = config.get('defender_state')
    if state == 'on':
        if config.get('defender_path') == str(folder):
            return True
        # Never lose track of an exclusion when the game folder changes.
        return False
    if state in ('unknown', 'declined'):
        # Previously denied or timed out; don't nag every run. Clear the config to ask again.
        return False
    log_message('Waiting for Windows permission… click Yes if Windows asks.')
    success = defender_elevated(folder, True)
    config['defender_state'] = 'on' if success else 'declined'
    if success:
        config['defender_path'] = str(folder)
    else:
        log_message('Continuing without optimizing.')
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix('.tmp')
    tmp.write_text(json.dumps(config), encoding='utf-8')
    os.replace(tmp, CONFIG)
    return success


def remove_defender_exclusion():
    config = defender_config()
    if config.get('defender_state') == 'on' and config.get('defender_path'):
        return defender_elevated(Path(config['defender_path']), False)
    return True


def create_link(link, target, working_dir):
    script = '$s=New-Object -ComObject WScript.Shell; $l=$s.CreateShortcut(' + ps_quote(link) + '); $l.TargetPath=' + ps_quote(target) + '; $l.WorkingDirectory=' + ps_quote(working_dir) + '; $l.Save()'
    for attempt in range(2):
        try:
            link.parent.mkdir(parents=True, exist_ok=True)
            powershell(script)
            return True
        except (OSError, subprocess.SubprocessError) as exc:
            stderr = getattr(exc, 'stderr', None)
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors='replace')
            log_message(f'Shortcut {link} attempt {attempt + 1}: {exc}; stderr: {stderr or "(empty)"}')
    return False


def start_menu_path():
    folder = Path(_TEST_STARTMENU_DIR) if _TEST_STARTMENU_DIR else Path(os.environ.get('APPDATA', str(Path.home()))) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs'
    return folder / (GAME_NAME + '.lnk')


UNINSTALL_KEY = _TEST_REG_ROOT.rstrip('\\') + r'\SilverLantern'
OWNED_FILES = ('SilverLantern.exe', 'HeyListen-Get.exe', 'SDL2.dll', 'sources.json',
               'README.md', 'Apply-HeyListen-Preset.ps1', 'SilverLantern-Get.cmd',
               'SilverLantern-Preset.cmd', 'Update-Game.ps1', 'Watch-Game.ps1',
               'SilverLantern-AutoUpdate.cmd', 'sources.cache.json', 'HeyListen-Get.log',
               'SilverLantern.exe.update-backup', 'HeyListen-Get.exe.update-backup', 'Run Silver Lantern.exe')
OWNED_FILES += tuple('support/' + name for name in OWNED_FILES if name not in ('Run Silver Lantern.exe',))


def register_install(folder, exe=None):
    if winreg is None:
        return
    exe = exe or launcher_target(folder)
    try:
        create_shortcut(Path(folder), exe=exe)
    except Exception as exc:
        log_message(f'Desktop shortcut: {exc}')
    try:
        link = start_menu_path()
        create_link(link, exe, exe.parent)
    except Exception as exc:
        log_message(f'Start Menu shortcut: {exc}')
    size = 0
    for base, dirs, files in os.walk(folder, followlinks=False):
        for name in files:
            try:
                size += (Path(base) / name).stat().st_size
            except OSError:
                pass
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            for name, value, kind in (
                ('DisplayName', GAME_NAME, winreg.REG_SZ), ('DisplayIcon', str(exe), winreg.REG_SZ),
                ('InstallLocation', str(exe.parent), winreg.REG_SZ),
                ('UninstallString', '"' + str(exe) + '" --uninstall', winreg.REG_SZ),
                ('NoModify', 1, winreg.REG_DWORD), ('NoRepair', 1, winreg.REG_DWORD),
                ('EstimatedSize', min((size + 1023) // 1024, 0xffffffff), winreg.REG_DWORD)):
                winreg.SetValueEx(key, name, 0, kind, value)
    except Exception as exc:
        log_message(f'Uninstall registration: {exc}')


def remove_registration():
    if winreg is not None:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
        except FileNotFoundError:
            pass


def cache_game_manifest(folder, manifest):
    cache = folder / '.heylisten-get' / 'game-manifest.json'
    cache.parent.mkdir(parents=True, exist_ok=True)
    temp = cache.with_suffix('.tmp')
    temp.write_text(json.dumps(manifest), encoding='utf-8')
    os.replace(temp, cache)


def manifest_digest(manifest):
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def installed_manifest_digest(folder):
    try:
        with (Path(folder) / '.heylisten-get' / 'game-manifest.json').open(encoding='utf-8') as source:
            return manifest_digest(json.load(source))
    except (OSError, ValueError, TypeError):
        return None


def fetch_channel_manifest(folder, session):
    import game_channel
    import importlib.util
    parser = sys.modules.get('__main__')
    if not hasattr(parser, 'parse_game_manifest'):
        spec = importlib.util.spec_from_file_location('silver_manifest_parser', ROOT / 'Get-HeyListen.py')
        parser = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(parser)
    with game_channel.install_lock(folder):
        return game_channel.fetch_for_folder(folder, lambda url: game_channel.fetch_bytes(session, url), lambda data: parser.parse_game_manifest(data.decode('utf-8-sig')), legacy_url=game_manifest_url())


def fetch_ownership_manifest():
    with urllib.request.urlopen(game_manifest_url(), timeout=10) as response:
        return json.load(response)


def ownership_manifest(folder):
    try:
        with (folder / '.heylisten-get' / 'game-manifest.json').open(encoding='utf-8') as stream:
            manifest = json.load(stream)
        if not isinstance(manifest.get('files'), list):
            raise ValueError('invalid manifest')
        return manifest
    except (OSError, ValueError, AttributeError):
        try:
            manifest = fetch_ownership_manifest()
            return manifest if isinstance(manifest.get('files'), list) else None
        except (OSError, ValueError, AttributeError):
            return None


def manifest_owned_files(folder, manifest):
    result = []
    root = folder.resolve()
    for item in (manifest or {}).get('files', []):
        try:
            name = item['path']
            if not isinstance(name, str) or '\\' in name or ':' in name:
                continue
            parts = name.split('/')
            if any(part in ('', '.', '..') or part.casefold() == 'user' for part in parts):
                continue
            path = folder.joinpath(*parts)
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                continue
            if path.is_file() and path.stat().st_size == item['size']:
                result.append(path)
        except (OSError, KeyError, TypeError):
            continue
    return result


def load_game_folder():
    try:
        value = json.loads(CONFIG.read_text(encoding='utf-8-sig')).get('game_dir')
        return Path(value).expanduser() if value else None
    except (OSError, ValueError, TypeError):
        return None


def resolve_game_folder():
    configured = load_game_folder()
    if configured:
        return configured
    profile = Path(os.environ.get('USERPROFILE', str(Path.home())))
    legacy = profile / 'Games' / 'HeyListen'
    if legacy.exists():
        return legacy
    primary = Path(r'C:\Games') / GAME_NAME
    try:
        probe_writable(primary)
        return primary
    except OSError:
        return profile / 'Games' / GAME_NAME


def _cleanup_stale_siblings(destination, keep):
    """Delete stacked ``Ocarina of Time-<random>.exe`` launcher copies, keeping only `keep`.

    Never removes the copy in use: the running executable is skipped explicitly, and any
    still-running (locked) sibling simply fails to delete and is left alone.
    """
    try:
        keep_resolved = Path(keep).resolve()
    except OSError:
        keep_resolved = Path(keep)
    running = None
    if getattr(sys, 'frozen', False):
        try:
            running = Path(sys.executable).resolve()
        except OSError:
            running = None
    try:
        candidates = list(Path(destination).glob('Ocarina of Time-*.exe'))
    except OSError:
        return
    for stale in candidates:
        try:
            resolved = stale.resolve()
        except OSError:
            continue
        if resolved == keep_resolved or (running is not None and resolved == running):
            continue
        try:
            stale.unlink()
        except OSError:
            pass


def relocate_launcher(folder):
    if not getattr(sys, 'frozen', False):
        return False
    source = Path(sys.executable).resolve()
    fallback = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Programs' / 'Ocarina of Time'
    try:
        probe_writable(folder)
        destination = Path(folder)
    except OSError:
        destination = fallback
        probe_writable(destination)
    target = destination / 'Ocarina of Time.exe'
    if source == target.resolve():
        return False
    if target.exists():
        destination = fallback
        probe_writable(destination)
        target = destination / 'Ocarina of Time.exe'
        if source.parent == destination.resolve():
            return False
    try:
        with source.open('rb') as original, target.open('xb') as copy:
            shutil.copyfileobj(original, copy)
    except FileExistsError:
        # Never overwrite or run an executable whose ownership is unknown.
        import tempfile
        with source.open('rb') as original, tempfile.NamedTemporaryFile(
                prefix='Ocarina of Time-', suffix='.exe', dir=destination, delete=False) as copy:
            target = Path(copy.name)
            try:
                shutil.copyfileobj(original, copy)
            except BaseException:
                copy.close()
                target.unlink(missing_ok=True)
                raise
    log_message(f'Launcher relocation: {target}')
    try:
        register_install(destination, exe=target)
    except Exception as exc:
        log_message(f'Install registration: {exc}')
    _cleanup_stale_siblings(destination, target)
    subprocess.Popen([str(target), *sys.argv[1:]], close_fds=False)
    return True


def schedule_owned_cleanup():
    if not getattr(sys, 'frozen', False):
        return
    exe = Path(sys.executable).resolve()
    folder = exe.parent
    script = (f'$p=Get-Process -Id {os.getpid()} -ErrorAction SilentlyContinue; '
              'if($p -and -not $p.WaitForExit(30000)){exit}; '
              f'$file={ps_quote(exe)}; for($i=0;$i -lt 60;$i++) {{ '
              'Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue; '
              'if(-not (Test-Path -LiteralPath $file)) { break }; Start-Sleep -Milliseconds 500 }; '
              f'try {{ [System.IO.Directory]::Delete({ps_quote(folder)}, $false) }} catch {{}}')
    powershell(script, wait=False)


def confirm_uninstall(message, gui=False):
    if gui and os.name == 'nt':
        return ctypes.windll.user32.MessageBoxW(None, message, 'Uninstall Silver Lantern', 0x24) == 6
    log_message(message)
    return input('Continue? [y/N] ').strip().lower() == 'y'


def uninstall(keep_saves=True, purge_saves=False, gui=False):
    folder = load_game_folder()
    if purge_saves:
        message = 'This removes the game folder, including your saves, and the launcher files and settings.'
    else:
        saves = folder / 'user' if folder else '(game folder not configured)'
        message = ('This removes game files, shortcuts, launcher files and settings; the saves folder remains.\n'
                   f'Your saves stay at {saves} — use --purge-saves to remove them too')
    message += f'\nGame folder: {folder or "(not configured)"}'
    if not confirm_uninstall(message, gui):
        log_message('Uninstall cancelled.')
        return False
    had_folder = bool(folder and folder.exists())
    if had_folder:
        game = folder.resolve()
        home = Path.home().resolve()
        state = STATE_DIR.resolve()
        if game == Path(game.anchor) or home == game or home.is_relative_to(game) or state == game or state.is_relative_to(game):
            raise RuntimeError('Refusing to remove an unsafe game folder.')
        if not remove_defender_exclusion():
            raise RuntimeError('Could not remove the Windows Defender exclusion; retry uninstall or remove it in Windows Security.')
        running = Path(sys.executable).resolve() if getattr(sys, 'frozen', False) else None
        owned = manifest_owned_files(folder, ownership_manifest(folder))
        owned.extend(folder / name for name in OWNED_FILES)
        owned.append(folder / game_settings.INI_NAME)
        owned.append(folder / '.heylisten-get' / 'game-manifest.json')
        for path in owned:
            try:
                if path.resolve() != running and path.resolve().is_relative_to(folder.resolve()) and not path.is_symlink() and path.is_file():
                    path.unlink()
            except OSError:
                pass
        if purge_saves:
            saves = folder / 'user'
            if saves.is_dir() and not saves.is_symlink():
                shutil.rmtree(saves)
        unknown = sum(1 for base, dirs, files in os.walk(folder, followlinks=False)
                      if not Path(base).relative_to(folder).parts or Path(base).relative_to(folder).parts[0].casefold() != 'user'
                      for name in files if (Path(base) / name).resolve() != running)
        log_message(f'Unknown user files preserved: {unknown}')
        for base, dirs, files in os.walk(folder, topdown=False, followlinks=False):
            try:
                Path(base).rmdir()
            except OSError:
                pass
    if not had_folder and not remove_defender_exclusion():
        raise RuntimeError('Could not remove the Windows Defender exclusion; retry uninstall or remove it in Windows Security.')
    shortcut_path().unlink(missing_ok=True)
    shortcut_path().with_name('Silver Lantern.lnk').unlink(missing_ok=True)
    start_menu_path().unlink(missing_ok=True)
    remove_registration()
    if STATE_DIR.exists():
        shutil.rmtree(STATE_DIR)
    if had_folder:
        try:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with LOG.open('a', encoding='utf-8') as log:
                log.write(f'Unknown user files preserved: {unknown}\n')
        except OSError:
            pass
    schedule_owned_cleanup()
    return True


def fmt_bytes(n):
    n = float(n or 0)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.0f} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1024
    return f'{n:.1f} GB'


def fmt_eta(seconds):
    seconds = max(0, int(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    if h:
        return f'{h} h {m:02d} min'
    if m:
        return f'{m} min {s:02d} s'
    return f'{s} s'


def parse_progress(line):
    if line.startswith('HLPROGRESS '):
        fields = dict(re.findall(r'(\w+)=([^ ]+)', line[11:]))
        try:
            done, total, speed = (int(fields.get(k, 0)) for k in ('done', 'total', 'speed'))
        except ValueError:
            return None
        phase = fields.get('phase', 'Downloading').replace('_', ' ')
        pct = done * 100 // total if total > 0 else None
        text = f'{phase}: {pct}%' if pct is not None else phase
        return {'text': text, 'phase': phase, 'done': done, 'total': total, 'speed': speed, 'pct': pct}
    if line.startswith('HLUPDATE '):
        return dict(re.findall(r'(\w+)=([^ ]+)', line[9:]))
    if 'Downloading:' in line:
        return {'text': line.strip()}
    return None


def _strip_ansi(text):
    return re.sub(r'\x1b\[[0-9;]*m', '', text)


def friendly_blocks(lines):
    """Extract the child's last Problem / What to do blocks and its details line.

    The installer prints friendly, wrapped ``Problem:`` and ``What to do:`` prose
    followed by ``Details were saved to: <path>``. The GUI error screen should show
    that prose instead of only the final ``Details were saved to`` line.
    """
    problem, action, details = [], [], None
    target = None
    for raw in lines:
        line = _strip_ansi(str(raw)).strip()
        if line.startswith('Problem:'):
            problem, action = [line[len('Problem:'):].strip()], []
            target = 'problem'
            continue
        if line.startswith('What to do:'):
            action = [line[len('What to do:'):].strip()]
            target = 'action'
            continue
        if line.startswith('Details were saved to:'):
            details = line[len('Details were saved to:'):].strip()
            target = None
            continue
        if not line:
            target = None
            continue
        if target == 'problem':
            problem.append(line)
        elif target == 'action':
            action.append(line)
    return ' '.join(x for x in problem if x).strip(), ' '.join(x for x in action if x).strip(), details


def worker_command(events, args):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, SILVER_LANTERN_GUI_PROGRESS='1')
        process = subprocess.Popen(cli_command(*args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', bufsize=1, env=env, creationflags=getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
        events.process = process
        if events.cancelled.is_set():
            terminate_worker(events)
        last_line = ''
        screen_lines = []
        with LOG.open('a', encoding='utf-8') as log:
            while True:
                line = process.stdout.readline()
                if not line:
                    break
                log.write(line)
                log.flush()
                if line.strip() and not line.startswith(('HLPROGRESS ', 'HLUPDATE ')):
                    last_line = line.strip()
                    screen_lines.append(line.strip())
                events.publish('progress', parse_progress(line) or {'text': line.strip()})
        code = process.wait()
        if events.cancelled.is_set():
            events.publish('cancelled', code)
        elif code == 0:
            events.publish('complete', code)
        else:
            problem, action, details = friendly_blocks(screen_lines)
            parts = []
            if problem:
                parts.append('Problem: ' + problem)
            if action:
                parts.append('What to do: ' + action)
            if details:
                parts.append('Details were saved to: ' + details)
            elif last_line:
                parts.append('Last output: ' + last_line)
            if not parts:
                parts.append('No diagnostic output was provided.')
            events.publish('error', f'The operation failed (exit {code}). ' + ' '.join(parts) + ' See the log for full details.')
    except Exception as exc:
        events.publish('error', str(exc))


def tool_update_worker(events, args):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        result = subprocess.run([sys.executable, '--self-update', *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        output = result.stdout or ''
        with LOG.open('a', encoding='utf-8') as log:
            log.write(output)
        if result.returncode:
            events.publish('error', f'Tool update failed (exit {result.returncode}).')
        elif 'SILVERLANTERN_UPDATE_HANDOFF' in output.splitlines():
            events.publish('tool_complete', 0)
        else:
            events.publish('tool_current', 0)
    except Exception as exc:
        events.publish('error', str(exc))


def terminate_worker(events):
    events.cancel()
    process = getattr(events, 'process', None)
    if process and process.poll() is None:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        else:
            process.terminate()


class InputMapper:
    def __init__(self):
        self.direction = None
        self.next_repeat = 0
        self.fast = False

    def feed(self, event, now):
        if event['type'] == 'button':
            button = event['button']
            if button in ('a', 'b'):
                return [] if event['down'] else [{'a': 'accept', 'b': 'back'}[button]]
            if event['down']:
                action = {'start': 'menu', 'x': 'cancel'}.get(button)
                return [action] if action else []
        if event['type'] == 'axis':
            x, y = event['x'], event['y']
            magnitude = (x*x + y*y) ** .5
            if magnitude < (.25 if self.direction else .35):
                self.direction = None
            elif magnitude >= .35:
                direction = ('right' if x > 0 else 'left') if abs(x) > abs(y) else ('down' if y > 0 else 'up')
                self.fast = magnitude > .85
                if direction != self.direction:
                    self.direction = direction
                    self.next_repeat = now + .4
                    return [direction]
        return []

    def tick(self, now):
        if self.direction and now >= self.next_repeat:
            self.next_repeat = now + (.07 if self.fast else .12)
            return [self.direction]
        return []


def resolve_folder(primary, fallback, probe):
    try:
        probe(primary)
        return primary, ''
    except OSError:
        probe(fallback)
        return fallback, "C:\\Games isn't writable on this PC, so the game goes in your user folder instead."


def legacy_exists(folder):
    return any((folder / part).exists() for part in ('hookshot_version.txt', 'bin/HeyListen.exe', '.heylisten-parts'))


class ProgressEvents:
    def __init__(self):
        self.queue = queue.Queue()
        self.cancelled = threading.Event()

    def publish(self, kind, value):
        self.queue.put((kind, value))

    def cancel(self):
        self.cancelled.set()


class State(Enum):
    STARTING = auto()
    FIRST_RUN = auto()
    NOT_INSTALLED = auto()
    DOWNLOADING = auto()
    READY = auto()
    UPDATE_AVAILABLE = auto()
    UPDATING = auto()
    PLAYING = auto()
    ERROR = auto()
    RESTARTING = auto()


class IllegalTransition(ValueError):
    pass


TRANSITIONS = {
    State.STARTING: {State.FIRST_RUN, State.NOT_INSTALLED, State.READY},
    State.FIRST_RUN: {State.NOT_INSTALLED, State.READY},
    State.NOT_INSTALLED: {State.DOWNLOADING},
    State.DOWNLOADING: {State.READY, State.ERROR, State.NOT_INSTALLED},
    State.READY: {State.UPDATE_AVAILABLE, State.PLAYING, State.UPDATING, State.RESTARTING},
    State.UPDATE_AVAILABLE: {State.UPDATING, State.PLAYING, State.RESTARTING},
    State.UPDATING: {State.READY, State.ERROR, State.UPDATE_AVAILABLE},
    State.PLAYING: {State.READY, State.UPDATE_AVAILABLE, State.ERROR},
    State.ERROR: {State.NOT_INSTALLED, State.DOWNLOADING, State.READY, State.UPDATE_AVAILABLE, State.UPDATING},
    State.RESTARTING: set(),
}


class App:
    def __init__(self, state=State.STARTING):
        self.state = state

    def transition(self, state):
        if state not in TRANSITIONS[self.state]:
            raise IllegalTransition(f'{self.state.name} -> {state.name}')
        self.state = state

# ---------------------------------------------------------------- look
# One logical canvas, a dark palette with a single warm accent, and a fixed type scale.
CANVAS = (1280, 720)
ACCENT = (232, 192, 112)
ACCENT_INK = (24, 18, 8)
INK = (238, 240, 243)
INK_DIM = (168, 176, 184)
INK_FAINT = (116, 124, 132)
SURFACE = (14, 18, 23)
SURFACE_HI = (36, 43, 52)
SURFACE_FOCUS = (52, 61, 72)
DANGER = (240, 128, 112)
SUCCESS = (120, 204, 150)
TYPE = {'display': ('segoeuib.ttf', 64), 'title': ('seguisb.ttf', 36), 'figure': ('segoeuil.ttf', 56),
        'heading': ('seguisb.ttf', 22), 'body': ('segoeui.ttf', 20), 'button': ('seguisb.ttf', 20),
        'cta': ('segoeuib.ttf', 24), 'caption': ('segoeui.ttf', 16), 'overline': ('seguisb.ttf', 14)}
MARGIN = 96


def render_density(width, height):
    """Physical pixels per logical pixel in SDL's aspect-preserving viewport."""
    if width <= 0 or height <= 0:
        return 1.0
    return max(1.0, min(width / CANVAS[0], height / CANVAS[1]))


def physical_span(span, scale, offset_x, offset_y):
    x, y, w, h = span
    left, top = round(x * scale), round(y * scale)
    right, bottom = round((x + w) * scale), round((y + h) * scale)
    return offset_x + left, offset_y + top, right - left, bottom - top


class Painter:
    """Measured text, rounded shapes and the backdrop; every string is fitted, never clipped."""

    def __init__(self, s, ttf, renderer):
        self.s, self.ttf, self.r = s, ttf, renderer
        self.fonts, self.cache, self.backdrop = {}, {}, None
        self.density = 0
        self.set_density(1)
        art = ROOT / 'assets' / 'ui-backdrop.bmp'
        if art.is_file():
            surface = s.SDL_LoadBMP(str(art).encode())
            if surface:
                self.backdrop = s.SDL_CreateTextureFromSurface(renderer, surface)
                s.SDL_FreeSurface(surface)
        s.SDL_SetRenderDrawBlendMode(renderer, s.SDL_BLENDMODE_BLEND)

    def set_density(self, density):
        density = max(1.0, round(density, 3))
        if density == self.density:
            return
        fonts = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts'
        new_fonts = {}
        try:
            for role, (name, size) in TYPE.items():
                font = None
                for candidate in (name, 'segoeui.ttf'):
                    font = self.ttf.TTF_OpenFont(str(fonts / candidate).encode(), max(1, round(size * density)))
                    if font:
                        break
                if not font:
                    raise RuntimeError('Could not open the system font')
                new_fonts[role] = font
        except Exception:
            for font in new_fonts.values():
                self.ttf.TTF_CloseFont(font)
            raise
        for texture, _, _ in self.cache.values():
            self.s.SDL_DestroyTexture(texture)
        self.cache.clear()
        for font in self.fonts.values():
            self.ttf.TTF_CloseFont(font)
        self.fonts, self.density = new_fonts, density

    def close(self):
        for texture, _, _ in self.cache.values():
            self.s.SDL_DestroyTexture(texture)
        self.cache.clear()
        if self.backdrop:
            self.s.SDL_DestroyTexture(self.backdrop)
        for font in self.fonts.values():
            self.ttf.TTF_CloseFont(font)

    # -- text
    def measure(self, role, value):
        w, h = ctypes.c_int(), ctypes.c_int()
        if self.ttf.TTF_SizeUTF8(self.fonts[role], str(value).encode('utf-8'), ctypes.byref(w), ctypes.byref(h)) != 0:
            return 10 ** 6, 0
        return w.value / self.density, h.value / self.density

    def height(self, role):
        return self.ttf.TTF_FontHeight(self.fonts[role]) / self.density

    def fit(self, role, value, max_width):
        value = str(value)
        if self.measure(role, value)[0] <= max_width:
            return value
        lo, hi = 0, len(value)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.measure(role, value[:mid].rstrip() + '…')[0] <= max_width:
                lo = mid
            else:
                hi = mid - 1
        return value[:lo].rstrip() + '…' if self.measure(role, '…')[0] <= max_width else ''

    def fit_middle(self, role, value, max_width):
        """Paths keep their start and their end."""
        value = str(value)
        if self.measure(role, value)[0] <= max_width:
            return value
        lo, hi = 0, len(value)
        clip = lambda n: value[:n - n // 2] + '…' + value[len(value) - n // 2:] if n else '…'
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.measure(role, clip(mid))[0] <= max_width:
                lo = mid
            else:
                hi = mid - 1
        return clip(lo) if self.measure(role, clip(lo))[0] <= max_width else ''

    def wrap(self, role, value, max_width, max_lines=3):
        lines, current = [], ''
        for word in str(value).split():
            trial = (current + ' ' + word).strip()
            if current and self.measure(role, trial)[0] > max_width:
                lines.append(current)
                current = word
            else:
                current = trial
        if current:
            lines.append(current)
        if len(lines) > max_lines:
            lines = lines[:max_lines - 1] + [' '.join(lines[max_lines - 1:])]
        return [self.fit(role, line, max_width) for line in lines]

    def text(self, role, value, x, y, color=INK, max_width=None, align='left', alpha=255, middle=False):
        """Draw fitted text; returns the drawn width."""
        s = self.s
        value = str(value)
        if max_width is not None:
            value = (self.fit_middle if middle else self.fit)(role, value, max_width)
        if not value:
            return 0
        key = (role, value, color)
        if key not in self.cache:
            if len(self.cache) > 400:
                for texture, _, _ in self.cache.values():
                    s.SDL_DestroyTexture(texture)
                self.cache.clear()
            surface = self.ttf.TTF_RenderUTF8_Blended(self.fonts[role], value.encode('utf-8'), s.SDL_Color(*color, 255))
            if not surface:
                return 0
            texture = s.SDL_CreateTextureFromSurface(self.r, surface)
            self.cache[key] = (texture, surface.contents.w, surface.contents.h)
            s.SDL_FreeSurface(surface)
        texture, pixel_w, pixel_h = self.cache[key]
        w, h = pixel_w / self.density, pixel_h / self.density
        if align == 'center':
            x -= w // 2
        elif align == 'right':
            x -= w
        s.SDL_SetTextureAlphaMod(texture, alpha)
        s.SDL_RenderCopy(self.r, texture, None, s.SDL_Rect(int(x), int(y), int(round(w)), int(round(h))))
        return w

    # -- shapes
    @staticmethod
    def _inset(w, h, radius, row):
        if radius <= 0:
            return 0
        if row < radius:
            dy = radius - row - .5
        elif row >= h - radius:
            dy = row - (h - radius) + .5
        else:
            return 0
        return radius - int(round(max(0.0, radius * radius - dy * dy) ** .5))

    def _spans(self, spans, color, alpha):
        s = self.s
        if not spans:
            return
        s.SDL_SetRenderDrawColor(self.r, *color, int(max(0, min(255, alpha))))
        width, height = ctypes.c_int(), ctypes.c_int()
        if s.SDL_GetRendererOutputSize(self.r, ctypes.byref(width), ctypes.byref(height)) != 0:
            return
        scale = min(width.value / CANVAS[0], height.value / CANVAS[1])
        if scale <= 0:
            return
        ox = round((width.value - CANVAS[0] * scale) / 2)
        oy = round((height.value - CANVAS[1] * scale) / 2)
        rects = (s.SDL_Rect * len(spans))(*[s.SDL_Rect(*physical_span(span, scale, ox, oy)) for span in spans])
        s.SDL_RenderSetLogicalSize(self.r, 0, 0)
        s.SDL_RenderFillRects(self.r, rects, len(spans))
        s.SDL_RenderSetLogicalSize(self.r, *CANVAS)

    def fill(self, x, y, w, h, color, alpha=255, radius=0):
        x, y, w, h = int(x), int(y), int(w), int(h)
        if w <= 0 or h <= 0:
            return
        radius = min(int(radius), w // 2, h // 2)
        spans = [(x, y + radius, w, h - 2 * radius)] if h > 2 * radius else []
        for row in list(range(radius)) + list(range(h - radius, h)):
            inset = self._inset(w, h, radius, row)
            spans.append((x + inset, y + row, w - 2 * inset, 1))
        self._spans(spans, color, alpha)

    def ring(self, x, y, w, h, color, alpha=255, radius=0, thickness=2):
        x, y, w, h = int(x), int(y), int(w), int(h)
        if w <= 2 * thickness or h <= 2 * thickness:
            return
        radius = min(int(radius), w // 2, h // 2)
        inner_r = max(0, radius - thickness)
        spans = []
        for row in range(h):
            a = self._inset(w, h, radius, row)
            if row < thickness or row >= h - thickness:
                spans.append((x + a, y + row, w - 2 * a, 1))
            else:
                b = thickness + self._inset(w - 2 * thickness, h - 2 * thickness, inner_r, row - thickness)
                spans.append((x + a, y + row, b - a, 1))
                spans.append((x + w - b, y + row, b - a, 1))
        self._spans(spans, color, alpha)

    def circle(self, cx, cy, radius, color, alpha=255):
        self.fill(cx - radius, cy - radius, 2 * radius, 2 * radius, color, alpha, radius)

    def scene(self, scrim=0):
        """Key-art backdrop (or a plain dark fill), optionally dimmed for dense screens."""
        s = self.s
        s.SDL_SetRenderDrawColor(self.r, *SURFACE, 255)
        s.SDL_RenderClear(self.r)
        if self.backdrop:
            s.SDL_RenderCopy(self.r, self.backdrop, None, None)
        if scrim:
            self.fill(0, 0, *CANVAS, SURFACE, scrim)


class LauncherUI:
    def __init__(self, startup_error=None):
        self.profile = Path(os.environ.get('USERPROFILE', str(Path.home())))
        self.primary = Path(r'C:\Games') / GAME_NAME
        self.fallback = self.profile / 'Games' / GAME_NAME
        self.legacy = self.profile / 'Games' / 'HeyListen'
        try:
            self.config = json.loads(CONFIG.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            self.config = {}
        self.folder = self.fallback if startup_error is not None else resolve_game_folder()
        self.screen = 'main' if self.config else 'folder'
        self.state = State.READY if installed(self.folder) else State.NOT_INSTALLED
        self.focus = 0
        self.reduce_motion = not windows_animations_enabled()
        self.status = 'Checking for updates…'
        self.progress = ''
        self.progress_info = {}
        self.events = ProgressEvents()
        self.mapper = InputMapper()
        self.controllers = {}
        self.operation = None
        self.axis = [0.0, 0.0]
        self.running = True
        self.last_error = ''
        self.tool_update_available = False
        self.tool_handoff_started = False
        self.gfx_values, self.gfx_picker, self.gfx_return, self.gfx_dlss_ok = {}, None, 0, True
        if self.state == State.READY and getattr(sys, 'frozen', False) and '--screenshot' not in sys.argv:
            threading.Thread(target=self.shortcut_worker, daemon=True).start()
        if startup_error is None:
            self.check_updates()

    def save(self):
        persisted = defender_config()
        for key in ('defender_state', 'defender_path'):
            if key in persisted:
                self.config[key] = persisted[key]
        self.config['game_dir'] = str(self.folder)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.config), encoding='utf-8')
        os.replace(tmp, CONFIG)

    def check_updates(self):
        events = self.events
        def check():
            try:
                events.publish('status', 'Fetching manifest…')
                from curl_cffi import requests as curl_requests, CurlOpt
                options = {CurlOpt.CONNECTTIMEOUT_MS: 10000, CurlOpt.TIMEOUT_MS: 0,
                           CurlOpt.LOW_SPEED_LIMIT: 1024, CurlOpt.LOW_SPEED_TIME: 30}
                for attempt in range(2):
                    try:
                        with curl_requests.Session(impersonate='chrome', curl_options=options) as client:
                            manifest = fetch_channel_manifest(self.folder, client)
                            events.publish('status', 'Planning…')
                        break
                    except Exception:
                        if attempt:
                            raise
                        events.publish('status', 'Connection stalled — retrying…')
                digest = manifest_digest(manifest)
                events.publish('check', (manifest.get('revision'), digest))
            except Exception:
                events.publish('offline', 'Could not reach the update server')
            try:
                events.publish('status', 'Checking for updates…')
                from curl_cffi import requests as curl_requests
                for attempt in range(2):
                    try:
                        response = curl_requests.get('https://api.github.com/repos/cputroll/Silver-Lantern/releases/latest', headers={'User-Agent':'SilverLantern', 'Accept':'application/vnd.github+json'}, timeout=(10, 60))
                        try:
                            response.raise_for_status()
                            release = response.json()
                        finally:
                            response.close()
                        break
                    except Exception:
                        if attempt:
                            raise
                        events.publish('status', 'Connection stalled — retrying…')
                events.publish('tool_check', release.get('tag_name', 'unknown'))
                if installed(self.folder):
                    result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-File', str(next((p for p in (BASE / 'Update-Game.ps1', BASE / 'support' / 'Update-Game.ps1') if p.is_file()), BASE / 'Update-Game.ps1')), '-DryRun', '-GameFolder', str(self.folder)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=90, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    events.publish('upstream_check', result.returncode)
            except Exception:
                events.publish('offline', 'Could not reach the update server')
        threading.Thread(target=check, daemon=True).start()

    def buttons(self):
        if self.screen == 'folder':
            return (['Update now'] if self.tool_update_available else []) + (['Use existing folder'] if legacy_exists(self.legacy) else []) + ['Use default folder', 'Choose another folder', 'Continue']
        if self.screen == 'settings':
            return ['Change game folder', 'Speed up installs (one Windows permission, instant)', 'Graphics', 'Desktop shortcut: ' + ('On' if self.config.get('shortcut', True) else 'Off'), 'Check for updates automatically: ' + ('On' if self.config.get('auto_tool_update', True) else 'Off'), 'Check for updates now', 'View log', 'Back']
        if self.screen == 'graphics':
            if self.gfx_picker is not None:
                opt = game_settings.BY_KEY[self.gfx_picker]
                return [opt.text(v) for v in game_settings.allowed(opt.key, self.gfx_dlss_ok)]
            return list(self.GFX_ACTIONS) + [o.label for o in game_settings.OPTIONS]
        if self.screen == 'progress':
            return [] if self.operation == 'tool' else ['Cancel']
        if self.screen == 'error':
            return ['Retry', 'View log', 'Back']
        if self.screen == 'update':
            return ['Update now', 'Later']
        if self.tool_update_available:
            return ['Update now', 'Settings']
        if self.state == State.PLAYING:
            return ['Settings']
        return (['Play', 'Update', 'Settings'] if self.state in (State.READY, State.UPDATE_AVAILABLE) else ['Download', 'Settings'])

    def set_screen(self, screen):
        self.screen, self.focus = screen, 0
        self.settings_first = 0

    # ---------------------------------------------------------------- graphics
    GFX_ACTIONS = ('Recommended for this PC', 'Reset to game defaults', 'Back')
    GFX_PICKER_MIN = 7  # options with more values than this open a value list on A

    def enter_graphics(self):
        try:
            self.gfx_values = game_settings.read_values(self.folder)
        except OSError as exc:
            self.last_error = f'Could not read the game settings: {exc}'
            self.set_screen('error')
            return
        self.gfx_dlss_ok = game_settings.dlss_available(self.folder)
        self.gfx_picker = None
        self.set_screen('graphics')
        self.focus = len(self.GFX_ACTIONS)
        self.status = 'Saved to the game at once. In game: F1 or Insert opens its menu; its last row, Quit (A twice), exits the game.'

    def leave_graphics(self):
        self.gfx_picker = None
        self.set_screen('settings')
        self.focus = 2

    def gfx_option(self, index=None):
        index = self.focus if index is None else index
        index -= len(self.GFX_ACTIONS)
        return game_settings.OPTIONS[index] if 0 <= index < len(game_settings.OPTIONS) else None

    def gfx_write(self, key, value):
        try:
            self.gfx_values = game_settings.set_value(self.folder, key, value, self.gfx_values)
            opt = game_settings.BY_KEY[key]
            self.status = f'{opt.label}: {opt.text(self.gfx_values[key])}' + (' · applies after a restart' if opt.restart else ' · saved')
        except OSError as exc:
            self.status = f"Couldn't save: {exc}"

    def gfx_step(self, direction):
        opt = self.gfx_option()
        if opt:
            self.gfx_write(opt.key, game_settings.cycle(opt.key, self.gfx_values.get(opt.key, opt.default), direction, self.gfx_dlss_ok))

    def gfx_apply_recommended(self):
        def work():
            try:
                preset, why = game_settings.choose_preset(self.folder)
                game_settings.write_values(self.folder, preset)
                self.events.publish('graphics', ('Recommended applied: ' + why.split('-> ')[-1], game_settings.read_values(self.folder)))
            except Exception as exc:
                self.events.publish('graphics', (f"Couldn't apply the recommended settings: {exc}", None))
        self.status = 'Detecting your graphics card…'
        threading.Thread(target=work, daemon=True).start()

    def gfx_activate(self):
        buttons = self.buttons()
        if not buttons:
            return
        if self.gfx_picker is not None:
            values = game_settings.allowed(self.gfx_picker, self.gfx_dlss_ok)
            self.gfx_write(self.gfx_picker, values[min(self.focus, len(values) - 1)])
            self.gfx_picker, self.focus = None, self.gfx_return
            return
        choice = buttons[self.focus]
        if choice == 'Back':
            self.leave_graphics()
        elif choice == 'Reset to game defaults':
            try:
                self.gfx_values = game_settings.reset_defaults(self.folder)
                self.status = "Game defaults restored (your controls and bindings are kept)."
            except OSError as exc:
                self.status = f"Couldn't save: {exc}"
        elif choice == 'Recommended for this PC':
            self.gfx_apply_recommended()
        else:
            opt = self.gfx_option()
            values = game_settings.allowed(opt.key, self.gfx_dlss_ok)
            if len(values) > self.GFX_PICKER_MIN:
                current = self.gfx_values.get(opt.key, opt.default)
                self.gfx_picker, self.gfx_return = opt.key, self.focus
                self.focus = values.index(current) if current in values else 0
            else:
                self.gfx_step(1)

    def gfx_action(self, action):
        """Graphics-only input; returns True when handled."""
        if action in ('left', 'right'):
            if self.gfx_picker is None:
                if self.gfx_option():
                    self.gfx_step(1 if action == 'right' else -1)
                else:
                    self.focus = max(0, min(len(self.GFX_ACTIONS) - 1, self.focus + (1 if action == 'right' else -1)))
            return True
        if action in ('back', 'cancel', 'menu'):
            if self.gfx_picker is not None:
                self.gfx_picker, self.focus = None, self.gfx_return
            else:
                self.leave_graphics()
            return True
        return False

    def start_tool_update(self):
        if self.operation == 'tool' and self.screen == 'progress':
            return
        self.operation = 'tool'
        self.progress = 'Updating Silver Lantern…'
        self.set_screen('progress')
        args = [arg for arg in sys.argv[1:] if arg not in ('--self-update', '--no-self-update')]
        if not args:
            args = ['--gui']
        threading.Thread(target=tool_update_worker, args=(self.events, args), daemon=True).start()

    def start_operation(self, update=False):
        try:
            probe_writable(self.folder)
            # The installer checks free space after verifying existing files and cache.
            # A fixed preflight here wrongly blocks already-complete installations.
            self.save()
        except OSError as exc:
            self.last_error = str(exc)
            self.set_screen('error')
            return
        self.operation = 'update' if update else 'download'
        self.progress = 'Checking for updates…' if update else 'Fetching manifest…'
        self.progress_info = {}
        self.state = State.UPDATING if update else State.DOWNLOADING
        self.events = ProgressEvents()
        args = ['--no-self-update', '--update-game' if update else '--install-game', str(self.folder)]
        self.set_screen('progress')
        threading.Thread(target=worker_command, args=(self.events, args), daemon=True).start()

    def activate(self):
        if self.screen == 'graphics':
            return self.gfx_activate()
        buttons = self.buttons()
        if not buttons:
            return
        choice = buttons[self.focus]
        if choice == 'Play':
            try:
                process = subprocess.Popen([str(self.folder / 'HeyListen.exe')], cwd=str(self.folder))
                self.state = State.PLAYING
                self.status = 'Playing...'
                def wait_game():
                    process.wait()
                    self.events.publish('game_done', None)
                threading.Thread(target=wait_game, daemon=True).start()
            except OSError as exc:
                self.last_error = str(exc)
                self.set_screen('error')
        elif choice == 'Download': self.start_operation()
        elif choice == 'Update': self.set_screen('update')
        elif choice == 'Update now':
            if self.tool_update_available: self.start_tool_update()
            else: self.start_operation(True)
        elif choice in ('Settings',): self.set_screen('settings')
        elif choice in ('Back', 'Later'): self.set_screen('main')
        elif choice == 'Cancel': terminate_worker(self.events)
        elif choice == 'Retry':
            if self.operation == 'startup':
                try:
                    self.folder = resolve_game_folder()
                    if relocate_launcher(self.folder):
                        self.running = False
                    else:
                        self.operation = None
                        self.set_screen('main' if self.config else 'folder')
                        self.check_updates()
                except Exception as exc:
                    self.last_error = str(exc)
                    log_message(f'Startup retry: {exc}')
                return
            if self.operation == 'check':
                self.operation = None
                self.status = 'Checking for updates…'
                self.set_screen('main')
                self.check_updates()
            elif self.operation == 'tool': self.start_tool_update()
            else: self.start_operation(self.operation == 'update')
        elif choice == 'View log':
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            LOG.touch(exist_ok=True)
            os.startfile(LOG)
        elif choice == 'Speed up installs (one Windows permission, instant)':
            try:
                self.status = 'Added' if optimize_defender(self.folder) else 'Windows declined'
            except Exception:
                self.status = 'Unavailable'
        elif choice == 'Change game folder': self.set_screen('folder')
        elif choice == 'Graphics': self.enter_graphics()
        elif choice.startswith('Desktop shortcut'):
            enabled = not self.config.get('shortcut', True)
            try:
                create_shortcut(self.folder, enabled)
                self.config['shortcut'] = enabled
                self.save()
            except Exception as exc:
                self.last_error = str(exc)
                self.set_screen('error')
        elif choice.startswith('Check for updates automatically:'):
            self.config['auto_tool_update'] = not self.config.get('auto_tool_update', True)
            self.save()
        elif choice == 'Check for updates now': self.check_updates()
        elif choice == 'Use existing folder': self.folder = self.legacy
        elif choice == 'Use default folder':
            try:
                self.folder, self.status = resolve_folder(self.primary, self.fallback, probe_writable)
            except OSError as exc:
                self.last_error = str(exc)
                self.set_screen('error')
        elif choice == 'Choose another folder':
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk(); root.withdraw()
            path = filedialog.askdirectory(parent=root)
            root.destroy()
            if path: self.folder = Path(path)
        elif choice == 'Continue':
            try:
                if self.folder == self.primary:
                    self.folder, self.status = resolve_folder(self.primary, self.fallback, probe_writable)
                else: probe_writable(self.folder)
                previous = load_game_folder()
                self.save()
                if getattr(sys, 'frozen', False) and relocate_launcher(self.folder):
                    current = Path(sys.executable).resolve()
                    fallback = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Programs' / 'Ocarina of Time'
                    if current.name.casefold() == 'ocarina of time.exe' and (previous and current.parent == previous.resolve() or current.parent == fallback.resolve()) and current.parent != self.folder.resolve():
                        schedule_owned_cleanup()
                    self.running = False
                    return
                self.state = State.READY if installed(self.folder) else State.NOT_INSTALLED
                self.set_screen('main')
            except OSError as exc:
                self.last_error = str(exc)
                self.set_screen('error')

    def action(self, action):
        if self.screen == 'progress' and self.operation == 'tool':
            return
        if self.screen == 'graphics' and self.gfx_action(action):
            return
        if action == 'up': self.focus = max(0, self.focus - 1)
        elif action == 'down': self.focus = min(len(self.buttons()) - 1, self.focus + 1)
        elif action == 'accept': self.activate()
        elif action == 'menu': self.set_screen('main' if self.screen == 'settings' else 'settings')
        elif action in ('back', 'cancel'):
            if self.screen == 'progress' and self.operation != 'tool': terminate_worker(self.events)
            elif self.screen != 'main': self.set_screen('main')

    def drain(self):
        while True:
            try: kind, value = self.events.queue.get_nowait()
            except queue.Empty: break
            if kind == 'status': self.status = str(value)
            elif kind == 'progress':
                self.progress = value.get('text', str(value)) if isinstance(value, dict) else str(value)
                if isinstance(value, dict) and 'done' in value:
                    self.progress_info = value
                elif isinstance(value, dict) and value.get('text'):
                    match = re.search(r'(\d{1,3})%', value['text'])
                    if match and not self.progress_info.get('total'):
                        self.progress_info = dict(self.progress_info, pct=min(100, int(match.group(1))))
            elif kind == 'complete':
                self.operation = None
                self.state = State.READY if installed(self.folder) else State.NOT_INSTALLED
                self.status = 'Ready to play.' if self.state == State.READY else 'Installation completed but game files were not found.'
                if self.state == State.READY:
                    threading.Thread(target=self.shortcut_worker, daemon=True).start()
                self.set_screen('main')
            elif kind == 'cancelled':
                self.state = State.NOT_INSTALLED
                self.status = 'Stopped. Run DOWNLOAD to resume.'
                self.set_screen('main')
            elif kind == 'error':
                self.last_error = str(value)
                self.state = State.ERROR
                self.set_screen('error')
            elif kind == 'game_done':
                self.state = State.READY
                self.status = 'Ready to play.'
                self.check_updates()
            elif kind == 'offline':
                if self.operation not in ('download', 'update', 'tool'):
                    self.last_error = str(value or 'Could not reach the update server')
                    self.operation = 'check'
                    self.set_screen('error')
            elif kind == 'check':
                revision, digest = value
                self.latest_manifest_hash = digest
                if self.state == State.READY and installed_manifest_digest(self.folder) != digest:
                    self.state = State.UPDATE_AVAILABLE
                    self.status = 'A game data update may be available.'
                    if self.operation is None and self.screen == 'main':
                        self.start_operation(True)
                else: self.status = 'Game data: ' + (str(revision) if revision is not None else 'unversioned')
            elif kind == 'tool_check':
                match = re.fullmatch(r'v(\d+)\.(\d+)\.(\d+)', value)
                if match and tuple(map(int, match.groups())) > tuple(map(int, APP_VERSION.split('.'))):
                    self.tool_update_available = True
                    self.status = 'Silver Lantern update available: ' + value
                    if self.operation in ('game', 'download', 'update'):
                        try:
                            STATE_DIR.mkdir(parents=True, exist_ok=True)
                            with LOG.open('a', encoding='utf-8') as log:
                                log.write('Automatic tool update deferred during game operation.\n')
                        except OSError:
                            pass
                    elif self.config.get('auto_tool_update', True) and self.screen != 'progress' and '--no-self-update' not in sys.argv and not self.tool_handoff_started:
                        self.tool_handoff_started = True
                        self.start_tool_update()
                elif match:
                    self.tool_update_available = False
                    self.status = 'Silver Lantern is up to date.'
            elif kind == 'tool_complete':
                # The child must exit first; its helper then waits for this GUI to exit before swapping.
                self.running = False
            elif kind == 'tool_current':
                self.tool_update_available = False
                self.operation = None
                self.status = 'Silver Lantern is up to date.'
                self.set_screen('main')
            elif kind == 'graphics':
                message, values = value
                self.status = message
                if values:
                    self.gfx_values = values
            elif kind == 'upstream_check' and value == 10:
                self.status = 'An upstream builder update is available.'

    def shortcut_worker(self):
        actions = ([lambda: create_shortcut(self.folder)] if self.config.get('shortcut', True) else [])
        actions.append(lambda: register_install(self.folder))
        for action in actions:
            try:
                action()
            except Exception as exc:
                STATE_DIR.mkdir(parents=True, exist_ok=True)
                with LOG.open('a', encoding='utf-8') as log: log.write('Shortcut: ' + str(exc) + '\n')

    # ---------------------------------------------------------------- drawing
    def free_space_text(self):
        now = time.monotonic()
        cached = getattr(self, '_free_cache', None)
        if cached and cached[0] == self.folder and now - cached[1] < 2:
            return cached[2]
        parent = self.folder
        while not parent.exists() and parent != parent.parent: parent = parent.parent
        try: value = f'{shutil.disk_usage(parent).free / 1e9:.1f} GB free · space needed is checked before download'
        except OSError: value = ''
        self._free_cache = (self.folder, now, value)
        return value

    def ease(self, key, target, dt, rate=0.035):
        """Exponential approach: ~95% of the way in about 105 ms."""
        anim = self.__dict__.setdefault('anim', {})
        current = anim.get(key, target)
        if getattr(self, 'snap', False) or dt <= 0:
            current = target
        else:
            current += (target - current) * (1 - math.exp(-dt / rate))
            if abs(current - target) < .5:
                current = target
        anim[key] = current
        return current

    def row_spec(self, label):
        """Split a settings label into (text, value, toggle) for display."""
        for prefix in ('Desktop shortcut', 'Check for updates automatically'):
            if label.startswith(prefix + ':'):
                return prefix, None, label.endswith('On')
        if label.startswith('Speed up installs'):
            return 'Speed up installs', 'One Windows permission', None
        if label == 'Change game folder':
            return 'Game folder', str(self.folder), None
        return label, None, None

    def draw_rows(self, p, labels, x, y, w, h, gap, dt, first=0):
        """Stacked rows with one eased focus highlight; registers mouse targets."""
        for i, _ in enumerate(labels):
            p.fill(x, y + i * (h + gap), w, h, SURFACE_HI, 165, 10)
        if first <= self.focus < first + len(labels):
            fy = self.ease('focus', y + (self.focus - first) * (h + gap), dt)
            p.fill(x, fy, w, h, SURFACE_FOCUS, 255, 10)
            p.ring(x, fy, w, h, ACCENT, 255, 10, 2)
        for i, label in enumerate(labels):
            index, top = first + i, y + i * (h + gap)
            focused = index == self.focus
            self.hits.append((x, top, w, h, index))
            text, value, toggle = self.row_spec(label)
            right = x + w - 20
            if toggle is not None:
                tw, th = 46, 26
                tx, ty = right - tw, top + (h - th) // 2
                p.fill(tx, ty, tw, th, ACCENT if toggle else (72, 80, 90), 255, th // 2)
                p.circle(tx + (tw - th // 2 if toggle else th // 2), ty + th // 2, th // 2 - 4, ACCENT_INK if toggle else INK)
                p.text('caption', 'On' if toggle else 'Off', tx - 12, top + (h - p.height('caption')) // 2, INK if toggle else INK_DIM, align='right')
                right = tx - 70
            elif value:
                fitted = p.fit_middle('caption', value, (w - 40) // 2)
                p.text('caption', fitted, right, top + (h - p.height('caption')) // 2, INK_DIM, align='right')
                right -= p.measure('caption', fitted)[0] + 24
            p.text('button', text, x + 20, top + (h - p.height('button')) // 2, INK if focused else (210, 215, 221), max_width=right - x - 20)

    def draw_hints(self, p, items):
        x, y = MARGIN, 672
        for key, label in items:
            kw = p.measure('overline', key)[0] + 16
            p.fill(x, y, kw, 24, SURFACE_HI, 230, 6)
            p.text('overline', key, x + 8, y + (24 - p.height('overline')) // 2, INK)
            x += kw + 8
            x += p.text('caption', label, x, y + (24 - p.height('caption')) // 2, INK_DIM) + 24

    def status_tone(self):
        status = self.status.lower()
        if "couldn't" in status or 'could not' in status or 'fail' in status:
            return DANGER
        if 'available' in status:
            return ACCENT
        if 'up to date' in status or 'ready' in status or 'game data:' in status:
            return SUCCESS
        return INK_FAINT

    def draw_hero(self, p, dt):
        p.scene()
        x = MARGIN
        if self.state in (State.READY, State.UPDATE_AVAILABLE):
            tip_y = 60 + tip_offset(time.monotonic(), getattr(self, 'snap', False) or self.reduce_motion)
            p.text('body', play_tip(self.folder), x, tip_y, INK_DIM, max_width=CANVAS[0] - 2 * MARGIN)
        p.text('overline', 'THE LEGEND OF ZELDA', x + 2, 160, ACCENT)
        p.text('display', 'Ocarina of Time', x - 2, 176, INK, max_width=760)
        if self.state == State.PLAYING:
            subtitle = 'Have fun! The launcher waits here while you play.'
        elif self.state in (State.READY, State.UPDATE_AVAILABLE):
            subtitle = 'Installed and ready to play.'
        else:
            subtitle = 'Not installed yet · about a 33 GB download.'
        p.text('body', subtitle, x, 270, INK_DIM, max_width=620)
        buttons = self.buttons()
        cta = {'Play': 'PLAY', 'Download': 'DOWNLOAD', 'Update now': 'UPDATE NOW'}.get(buttons[0]) if buttons else None
        top, w = 336, 340
        if cta:
            h = 68
            self.hits.append((x, top, w, h, 0))
            p.fill(x, top, w, h, ACCENT, 255, 12)
            if self.focus == 0:
                p.ring(x - 6, top - 6, w + 12, h + 12, ACCENT, 220, 17, 2)
            p.text('cta', cta, x + w // 2, top + (h - p.height('cta')) // 2, ACCENT_INK, max_width=w - 40, align='center')
            rows, first = buttons[1:], 1
        else:
            h = 68
            p.fill(x, top, w, h, SURFACE_HI, 200, 12)
            p.circle(x + 30, top + h // 2, 6, SUCCESS)
            p.text('cta', 'NOW PLAYING', x + 50, top + (h - p.height('cta')) // 2, INK, max_width=w - 70)
            rows, first = buttons, 0
        self.draw_rows(p, rows, x, top + h + 22, w, 50, 10, dt, first)
        p.circle(x + 5, 626 + p.height('caption') // 2, 4, self.status_tone())
        p.text('caption', self.status, x + 18, 626, INK_DIM, max_width=700)
        self.draw_hints(p, [('A / ENTER', 'Select'), ('START / F1', 'Settings')])
        p.text('caption', 'Silver Lantern v' + APP_VERSION, CANVAS[0] - MARGIN, 674, INK_FAINT, align='right')

    def draw_progress(self, p, dt):
        p.scene(scrim=150)
        x, bar_w = MARGIN, 780
        info = self.progress_info or {}
        tool = self.operation == 'tool'
        title = 'Updating Silver Lantern' if tool else ('Updating the game' if self.operation == 'update' else 'Installing Ocarina of Time')
        pct = info.get('pct')
        if pct is None:
            match = re.search(r'(\d{1,3})%', self.progress)
            pct = min(100, int(match.group(1))) if match else None
        phase = info.get('phase') or ('Preparing' if pct is None else 'Downloading')
        p.text('overline', phase.upper(), x + 2, 124, ACCENT, max_width=bar_w)
        p.text('title', title, x, 142, INK, max_width=bar_w)
        if pct is not None:
            p.text('figure', f'{pct}%', x - 2, 204, INK)
        else:
            body = 'Please wait — this usually takes under a minute.' if tool else (self.progress or 'Getting things ready…')
            p.text('body', body, x, 250, INK_DIM, max_width=bar_w)
        p.fill(x, 300, bar_w, 12, (255, 255, 255), 30, 6)
        if pct is not None:
            fill = self.ease('bar', bar_w * max(0, min(100, pct)) / 100, dt, rate=.12)
            if fill >= 12:
                p.fill(x, 300, fill, 12, ACCENT, 255, 6)
        else:
            phase_t = (time.monotonic() * .6) % 1
            seg = 180
            start = -seg + phase_t * (bar_w + seg)
            left, right = max(x, x + start), min(x + bar_w, x + start + seg)
            if right - left >= 12:
                p.fill(left, 300, right - left, 12, ACCENT, 255, 6)
        done, total, speed = info.get('done'), info.get('total'), info.get('speed') or 0
        if total:
            p.text('body', f'{fmt_bytes(done)} of {fmt_bytes(total)}', x, 326, INK)
            right = (fmt_bytes(speed) + '/s') if speed else ''
            if speed and total > done:
                right += ('  ·  ' if right else '') + fmt_eta((total - done) / speed) + ' left'
            if right:
                p.text('body', right, x + bar_w, 326, INK_DIM, align='right')
        if pct is not None or tool:
            detail = self.progress if not tool else "The launcher restarts by itself when it's done."
            p.text('caption', detail, x, 364, INK_FAINT, max_width=bar_w, middle=True)
        buttons = self.buttons()
        if buttons:
            self.draw_rows(p, buttons, x, 420, 240, 50, 10, dt)
            self.draw_hints(p, [('A / ENTER', 'Select'), ('B / ESC', 'Stop download')])
        else:
            self.draw_hints(p, [])

    def draw_panel(self, p, dt):
        p.scene(scrim=205)
        x, w = MARGIN, 720
        overline, title = {'settings': ('LAUNCHER', 'Settings'), 'folder': ('GAME FOLDER', 'Where should the game go?'),
                           'error': ('ERROR', 'Something went wrong'), 'update': ('GAME DATA', 'Update the game?')}[self.screen]
        p.text('overline', overline, x + 2, 76, DANGER if self.screen == 'error' else ACCENT)
        p.text('title', title, x, 94, INK, max_width=w)
        top = 172
        buttons = self.buttons()
        if self.screen == 'settings':
            visible = 7
            first = min(getattr(self, 'settings_first', 0), max(0, len(buttons) - visible))
            first = max(0, min(first, self.focus))
            first = max(first, self.focus - visible + 1)
            self.settings_first = first
            self.draw_rows(p, buttons[first:first + visible], x, top, w, 52, 8, dt, first=first)
            p.text('caption', self.free_space_text(), x, 604, INK_FAINT, max_width=w)
        elif self.screen == 'folder':
            p.fill(x, top, w, 66, SURFACE_HI, 200, 10)
            p.text('overline', 'INSTALL LOCATION', x + 20, top + 10, INK_DIM)
            p.text('body', str(self.folder), x + 20, top + 30, INK, max_width=w - 40, middle=True)
            p.text('caption', self.free_space_text(), x, top + 78, INK_FAINT, max_width=w)
            self.draw_rows(p, buttons, x, top + 120, 420, 50, 10, dt)
        elif self.screen == 'error':
            lines = p.wrap('body', self.last_error or 'An unknown error occurred.', w - 24, 4)
            p.fill(x, top, 4, len(lines) * 30 - 2, DANGER, 255, 2)
            for i, line in enumerate(lines):
                p.text('body', line, x + 20, top + i * 30, INK)
            p.text('caption', 'Details are in the log.', x + 20, top + len(lines) * 30 + 6, INK_FAINT)
            self.draw_rows(p, buttons, x, top + len(lines) * 30 + 50, 320, 50, 10, dt)
        else:
            p.text('body', 'Only changed files are downloaded. Your saves are not touched.', x, top, INK_DIM, max_width=w)
            self.draw_rows(p, buttons, x, top + 56, 320, 50, 10, dt)
        if self.screen in ('settings', 'folder') and self.status:
            p.circle(x + 5, 630 + p.height('caption') // 2, 4, self.status_tone())
            p.text('caption', self.status, x + 18, 630, INK_DIM, max_width=w - 18)
        self.draw_hints(p, [('A / ENTER', 'Select'), ('B / ESC', 'Back')])

    GFX_ACTION_HELP = {'Recommended for this PC': "DLSS (NVIDIA RTX) or FSR 3.1 Performance with frame generation and pacing, picked for this PC's graphics card.",
                       'Reset to game defaults': "Every option back to the game's own default. Your controls and bindings stay.",
                       'Back': 'Back to the launcher settings.'}

    def gfx_layout(self):
        """(kind, payload, top, height) for the scrolling list: group headings and option rows."""
        rows, y, group = [], 0, None
        for i, o in enumerate(game_settings.OPTIONS):
            if o.group != group:
                group = o.group
                y += 12 if rows else 0
                rows.append(('heading', group, y, 30))
                y += 30
            rows.append(('option', len(self.GFX_ACTIONS) + i, y, 44))
            y += 50
        return rows, y

    def draw_graphics(self, p, dt):
        p.scene(scrim=215)
        x, w = MARGIN, CANVAS[0] - 2 * MARGIN
        values, picker = self.gfx_values, self.gfx_picker
        p.text('overline', 'SETTINGS · GAME', x + 2, 56, ACCENT)
        p.text('title', 'Graphics', x, 74, INK, max_width=400)
        p.text('caption', str(game_settings.ini_path(self.folder)), x + w, 96, INK_FAINT, max_width=560, align='right', middle=True)
        # Action buttons along the top.
        ax, ay, ah = x, 138, 44
        for i, label in enumerate(self.GFX_ACTIONS):
            aw = p.measure('button', label)[0] + 40
            focused = picker is None and self.focus == i
            p.fill(ax, ay, aw, ah, SURFACE_FOCUS if focused else SURFACE_HI, 255 if focused else 200, 10)
            if focused:
                p.ring(ax, ay, aw, ah, ACCENT, 255, 10, 2)
            p.text('button', label, ax + 20, ay + (ah - p.height('button')) // 2, ACCENT if i == 0 else INK)
            if picker is None:
                self.hits.append((ax, ay, aw, ah, i))
            ax += aw + 12
        # The scrolling, grouped option list.
        rows, total = self.gfx_layout()
        view_top, view_h = 200, 384
        target = getattr(self, 'gfx_scroll_target', 0)
        if self.focus < len(self.GFX_ACTIONS):
            target = 0
        for kind, idx, top, h in rows:
            if kind == 'option' and idx == self.focus:
                target = min(target, top - 42)
                target = max(target, top + h + 12 - view_h)
        target = max(0, min(target, max(0, total - view_h)))
        self.gfx_scroll_target = target
        scroll = self.ease('gfx_scroll', target, dt, rate=.06)
        s = p.s
        s.SDL_RenderSetClipRect(p.r, s.SDL_Rect(x - 4, view_top, w + 8, view_h))
        focus_y = None
        for kind, idx, top, h in rows:
            y = view_top + top - scroll
            if y + h < view_top or y > view_top + view_h:
                continue
            if kind == 'heading':
                p.text('overline', idx.upper(), x + 2, y + 10, ACCENT)
                continue
            p.fill(x, y, w, h, SURFACE_HI, 150, 10)
            if idx == self.focus and picker is None:
                focus_y = y
        if focus_y is not None:
            fy = self.ease('focus', focus_y, dt)
            p.fill(x, fy, w, 44, SURFACE_FOCUS, 255, 10)
            p.ring(x, fy, w, 44, ACCENT, 255, 10, 2)
        for kind, idx, top, h in rows:
            y = view_top + top - scroll
            if kind != 'option' or y + h < view_top or y > view_top + view_h:
                continue
            opt = self.gfx_option(idx)
            value = values.get(opt.key, opt.default)
            lock = game_settings.lock_reason(opt.key, values)
            focused = idx == self.focus and picker is None
            mid = y + (h - p.height('body')) // 2
            right = x + w - 20
            if focused:
                right -= p.text('body', '›', right, mid, ACCENT, align='right') + 10
            vw = p.text('body', opt.text(value), right, mid, INK_FAINT if lock else (ACCENT if focused else INK), max_width=300, align='right')
            right -= vw + 10
            if focused:
                right -= p.text('body', '‹', right, mid, ACCENT, align='right') + 14
            if lock:
                right -= p.text('caption', lock, right, y + (h - p.height('caption')) // 2, INK_FAINT, max_width=280, align='right') + 16
            elif opt.restart:
                right -= p.text('caption', 'after restart', right, y + (h - p.height('caption')) // 2, INK_FAINT, align='right') + 16
            p.text('button', opt.label, x + 20, y + (h - p.height('button')) // 2, INK if focused else (210, 215, 221), max_width=right - x - 30)
            if picker is None and y >= view_top and y + h <= view_top + view_h:
                self.hits.append((x, y, w, h, idx))
        s.SDL_RenderSetClipRect(p.r, None)
        if total > view_h:
            thumb = max(40, view_h * view_h // total)
            ty = view_top + (view_h - thumb) * scroll / max(1, total - view_h)
            p.fill(x + w + 14, view_top, 4, view_h, (255, 255, 255), 25, 2)
            p.fill(x + w + 14, ty, 4, thumb, ACCENT, 200, 2)
        # Help for the focused row, then the status line.
        opt = game_settings.BY_KEY[picker] if picker is not None else self.gfx_option()
        help_text = opt.desc if opt else self.GFX_ACTION_HELP.get(self.GFX_ACTIONS[min(self.focus, len(self.GFX_ACTIONS) - 1)], '')
        p.text('caption', help_text, x, 598, INK_DIM, max_width=w)
        if self.status:
            p.circle(x + 5, 628 + p.height('caption') // 2, 4, self.status_tone())
            p.text('caption', self.status, x + 18, 628, INK_DIM, max_width=w - 18)
        if picker is not None:
            self.draw_gfx_picker(p, dt)
            self.draw_hints(p, [('A / ENTER', 'Choose'), ('B / ESC', 'Cancel')])
        else:
            self.draw_hints(p, [('A / ENTER', 'Change'), ('← / →', 'Adjust'), ('B / ESC', 'Back')])

    def draw_gfx_picker(self, p, dt):
        opt = game_settings.BY_KEY[self.gfx_picker]
        labels = self.buttons()
        p.fill(0, 0, *CANVAS, SURFACE, 170)
        pw, rh, gap = 460, 40, 6
        ph = 84 + len(labels) * (rh + gap)
        px, py = (CANVAS[0] - pw) // 2, max(40, (CANVAS[1] - ph) // 2 - 10)
        p.fill(px, py, pw, ph, SURFACE, 250, 16)
        p.ring(px, py, pw, ph, SURFACE_FOCUS, 255, 16, 2)
        p.text('overline', 'CHOOSE A VALUE', px + 24, py + 18, ACCENT)
        p.text('heading', opt.label, px + 24, py + 36, INK, max_width=pw - 48)
        top = py + 72
        self.draw_rows(p, labels, px + 20, top, pw - 40, rh, gap, dt)
        allowed = game_settings.allowed(opt.key, self.gfx_dlss_ok)
        current = self.gfx_values.get(opt.key, opt.default)
        if current in allowed:
            i = allowed.index(current)
            p.text('caption', 'Current', px + pw - 40, top + i * (rh + gap) + (rh - p.height('caption')) // 2, ACCENT, align='right')

    def render(self, p, dt):
        self.hits = []
        count = len(self.buttons())
        if count and self.focus >= count:
            self.focus = count - 1
        if self.screen == 'main': self.draw_hero(p, dt)
        elif self.screen == 'progress': self.draw_progress(p, dt)
        elif self.screen == 'graphics': self.draw_graphics(p, dt)
        else: self.draw_panel(p, dt)

    def screenshot_scenes(self):
        """Realistic fake states for the --screenshot design/regression loop."""
        long = Path('C:/Games/' + 'Very Long Game Folder Name/' * 8 + 'Ocarina of Time')
        def scene(name, screen, state, status='', focus=0, **values):
            def setup():
                self.state, self.status = state, status
                self.tool_update_available, self.operation = False, None
                self.set_screen(screen)
                for key, value in values.items(): setattr(self, key, value)
                self.focus = focus
            return name, setup
        defaults = {o.key: o.default for o in game_settings.OPTIONS}
        dlss_values = dict(defaults, **{k: int(v) for k, v in game_settings.PRESET_DLSS.items() if k in game_settings.BY_KEY})
        fsr_values = dict(defaults, **{k: int(v) for k, v in game_settings.PRESET_FSR.items() if k in game_settings.BY_KEY}, hdr=1, output_res=2)
        info = {'phase': 'Downloading', 'done': int(20.83 * 1024 ** 3), 'total': int(33.05 * 1024 ** 3), 'speed': int(58.4 * 1024 ** 2), 'pct': 63}
        return [
            scene('main', 'main', State.READY, 'Silver Lantern is up to date.'),
            scene('hero-download', 'main', State.NOT_INSTALLED, 'Game data: 2026.11.16', folder=long),
            scene('progress', 'progress', State.DOWNLOADING, operation='download', progress_info=info,
                  progress='Downloading: 63% — ' + str(long / 'data' / 'textures' / 'kokiri_forest_hd_pack_part_07.pak')),
            scene('tool-update', 'progress', State.READY, operation='tool', progress_info={}, progress='Updating Silver Lantern…'),
            scene('settings', 'settings', State.READY, 'Silver Lantern is up to date.', focus=2, folder=long),
            scene('settings-back', 'settings', State.READY, 'Silver Lantern is up to date.', focus=7, folder=long),
            scene('folder', 'folder', State.NOT_INSTALLED, '', focus=1, folder=long),
            scene('update', 'update', State.UPDATE_AVAILABLE),
            scene('error', 'error', State.ERROR, operation='download', last_error='Could not reach the update server. The connection stalled three times while downloading whole-07.bin from objects.githubusercontent.com; check your network and try again.'),
            scene('graphics', 'graphics', State.READY, 'Frame generation: Auto · saved', focus=len(self.GFX_ACTIONS) + 1,
                  gfx_values=dlss_values, gfx_picker=None, gfx_dlss_ok=True),
            scene('graphics-fsr', 'graphics', State.READY, 'Recommended applied: FSR 3.1 Performance', focus=len(self.GFX_ACTIONS) + 16,
                  gfx_values=fsr_values, gfx_picker=None, gfx_dlss_ok=False, folder=long),
            scene('graphics-actions', 'graphics', State.READY, 'Saved to the game at once. In game: F1 or Insert opens its menu; its last row, Quit (A twice), exits the game.', focus=0,
                  gfx_values=dlss_values, gfx_picker=None, gfx_dlss_ok=True),
            scene('graphics-picker', 'graphics', State.READY, '', focus=1, gfx_values=dlss_values, gfx_picker='frame_generation',
                  gfx_return=len(self.GFX_ACTIONS) + 5, gfx_dlss_ok=True),
        ]

    def run(self, screenshot=None):
        if screenshot:
            os.environ['SDL_VIDEODRIVER'] = 'dummy'
        if getattr(sys, 'frozen', False): os.environ['PYSDL2_DLL_PATH'] = str(ROOT)
        import sdl2 as s
        import sdl2.sdlttf as ttf
        s.SDL_SetHint(b'SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS', b'0')
        s.SDL_SetHint(b'SDL_GAMECONTROLLER_USE_BUTTON_LABELS', b'1')
        if s.SDL_Init(s.SDL_INIT_VIDEO | s.SDL_INIT_GAMECONTROLLER) != 0: raise RuntimeError(s.SDL_GetError().decode())
        if ttf.TTF_Init() != 0: raise RuntimeError('SDL_ttf initialization failed')
        size = CANVAS
        if screenshot and os.environ.get('HEYLISTEN_SCREENSHOT_SIZE'):
            match = re.fullmatch(r'(\d{1,4})x(\d{1,4})', os.environ['HEYLISTEN_SCREENSHOT_SIZE'])
            if not match or not all(1 <= int(n) <= 4096 for n in match.groups()):
                raise ValueError('Invalid screenshot size')
            size = tuple(map(int, match.groups()))
        window = s.SDL_CreateWindow('Ocarina of Time'.encode('utf-8'), s.SDL_WINDOWPOS_CENTERED, s.SDL_WINDOWPOS_CENTERED, *size, s.SDL_WINDOW_RESIZABLE | s.SDL_WINDOW_ALLOW_HIGHDPI)
        renderer = create_renderer(s, window, screenshot=bool(screenshot))
        if not window or not renderer: raise RuntimeError('Could not create the launcher window')
        s.SDL_RenderSetLogicalSize(renderer, 1280, 720)
        s.SDL_SetRenderDrawColor(renderer, *SURFACE, 255)
        s.SDL_RenderClear(renderer)
        s.SDL_RenderPresent(renderer)
        painter = Painter(s, ttf, renderer)
        def open_pads():
            for i in range(s.SDL_NumJoysticks()):
                if s.SDL_IsGameController(i):
                    p = s.SDL_GameControllerOpen(i)
                    if p:
                        joystick = s.SDL_GameControllerGetJoystick(p)
                        ident = s.SDL_JoystickInstanceID(joystick)
                        if ident in self.controllers: s.SDL_GameControllerClose(p)
                        else: self.controllers[ident] = p
        open_pads()
        event = s.SDL_Event()
        scenes = iter(self.screenshot_scenes()) if screenshot else None
        scene_name = None
        if screenshot:
            self.snap = True
            scene_name, setup = next(scenes)
            setup()
        self.hits = []
        last = time.monotonic()
        try:
            while self.running:
                self.drain()
                now = time.monotonic()
                for action in self.mapper.tick(now): self.action(action)
                while s.SDL_PollEvent(ctypes.byref(event)):
                    typ = event.type
                    if typ == s.SDL_QUIT:
                        if not (self.screen == 'progress' and self.operation == 'tool'): self.running = False
                    elif typ == s.SDL_CONTROLLERDEVICEADDED: open_pads()
                    elif typ == s.SDL_CONTROLLERDEVICEREMOVED:
                        pad = self.controllers.pop(event.cdevice.which, None)
                        if pad: s.SDL_GameControllerClose(pad)
                    elif typ in (s.SDL_CONTROLLERBUTTONDOWN, s.SDL_CONTROLLERBUTTONUP):
                        button = {s.SDL_CONTROLLER_BUTTON_A:'a', s.SDL_CONTROLLER_BUTTON_B:'b', s.SDL_CONTROLLER_BUTTON_START:'start', s.SDL_CONTROLLER_BUTTON_X:'x'}.get(event.cbutton.button)
                        direction = {s.SDL_CONTROLLER_BUTTON_DPAD_UP:'up', s.SDL_CONTROLLER_BUTTON_DPAD_DOWN:'down', s.SDL_CONTROLLER_BUTTON_DPAD_LEFT:'left', s.SDL_CONTROLLER_BUTTON_DPAD_RIGHT:'right'}.get(event.cbutton.button)
                        if direction and typ == s.SDL_CONTROLLERBUTTONDOWN: self.action(direction)
                        if button:
                            for action in self.mapper.feed({'type':'button','button':button,'down':typ == s.SDL_CONTROLLERBUTTONDOWN}, now): self.action(action)
                    elif typ == s.SDL_CONTROLLERAXISMOTION and event.caxis.axis in (0, 1):
                        self.axis[event.caxis.axis] = max(-1, event.caxis.value / 32767)
                        for action in self.mapper.feed({'type':'axis','x':self.axis[0],'y':self.axis[1]}, now): self.action(action)
                    elif typ == s.SDL_KEYDOWN and not event.key.repeat:
                        action = {s.SDLK_UP:'up',s.SDLK_w:'up',s.SDLK_DOWN:'down',s.SDLK_s:'down',s.SDLK_LEFT:'left',s.SDLK_a:'left',s.SDLK_RIGHT:'right',s.SDLK_d:'right',s.SDLK_RETURN:'accept',s.SDLK_SPACE:'accept',s.SDLK_ESCAPE:'back',s.SDLK_BACKSPACE:'back',s.SDLK_F1:'menu',s.SDLK_TAB:'menu'}.get(event.key.keysym.sym)
                        if action: self.action(action)
                    elif typ == s.SDL_MOUSEWHEEL and self.screen in ('graphics', 'settings'):
                        for _ in range(min(5, abs(event.wheel.y))): self.action('up' if event.wheel.y > 0 else 'down')
                    elif typ in (s.SDL_MOUSEMOTION, s.SDL_MOUSEBUTTONUP):
                        mx = event.motion.x if typ == s.SDL_MOUSEMOTION else event.button.x
                        my = event.motion.y if typ == s.SDL_MOUSEMOTION else event.button.y
                        # The renderer reports mouse positions in logical (1280x720) coordinates.
                        for hx, hy, hw, hh, index in self.hits:
                            if hx <= mx < hx + hw and hy <= my < hy + hh and index < len(self.buttons()):
                                if not (self.screen == 'progress' and self.operation == 'tool'):
                                    self.focus = index
                                    if typ == s.SDL_MOUSEBUTTONUP and event.button.button == s.SDL_BUTTON_LEFT: self.activate()
                                break
                frame_now = time.monotonic()
                dt, last = min(.1, frame_now - last), frame_now
                output_w, output_h = ctypes.c_int(), ctypes.c_int()
                if s.SDL_GetRendererOutputSize(renderer, ctypes.byref(output_w), ctypes.byref(output_h)) == 0:
                    painter.set_density(render_density(output_w.value, output_h.value))
                self.render(painter, dt)
                if screenshot:
                    surface = s.SDL_CreateRGBSurfaceWithFormat(0, size[0], size[1], 32, s.SDL_PIXELFORMAT_ARGB8888)
                    s.SDL_RenderSetLogicalSize(renderer, 0, 0)
                    if not surface or s.SDL_RenderReadPixels(renderer, None, s.SDL_PIXELFORMAT_ARGB8888, surface.contents.pixels, surface.contents.pitch) != 0:
                        raise RuntimeError('Could not capture launcher frame')
                    path = Path(screenshot)
                    output = path.with_name(path.stem + '-' + scene_name + '.bmp')
                    if s.SDL_SaveBMP(surface, str(output).encode()) != 0:
                        raise RuntimeError('Could not save launcher frame')
                    s.SDL_FreeSurface(surface)
                    s.SDL_RenderSetLogicalSize(renderer, *CANVAS)
                    nxt = next(scenes, None)
                    if nxt is None:
                        self.operation = None
                        break
                    scene_name, setup = nxt
                    setup()
                    continue
                s.SDL_RenderPresent(renderer)
                s.SDL_Delay(16)
        finally:
            if self.screen == 'progress' and self.operation != 'tool': terminate_worker(self.events)
            for pad in self.controllers.values(): s.SDL_GameControllerClose(pad)
            painter.close(); s.SDL_DestroyRenderer(renderer); s.SDL_DestroyWindow(window); ttf.TTF_Quit(); s.SDL_Quit()


def show_startup_error(exc):
    log_message(f'Startup error: {exc}')
    try:
        ui = LauncherUI(startup_error=exc)
        ui.last_error = str(exc)
        ui.operation = 'startup'
        ui.set_screen('error')
        ui.run()
    except Exception as screen_error:
        log_message(f'Unable to display error screen: {screen_error}')
    return 1


def main():
    if '--cli' in sys.argv:
        args = sys.argv[1:]; args.remove('--cli')
        return subprocess.call(cli_command(*args))
    if '--self-test' in sys.argv:
        app = App(State.NOT_INSTALLED); app.transition(State.DOWNLOADING); app.transition(State.READY)
        return 0
    try:
        if '--screenshot' in sys.argv:
            index = sys.argv.index('--screenshot')
            if index + 1 >= len(sys.argv):
                raise ValueError('--screenshot requires an output path')
            original_check = LauncherUI.check_updates
            try:
                LauncherUI.check_updates = lambda self: None
                LauncherUI().run(screenshot=sys.argv[index + 1])
            finally:
                LauncherUI.check_updates = original_check
        else:
            LauncherUI().run()
        return 0
    except Exception as exc:
        return show_startup_error(exc)


if __name__ == '__main__':
    raise SystemExit(main())
