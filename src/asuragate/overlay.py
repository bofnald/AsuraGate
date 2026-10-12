"""Installs the external-dx11-overlay package, which renders Blish HUD inside the game.

The package (https://github.com/SorryQuick/external-dx11-overlay) is extracted into the GW2 folder as
addons/LOADER_public/: Gw2-Simple-Addon-Loader.exe starts GW2 with the same arguments, injects
external_dx11_overlay.dll into the game and runs the bundled (forked) Blish HUD. The Blish HUD window stays
hidden; its image is drawn onto every game frame through a shared texture. This avoids all window manager,
transparency and fullscreen problems.

Every installed file is recorded in a manifest (sha256): uninstalling only removes files we installed and
that are unchanged; existing files we do not own are never overwritten.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class Release:
    tag: str
    url: str
    sha256: str


RELEASE = Release(
    tag="release-0.96",
    url="https://github.com/SorryQuick/external-dx11-overlay/releases/download/release-0.96/external-dx11-overlay.zip",
    sha256="f6d04b4a3e325b5f8121bb100c0974f0d259724c3efb731a39839fdcfe4de57d",
)

LOADER_DIR = PurePosixPath("addons/LOADER_public")
LOADER_EXE = LOADER_DIR / "Gw2-Simple-Addon-Loader.exe"
MANIFEST = LOADER_DIR / "asuragate.json"
# Created by the overlay itself while the game runs (not in the package or the manifest).
LOGS_DIR = LOADER_DIR / "logs"
KEYBINDS = LOADER_DIR / "keybinds.conf"  # the overlay's own shortcuts (e.g. Ctrl+Alt+O), not the game's
# The loader resolves relative paths in these lists against the working directory, not its own folder;
# when started from GW2's Steam entry the working directory is the game folder. So they become absolute.
PATH_LISTS = (str(LOADER_DIR / "dlls.txt"), str(LOADER_DIR / "exes.txt"))


class OverlayError(Exception):
    pass


@dataclass
class Status:
    installed: bool
    tag: str | None = None
    missing: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.installed and self.tag == RELEASE.tag and not self.missing and not self.modified


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def wine_path(path: Path) -> str:
    """Converts a Linux path to a Windows path via Wine's Z: (root) drive."""
    return "Z:" + str(path).replace("/", "\\")


def _absolutize(game_dir: Path, data: bytes) -> bytes:
    base = game_dir / LOADER_DIR
    lines = []
    for line in data.decode("utf-8-sig").splitlines():
        entry = line.strip()
        if entry and not (len(entry) > 1 and entry[1] == ":") and not entry.startswith("\\"):
            entry = wine_path(base / PurePosixPath(entry.replace("\\", "/")))
        lines.append(entry)
    return ("\n".join(lines) + "\n").encode("utf-8")  # the loader splits on \n only and keeps \r


def download(release: Release, cache_dir: Path, opener=urllib.request.urlopen) -> Path:
    """Downloads the package (unless cached) and verifies its sha256."""
    dest = cache_dir / f"external-dx11-overlay-{release.tag}.zip"
    if dest.is_file() and _sha256(dest) == release.sha256:
        return dest
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    try:
        with opener(release.url, timeout=60) as resp, open(tmp, "wb") as f:
            while chunk := resp.read(1 << 20):
                f.write(chunk)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise OverlayError(f"Download failed: {release.url} ({e})") from e
    got = _sha256(tmp)
    if got != release.sha256:
        tmp.unlink(missing_ok=True)
        raise OverlayError(f"sha256 mismatch: expected {release.sha256}, got {got}")
    os.replace(tmp, dest)
    return dest


def _members(zf: zipfile.ZipFile) -> list[tuple[str, zipfile.ZipInfo]]:
    """Only files under addons/LOADER_public/; paths escaping it are rejected."""
    out = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        p = PurePosixPath(info.filename.replace("\\", "/"))
        if p.is_absolute() or ".." in p.parts:
            raise OverlayError(f"Unsafe path in package: {info.filename!r}")
        if p.parts[: len(LOADER_DIR.parts)] != LOADER_DIR.parts:
            raise OverlayError(f"Unexpected path in package: {info.filename!r}")
        out.append((str(p), info))
    if str(LOADER_EXE) not in {name for name, _ in out}:
        raise OverlayError(f"Package does not contain {LOADER_EXE}.")
    return out


