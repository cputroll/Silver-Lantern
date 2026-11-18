"""Fail-closed packaged friend-machine checks."""
import argparse
import functools
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
        ('zip-first-run-install', unsupported('GUI input automation and isolated first-run process supervision are not implemented.')),
        ('interrupt-resume', unsupported('A throttled multi-part server and proof of no redownload are not implemented.')),
        ('file-lock', file_lock),
        ('previous-release-update', unsupported('A distinct prior-version packaged binary and version-change assertion are not implemented.')),
        ('uninstall-keeps-saves', uninstall),
        ('crash-and-zombie-safety', safety),
    ]
    try:
        results = []
        for name, check in checks:
            results.append(run_check(name, artifacts, check))
            if name != 'crash-and-zombie-safety':
                results.append(run_check(name + '-safety', artifacts, safety))
    finally:
        server.shutdown()
        server.server_close()
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
