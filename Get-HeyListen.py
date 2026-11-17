#!/usr/bin/env python3
"""
HeyListen Get — one-click downloader for HeyListen + the game dump.

  * Reads a manifest (sources.json, or the same file at a URL) that lists where the game
    comes from. Edit that manifest and every run picks the new links up — no code change.
  * Fetches the latest HeyListen release from GitHub and keeps HeyListenBuilder.exe current.
  * Downloads the Ocarina of Time (Switch 2) .nup, resumable, size/hash verified.
  * Seeds a Performance preset (DLSS on NVIDIA RTX + DLSS builds, else FSR 3.1) and can launch the Builder.

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
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, urlparse

try:
    from curl_cffi import requests
except ImportError:
    sys.exit("Missing dependency. Run:  python -m pip install curl_cffi")

HERE = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
APP_VERSION = "1.3.0"
MAX_UPDATE_BYTES = 150 * 1024 * 1024
MANIFEST_LOCAL = HERE / "sources.json"
MANIFEST_CACHE = HERE / "sources.cache.json"
VERSION_FILE = HERE / "VERSION"
UPDATE_ASSET = "HeyListen-Get.zip"
SUMS_ASSET = "SHA256SUMS.txt"

PRESET_DLSS = {         # engine/gpu/vk/vk_menu.cpp ini_key names
    "dlss": "1",        # 1 = NVIDIA DLSS
    "resolution": "4",  # 4 = 50% = DLSS Performance
    "sharpness": "80",
    "reflex": "1",
    "pace": "1",
    "framegen": "1",
    "options_version": "3",
}
PRESET_FSR = {
    "dlss": "3",        # 3 = AMD FSR 3.1 (compiled into every build)
    "resolution": "4",  # 4 = 50% = FSR Performance
    "sharpness": "80",
    "reflex": "0",      # Reflex is NVIDIA-only
    "pace": "1",
    "framegen": "1",
    "options_version": "3",
}
PRESET = PRESET_DLSS    # kept for backwards compatibility
DLSS_DLL = Path("ngx") / "nvngx_dlss.dll"
GPU_QUERY = ("Get-CimInstance Win32_VideoController | Sort-Object AdapterRAM -Descending | "
             "Select-Object -First 1 -ExpandProperty Name")


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


def content_range_start(r) -> int | None:
    """First byte offset of a 206 response's Content-Range, or None if absent/invalid."""
    m = re.fullmatch(r"\s*bytes\s+([0-9]+)-[0-9]+/(?:[0-9]+|\*)\s*", r.headers.get("content-range") or "")
    return int(m.group(1)) if m else None


def stream_to(s: "requests.Session", url: str, dest: Path, expect: int | None = None,
              referer: str | None = None, max_retries: int = 6, part: Path | None = None,
              progress=None, quiet: bool = False) -> int:
    """Resumable streaming download into dest (via `part`, default dest + '.part')."""
    part = part or Path(str(dest) + ".part")
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
            elif have and content_range_start(r) != have:
                # Never append bytes that do not continue the file on disk.
                r.close()
                part.unlink(missing_ok=True)
                raise ValueError(f"resume offset mismatch (have {have}), restarting part from zero")
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
                        if progress:
                            progress(len(chunk))
                        now = time.time()
                        if not quiet and now - last >= 2:
                            last = now
                            pct = f"{done * 100 / total:5.1f}%" if total else "  ?  "
                            rate = (done - have) / max(now - t0, 0.001)
                            log(f"  {pct}  {human(done)} / {human(total) if total else '?'}  ({human(rate)}/s)")
            finally:
                r.close()
            if total and done < total and attempt < max_retries:
                if not quiet:
                    log(f"  short read ({done}/{total}), resuming…")
                continue
            break
        except Exception as e:  # noqa: BLE001
            if attempt == max_retries:
                raise
            if not quiet:
                log(f"  network hiccup ({type(e).__name__}), retry {attempt}/{max_retries} in 3 s")
            time.sleep(3)
    if expect is not None and part.stat().st_size != expect:
        raise RuntimeError(f"size mismatch: {part.stat().st_size} != {expect}")
    part.replace(dest)
    return dest.stat().st_size


def source_part_tag(idx: int, src: dict) -> str:
    """Stable per-source suffix so partial files from different sources never mix."""
    ident = json.dumps([src.get("url"), src.get("parts"), src.get("size")], sort_keys=True)
    return f"s{idx}-{hashlib.sha256(ident.encode('utf-8')).hexdigest()[:10]}"


