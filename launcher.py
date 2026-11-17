"""Headless launcher state model; UI integration is not yet implemented."""
from enum import Enum, auto
import queue
import threading
import argparse
import ctypes
import json
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

ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
BASE = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
STATE_DIR = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'SilverLantern'
CONFIG = STATE_DIR / 'launcher.json'
LOG = STATE_DIR / 'launcher.log'
GAME_NAME = 'Legend of Zelda Ocarina of Time'
MANIFEST_URL = 'https://raw.githubusercontent.com/cputroll/Silver-Lantern/main/game-manifest.json'
APP_VERSION = getattr(sys.modules.get('__main__'), 'APP_VERSION', '1.4.2')


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


def shortcut_path():
    return Path(os.environ.get('USERPROFILE', str(Path.home()))) / 'Desktop' / (GAME_NAME + '.lnk')


def create_shortcut(folder, enabled=True):
    link = shortcut_path()
    if not enabled:
        link.unlink(missing_ok=True)
        return
    target = folder / 'HeyListen.exe'
    if not target.exists():
        target = Path(sys.executable) if getattr(sys, 'frozen', False) else BASE / 'SilverLantern.exe'
    create_link(link, target, folder)


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def powershell(script, wait=True):
    encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
    args = ['powershell', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded]
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    if wait:
        return subprocess.run(args, check=True, creationflags=flags)
    return subprocess.Popen(args, creationflags=flags, close_fds=True)


