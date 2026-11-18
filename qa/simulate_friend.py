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
    (source / 'hookshot_version.txt').write_text('fixture\n', encoding='utf-8')
    spec = importlib.util.spec_from_file_location('pack_game', Path(__file__).resolve().parents[1] / 'pack-game.py')
    packer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packer)
    manifest = packer.pack(source, root / 'assets')
    path = root / 'assets' / 'game-manifest.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    return path


def process_creation_time(pid):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        exit_code = wintypes.DWORD()
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            raise ctypes.WinError(ctypes.get_last_error())
        if exit_code.value != 259:
            return None
        created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel_time), ctypes.byref(user_time)):
            raise ctypes.WinError(ctypes.get_last_error())
        return (created.dwHighDateTime << 32) | created.dwLowDateTime
    finally:
        kernel.CloseHandle(handle)


def descendant_identities(rows, root_pid):
    by_pid = {row['ProcessId']: row for row in rows}
    if root_pid not in by_pid:
        raise RuntimeError('monitor root process disappeared')
    children = {root_pid: by_pid[root_pid]['Created']}
    changed = True
    while changed:
        old = len(children)
        for row in rows:
            parent = row['ParentProcessId']
            if parent in children and children[parent] <= row['Created']:
                children[row['ProcessId']] = row['Created']
        changed = len(children) != old
    return set(children.items())


