"""Read-only benchmark for an exact, previously verified NTFS installation."""

import argparse
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from threading import Lock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from install_journal import build_baseline, verify_baseline


def measure_handles(root, files, workers=1, budget=10):
    from install_scan_windows import PlainTree

    started = time.perf_counter()
    count = 0
    with PlainTree(root) as tree, ExitStack() as handles:
        ownership = Lock()

        def pin(entry):
            handle = tree.pin(entry['path'], entry['size'])
            try:
                with ownership:
                    handles.callback(tree.unpin, handle)
            except BaseException:
                tree.unpin(handle)
                raise

        if workers == 1:
            for entry in files:
                if time.perf_counter() - started >= budget:
                    break
                pin(entry)
                count += 1
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for offset in range(0, len(files), workers * 4):
                    if time.perf_counter() - started >= budget:
                        break
                    jobs = [pool.submit(pin, entry) for entry in files[offset:offset + workers * 4]]
                    for job in jobs:
                        job.result()
                        count += 1
        elapsed = time.perf_counter() - started
    return count, elapsed, time.perf_counter() - started


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('sha256')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--full-native', action='store_true')
    mode.add_argument('--handle-budget', action='store_true',
                      help='measure protected file opens, not an integrity decision')
    parser.add_argument('--handle-workers', type=int, choices=(1, 8), default=1)
    parser.add_argument('--sha-batch', type=int, choices=(1, 16), default=1,
                        help='files per full-SHA executor task')
    parser.add_argument('--sha-workers', type=int, choices=(8, 32, 64), default=8,
                        help='threads in the full-SHA pool')
    parser.add_argument('--bulk-identities', action='store_true',
                        help='require exact bulk inventory and compare handle IDs before full SHA')
    args = parser.parse_args()
    if args.bulk_identities and not args.full_native:
        parser.error('--bulk-identities requires --full-native')
    data = args.manifest.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != args.sha256.lower():
        parser.error('manifest checksum does not match')
    files = json.loads(data)['files']
    print(f'FILES={len(files)} SHA256={digest}', flush=True)
    start = time.perf_counter()
    if args.handle_budget:
        count, elapsed, wall = measure_handles(args.root, files, args.handle_workers)
        print(f'HANDLE_SECONDS={elapsed:.3f} WITH_CLEANUP_SECONDS={wall:.3f} '
              f'FILES_PINNED={count} TOTAL={len(files)} WORKERS={args.handle_workers}', flush=True)
        return int(count != len(files) or elapsed >= 10)
    if args.full_native:
        from install_scan_windows import PlainTree
        with PlainTree(args.root) as tree, ThreadPoolExecutor(max_workers=args.sha_workers) as pool:
            inventory = None
            if args.bulk_identities:
                from qa.bulk_inventory_research import read_inventory
                directory_ids = {}
                inventory = read_inventory(tree, directory_ids=directory_ids)
                if {path: size for path, (size, _) in inventory.items()} != {
                        entry['path']: entry['size'] for entry in files}:
                    raise ValueError('Exact inventory names or sizes differ from manifest')
                print(f'BULK_INVENTORY_SECONDS={time.perf_counter() - start:.3f}', flush=True)

            def verify_file(entry):
                expected_id = inventory[entry['path']][1] if inventory is not None else None
                with tree.open(entry['path'], expected_identity=expected_id) as stream:
                    return (os.fstat(stream.fileno()).st_size == entry['size']
                            and hashlib.file_digest(stream, 'sha256').hexdigest() == entry['sha256'])

            def verify_batch(entries):
                return sum(not verify_file(entry) for entry in entries)

            errors = 0
            for offset in range(0, len(files), 256):
                window = files[offset:offset + 256]
                if args.sha_batch == 1:
                    errors += sum(not ok for ok in pool.map(verify_file, window))
                else:
                    batches = [window[i:i + args.sha_batch] for i in range(0, len(window), args.sha_batch)]
                    errors += sum(pool.map(verify_batch, batches))
            if inventory is not None:
                final_directory_ids = {}
                final_inventory = read_inventory(tree, directory_ids=final_directory_ids)
                if final_inventory != inventory or final_directory_ids != directory_ids:
                    raise ValueError('Inventory changed during verification')
            print(f'FULL_SHA_SECONDS={time.perf_counter() - start:.3f} ERRORS={errors} BATCH={args.sha_batch}', flush=True)
            return int(bool(errors))
    receipt = build_baseline(args.root, digest, files)
    print(f'BASELINE_SECONDS={time.perf_counter() - start:.3f} ACCEPTED={receipt is not None}', flush=True)
    if receipt is None:
        return 1
    start = time.perf_counter()
    verified = verify_baseline(args.root, digest, receipt)
    elapsed = time.perf_counter() - start
    print(f'REPLAY_SECONDS={elapsed:.3f} ACCEPTED={verified}', flush=True)
    return 0 if verified and elapsed < 10 else 1


if __name__ == '__main__':
    raise SystemExit(main())