def defender_elevated(folder, enable):
    if os.name != 'nt':
        return False
    cmd = ('Add-MpPreference' if enable else 'Remove-MpPreference') + ' -ExclusionPath ' + ps_quote(folder) + ' -ErrorAction Stop'
    encoded = base64.b64encode(cmd.encode('utf-16le')).decode('ascii')
    script = ("$ErrorActionPreference='Stop'; try { $p=Start-Process powershell -Verb RunAs -Wait -PassThru -WindowStyle Hidden "
              "-ArgumentList '-NoProfile -NonInteractive -EncodedCommand " + encoded + "'; if ($null -eq $p) { exit 1 }; exit $p.ExitCode } catch { exit 1 }")
    try:
        powershell(script)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def defender_config():
    try:
        return json.loads(CONFIG.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return {}


def optimize_defender(folder):
    config = defender_config()
    folder = Path(folder).resolve()
    if config.get('defender_state') == 'on':
        if config.get('defender_path') == str(folder):
            return True
        # Never lose track of an exclusion when the game folder changes.
        return False
    print('Optimizing for a faster install — click Yes if Windows asks.', flush=True)
    success = defender_elevated(folder, True)
    config['defender_state'] = 'on' if success else 'unknown'
    if success:
        config['defender_path'] = str(folder)
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
    powershell(script)


def start_menu_path():
    return Path(os.environ.get('APPDATA', str(Path.home()))) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs' / (GAME_NAME + '.lnk')


UNINSTALL_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\SilverLantern'
OWNED_FILES = ('SilverLantern.exe', 'HeyListen-Get.exe', 'SDL2.dll', 'sources.json',
               'README.md', 'Apply-HeyListen-Preset.ps1', 'SilverLantern-Get.cmd',
               'SilverLantern-Preset.cmd', 'Update-Game.ps1', 'Watch-Game.ps1',
               'SilverLantern-AutoUpdate.cmd', 'sources.cache.json', 'HeyListen-Get.log',
               'SilverLantern.exe.update-backup', 'HeyListen-Get.exe.update-backup')


def register_install(folder):
    if winreg is None:
        return
    exe = Path(sys.executable)
    link = start_menu_path()
    link.parent.mkdir(parents=True, exist_ok=True)
    create_link(link, exe, exe.parent)
    size = 0
    for base, dirs, files in os.walk(folder, followlinks=False):
        for name in files:
            try:
                size += (Path(base) / name).stat().st_size
            except OSError:
                pass
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        for name, value, kind in (
            ('DisplayName', GAME_NAME, winreg.REG_SZ), ('DisplayIcon', str(exe), winreg.REG_SZ),
            ('InstallLocation', str(exe.parent), winreg.REG_SZ),
            ('UninstallString', '"' + str(exe) + '" --uninstall', winreg.REG_SZ),
            ('NoModify', 1, winreg.REG_DWORD), ('NoRepair', 1, winreg.REG_DWORD),
            ('EstimatedSize', min((size + 1023) // 1024, 0xffffffff), winreg.REG_DWORD)):
            winreg.SetValueEx(key, name, 0, kind, value)


def remove_registration():
    if winreg is not None:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
        except FileNotFoundError:
            pass


def load_game_folder():
    try:
        value = json.loads(CONFIG.read_text(encoding='utf-8')).get('game_dir')
        return Path(value).expanduser() if value else None
    except (OSError, ValueError, TypeError):
        return None


def schedule_owned_cleanup():
    if not getattr(sys, 'frozen', False):
        return
    folder = Path(sys.executable).resolve().parent
    files = ','.join(ps_quote(folder / name) for name in OWNED_FILES)
    script = (f'$p=Get-Process -Id {os.getpid()} -ErrorAction SilentlyContinue; '
              'if($p){$p.WaitForExit()}; '
              f'@({files}) | ForEach-Object {{ Remove-Item -LiteralPath $_ -Force -ErrorAction SilentlyContinue }}; '
              f'try {{ [System.IO.Directory]::Delete({ps_quote(folder)}, $false) }} catch {{}}')
    powershell(script, wait=False)


def uninstall(keep_saves=False):
    folder = load_game_folder()
    print('This removes the entire game folder and all its contents' + (' except the user saves directory' if keep_saves else ', including saves') + ', shortcuts, launcher files and settings.')
    print('Game folder:', folder or '(not configured)')
    if input('Continue? [y/N] ').strip().lower() != 'y':
        print('Uninstall cancelled.')
        return False
    had_folder = bool(folder and folder.exists())
    if had_folder:
        game = folder.resolve()
        home = Path.home().resolve()
        state = STATE_DIR.resolve()
        if game == Path(game.anchor) or home == game or home.is_relative_to(game) or state == game or state.is_relative_to(game):
            raise RuntimeError('Refusing to remove an unsafe game folder.')
        if getattr(sys, 'frozen', False):
            program = Path(sys.executable).resolve().parent
            game = folder.resolve()
            if program == game or program.is_relative_to(game):
                raise RuntimeError('Cannot safely remove a game folder containing the launcher; move the launcher first.')
        if not remove_defender_exclusion():
            raise RuntimeError('Could not remove the Windows Defender exclusion; retry uninstall or remove it in Windows Security.')
        if keep_saves:
            for child in folder.iterdir():
                if child.name.casefold() == 'user':
                    continue
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        else:
            shutil.rmtree(folder)
    if not had_folder and not remove_defender_exclusion():
        raise RuntimeError('Could not remove the Windows Defender exclusion; retry uninstall or remove it in Windows Security.')
    shortcut_path().unlink(missing_ok=True)
    shortcut_path().with_name('Silver Lantern.lnk').unlink(missing_ok=True)
    start_menu_path().unlink(missing_ok=True)
    remove_registration()
    if STATE_DIR.exists():
        shutil.rmtree(STATE_DIR)
    schedule_owned_cleanup()
    return True


def parse_progress(line):
    if line.startswith('HLUPDATE '):
        return dict(re.findall(r'(\w+)=([^ ]+)', line[9:]))
    if 'Downloading:' in line:
        return {'text': line.strip()}
    return None


def worker_command(events, args):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen(cli_command(*args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', bufsize=1, creationflags=getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
        events.process = process
        if events.cancelled.is_set():
            terminate_worker(events)
        with LOG.open('a', encoding='utf-8') as log:
            while True:
                line = process.stdout.readline()
                if not line:
                    break
                log.write(line)
                log.flush()
                events.publish('progress', parse_progress(line) or {'text': line.strip()})
        code = process.wait()
        events.publish('cancelled' if events.cancelled.is_set() else ('complete' if code == 0 else 'error'), code)
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

class LauncherUI:
    def __init__(self):
        self.profile = Path(os.environ.get('USERPROFILE', str(Path.home())))
        self.primary = Path(r'C:\Games') / GAME_NAME
        self.fallback = self.profile / 'Games' / GAME_NAME
        self.legacy = self.profile / 'Games' / 'HeyListen'
        try:
            self.config = json.loads(CONFIG.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.config = {}
        self.folder = Path(self.config.get('game_dir', self.legacy if legacy_exists(self.legacy) else self.primary))
        self.screen = 'main' if self.config else 'folder'
        self.state = State.READY if installed(self.folder) else State.NOT_INSTALLED
        self.focus = 0
        self.status = 'Ready to play.' if self.state == State.READY else 'The game is not installed yet.'
        self.progress = ''
        self.events = ProgressEvents()
        self.mapper = InputMapper()
        self.controllers = {}
        self.operation = None
        self.axis = [0.0, 0.0]
        self.running = True
        self.last_error = ''
        self.tool_update_available = False
        if self.state == State.READY and getattr(sys, 'frozen', False):
            threading.Thread(target=self.shortcut_worker, daemon=True).start()
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
        def check():
            try:
                req = urllib.request.Request(MANIFEST_URL, headers={'User-Agent': 'SilverLantern'})
                with urllib.request.urlopen(req, timeout=10) as response:
                    manifest = json.load(response)
                # TODO v1.5: cache the verified manifest hash after installation.
                digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                self.events.publish('check', (manifest.get('revision'), digest))
            except Exception:
                self.events.publish('offline', None)
            try:
                req = urllib.request.Request('https://api.github.com/repos/cputroll/Silver-Lantern/releases/latest', headers={'User-Agent':'SilverLantern', 'Accept':'application/vnd.github+json'})
                with urllib.request.urlopen(req, timeout=10) as response:
                    release = json.load(response)
                self.events.publish('tool_check', release.get('tag_name', 'unknown'))
                if installed(self.folder):
                    result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-File', str(BASE / 'Update-Game.ps1'), '-DryRun', '-GameFolder', str(self.folder)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=90, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    self.events.publish('upstream_check', result.returncode)
            except Exception:
                self.events.publish('offline', None)
        threading.Thread(target=check, daemon=True).start()

    def buttons(self):
        if self.screen == 'folder':
            return (['Update now'] if self.tool_update_available else []) + (['Use existing folder'] if legacy_exists(self.legacy) else []) + ['Use default folder', 'Choose another folder', 'Continue']
        if self.screen == 'settings':
            return ['Change game folder', 'Speed up installs (one Windows permission, instant)', 'Desktop shortcut: ' + ('On' if self.config.get('shortcut', True) else 'Off'), 'Check for updates automatically: ' + ('On' if self.config.get('auto_tool_update', True) else 'Off'), 'Check for updates now', 'View log', 'Back']
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

    def start_tool_update(self):
        if self.operation == 'tool' and self.screen == 'progress':
            return
        self.operation = 'tool'
        self.progress = 'Updating Silver Lantern…'
        self.set_screen('progress')
        args = [arg for arg in sys.argv[1:] if arg not in ('--self-update', '--no-self-update')]
        threading.Thread(target=tool_update_worker, args=(self.events, args), daemon=True).start()

    def start_operation(self, update=False):
        try:
            probe_writable(self.folder)
            if shutil.disk_usage(self.folder).free < 40_000_000_000 and not update:
                raise OSError('Not enough free space. About 40 GB is needed.')
            self.save()
        except OSError as exc:
            self.last_error = str(exc)
            self.set_screen('error')
            return
        self.operation = 'update' if update else 'download'
        self.state = State.UPDATING if update else State.DOWNLOADING
        self.events = ProgressEvents()
        args = ['--no-self-update', '--update-game' if update else '--install-game', str(self.folder)]
        self.set_screen('progress')
        threading.Thread(target=worker_command, args=(self.events, args), daemon=True).start()

    def activate(self):
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
            if self.operation == 'tool': self.start_tool_update()
            else: self.start_operation(self.operation == 'update')
        elif choice == 'View log':
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            LOG.touch(exist_ok=True)
            os.startfile(LOG)
        elif choice == 'Speed up installs (one Windows permission, instant)':
            self.status = 'Optimization will be attempted with the next install plan.'
        elif choice == 'Change game folder': self.set_screen('folder')
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
                self.save()
                self.state = State.READY if installed(self.folder) else State.NOT_INSTALLED
                self.set_screen('main')
            except OSError as exc:
                self.last_error = str(exc)
                self.set_screen('error')

    def action(self, action):
        if self.screen == 'progress' and self.operation == 'tool':
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
            if kind == 'progress': self.progress = value.get('text', str(value))
            elif kind == 'complete':
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
            elif kind == 'offline': self.status = "Couldn't check for updates. You can still play."
            elif kind == 'check':
                revision, digest = value
                self.latest_manifest_hash = digest
                if self.state == State.READY and self.config.get('manifest_hash') != digest:
                    self.state = State.UPDATE_AVAILABLE
                    self.status = 'A game data update may be available.'
                else: self.status = 'Game data: ' + (str(revision) if revision is not None else 'unversioned')
            elif kind == 'tool_check':
                match = re.fullmatch(r'v(\d+)\.(\d+)\.(\d+)', value)
                if match and tuple(map(int, match.groups())) > tuple(map(int, APP_VERSION.split('.'))):
                    self.tool_update_available = True
                    self.status = 'Silver Lantern update available: ' + value
                    if self.config.get('auto_tool_update', True) and self.screen != 'progress':
                        self.start_tool_update()
            elif kind == 'tool_complete':
                # The child must exit first; its helper then waits for this GUI to exit before swapping.
                self.running = False
            elif kind == 'tool_current':
                self.tool_update_available = False
                self.operation = None
                self.status = 'Silver Lantern is up to date.'
                self.set_screen('main')
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

    def run(self):
        if getattr(sys, 'frozen', False): os.environ['PYSDL2_DLL_PATH'] = str(ROOT)
        import sdl2 as s
        import sdl2.sdlttf as ttf
        s.SDL_SetHint(b'SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS', b'0')
        s.SDL_SetHint(b'SDL_GAMECONTROLLER_USE_BUTTON_LABELS', b'1')
        if s.SDL_Init(s.SDL_INIT_VIDEO | s.SDL_INIT_GAMECONTROLLER) != 0: raise RuntimeError(s.SDL_GetError().decode())
        if ttf.TTF_Init() != 0: raise RuntimeError('SDL_ttf initialization failed')
        window = s.SDL_CreateWindow(b'Silver Lantern', s.SDL_WINDOWPOS_CENTERED, s.SDL_WINDOWPOS_CENTERED, 1280, 720, s.SDL_WINDOW_RESIZABLE)
        renderer = s.SDL_CreateRenderer(window, -1, s.SDL_RENDERER_ACCELERATED | s.SDL_RENDERER_PRESENTVSYNC)
        if not renderer: renderer = s.SDL_CreateRenderer(window, -1, s.SDL_RENDERER_SOFTWARE)
        if not window or not renderer: raise RuntimeError('Could not create the launcher window')
        s.SDL_RenderSetLogicalSize(renderer, 1280, 720)
        fontpath = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts' / 'segoeui.ttf'
        font = ttf.TTF_OpenFont(str(fontpath).encode(), 26)
        if not font: raise RuntimeError('Could not open the system font')
        def open_pads():
            for i in range(s.SDL_NumJoysticks()):
                if s.SDL_IsGameController(i):
                    p = s.SDL_GameControllerOpen(i)
                    if p:
                        joystick = s.SDL_GameControllerGetJoystick(p)
                        ident = s.SDL_JoystickInstanceID(joystick)
                        if ident in self.controllers: s.SDL_GameControllerClose(p)
                        else: self.controllers[ident] = p
        def text(value, x, y, color=(230, 234, 238)):
            surface = ttf.TTF_RenderUTF8_Blended(font, str(value).encode('utf-8'), s.SDL_Color(*color, 255))
            if surface:
                texture = s.SDL_CreateTextureFromSurface(renderer, surface)
                rect = s.SDL_Rect(x, y, surface.contents.w, surface.contents.h)
                s.SDL_RenderCopy(renderer, texture, None, rect)
                s.SDL_DestroyTexture(texture); s.SDL_FreeSurface(surface)
        open_pads()
        event = s.SDL_Event()
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
                        direction = {s.SDL_CONTROLLER_BUTTON_DPAD_UP:'up', s.SDL_CONTROLLER_BUTTON_DPAD_DOWN:'down'}.get(event.cbutton.button)
                        if direction and typ == s.SDL_CONTROLLERBUTTONDOWN: self.action(direction)
                        if button:
                            for action in self.mapper.feed({'type':'button','button':button,'down':typ == s.SDL_CONTROLLERBUTTONDOWN}, now): self.action(action)
                    elif typ == s.SDL_CONTROLLERAXISMOTION and event.caxis.axis in (0, 1):
                        self.axis[event.caxis.axis] = max(-1, event.caxis.value / 32767)
                        for action in self.mapper.feed({'type':'axis','x':self.axis[0],'y':self.axis[1]}, now): self.action(action)
                    elif typ == s.SDL_KEYDOWN and not event.key.repeat:
                        action = {s.SDLK_UP:'up',s.SDLK_w:'up',s.SDLK_DOWN:'down',s.SDLK_s:'down',s.SDLK_RETURN:'accept',s.SDLK_SPACE:'accept',s.SDLK_ESCAPE:'back',s.SDLK_BACKSPACE:'back',s.SDLK_F1:'menu',s.SDLK_TAB:'menu'}.get(event.key.keysym.sym)
                        if action: self.action(action)
                    elif typ in (s.SDL_MOUSEMOTION, s.SDL_MOUSEBUTTONUP):
                        mx = event.motion.x if typ == s.SDL_MOUSEMOTION else event.button.x
                        my = event.motion.y if typ == s.SDL_MOUSEMOTION else event.button.y
                        w, h = ctypes.c_int(), ctypes.c_int(); s.SDL_GetWindowSize(window, ctypes.byref(w), ctypes.byref(h))
                        x, y = mx * 1280 / max(w.value, 1), my * 720 / max(h.value, 1)
                        spacing = 50 if self.screen == 'settings' else 65
                        height = 44 if self.screen == 'settings' else 56
                        index = int((y - 210) // spacing)
                        if 250 <= x <= 1030 and 0 <= index < len(self.buttons()) and 210 + index * spacing <= y < 210 + height + index * spacing:
                            self.focus = index
                            if typ == s.SDL_MOUSEBUTTONUP and event.button.button == s.SDL_BUTTON_LEFT: self.activate()
                s.SDL_SetRenderDrawColor(renderer, 18, 26, 32, 255); s.SDL_RenderClear(renderer)
                text('SILVER LANTERN', 65, 45, (230, 198, 130))
                titles = {'main':'Welcome', 'folder':'Where should the game go?', 'settings':'Settings', 'progress':'Working...', 'error':'Something went wrong', 'update':'Update the game?'}
                text(titles[self.screen], 250, 130)
                spacing = 50 if self.screen == 'settings' else 65
                height = 44 if self.screen == 'settings' else 56
                for i, label in enumerate(self.buttons()):
                    selected = i == self.focus
                    s.SDL_SetRenderDrawColor(renderer, *( (90, 108, 100, 255) if selected else (42, 54, 58, 255) ))
                    rect = s.SDL_Rect(250, 210 + i * spacing, 780, height); s.SDL_RenderFillRect(renderer, ctypes.byref(rect))
                    text(label, 275, 220 + i * spacing)
                if self.screen == 'error': text(self.last_error[:70], 65, 580, (255, 180, 160))
                elif self.screen == 'progress': text(self.progress[:80], 65, 580)
                else: text(self.status[:80], 65, 580)
                if self.screen in ('folder', 'settings'):
                    if self.screen == 'settings': text('Start menu + uninstall entry: created with install', 65, 610, (170, 180, 180))
                    text(str(self.folder)[:85], 65, 630)
                    parent = self.folder
                    while not parent.exists() and parent != parent.parent: parent = parent.parent
                    try: text(f'{shutil.disk_usage(parent).free / 1e9:.1f} GB free (about 40 GB needed)', 65, 665, (170, 180, 180))
                    except OSError: pass
                else:
                    text('A / Enter: Select   B / Esc: Back   Start / F1: Settings' if self.controllers else 'No controller found. Plug one in, or use the keyboard and mouse.', 65, 650, (170, 180, 180))
                s.SDL_RenderPresent(renderer)
                s.SDL_Delay(16)
        finally:
            if self.screen == 'progress' and self.operation != 'tool': terminate_worker(self.events)
            for pad in self.controllers.values(): s.SDL_GameControllerClose(pad)
            ttf.TTF_CloseFont(font); s.SDL_DestroyRenderer(renderer); s.SDL_DestroyWindow(window); ttf.TTF_Quit(); s.SDL_Quit()


def main():
    if '--cli' in sys.argv:
        args = sys.argv[1:]; args.remove('--cli')
        return subprocess.call(cli_command(*args))
    if '--self-test' in sys.argv:
        app = App(State.NOT_INSTALLED); app.transition(State.DOWNLOADING); app.transition(State.READY)
        return 0
    try:
        LauncherUI().run()
        return 0
    except Exception as exc:
        if os.name == 'nt': ctypes.windll.user32.MessageBoxW(None, str(exc) + '\nUse --cli for the console version.', 'Silver Lantern', 0x10)
        else: print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
