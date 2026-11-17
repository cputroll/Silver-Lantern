import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import tarfile
import tempfile
import unittest

LIMIT = 1536 * 1024 * 1024
BLOCK = 8 * 1024 * 1024


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        while data := stream.read(BLOCK):
            h.update(data)
    return h.hexdigest()


class PackTest(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'source'; source.mkdir()
            (source / 'a').write_bytes(b'hello')
            (source / 'large').write_bytes(b'x' * 25000)
            (source / 'user').mkdir(); (source / 'user' / 'save').write_bytes(b'private')
            (source / 'x.log').write_bytes(b'log')
            result = pack(source, root / 'out', limit=10240)
            self.assertEqual([f['path'] for f in result['files']], ['a', 'large'])
            for entry in result['files']:
                content = b''
                for segment in entry['segments']:
                    with (root / 'out' / segment['asset']).open('rb') as f:
                        f.seek(segment['offset']); piece = f.read(segment['length'])
                    self.assertEqual(hashlib.sha256(piece).hexdigest(), segment['sha256'])
                    content += piece
                self.assertEqual(hashlib.sha256(content).hexdigest(), entry['sha256'])
                self.assertEqual(content, (source / entry['path']).read_bytes())
            again = pack(source, root / 'again', limit=10240)
            self.assertEqual(result, again)
            for asset in result['assets']:
                self.assertEqual((root / 'out' / asset['asset']).read_bytes(), (root / 'again' / asset['asset']).read_bytes())


def pack(source, out, limit=LIMIT, prefix=''):
    if not isinstance(prefix, str) or (prefix and not re.fullmatch(r'[A-Za-z0-9_-]+', prefix)):
        raise ValueError('invalid asset prefix')
    if not 0 < limit < 2 * 1024**3:
        raise ValueError('bundle limit must be positive and below 2 GiB')
    source = source.resolve(); out = out.resolve()
    if not source.is_dir() or source == out or source in out.parents:
        raise ValueError('source must be a directory and output must be outside it')
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for root, dirs, files in os.walk(source, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d.lower() not in ('user', 'dist-release'))
        for name in dirs + files:
            if (Path(root) / name).is_symlink():
                raise ValueError('symlinks are not supported')
        paths.extend(Path(root) / n for n in files if not n.lower().endswith('.log'))
    paths.sort(key=lambda p: p.relative_to(source).as_posix())
    manifest = {'version': 2, 'build': 'game-v1', 'layout': 'whole', 'total_bytes': 0, 'files': [], 'assets': []}
    bundle = None; bundle_path = None; bundle_index = 0; chunk_index = 0

    def finish_bundle():
        nonlocal bundle
        if bundle:
            bundle.write(b'\0' * (10240 - (bundle.tell() % 10240)))
            bundle.close(); bundle = None
            manifest['assets'].append({'asset': bundle_path.name, 'size': bundle_path.stat().st_size, 'sha256': digest(bundle_path)})

    for path in paths:
        rel = path.relative_to(source).as_posix(); size = path.stat().st_size
        entry = {'path': rel, 'size': size, 'sha256': digest(path), 'segments': []}
        info = tarfile.TarInfo(rel); info.size = size; info.mode = 0o644
        header = info.tobuf(format=tarfile.PAX_FORMAT, encoding='utf-8')
        needed = len(header) + ((size + 511) // 512) * 512
        if needed + 10240 > limit:
            finish_bundle()
            with path.open('rb') as src:
                remaining = size
                while remaining:
                    chunk_index += 1; name = f'{prefix}whole-chunk-{chunk_index:03d}.bin'; dest = out / name
                    h = hashlib.sha256(); length = min(limit, remaining); left = length
                    with dest.open('wb') as dst:
                        while left:
                            data = src.read(min(BLOCK, left))
                            if not data: raise ValueError('source changed while packing')
                            dst.write(data); h.update(data); left -= len(data)
                    sha = h.hexdigest(); remaining -= length
                    entry['segments'].append({'asset': name, 'offset': 0, 'length': length, 'sha256': sha})
                    manifest['assets'].append({'asset': name, 'size': length, 'sha256': sha})
        else:
            if bundle and ((bundle.tell() + needed) // 10240 + 1) * 10240 > limit:
                finish_bundle()
            if not bundle:
                bundle_index += 1; bundle_path = out / f'{prefix}whole-bundle-{bundle_index:03d}.tar'; bundle = bundle_path.open('wb')
            bundle.write(header); offset = bundle.tell(); h = hashlib.sha256()
            with path.open('rb') as src:
                left = size
                while left:
                    data = src.read(min(BLOCK, left))
                    if not data: raise ValueError('source changed while packing')
                    bundle.write(data); h.update(data); left -= len(data)
            bundle.write(b'\0' * ((-size) % 512))
            if h.hexdigest() != entry['sha256']: raise ValueError('source changed while packing')
            entry['segments'].append({'asset': bundle_path.name, 'offset': offset, 'length': size, 'sha256': entry['sha256']})
        if path.stat().st_size != size: raise ValueError('source changed while packing')
        manifest['files'].append(entry); manifest['total_bytes'] += size
    finish_bundle()
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path); parser.add_argument('--out', type=Path)
    parser.add_argument('--manifest-out', type=Path); parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--prefix', default='')
    args = parser.parse_args()
    if args.self_test:
        return 0 if unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(PackTest)).wasSuccessful() else 1
    if not args.source or not args.out: parser.error('--source and --out are required')
    result = pack(args.source, args.out, prefix=args.prefix)
    target = args.manifest_out or args.out / 'game-manifest.json'
    target.write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
