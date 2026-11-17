#!/usr/bin/env python3
"""
HeyListen Get — one-click downloader for HeyListen + the game dump.

  * Reads a manifest (sources.json, or the same file at a URL) that lists where the game
    comes from. Edit that manifest and every run picks the new links up — no code change.
  * Fetches the latest HeyListen release from GitHub and keeps HeyListenBuilder.exe current.
  * Downloads the Ocarina of Time (Switch 2) .nup, resumable, size/hash verified.
  * Seeds the DLSS Performance preset and can launch the Builder.

Usage:
  python Get-HeyListen.py
  python Get-HeyListen.py --manifest https://raw.githubusercontent.com/OWNER/REPO/main/sources.json
  python Get-HeyListen.py --builder-only | --game-only | --skip-hash | --force
  python Get-HeyListen.py --out D:\\Games --game-dir D:\\Games\\HeyListen --no-launch

Requires: pip install curl_cffi
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    from curl_cffi import requests
except ImportError:
    sys.exit("Missing dependency. Run:  python -m pip install curl_cffi")

HERE = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
APP_VERSION = "1.2.0"
MAX_UPDATE_BYTES = 150 * 1024 * 1024
MANIFEST_LOCAL = HERE / "sources.json"
MANIFEST_CACHE = HERE / "sources.cache.json"
VERSION_FILE = HERE / "VERSION"
UPDATE_ASSET = "HeyListen-Get.zip"
SUMS_ASSET = "SHA256SUMS.txt"

PRESET = {              # engine/gpu/vk/vk_menu.cpp ini_key names
    "dlss": "1",        # 1 = NVIDIA DLSS
    "resolution": "4",  # 4 = 50% = DLSS Performance
    "sharpness": "80",
    "reflex": "1",
    "pace": "1",
    "framegen": "1",
    "options_version": "3",
}


def log(msg: str) -> None:
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def session() -> "requests.Session":
    return requests.Session(impersonate="chrome", timeout=90)


# ---------------------------------------------------------------- manifest
def load_manifest(manifest_arg: str | None) -> dict:
    """Local file, a URL, or the last good copy. A dead link never stops the run."""
    candidates: list[tuple[str, str]] = []       # (label, kind)
    if manifest_arg:
        kind = "url" if manifest_arg.startswith(("http://", "https://")) else "file"
        candidates.append((manifest_arg, kind))
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
                r = session().get(label, headers={"accept": "application/json"}, timeout=45)
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
            return data
        except Exception as e:  # noqa: BLE001
            last_err = e
            log(f"  manifest {label} unusable: {e}")
    raise RuntimeError(f"no usable manifest ({last_err})")


# ---------------------------------------------------------------- builder
def latest_release(s: "requests.Session", repo: str) -> dict:
    r = s.get(f"https://api.github.com/repos/{repo}/releases/latest",
              headers={"accept": "application/vnd.github+json"}, timeout=60)
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
    response = s.get(assets[sums_name]["browser_download_url"], timeout=60)
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
        return builder

    size = assets[asset_name]["size"]
    log(f"Downloading {asset_name} ({tag}, {human(size)})")
    tmp = builder.with_suffix(builder.suffix + ".part")
    stream_to(s, assets[asset_name]["browser_download_url"], tmp, expect=size)
    got = sha256(tmp)
    if want and got != want:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"builder checksum mismatch: {got} != {want}")
    tmp.replace(builder)
    state.update({"release": tag, "builder_sha256": got, "updated": time.strftime("%Y-%m-%dT%H:%M:%S")})
    state_file.write_text(json.dumps(state, indent=2))
    log(f"Builder ready and checksum-verified: {tag}")
    return builder


# ---------------------------------------------------------------- game
def buzz_download_url(s: "requests.Session", page_url: str) -> tuple[str, str]:
    page = s.get(page_url, headers={"accept": "text/html", "referer": "https://buzzheavier.com/"}, timeout=60)
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


def stream_to(s: "requests.Session", url: str, dest: Path, expect: int | None = None,
              referer: str | None = None, max_retries: int = 6) -> int:
    """Resumable streaming download into dest (writes dest + '.part')."""
    part = Path(str(dest) + ".part")
    for attempt in range(1, max_retries + 1):
        have = part.stat().st_size if part.exists() else 0
        if expect is not None and have > expect:
            part.unlink()
            have = 0
        headers = {"accept": "*/*", "referer": referer or "https://buzzheavier.com/"}
        if have:
            headers["range"] = f"bytes={have}-"
        try:
            r = s.get(url, headers=headers, stream=True, timeout=120)
            if r.status_code == 416 and expect and have >= expect:
                r.close()
                break
            r.raise_for_status()
            if have and r.status_code == 200:      # server ignored Range
                have = 0
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
                        if expect is not None and done + len(chunk) > expect:
                            raise ValueError("download exceeds expected size")
                        f.write(chunk)
                        done += len(chunk)
                        now = time.time()
                        if now - last >= 2:
                            last = now
                            pct = f"{done * 100 / total:5.1f}%" if total else "  ?  "
                            rate = (done - have) / max(now - t0, 0.001)
                            log(f"  {pct}  {human(done)} / {human(total) if total else '?'}  ({human(rate)}/s)")
            finally:
                r.close()
            if total and done < total and attempt < max_retries:
                log(f"  short read ({done}/{total}), resuming…")
                continue
            break
        except Exception as e:  # noqa: BLE001
            if attempt == max_retries:
                raise
            log(f"  network hiccup ({type(e).__name__}), retry {attempt}/{max_retries} in 3 s")
            time.sleep(3)
    if expect is not None and part.stat().st_size != expect:
        raise RuntimeError(f"size mismatch: {part.stat().st_size} != {expect}")
    part.replace(dest)
    return dest.stat().st_size


def download_game(s: "requests.Session", man: dict, out: Path, skip_hash: bool = False,
                  force: bool = False) -> Path:
    game = man["game"]
    dest = out / game["filename"]
    sizes = {src.get("size") for src in game["sources"] if src.get("size")}

    if dest.exists() and dest.stat().st_size in sizes and not force:
        log(f"Game already downloaded: {dest.name}")
    else:
        last_err = None
        for src in game["sources"]:
            try:
                urls, size = source_urls(s, src)
                log(f"Game source: {src.get('url') or src.get('parts')[0]}  ->  {human(size) if size else '?'}")
                if len(urls) == 1:
                    stream_to(s, urls[0][0], dest, expect=size, referer=urls[0][1])
                else:   # parts joined in order
                    tmp = Path(str(dest) + ".join")
                    with open(tmp, "wb") as f:
                        for i, (u, ref) in enumerate(urls, 1):
                            p = Path(str(dest) + f".p{i}")
                            stream_to(s, u, p, referer=ref)
                            log(f"  part {i}/{len(urls)} done ({human(p.stat().st_size)})")
                            with open(p, "rb") as pf:
                                while (b := pf.read(8 * 1024 * 1024)):
                                    f.write(b)
                            p.unlink()
                    if size and tmp.stat().st_size != size:
                        raise RuntimeError(f"joined size {tmp.stat().st_size} != {size}")
                    tmp.replace(dest)
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
        log("Verifying SHA-256 (a few minutes on 25 GB)…")
        got = sha256(dest)
        if want and got.lower() != want.lower():
            raise RuntimeError(f"game checksum mismatch: {got}")
        log("Checksum OK — this is the supported build." if want else f"Checksum (re-pack, no pinned hash): {got}")
    return dest


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
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
        candidates = []
        for entry in entries:
            path = entry.filename.replace('\\', '/')
            parts = path.split('/')
            if path.startswith('/') or any(part in ('.', '..') or ':' in part for part in parts):
                raise ValueError("unsafe archive path")
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("archive symlink rejected")
            if not entry.is_dir() and parts[-1].lower() == name.lower():
                candidates.append(entry)
        if len(candidates) != 1 or not 0 < candidates[0].file_size <= MAX_UPDATE_BYTES:
            raise ValueError("release must contain exactly one bounded executable")
        dest = directory / 'replacement.exe'
        with archive.open(candidates[0]) as src, dest.open('xb') as out:
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


def replace_transactionally(target: Path, replacement: Path) -> None:
    backup = target.with_name(target.name + '.update-backup')
    if backup.exists():
        raise RuntimeError("update backup already exists; manual recovery required")
    os.replace(target, backup)
    try:
        os.replace(replacement, target)
    except BaseException:
        os.replace(backup, target)
        raise
    try:
        backup.unlink()
    except OSError as e:
        log(f"Update installed; backup retained for manual cleanup: {e}")


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
    wait_for_parent(parent)
    deadline = time.monotonic() + 30
    while True:
        try:
            replace_transactionally(target, replacement)
            break
        except PermissionError:
            if time.monotonic() >= deadline:
                raise RuntimeError("timed out waiting for executable replacement")
            time.sleep(0.25)
    try:
        subprocess.Popen([str(target), *args], cwd=str(target.parent))
    finally:
        cleanup_stage(replacement.parent)


def fetch_update_checksum(s: "requests.Session", url: str) -> str:
    response = s.get(url, stream=True, timeout=60)
    try:
        response.raise_for_status()
        data = bytearray()
        for chunk in response.iter_content(8192):
            if len(data) + len(chunk) > 65536:
                raise ValueError("checksum file too large")
            data.extend(chunk)
        return parse_update_checksum(data.decode('utf-8-sig'))
    finally:
        response.close()


def check_self_update(s: "requests.Session", man: dict, args: list[str],
                      local_zip: str | None = None) -> None:
    if not getattr(sys, "frozen", False):
        log("Self-update is available only for the packaged executable.")
        return
    if local_zip:
        raise ValueError("unsigned local updates are disabled")
    # Executable updates must not be redirected by the mutable sources manifest.
    rel = latest_release(s, "cputroll/Silver-Lantern")
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
        stream_to(s, assets[UPDATE_ASSET]['browser_download_url'], zp, expect=size)
        if zp.stat().st_size != size or sha256(zp) != want:
            raise ValueError("update size or checksum mismatch")
        replacement = stage_executable(zp, stage, target.name)
        helper = stage / 'update-helper.exe'
        shutil.copy2(target, helper)
        rest = [arg for arg in args if arg != '--self-update']
        subprocess.Popen([str(helper), '--apply-update-internal', str(target), str(replacement), str(os.getpid()), *rest], cwd=str(target.parent))
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    sys.exit(0)


# ---------------------------------------------------------------- main
def main() -> int:
    if len(sys.argv) >= 5 and sys.argv[1] == "--apply-update-internal":
        _apply_update(Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4]), sys.argv[5:])
        return 0
    ap = argparse.ArgumentParser(description="One-click HeyListen + game downloader")
    ap.add_argument("--manifest", help="sources.json path or URL (default: next to this script)")
    ap.add_argument("--out", default=str(Path.home() / "Downloads"),
                    help="folder for the .nup and builder (default: Downloads)")
    ap.add_argument("--builder-only", action="store_true")
    ap.add_argument("--game-only", action="store_true")
    ap.add_argument("--skip-hash", action="store_true", help="skip the 25 GB SHA-256 check")
    ap.add_argument("--force", action="store_true", help="re-download even if present")
    ap.add_argument("--no-launch", action="store_true", help="don't open HeyListenBuilder.exe")
    ap.add_argument("--no-preset", action="store_true", help="don't write the DLSS preset ini")
    ap.add_argument("--game-dir", default=str(Path.home() / "Games" / "HeyListen"),
                    help="built game folder for the DLSS preset")
    ap.add_argument("--print-sources", action="store_true", help="show the manifest and exit")
    ap.add_argument("--version", action="store_true", help="print the installed version and exit")
    ap.add_argument("--no-self-update", action="store_true",
                    help="skip the GitHub self-update check")
    ap.add_argument("--self-update", action="store_true",
                    help="run only the self-update check (installs a newer release if there is one)")
    ap.add_argument("--self-update-from", metavar="ZIP",
                    help="install a release zip from a local path (testing)")
    a = ap.parse_args()

    if a.version:
        print(local_version({}))
        return 0
    man = load_manifest(a.manifest)
    if a.print_sources:
        print(json.dumps(man, indent=2))
        return 0

    tool = man.get("tool", {})
    log(f"Manifest v{tool.get('version', '?')} — game: {man['game'].get('title', '?')}")

    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    s = session()

    if not a.no_self_update:
        try:
            check_self_update(s, man, sys.argv[1:], a.self_update_from)
        except Exception as e:
            log(f"Self-update failed safely: {e}")
            if a.self_update or a.self_update_from:
                return 1
    if a.self_update or a.self_update_from:
        log("Self-update check complete.")
        return 0

    builder = None
    if not a.game_only:
        builder = download_builder(s, man, out, force=a.force)
    if not a.builder_only:
        download_game(s, man, out, skip_hash=a.skip_hash, force=a.force)
        if not a.no_preset:
            apply_preset(Path(a.game_dir).expanduser())

    log("All set.")
    if builder and not a.no_launch:
        log("Opening HeyListenBuilder — pick the .nup, tick NVIDIA DLSS, press “Build the game”.")
        subprocess.Popen([str(builder)], cwd=str(builder.parent))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted — run again to resume.")
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