def download_game(s: "requests.Session", man: dict, out: Path, skip_hash: bool = False,
                  force: bool = False) -> Path:
    game = man["game"]
    dest = out / game["filename"]
    sizes = {src.get("size") for src in game["sources"] if src.get("size")}

    if dest.exists() and dest.stat().st_size in sizes and not force:
        log(f"Game already downloaded: {dest.name}")
    else:
        # Older runs shared one dest.part across sources; its origin is unknown.
        Path(str(dest) + ".part").unlink(missing_ok=True)
        last_err = None
        prev_parts: list[Path] = []
        for idx, src in enumerate(game["sources"]):
            tag = source_part_tag(idx, src)
            for stale in prev_parts:            # never resume another source's bytes
                stale.unlink(missing_ok=True)
            prev_parts = [Path(str(dest) + f".{tag}.part")]
            try:
                urls, size = source_urls(s, src)
                log(f"Game source: {src.get('url') or src.get('parts')[0]}  ->  {human(size) if size else '?'}")
                if len(urls) == 1:
                    stream_to(s, urls[0][0], dest, expect=size, referer=urls[0][1], part=prev_parts[0])
                else:   # parts joined in order
                    tmp = Path(str(dest) + ".join")
                    prev_parts.append(tmp)
                    with open(tmp, "wb") as f:
                        for i, (u, ref) in enumerate(urls, 1):
                            p = Path(str(dest) + f".p{i}")
                            pp = Path(str(dest) + f".{tag}.p{i}.part")
                            prev_parts.append(pp)
                            stream_to(s, u, p, referer=ref, part=pp)
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


def is_dlss_gpu(name: str | None) -> bool:
    """True for an NVIDIA RTX adapter name (the only GPUs that run DLSS)."""
    n = (name or "").upper()
    return "NVIDIA" in n and "RTX" in n


