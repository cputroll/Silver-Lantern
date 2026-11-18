"""Fail-closed packaged friend-machine checks."""
import argparse
import functools
import hashlib
import http.server
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import zipfile
from pathlib import Path


def fixture_manifest(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    source = root / 'source'
    source.mkdir()
    (source / 'bin').mkdir()
    (source / 'bin' / 'HeyListen.exe').write_bytes(b'silver-lantern-fixture\n')
    spec = importlib.util.spec_from_file_location('pack_game', Path(__file__).resolve().parents[1] / 'pack-game.py')
    packer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packer)
    manifest = packer.pack(source, root / 'assets')
    path = root / 'assets' / 'game-manifest.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    return path


def run_check(name, artifacts, check):
    path = artifacts / (name + '.log')
    try:
        result = check()
        path.write_text('PASS\n' + (str(result) if result is not None else ''), encoding='utf-8')
        print(f'PASS {name}: {path}', flush=True)
        return True
    except Exception:
        path.write_text(traceback.format_exc(), encoding='utf-8')
        print(f'FAIL {name}: {path}', flush=True)
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--zip', required=True, type=Path)
    parser.add_argument('--artifacts', required=True, type=Path)
    parser.add_argument('--old-zip', type=Path, help='test-enabled 0.0.1 release zip')
    args = parser.parse_args()
    artifacts = args.artifacts.resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    root = artifacts / 'package'
    root.mkdir()
    with zipfile.ZipFile(args.zip) as package:
        for item in package.infolist():
            if not (root / item.filename).resolve().is_relative_to(root):
                raise ValueError('zip member escapes package directory')
        package.extractall(root)
    env = os.environ.copy()
    profile = artifacts / 'profile'
    env.update(USERPROFILE=str(profile), LOCALAPPDATA=str(profile / 'local'), APPDATA=str(profile / 'roaming'), TEMP=str(profile / 'temp'), TMP=str(profile / 'temp'), SILVER_LANTERN_TEST_NO_DEFENDER='1', SILVER_LANTERN_MIN_FREE_GB='0', SILVER_LANTERN_ALLOW_LOCAL_UPDATE='1', SILVER_LANTERN_REG_ROOT=r'Software\SilverLanternFriendTest', SILVER_LANTERN_DESKTOP_DIR=str(profile / 'desktop'), SILVER_LANTERN_STARTMENU_DIR=str(profile / 'start'))
    for name in ('TEMP', 'SILVER_LANTERN_DESKTOP_DIR', 'SILVER_LANTERN_STARTMENU_DIR'):
        Path(env[name]).mkdir(parents=True, exist_ok=True)
    manifest = fixture_manifest(artifacts / 'fixture')
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(manifest.parent))
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env['SILVER_LANTERN_TEST_ASSET_URL'] = f'http://127.0.0.1:{server.server_port}/'
    env['SILVER_LANTERN_GAME_MANIFEST'] = str(manifest)
    exe = root / 'support' / 'HeyListen-Get.exe'
    def command(binary, *argv):
        child = subprocess.run([str(binary), '--cli', *map(str, argv)], env=env, capture_output=True, text=True, timeout=90)
        output = child.stdout + child.stderr
        if child.returncode or 'Traceback (most recent call last)' in output or 'Unhandled exception' in output:
            raise RuntimeError(f'exit={child.returncode}\n{output}')
        return output
    def install():
        target = artifacts / 'cli-install'
        output = command(exe, '--install-game', target, '--game-manifest', manifest, '--no-self-update', '--no-preset', '--no-pause', '--plain')
        assert (target / 'bin' / 'HeyListen.exe').read_bytes() == b'silver-lantern-fixture\n'
        return output
    def update():
        target = artifacts / 'update' / 'HeyListen-Get.exe'
        target.parent.mkdir()
        shutil.copy2(exe, target)
        backup = target.with_name(target.name + '.update-backup')
        backup.write_bytes(b'stale')
        output = command(target, '--self-update-from', args.zip.resolve(), '--no-pause')
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            stages = list(target.parent.glob('.heylisten-update-*'))
            if stages and not backup.exists() and target.exists() and not any((stage / 'replacement.exe').exists() for stage in stages):
                return output + command(target, '--version')
            time.sleep(.25)
        raise TimeoutError('update helper did not finish')
    def prior_update():
        if not args.old_zip:
            raise RuntimeError('Provide --old-zip containing the test-enabled 0.0.1 client')
        folder = artifacts / 'prior-update'
        folder.mkdir()
        with zipfile.ZipFile(args.old_zip) as archive:
            payload = archive.read('support/HeyListen-Get.exe')
        target = folder / 'HeyListen-Get.exe'
        target.write_bytes(payload)
        before = command(target, '--version').strip()
        expected = command(exe, '--version').strip()
        if before != '0.0.1' or expected == before:
            raise RuntimeError(f'expected distinct old/new versions, got {before!r} and {expected!r}')
        command(target, '--self-update-from', args.zip.resolve(), '--no-pause')
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                if command(target, '--version').strip() == expected:
                    return f'version {before} -> {expected}'
            except (OSError, RuntimeError):
                pass
            time.sleep(.5)
        raise TimeoutError('updated executable did not report new version')

    def interrupt_resume():
        fixture = artifacts / 'resume-fixture'
        source = fixture / 'source'
        source.mkdir(parents=True)
        (source / 'first.dat').write_bytes(b'A' * 20000)
        (source / 'second.dat').write_bytes(b'B' * 200000)
        spec = importlib.util.spec_from_file_location('resume_packer', Path(__file__).resolve().parents[1] / 'pack-game.py')
        packer = importlib.util.module_from_spec(spec); spec.loader.exec_module(packer)
        assets = fixture / 'assets'
        data = packer.pack(source, assets, limit=65536)
        mf = assets / 'game-manifest.json'; mf.write_text(json.dumps(data), encoding='utf-8')
        requests = []
        second_started = threading.Event()
        class Throttled(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                requests.append(self.path)
                if len(requests) > 1:
                    second_started.set()
                    time.sleep(10)
                try:
                    super().do_GET()
                except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                    pass
        local = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Throttled, directory=str(assets)))
        threading.Thread(target=local.serve_forever, daemon=True).start()
        run_env = dict(env, SILVER_LANTERN_TEST_ASSET_URL=f'http://127.0.0.1:{local.server_port}/', SILVER_LANTERN_ASSEMBLY_WORKERS='1')
        target = artifacts / 'resume-game'
        argv = [str(exe), '--cli', '--install-game', str(target), '--game-manifest', str(mf), '--no-self-update', '--no-preset', '--no-pause', '--plain']
        try:
            child = subprocess.Popen(argv, env=run_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and child.poll() is None:
                if second_started.is_set() and (target / 'first.dat').exists(): break
                time.sleep(.1)
            else: raise RuntimeError('first part did not complete before interruption')
            subprocess.run(['taskkill', '/T', '/F', '/PID', str(child.pid)], capture_output=True, timeout=10)
            child.wait(timeout=10)
            before = list(requests)
            complete = '/' + data['files'][0]['segments'][0]['asset']
            if complete not in before: raise RuntimeError('completed bundle was not observed')
            resumed = subprocess.run(argv, env=run_env, capture_output=True, text=True, timeout=90)
            if resumed.returncode or (target / 'second.dat').read_bytes() != b'B' * 200000:
                raise RuntimeError(f'resume failed: {resumed.returncode} {resumed.stdout} {resumed.stderr}')
            if requests[len(before):].count(complete): raise RuntimeError('completed bundle was downloaded again')
            return f'requests before kill={before}; after={requests[len(before):]}'
        finally:
            if 'child' in locals() and child.poll() is None: child.kill(); child.wait(timeout=10)
            local.shutdown(); local.server_close()

    def gui_first_run():
        gui_profile = artifacts / 'gui-profile'
        folder = gui_profile / 'Games' / 'HeyListen'
        folder.mkdir(parents=True)
        local = gui_profile / 'local'
        state = local / 'SilverLantern'
        state.mkdir(parents=True)
        (state / 'launcher.json').write_text(json.dumps({'game_dir': str(folder)}), encoding='utf-8')
        gui_env = dict(env, USERPROFILE=str(gui_profile), LOCALAPPDATA=str(local), APPDATA=str(gui_profile / 'roaming'), SDL_VIDEODRIVER='dummy', SILVER_LANTERN_TEST_GUI_INSTALL='1')
        child = subprocess.Popen([str(root / 'Run Silver Lantern.exe'), '--gui', '--no-self-update'], env=gui_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            exit_code = child.wait(timeout=30)
        except subprocess.TimeoutExpired:
            subprocess.run(['taskkill', '/T', '/F', '/PID', str(child.pid)], capture_output=True, timeout=10)
            child.wait(timeout=10)
            raise RuntimeError('zip-root GUI did not exit within 30 seconds')
        if exit_code:
            raise RuntimeError(f'zip-root GUI exit={exit_code}')
        relocated = folder / 'Ocarina of Time.exe'
        deadline = time.monotonic() + 30
        installed = folder / 'bin' / 'HeyListen.exe'
        while time.monotonic() < deadline and not installed.exists():
            time.sleep(.25)
        if not relocated.exists() or not installed.exists() or installed.read_bytes() != b'silver-lantern-fixture\n':
            script = "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($env:SILVER_FRIEND_ROOT, [StringComparison]::OrdinalIgnoreCase) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
            subprocess.run(['powershell', '-NoProfile', '-Command', script], env=dict(env, SILVER_FRIEND_ROOT=str(gui_profile)), capture_output=True, timeout=20)
            raise RuntimeError('GUI did not relocate and install fixture')
        return f'Relocated to {relocated}; fixture installed at {installed}'

    def file_lock():
        target = artifacts / 'locked-game'
        locked = target / 'bin' / 'HeyListen.exe'
        locked.parent.mkdir(parents=True)
        locked.write_bytes(b'old executable')
        ready = artifacts / 'lock-ready'
        code = "import ctypes,sys,time,pathlib; k=ctypes.WinDLL('kernel32',use_last_error=True); k.CreateFileW.restype=ctypes.c_void_p; h=k.CreateFileW(sys.argv[1],0x80000000,1,None,3,0,None); assert h != ctypes.c_void_p(-1).value; pathlib.Path(sys.argv[2]).write_text('ready'); time.sleep(8); k.CloseHandle(ctypes.c_void_p(h))"
        holder = subprocess.Popen([sys.executable, '-c', code, str(locked), str(ready)])
        try:
            deadline = time.monotonic() + 15
            while not ready.exists() and time.monotonic() < deadline and holder.poll() is None:
                time.sleep(.1)
            if not ready.exists():
                raise RuntimeError('exclusive lock helper did not start')
            started = time.monotonic()
            output = command(exe, '--install-game', target, '--game-manifest', manifest, '--no-self-update', '--no-preset', '--no-pause', '--plain')
            if time.monotonic() - started < 4 or locked.read_bytes() != b'silver-lantern-fixture\n':
                raise RuntimeError('install did not wait for lock and replace destination')
            return output
        finally:
            if holder.poll() is None:
                holder.terminate()
            holder.wait(timeout=10)

    def uninstall():
        import winreg
        target = artifacts / 'uninstall-game'
        output = command(exe, '--install-game', target, '--game-manifest', manifest, '--no-self-update', '--no-preset', '--no-pause', '--plain')
        save = target / 'user' / 'save.dat'
        save.parent.mkdir(parents=True)
        save.write_bytes(b'fixture save')
        state = Path(env['LOCALAPPDATA']) / 'SilverLantern'
        state.mkdir(parents=True, exist_ok=True)
        (state / 'launcher.json').write_text(json.dumps({'game_dir': str(target)}), encoding='utf-8')
        key = env['SILVER_LANTERN_REG_ROOT'] + r'\SilverLantern'
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key):
            pass
        child = subprocess.run([str(exe), '--cli', '--uninstall'], input='y\n', env=env, capture_output=True, text=True, timeout=90)
        output += child.stdout + child.stderr
        if child.returncode or 'Traceback' in output or 'Unhandled exception' in output:
            raise RuntimeError(f'uninstall exit={child.returncode}\n{output}')
        if save.read_bytes() != b'fixture save' or (target / 'bin' / 'HeyListen.exe').exists():
            raise RuntimeError('uninstall did not preserve saves and remove game files')
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key):
                raise RuntimeError('uninstall registration remains')
        except FileNotFoundError:
            pass
        return output

    def unsupported(reason):
        def fail():
            raise RuntimeError(reason)
        return fail
    def process_tree(root_pid):
        script = "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId | ConvertTo-Json -Compress"
        result = subprocess.run(['powershell', '-NoProfile', '-Command', script], capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise RuntimeError('process tree query failed: ' + result.stderr)
        rows = json.loads(result.stdout or '[]')
        if isinstance(rows, dict): rows = [rows]
        children = {root_pid}
        changed = True
        while changed:
            old = len(children)
            children.update(row['ProcessId'] for row in rows if row['ParentProcessId'] in children)
            changed = len(children) != old
        return children

    def safety():
        import ctypes
        import subprocess as sp
        found = []
        callback = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        @callback
        def visit(hwnd, _):
            buf = ctypes.create_unicode_buffer(512)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, len(buf))
            if 'unhandled exception' in buf.value.lower():
                found.append(buf.value)
            return True
        ctypes.windll.user32.EnumWindows(visit, 0)
        if found:
            raise RuntimeError('Unhandled exception window: ' + repr(found))
        script = "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($env:SILVER_FRIEND_ROOT, [StringComparison]::OrdinalIgnoreCase) } | Select-Object -ExpandProperty ProcessId"
        child = sp.run(['powershell', '-NoProfile', '-Command', script], env=dict(env, SILVER_FRIEND_ROOT=str(artifacts)), capture_output=True, text=True, timeout=20)
        if child.returncode or child.stdout.strip():
            raise RuntimeError('leftover packaged processes or process scan error: ' + child.stdout + child.stderr)
        for log in artifacts.rglob('*.log'):
            if log.parent == artifacts:
                continue  # Harness failure logs intentionally contain diagnostic tracebacks.
            if 'Traceback' in log.read_text(encoding='utf-8', errors='replace'):
                raise RuntimeError('traceback in ' + str(log))
        return 'No unhandled-exception window, isolated packaged process, or profile traceback found.'

    checks = [
        ('fixture-cli-install', install),
        ('stale-update-backup', update),
        ('zip-first-run-install', gui_first_run),
        ('interrupt-resume', interrupt_resume),
        ('file-lock', file_lock),
        ('previous-release-update', prior_update),
        ('uninstall-keeps-saves', uninstall),
        ('crash-and-zombie-safety', safety),
    ]
    try:
        results = []
        for name, check in checks:
            tracked = set()
            stop = threading.Event()
            def monitor():
                while not stop.is_set():
                    try: tracked.update(process_tree(os.getpid()))
                    except Exception: pass
                    stop.wait(.5)
            watcher = threading.Thread(target=monitor, daemon=True); watcher.start()
            results.append(run_check(name, artifacts, check))
            stop.set(); watcher.join(timeout=25)
            if name != 'crash-and-zombie-safety':
                def checked_safety():
                    result = safety()
                    import ctypes
                    alive = set()
                    for pid in tracked - {os.getpid()}:
                        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
                        if handle:
                            code = ctypes.c_ulong()
                            if ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value == 259:
                                alive.add(pid)
                            ctypes.windll.kernel32.CloseHandle(handle)
                    if alive: raise RuntimeError('remaining descendant processes: ' + repr(alive))
                    return result
                results.append(run_check(name + '-safety', artifacts, checked_safety))
    finally:
        server.shutdown()
        server.server_close()
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
