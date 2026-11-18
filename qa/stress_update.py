"""Run loaded packaged local-update trials; retain artifacts on failure."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import zipfile

PHASES = ('download done', 'helper spawned', 'helper startup', 'parent-wait done', 'verification done', 'swap done', 'relaunch')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--old-zip', required=True, type=Path)
    parser.add_argument('--new-zip', required=True, type=Path)
    parser.add_argument('--artifacts', required=True, type=Path)
    parser.add_argument('--count', type=int, default=10)
    parser.add_argument('--timeout', type=int, default=180)
    args = parser.parse_args()
    root = args.artifacts.resolve(); root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.old_zip) as archive:
        old = archive.read('support/HeyListen-Get.exe')
    env = dict(os.environ, SILVER_LANTERN_ALLOW_LOCAL_UPDATE='1', LOCALAPPDATA=str(root / 'profile'), USERPROFILE=str(root / 'profile'), SILVER_LANTERN_TEST_NO_DEFENDER='1')
    load_code = "import hashlib,os,sys,time; p=sys.argv[1]; end=time.monotonic()+float(sys.argv[2]); data=os.urandom(1048576); f=open(p,'wb',buffering=0);\nwhile time.monotonic()<end:\n hashlib.sha256(data).digest(); f.seek(0); f.write(data); f.flush()\nf.close()"
    load = subprocess.Popen([sys.executable, '-c', load_code, str(root / 'load.bin'), str(args.count * args.timeout + 60)])
    results = []
    try:
        for i in range(args.count):
            folder = root / f'run-{i + 1:02d}'; folder.mkdir()
            target = folder / 'HeyListen-Get.exe'; target.write_bytes(old)
            start = time.monotonic()
            child = subprocess.run([str(target), '--cli', '--self-update-from', str(args.new_zip.resolve()), '--no-pause'], env=env, capture_output=True, text=True, timeout=args.timeout)
            if child.returncode:
                raise RuntimeError(f'run {i+1}: update exit {child.returncode}: {child.stdout} {child.stderr}')
            log = folder / 'HeyListen-Get.log'; deadline = start + args.timeout
            phases = {}
            while time.monotonic() < deadline:
                text = log.read_text(encoding='utf-8', errors='replace') if log.exists() else ''
                phases = {name: int(ms) for name, ms in re.findall(r'Self-update phase=(.*?) elapsed_ms=(\d+)', text)}
                if all(phase in phases for phase in PHASES): break
                time.sleep(.2)
            if not all(phase in phases for phase in PHASES):
                raise TimeoutError(f'run {i+1}: missing phases {set(PHASES) - phases.keys()}; see {log}')
            version = subprocess.run([str(target), '--cli', '--version'], env=env, capture_output=True, text=True, timeout=60)
            if version.returncode or version.stdout.strip() != '1.6.8':
                raise RuntimeError(f'run {i+1}: version check failed: {version.stdout} {version.stderr}')
            if any(folder.glob('.heylisten-update-*/replacement.exe')) or list(folder.glob('*.update-backup')):
                raise RuntimeError(f'run {i+1}: staged replacement or backup remains')
            result = {'run': i+1, 'total_ms': round((time.monotonic() - start) * 1000), 'phases_ms': phases}
            results.append(result)
            (root / 'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
            print(json.dumps(result), flush=True)
        times = sorted(r['total_ms'] for r in results)
        p95 = times[min(len(times)-1, __import__('math').ceil(len(times)*.95)-1)]
        budget = p95 * 3
        assert all(t <= budget for t in times)
        print(f'p95_ms={p95} budget_ms={budget} max_ms={max(times)}', flush=True)
        return 0
    finally:
        load.terminate()
        try: load.wait(timeout=10)
        except subprocess.TimeoutExpired: load.kill(); load.wait(timeout=10)


if __name__ == '__main__':
    sys.exit(main())