def detect_gpu_name() -> str | None:
    """Name of the primary GPU (largest adapter RAM), or None if it can't be read."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", GPU_QUERY],
                       capture_output=True, text=True, timeout=30, creationflags=flags)
    name = (r.stdout or "").strip().splitlines()
    return name[0].strip() if r.returncode == 0 and name and name[0].strip() else None


def choose_preset(game_dir: Path) -> tuple[dict, str]:
    """Pick the DLSS or FSR 3.1 preset for this PC + build. Never raises."""
    try:
        gpu = detect_gpu_name()
    except Exception as e:
        return PRESET_FSR, f"GPU detection failed ({e.__class__.__name__}) -> FSR 3.1 Performance"
    if gpu is None:
        return PRESET_FSR, "GPU could not be identified -> FSR 3.1 Performance"
    try:
        has_dll = (Path(game_dir) / DLSS_DLL).is_file()
    except Exception:
        has_dll = False
    if is_dlss_gpu(gpu) and has_dll:
        return PRESET_DLSS, f"NVIDIA RTX with DLSS build ({gpu}) -> DLSS Performance"
    if is_dlss_gpu(gpu):
        return PRESET_FSR, f"No RTX/DLSS build ({gpu}, but {DLSS_DLL} missing) -> FSR 3.1 Performance"
    return PRESET_FSR, f"No RTX/DLSS build ({gpu}) -> FSR 3.1 Performance"


def apply_preset(game_dir: Path) -> Path:
    """Merge the auto-chosen preset into game_dir/hookshot_options.ini, keeping every line we do not own."""
    game_dir = Path(game_dir)
    preset, why = choose_preset(game_dir)
    log(why)
    game_dir.mkdir(parents=True, exist_ok=True)
    ini = game_dir / "hookshot_options.ini"
    lines = ini.read_text(encoding="ascii", errors="replace").splitlines() if ini.exists() else []
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        eq = line.find("=")
        key = line[:eq].strip() if eq > 0 else None
        if key in preset:
            if key not in seen:
                out.append(f"{key}={preset[key]}")
                seen.add(key)
        else:
            out.append(line)
    out.extend(f"{k}={v}" for k, v in preset.items() if k not in seen)
    ini.write_text("\n".join(out) + "\n", encoding="ascii", errors="replace")
    label = "DLSS" if preset is PRESET_DLSS else "FSR 3.1"
    log(f"{label} preset written: {ini}")
    return ini


def sha256(p: Path, progress: bool = False) -> str:
    h = hashlib.sha256()
    total = p.stat().st_size if progress else 0
    done = 0
    last = 0.0
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
            if progress:
                done += len(block)
                if time.monotonic() - last >= 0.5:
                    last = time.monotonic()
                    print(f'\rChecking SHA-256: {human(done)} / {human(total)}', end='', flush=True)
    if progress:
        print(f'\rChecking SHA-256: {human(done)} / {human(total)}', flush=True)
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


APP_TAG_RE = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")


def latest_app_release(s: "requests.Session", repo: str) -> dict | None:
    """Highest published, non-prerelease release tagged exactly vX.Y.Z, or None.

    Other releases on the same repo (e.g. 'game-v1') must never break updates.
    """
    r = s.get(f"https://api.github.com/repos/{repo}/releases?per_page=30",
              headers={"accept": "application/vnd.github+json"}, timeout=60)
    r.raise_for_status()
    best, best_key = None, None
    for rel in r.json() or []:
        if not isinstance(rel, dict) or rel.get("draft") or rel.get("prerelease"):
            continue
        tag = rel.get("tag_name") or ""
        if not APP_TAG_RE.fullmatch(tag):
            continue
        key = version_key(tag)
        if best_key is None or key > best_key:
            best, best_key = rel, key
    return best


def check_self_update(s: "requests.Session", man: dict, args: list[str],
                      local_zip: str | None = None) -> None:
    if not getattr(sys, "frozen", False):
        log("Self-update is available only for the packaged executable.")
        return
    if local_zip:
        raise ValueError("unsigned local updates are disabled")
    # Executable updates must not be redirected by the mutable sources manifest.
    rel = latest_app_release(s, "cputroll/Silver-Lantern")
    if rel is None:
        log(f"Self-update: no stable vX.Y.Z release found, up to date ({APP_VERSION})")
        return
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
GAME_MANIFEST_URL = "https://raw.githubusercontent.com/cputroll/Silver-Lantern/main/game-manifest.json"
GAME_ASSET_URL = "https://github.com/cputroll/Silver-Lantern/releases/download/game-v1/"


def parse_game_manifest(text: str) -> dict:
    try:
        man = json.loads(text)
        if not isinstance(man, dict) or man['version'] != 1 or man['build'] != 'game-v1':
            raise ValueError('unsupported manifest')
        files = man['files']
        if not isinstance(files, list):
            raise ValueError('files must be a list')
        seen = set()
        assets = set()
        def size(value):
            if type(value) is not int or value < 0:
                raise ValueError('invalid size')
            return value
        def digest(value):
            if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
                raise ValueError('invalid SHA-256')
        for item in files:
            path = item['path']
            if not isinstance(path, str) or not path or '\\' in path or ':' in path:
                raise ValueError('invalid path')
            segments = path.split('/')
            if any(not p or p in ('.', '..') or p.endswith(('.', ' ')) for p in segments):
                raise ValueError('invalid path')
            key = path.casefold()
            if (key in seen or segments[0].casefold() == '.heylisten-parts'
                    or any(p.casefold() == 'user' for p in segments[:-1])):
                raise ValueError('duplicate or reserved path')
            seen.add(key)
            size(item['size']); digest(item['sha256'])
            parts = item['parts']
            if not isinstance(parts, list) or not parts:
                raise ValueError('missing parts')
            for part in parts:
                asset = part['asset']
                if not isinstance(asset, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', asset) or asset in ('.', '..') or asset.casefold() in assets:
                    raise ValueError('invalid or duplicate asset')
                assets.add(asset.casefold())
                size(part['size']); digest(part['sha256'])
            if sum(p['size'] for p in parts) != item['size']:
                raise ValueError('part sizes do not match file')
        if size(man['total_bytes']) != sum(f['size'] for f in files):
            raise ValueError('total size mismatch')
        return man
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('invalid game manifest') from exc


def load_game_manifest(s, source: str) -> dict:
    if urlparse(source).scheme in ('https', 'http'):
        response = s.get(source, timeout=120)
        try:
            response.raise_for_status()
            text = response.text
        finally:
            response.close()
    else:
        text = Path(source).expanduser().read_text(encoding='utf-8')
    return parse_game_manifest(text)


class GameProgress:
    def __init__(self, total):
        self.total = total
        self.done = 0
        self.lock = threading.Lock()
        self.last = 0.0

    def add(self, amount):
        with self.lock:
            self.done += amount
            if time.monotonic() - self.last >= 0.5:
                self.last = time.monotonic()
                print(f'\rDownloading: {human(self.done)} / {human(self.total)}', end='', flush=True)

    def finish(self):
        with self.lock:
            print(f'\rDownloading: {human(self.done)} / {human(self.total)}', flush=True)


def game_path(root: Path, relative: str) -> Path:
    target = root.joinpath(*relative.split('/'))
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('manifest path escapes install directory')
    return target


def install_game(s, man: dict, root: Path, dry_run: bool = False, prune: bool = False,
                 manifest_path: Path | None = None) -> None:
    root = root.expanduser()
    pending = []
    kept = 0
    for item in man['files']:
        target = game_path(root, item['path'])
        if target.is_file() and target.stat().st_size == item['size']:
            if item['size'] >= 256 * 1024 * 1024:
                log(f"Checking SHA-256: {item['path']} ({human(item['size'])})")
            if sha256(target, progress=item['size'] >= 256 * 1024 * 1024) == item['sha256']:
                kept += item['size']
                continue
        pending.append(item)
    cache = root / '.heylisten-parts'
    download_bytes = 0
    for item in pending:
        for part in item['parts']:
            done = cache / part['asset']
            partial = Path(str(done) + '.part')
            if done.is_file() and done.stat().st_size == part['size'] and sha256(done) == part['sha256']:
                continue
            have = partial.stat().st_size if partial.is_file() else 0
            download_bytes += part['size'] - (have if have < part['size'] else 0)
    log(f"Plan: {len(man['files']) - len(pending)} files present, {len(pending)} to download ({download_bytes / 1024**3:.2f} GB), {kept / 1024**3:.2f} GB kept")
    if dry_run:
        return
    root.mkdir(parents=True, exist_ok=True)
    if pending:
        cache.mkdir(parents=True, exist_ok=True)
        progress = GameProgress(download_bytes)
        def fetch(part):
            dest = cache / part['asset']
            if dest.is_file() and dest.stat().st_size == part['size'] and sha256(dest) == part['sha256']:
                return
            dest.unlink(missing_ok=True)
            partial = Path(str(dest) + '.part')
            if partial.is_file() and partial.stat().st_size == part['size']:
                if sha256(partial) == part['sha256']:
                    partial.replace(dest)
                    return
                partial.unlink()
            # Each worker owns its HTTP session; sessions are not shared between threads.
            worker = session() if s is None else s
            stream_to(worker, GAME_ASSET_URL + quote(part['asset']), dest, expect=part['size'], progress=progress.add, quiet=True)
            if sha256(dest) != part['sha256']:
                dest.unlink(missing_ok=True)
                raise ValueError(f"SHA-256 mismatch: {part['asset']}")
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(fetch, (p for f in pending for p in f['parts'])))
        progress.finish()
        for item in pending:
            target = game_path(root, item['path'])
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(target.name + '.heylisten-new')
            try:
                with temp.open('wb') as output:
                    for part in item['parts']:
                        with (cache / part['asset']).open('rb') as source:
                            shutil.copyfileobj(source, output, 1024 * 1024)
                if temp.stat().st_size != item['size'] or sha256(temp) != item['sha256']:
                    raise ValueError(f"assembled SHA-256 mismatch: {item['path']}")
                temp.replace(target)
            finally:
                temp.unlink(missing_ok=True)
        shutil.rmtree(cache)
    if prune:
        wanted = {f['path'].casefold() for f in man['files']}
        manifest_resolved = manifest_path.resolve() if manifest_path else None
        for path in root.rglob('*'):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(root)
            if any(x.casefold() == 'user' for x in relative.parts[:-1]):
                continue
            if relative.as_posix().casefold() in wanted or path.name.casefold() == 'game-manifest.json' or (manifest_resolved and path.resolve() == manifest_resolved):
                continue
            path.unlink()


def default_game_folder() -> str:
    return str(Path(os.environ.get('USERPROFILE', str(Path.home()))) / 'Games' / 'HeyListen')


UPDATE_LOG = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'HeyListenBuilder' / 'game-update-cli.log'


def parse_update_line(line: str) -> dict:
    if not line.startswith('HLUPDATE '):
        return {}
    payload = line[len('HLUPDATE '):].strip()
    return dict(re.findall(r'([A-Za-z_][\w-]*)=([^\s]+)', payload))


def render_update_status(fields: dict) -> str | None:
    version = lambda value: 'v' + str(value or '?').lstrip('vV')
    old = version(fields.get('installed', fields.get('current')))
    new = version(fields.get('latest', fields.get('available')))
    status = fields.get('status')
    if status in ('current', 'up-to-date'):
        return f'Already up to date ({old}).'
    if status in ('available', 'update-available'):
        return f'Update available: {old} -> {new} (dry run).'
    if status == 'updated':
        return f"Updated {old} -> {new} in {fields.get('total', fields.get('seconds', '?'))}s."
    if status == 'error':
        return f"Update failed: {fields.get('error', fields.get('reason', 'unknown error'))}"
    return None


def update_exit_code(code: int) -> int:
    return 0 if code in (0, 10) else 1


def run_game_update(folder: Path, dry_run: bool = False, force: bool = False, silent: bool = False) -> int:
    script = next((p for p in (HERE / 'Update-Game.ps1', Path.cwd() / 'Update-Game.ps1') if p.is_file()), None)
    if script is None:
        log('Update failed: Update-Game.ps1 not found.')
        return 1
    args = ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script), '-GameFolder', str(folder)]
    if dry_run:
        args.append('-DryRun')
    if force:
        args.append('-Force')
    fields = {}
    shown = False
    try:
        UPDATE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with UPDATE_LOG.open('a', encoding='utf-8', errors='replace') as output:
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', bufsize=1)
            for line in proc.stdout:
                output.write(line)
                output.flush()
                parsed = parse_update_line(line)
                fields.update(parsed)
                # The script emits its error reason after status=error.
                if not silent and parsed.get('status') not in (None, 'error'):
                    message = render_update_status(fields)
                    if message:
                        log(message.encode('ascii', 'replace').decode('ascii'))
                        shown = True
            code = update_exit_code(proc.wait())
        if silent or (not shown and (fields.get('status') == 'error' or code)):
            message = render_update_status(fields) or 'Update failed: update check failed.'
            log(message.encode('ascii', 'replace').decode('ascii'))
        return code
    except (OSError, ValueError) as e:
        log(('Update failed: ' + str(e)).encode('ascii', 'replace').decode('ascii'))
        return 1


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
    ap.add_argument("--no-preset", action="store_true", help="don't write the upscaler preset ini")
    ap.add_argument("--game-dir", default=str(Path.home() / "Games" / "HeyListen"),
                    help="built game folder for the upscaler preset")
    ap.add_argument("--print-sources", action="store_true", help="show the manifest and exit")
    ap.add_argument("--version", action="store_true", help="print the installed version and exit")
    ap.add_argument("--no-self-update", action="store_true",
                    help="skip the GitHub self-update check")
    ap.add_argument("--self-update", action="store_true",
                    help="run only the self-update check (installs a newer release if there is one)")
    ap.add_argument("--self-update-from", metavar="ZIP",
                    help="install a release zip from a local path (testing)")
    ap.add_argument('--install-game', nargs='?', const=str(Path(os.environ.get('USERPROFILE', str(Path.home()))) / 'Games' / 'HeyListen'), metavar='FOLDER')
    ap.add_argument('--game-manifest', default=GAME_MANIFEST_URL, metavar='PATH_OR_URL')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--prune', action='store_true')
    ap.add_argument('--update-game', nargs='?', const=default_game_folder(), metavar='FOLDER')
    ap.add_argument('--update-dry-run', action='store_true')
    ap.add_argument('--force-update', action='store_true')
    ap.add_argument('--update-on-launch', action='store_true')
    a = ap.parse_args()

    if a.update_game is not None:
        return run_game_update(Path(a.update_game).expanduser(), dry_run=a.update_dry_run, force=a.force_update)
    if a.install_game is not None:
        s = session()
        game_manifest = load_game_manifest(s, a.game_manifest)
        local_manifest = None if urlparse(a.game_manifest).scheme in ('http', 'https') else Path(a.game_manifest).expanduser()
        install_game(None, game_manifest, Path(a.install_game), a.dry_run, a.prune, local_manifest)
        return 0
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
    if a.update_on_launch:
        try:
            run_game_update(Path(a.game_dir).expanduser(), dry_run=True, silent=True)
        except Exception as e:
            log(('Update failed: ' + str(e)).encode('ascii', 'replace').decode('ascii'))
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
