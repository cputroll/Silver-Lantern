"""Recover old channel version metadata from verified manifest bundle segments."""
import hashlib
import json
import re
import sys
import urllib.request

MAX_VERSION_BYTES = 65536
ASSET = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
VERSION = re.compile(r'v?\d+\.\d+\.\d+')


def recover_version(manifest, fetch):
    entries = [f for f in manifest.get('files', []) if f.get('path') == 'hookshot_version.txt']
    if len(entries) != 1:
        raise ValueError('version file missing or ambiguous')
    entry = entries[0]
    size = entry.get('size')
    if type(size) is not int or not 0 < size <= MAX_VERSION_BYTES:
        raise ValueError('invalid version file size')
    segments = entry.get('segments')
    if not isinstance(segments, list) or not segments or len(segments) > 128:
        raise ValueError('invalid version segments')
    chunks = []
    total = 0
    for segment in segments:
        asset, offset, length = segment.get('asset'), segment.get('offset'), segment.get('length')
        if not isinstance(asset, str) or not ASSET.fullmatch(asset) or type(offset) is not int or offset < 0 or type(length) is not int or length <= 0 or length > size - total:
            raise ValueError('invalid version segment')
        chunk = fetch(asset, offset, length)
        if len(chunk) != length or hashlib.sha256(chunk).hexdigest() != segment.get('sha256', '').lower():
            raise ValueError('version segment verification failed')
        chunks.append(chunk)
        total += length
    data = b''.join(chunks)
    if total != size or hashlib.sha256(data).hexdigest() != entry.get('sha256', '').lower():
        raise ValueError('version file verification failed')
    text = data.decode('utf-8-sig').strip()
    match = re.search(r'(?m)^version\s*=\s*([^\r\n]+)', text)
    value = match.group(1).strip() if match else text
    if not VERSION.fullmatch(value):
        raise ValueError('invalid or ambiguous game version')
    return value


def fetch_range(asset, offset, length):
    url = 'https://github.com/cputroll/Silver-Lantern/releases/download/game-v1/' + asset
    end = offset + length - 1
    request = urllib.request.Request(url, headers={'Range': f'bytes={offset}-{end}', 'Accept-Encoding': 'identity', 'User-Agent': 'Silver-Lantern-AutoShip'})
    with urllib.request.urlopen(request, timeout=30) as response:
        if not response.geturl().lower().startswith('https://') or response.status != 206:
            raise ValueError('range response required over HTTPS')
        if not re.fullmatch(rf'bytes {offset}-{end}/\d+', response.headers.get('Content-Range', '')):
            raise ValueError('invalid content range')
        return response.read(length + 1)


def main():
    with open(sys.argv[1], encoding='utf-8-sig') as file:
        manifest = json.load(file)
    print(recover_version(manifest, fetch_range))


if __name__ == '__main__':
    main()