def live_identities(tracked, current):
    return {(pid, created) for pid, created in tracked if current.get(pid) == created}


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
        # Ten loaded trials: worst 10.961 s including version probe; 3x rounded up.
        deadline = time.monotonic() + 33
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

    def early_partial_adoption():
        fixture = artifacts / 'early-resume-fixture'
        source = fixture / 'source'
        source.mkdir(parents=True)
        payload = b'A' * 200000
        (source / 'game.dat').write_bytes(payload)
        spec = importlib.util.spec_from_file_location('early_packer', Path(__file__).resolve().parents[1] / 'pack-game.py')
        packer = importlib.util.module_from_spec(spec); spec.loader.exec_module(packer)
        assets = fixture / 'assets'
        data = packer.pack(source, assets, limit=65536)
        mf = assets / 'game-manifest.json'; mf.write_text(json.dumps(data), encoding='utf-8')
        requests = []
        class Throttled(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                requests.append(self.path)
                if len(requests) > 1: time.sleep(10)
                try: super().do_GET()
                except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError): pass
        local = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Throttled, directory=str(assets)))
        threading.Thread(target=local.serve_forever, daemon=True).start()
        run_env = dict(env, SILVER_LANTERN_TEST_ASSET_URL=f'http://127.0.0.1:{local.server_port}/', SILVER_LANTERN_ASSEMBLY_WORKERS='1')
        target = artifacts / 'early-resume-game'
        argv = [str(exe), '--cli', '--install-game', str(target), '--game-manifest', str(mf), '--no-self-update', '--no-preset', '--no-pause', '--plain']
        try:
            child = subprocess.Popen(argv, env=run_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            checkpoint = target / 'game.dat.heylisten-new.json'
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and child.poll() is None:
                if checkpoint.exists() and len(requests) > 1: break
                time.sleep(.1)
            else: raise RuntimeError('partial checkpoint was not established')
            if (target / 'game.dat').exists(): raise RuntimeError('game file completed before interruption')
            subprocess.run(['taskkill', '/T', '/F', '/PID', str(child.pid)], capture_output=True, timeout=10)
            child.wait(timeout=10)
            before = list(requests)
            completed = '/' + data['files'][0]['segments'][0]['asset']
            if completed not in before: raise RuntimeError('first partial segment was not downloaded')
            resumed = subprocess.run(argv, env=run_env, capture_output=True, text=True, timeout=90)
            if resumed.returncode or (target / 'game.dat').read_bytes() != payload:
                raise RuntimeError(f'partial resume failed: {resumed.returncode} {resumed.stdout} {resumed.stderr}')
            if completed in requests[len(before):]: raise RuntimeError('adopted segment was downloaded again')
            return f'partial segment adopted without refetch: {completed}'
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
        gui_env = dict(env, USERPROFILE=str(gui_profile), LOCALAPPDATA=str(local), APPDATA=str(gui_profile / 'roaming'), SDL_VIDEODRIVER='dummy', SDL_RENDER_DRIVER='software', SILVER_LANTERN_TEST_GUI_INSTALL='1')
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
        gui_log = state / 'launcher.log'
        while time.monotonic() < deadline:
            if installed.exists() and gui_log.exists() and 'Test GUI: installation finished, state=State.READY' in gui_log.read_text(encoding='utf-8', errors='replace'):
                break
            time.sleep(.25)
        if not relocated.exists() or not installed.exists() or installed.read_bytes() != b'silver-lantern-fixture\n' or not gui_log.exists() or 'Test GUI: installation finished, state=State.READY' not in gui_log.read_text(encoding='utf-8', errors='replace'):
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

    def first_temp_write_lock():
        target = artifacts / 'locked-temp-game'
        final = target / 'bin' / 'HeyListen.exe'
        temporary = final.with_name(final.name + '.heylisten-new')
        temporary.parent.mkdir(parents=True)
        temporary.write_bytes(b'old temporary data')
        ready = artifacts / 'temp-lock-ready'
        code = "import ctypes,sys,time,pathlib; k=ctypes.WinDLL('kernel32',use_last_error=True); k.CreateFileW.restype=ctypes.c_void_p; h=k.CreateFileW(sys.argv[1],0x80000000,1,None,3,0,None); assert h != ctypes.c_void_p(-1).value; pathlib.Path(sys.argv[2]).write_text('ready'); time.sleep(8); k.CloseHandle(ctypes.c_void_p(h))"
        holder = subprocess.Popen([sys.executable, '-c', code, str(temporary), str(ready)])
        try:
            deadline = time.monotonic() + 15
            while not ready.exists() and time.monotonic() < deadline and holder.poll() is None:
                time.sleep(.1)
            if not ready.exists():
                raise RuntimeError('temporary-file lock helper did not start')
            started = time.monotonic()
            output = command(exe, '--install-game', target, '--game-manifest', manifest, '--no-self-update', '--no-preset', '--no-pause', '--plain')
            if time.monotonic() - started < 4 or final.read_bytes() != b'silver-lantern-fixture\n':
                raise RuntimeError('install did not wait for the first temporary-file write')
            return output
        finally:
            if holder.poll() is None:
                holder.terminate()
            holder.wait(timeout=10)

    def swap_boundary(boundary):
        folder = artifacts / ('swap-' + boundary)
        folder.mkdir()
        target = folder / 'HeyListen-Get.exe'
        shutil.copy2(exe, target)
        ready, release_file = folder / 'ready', folder / 'release'
        pause_env = dict(env, SILVER_LANTERN_PAUSE_AT=boundary, SILVER_LANTERN_PAUSE_READY=str(ready), SILVER_LANTERN_PAUSE_RELEASE=str(release_file))
        result = subprocess.run([str(target), '--cli', '--self-update-from', str(args.zip.resolve()), '--no-pause'], env=pause_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
        if result.returncode:
            raise RuntimeError(f'update launch failed: {result.returncode}')
        deadline = time.monotonic() + 30
        while not ready.exists() and time.monotonic() < deadline:
            if not target.exists(): raise RuntimeError('entry point disappeared before checkpoint')
            time.sleep(.1)
        if not ready.exists(): raise RuntimeError('swap checkpoint not reached')
        if not target.exists(): raise RuntimeError('entry point absent at checkpoint')
        pid = int(ready.read_text())
        subprocess.run(['taskkill', '/F', '/PID', str(pid)], capture_output=True, timeout=10, check=True)
        if not target.exists(): raise RuntimeError('entry point absent after interruption')
        old_stages = set(folder.glob('.heylisten-update-*'))
        command(target, '--self-update-from', args.zip.resolve(), '--no-pause')
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if not target.exists(): raise RuntimeError('entry point absent during recovery')
            try:
                new_stages = set(folder.glob('.heylisten-update-*')) - old_stages
                if new_stages and all(not (stage / 'replacement.exe').exists() for stage in new_stages) and command(target, '--version').strip() == command(exe, '--version').strip():
                    return 'entry point retained; recovery executable runs'
            except (OSError, RuntimeError): pass
            time.sleep(.25)
        raise TimeoutError('recovery did not complete')

    def sibling_ownership_race():
        gui_profile = artifacts / 'sibling-profile'
        folder = gui_profile / 'Games' / 'HeyListen'
        folder.mkdir(parents=True)
        stale = folder / 'Ocarina of Time-stale.exe'
        stale.write_bytes(b'recorded stale copy')
        unknown = folder / 'Ocarina of Time-unknown.exe'
        unknown.write_bytes(b'unknown replacement')
        records = folder / '.heylisten-get' / 'launcher-copies.json'
        records.parent.mkdir()
        records.write_text(json.dumps({stale.name: hashlib.sha256(stale.read_bytes()).hexdigest(), unknown.name: hashlib.sha256(b'former owned content').hexdigest()}), encoding='utf-8')
        state = gui_profile / 'local' / 'SilverLantern'
        state.mkdir(parents=True)
        (state / 'launcher.json').write_text(json.dumps({'game_dir': str(folder)}), encoding='utf-8')
        gui_env = dict(env, USERPROFILE=str(gui_profile), LOCALAPPDATA=str(gui_profile / 'local'), APPDATA=str(gui_profile / 'roaming'), SDL_VIDEODRIVER='dummy', SDL_RENDER_DRIVER='software', SILVER_LANTERN_TEST_GUI_INSTALL='1')
        ready, release_file = artifacts / 'sibling-ready', artifacts / 'sibling-release'
        gui_env.update(SILVER_LANTERN_PAUSE_AT='sibling_race', SILVER_LANTERN_PAUSE_READY=str(ready), SILVER_LANTERN_PAUSE_RELEASE=str(release_file))
        replacement = folder / 'replacement.tmp'
        replacement.write_bytes(b'unknown replacement')
        child = subprocess.Popen([str(root / 'Run Silver Lantern.exe'), '--gui', '--no-self-update'], env=gui_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 30
            while not ready.exists() and time.monotonic() < deadline: time.sleep(.1)
            if not ready.exists(): raise RuntimeError('sibling checkpoint not reached')
            try:
                os.replace(replacement, stale)
            except OSError as exc:
                if exc.winerror != 5: raise
            else: raise RuntimeError('concurrent replacement unexpectedly succeeded')
            if replacement.read_bytes() != b'unknown replacement': raise RuntimeError('replacement damaged')
            release_file.touch()
            if child.wait(timeout=30): raise RuntimeError('GUI relocation failed')
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not (folder / 'bin' / 'HeyListen.exe').exists(): time.sleep(.25)
            if stale.exists() or unknown.read_bytes() != b'unknown replacement':
                raise RuntimeError('stale copy not removed or unknown sibling damaged')
            return 'replacement rejected with Windows error 5; unknown and stale files handled safely'
        finally:
            release_file.touch(exist_ok=True)
            if child.poll() is None: child.kill(); child.wait(timeout=10)

    def parent_only_kill(mode):
        target = artifacts / ('orphan-' + mode)
        target.mkdir()
        sentinel = target / 'unknown.txt'; sentinel.write_bytes(b'unknown')
        original = target / 'bin' / 'HeyListen.exe'
        original.parent.mkdir()
        original.write_bytes(b'original entry point')
        ready = artifacts / ('orphan-' + mode + '.pid')
        started = threading.Event()
        release = threading.Event()
        class Blocking(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                started.set()
                try: super().do_GET()
                except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError): pass
        local = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Blocking, directory=str(manifest.parent)))
        threading.Thread(target=local.serve_forever, daemon=True).start()
        run_env = dict(env, SILVER_LANTERN_TEST_ASSET_URL=f'http://127.0.0.1:{local.server_port}/')
        pause_ready, pause_release = artifacts / 'assembly-ready', artifacts / 'assembly-release'
        if mode == 'assembly':
            run_env.update(SILVER_LANTERN_PAUSE_AT='assembly', SILVER_LANTERN_PAUSE_READY=str(pause_ready), SILVER_LANTERN_PAUSE_RELEASE=str(pause_release), SILVER_LANTERN_ASSEMBLY_WORKERS='1')
        argv = [str(exe), '--cli', '--install-game', str(target), '--game-manifest', str(manifest), '--no-self-update', '--no-preset', '--no-pause', '--plain']
        lock = None
        if mode == 'retry':
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            kernel.CreateFileW.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            lock = kernel.CreateFileW(str(original), 0x80000000, 1, None, 3, 0, None)
            if lock == ctypes.c_void_p(-1).value: raise ctypes.WinError(ctypes.get_last_error())
        child_output = artifacts / ('orphan-' + mode + '.output')
        parent_code = "import os,sys,time,subprocess; sys.path.insert(0,sys.argv[1]); from parent_identity import parent_environment; f=open(sys.argv[3],'w'); p=subprocess.Popen(sys.argv[4:],env=parent_environment(),stdout=f,stderr=subprocess.STDOUT); open(sys.argv[2],'w').write(str(p.pid)); time.sleep(90)"
        parent = subprocess.Popen([sys.executable, '-c', parent_code, str(Path(__file__).resolve().parents[1]), str(ready), str(child_output), *argv], env=run_env)
        child_pid = None
        identity = None
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and parent.poll() is None and not ready.exists(): time.sleep(.05)
            if not ready.exists(): raise RuntimeError('parent did not launch packaged child')
            child_pid = int(ready.read_text())
            identity = process_creation_time(child_pid)
            if identity is None or not started.wait(60): raise RuntimeError('child did not begin fixture download')
            if mode == 'retry':
                temporary = original.with_name(original.name + '.heylisten-new')
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    if temporary.exists() and child_output.exists() and any(message in child_output.read_text(encoding='utf-8', errors='replace') for message in ('Waiting for Windows to release a file', 'Windows is checking the game files')): break
                    time.sleep(.1)
                else: raise RuntimeError('child did not enter locked replacement retry')
            if mode == 'assembly':
                deadline = time.monotonic() + 30
                while not pause_ready.exists() and time.monotonic() < deadline: time.sleep(.1)
                if not pause_ready.exists(): raise RuntimeError('assembly checkpoint not reached')
            paused_files = {path: path.read_bytes() for path in target.rglob('*.heylisten-new') if path.is_file()} if mode == 'assembly' else {}
            if mode == 'assembly' and not paused_files: raise RuntimeError('paused assembly has no destination temporary file')
            parent.terminate(); parent.wait(timeout=10)
            pause_release.touch()
            release.set()
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and process_creation_time(child_pid) == identity: time.sleep(.1)
            if process_creation_time(child_pid) == identity: raise RuntimeError('orphaned child did not stop')
            if lock:
                kernel.CloseHandle(lock); lock = None
            time.sleep(2)
            if original.read_bytes() != b'original entry point' or sentinel.read_bytes() != b'unknown':
                raise RuntimeError('orphan replaced entry point or removed unknown file')
            if mode == 'assembly':
                for path, content in paused_files.items():
                    if path.exists() and path.read_bytes() != content: raise RuntimeError('orphan wrote further assembly data')
                command(exe, '--install-game', target, '--game-manifest', manifest, '--no-self-update', '--no-preset', '--no-pause', '--plain')
                if original.read_bytes() != b'silver-lantern-fixture\n': raise RuntimeError('rerun did not complete')
            return f'parent killed during {mode}; child stopped and files preserved'
        finally:
            release.set()
            if lock: kernel.CloseHandle(lock)
            if parent.poll() is None: parent.terminate(); parent.wait(timeout=10)
            if child_pid and identity is not None and process_creation_time(child_pid) == identity:
                subprocess.run(['taskkill', '/T', '/F', '/PID', str(child_pid)], capture_output=True, timeout=10)
            local.shutdown(); local.server_close()

    def concurrent_uninstall():
        target = artifacts / 'concurrent-game'
        target.mkdir()
        preserved = target / 'unknown.dat'; preserved.write_bytes(b'keep this file')
        state = Path(env['LOCALAPPDATA']) / 'SilverLantern'
        state.mkdir(parents=True, exist_ok=True)
        (state / 'launcher.json').write_text(json.dumps({'game_dir': str(target)}), encoding='utf-8')
        started = threading.Event()
        release = threading.Event()
        class Blocking(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                started.set()
                release.wait(60)
                try: super().do_GET()
                except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError): pass
        local = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Blocking, directory=str(manifest.parent)))
        threading.Thread(target=local.serve_forever, daemon=True).start()
        run_env = dict(env, SILVER_LANTERN_TEST_ASSET_URL=f'http://127.0.0.1:{local.server_port}/')
        argv = [str(exe), '--cli', '--install-game', str(target), '--game-manifest', str(manifest), '--no-self-update', '--no-preset', '--no-pause', '--plain']
        child = subprocess.Popen(argv, env=run_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            if not started.wait(60) or child.poll() is not None: raise RuntimeError('install did not reach blocked download')
            result = subprocess.run([str(exe), '--cli', '--uninstall'], input='y\n', env=env, capture_output=True, text=True, timeout=90)
            output = result.stdout + result.stderr
            if 'installation or update is in progress' not in output.lower():
                raise RuntimeError(f'uninstall did not refuse concurrent install: {result.returncode} {output}')
            if preserved.read_bytes() != b'keep this file' or child.poll() is not None:
                raise RuntimeError('concurrent uninstall destroyed files or stopped install')
            release.set()
            if child.wait(timeout=90) or (target / 'bin' / 'HeyListen.exe').read_bytes() != b'silver-lantern-fixture\n':
                raise RuntimeError('install failed after refused uninstall')
            return output
        finally:
            release.set()
            if child.poll() is None: child.kill(); child.wait(timeout=10)
            local.shutdown(); local.server_close()

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
        by_pid = {}
        for row in rows:
            created = process_creation_time(row['ProcessId'])
            if created is not None:
                row['Created'] = created
                by_pid[row['ProcessId']] = row
        return descendant_identities(list(by_pid.values()), root_pid)

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
        ('early-partial-adoption', early_partial_adoption),
        ('file-lock', file_lock),
        ('first-temp-write-lock', first_temp_write_lock),
        ('previous-release-update', prior_update),
        ('swap-pre-swap', lambda: swap_boundary('pre_swap')),
        ('swap-post-swap', lambda: swap_boundary('post_swap')),
        ('sibling-ownership-race', sibling_ownership_race),
        ('parent-kill-assembly', lambda: parent_only_kill('assembly')),
        ('parent-kill-lock-retry', lambda: parent_only_kill('retry')),
        ('concurrent-uninstall', concurrent_uninstall),
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
                    deadline = time.monotonic() + 15
                    while True:
                        identities = {identity for identity in tracked if identity[0] != os.getpid()}
                        alive = live_identities(identities, {pid: process_creation_time(pid) for pid, _ in identities})
                        try:
                            result = safety()
                            error = None
                        except RuntimeError as exc:
                            error = exc
                        if not alive and error is None:
                            return result
                        if time.monotonic() >= deadline:
                            raise RuntimeError(f'process safety did not settle: descendants={alive}; {error}')
                        time.sleep(.5)
                results.append(run_check(name + '-safety', artifacts, checked_safety))
    finally:
        server.shutdown()
        server.server_close()
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