def read_manifest(game_dir: Path) -> dict:
    try:
        data = json.loads((game_dir / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and isinstance(data.get("files"), dict) else {}


def status(game_dir: Path) -> Status:
    m = read_manifest(game_dir)
    if not m:
        return Status(installed=False)
    st = Status(installed=True, tag=m.get("tag"))
    for rel, digest in m["files"].items():
        p = game_dir / rel
        if not p.is_file():
            st.missing.append(rel)
        elif _sha256(p) != digest:
            st.modified.append(rel)
    return st


def install(game_dir: Path, zip_path: Path, release: Release = RELEASE) -> list[str]:
    """Installs/updates the package in the game folder; returns the files written."""
    old = read_manifest(game_dir).get("files", {})
    with zipfile.ZipFile(zip_path) as zf:
        members = _members(zf)
        # Never overwrite existing files we don't own whose content differs.
        contents = {rel: zf.read(info) for rel, info in members}
        for rel in PATH_LISTS:
            if rel in contents:
                contents[rel] = _absolutize(game_dir, contents[rel])
        conflicts = []
        for rel, data in contents.items():
            p = game_dir / rel
            if p.exists() and rel not in old and _sha256(p) != hashlib.sha256(data).hexdigest():
                conflicts.append(rel)
        if conflicts:
            raise OverlayError("These files already exist and do not belong to AsuraGate: " + ", ".join(conflicts))
        files, written = {}, []
        for rel, data in contents.items():
            digest = hashlib.sha256(data).hexdigest()
            p = game_dir / rel
            files[rel] = digest
            if p.is_file() and _sha256(p) == digest:
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, p)
            written.append(rel)
    # Remove old files that are not part of the new release.
    for rel in set(old) - set(files):
        _remove_if_unchanged(game_dir, rel, old[rel])
    manifest = game_dir / MANIFEST
    manifest.write_text(json.dumps({"tag": release.tag, "files": files}, indent=2) + "\n", encoding="utf-8")
    return written


def _remove_if_unchanged(game_dir: Path, rel: str, digest: str) -> bool:
    p = game_dir / rel
    if p.is_file() and _sha256(p) == digest:
        p.unlink()
        _prune_dirs(game_dir, p.parent)
        return True
    return False


def _prune_dirs(game_dir: Path, d: Path) -> None:
    stop = game_dir.resolve()
    while d.resolve() != stop and d.is_dir():
        try:
            d.rmdir()
        except OSError:
            return
        d = d.parent


def uninstall(game_dir: Path) -> list[str]:
    """Removes the files we installed that are unchanged; returns the ones kept (modified)."""
    m = read_manifest(game_dir)
    kept = []
    for rel, digest in m.get("files", {}).items():
        if (game_dir / rel).exists() and not _remove_if_unchanged(game_dir, rel, digest):
            kept.append(rel)
    manifest = game_dir / MANIFEST
    if manifest.exists():
        manifest.unlink()
        _prune_dirs(game_dir, manifest.parent)
    return kept


def log_files(game_dir: Path) -> list[Path]:
    d = game_dir / LOGS_DIR
    return sorted(p for p in d.iterdir() if p.is_file()) if d.is_dir() and not d.is_symlink() else []


def keybinds_file(game_dir: Path) -> Path | None:
    p = game_dir / KEYBINDS
    return p if p.is_file() else None


def remove_runtime_files(game_dir: Path, logs: bool, keybinds: bool) -> list[str]:
    """Removes what the overlay created while running (log folder, shortcut file) if asked, then the
    empty addons folders; returns what was removed."""
    removed = []
    if logs and log_files(game_dir):
        shutil.rmtree(game_dir / LOGS_DIR)
        removed.append(str(LOGS_DIR) + "/")
    if keybinds and keybinds_file(game_dir):
        (game_dir / KEYBINDS).unlink()
        removed.append(str(KEYBINDS))
    if (game_dir / LOADER_DIR).is_dir():
        _prune_dirs(game_dir, game_dir / LOADER_DIR)
    return removed
