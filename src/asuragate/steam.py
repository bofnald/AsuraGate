"""Steam directory discovery and a minimal VDF parser/editor."""

from __future__ import annotations

import os
import re
from pathlib import Path

_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')
_ESC = {"n": "\n", "t": "\t"}


def _unescape(s: str) -> str:
    return re.sub(r"\\(.)", lambda m: _ESC.get(m.group(1), m.group(1)), s)


def parse_vdf(text: str) -> dict:
    """Parses Valve KeyValues text into nested dicts (comment lines are skipped)."""
    text = re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE)
    root: dict = {}
    stack = [root]
    key = None
    for m in _TOKEN.finditer(text):
        s, brace = m.groups()
        if brace == "{":
            child: dict = {}
            if key is not None:
                stack[-1][key] = child
            stack.append(child)
            key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        else:
            s = _unescape(s)
            if key is None:
                key = s
            else:
                stack[-1][key] = s
                key = None
    return root


def vdf_get(node, *path: str):
    """Case-insensitive path lookup; None if not found."""
    for part in path:
        if not isinstance(node, dict):
            return None
        for k, v in node.items():
            if k.casefold() == part.casefold():
                node = v
                break
        else:
            return None
    return node


def _read_vdf(path: Path) -> dict:
    try:
        return parse_vdf(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return {}


def is_steam_root(path: Path) -> bool:
    """Steam's main folder: has steamapps/ and config/ (a library on another drive has only steamapps/)."""
    return (path / "steamapps").is_dir() and (path / "config").is_dir()


def find_steam_root(home: Path | None = None) -> Path | None:
    home = home or Path.home()
    candidates = [
        home / ".local/share/Steam",
        home / ".steam/steam",
        home / ".steam/root",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",  # Flatpak
        home / "snap/steam/common/.local/share/Steam",  # Snap
    ]
    for c in candidates:
        if is_steam_root(c):
            return c.resolve()
    return None


def library_folders(steam_root: Path) -> list[Path]:
    """Steam library folders (including steam_root, without duplicates)."""
    libs = [steam_root]
    data = _read_vdf(steam_root / "steamapps" / "libraryfolders.vdf")
    root = vdf_get(data, "libraryfolders") or {}
    for entry in root.values() if isinstance(root, dict) else []:
        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
            p = Path(entry["path"])
            if p not in libs:
                libs.append(p)
    return libs


def find_compatdata(steam_root: Path, app_id: int) -> Path | None:
    for lib in library_folders(steam_root):
        c = lib / "steamapps" / "compatdata" / str(app_id)
        if c.is_dir():
            return c
    return None


def prefix_path(compatdata: Path) -> Path:
    return compatdata / "pfx"


def compat_tool_name(steam_root: Path, app_id: int) -> str | None:
    """Internal name of the compatibility tool Steam assigned to this game (else the global default)."""
    data = _read_vdf(steam_root / "config" / "config.vdf")
    mapping = vdf_get(data, "InstallConfigStore", "Software", "Valve", "Steam", "CompatToolMapping")
    for key in (str(app_id), "0"):
        name = vdf_get(mapping, key, "name")
        if isinstance(name, str) and name:
            return name
    return None


def read_launch_options(steam_root: Path, app_id: int) -> list[str]:
    """Saved launch options of every Steam user."""
    out = []
    for cfg in sorted((steam_root / "userdata").glob("*/config/localconfig.vdf")):
        data = _read_vdf(cfg)
        opts = vdf_get(data, "UserLocalConfigStore", "Software", "Valve", "Steam", "apps", str(app_id), "LaunchOptions")
        if isinstance(opts, str) and opts:
            out.append(opts)
    return out


def find_app_dir(steam_root: Path, app_id: int) -> Path | None:
    """The game's install folder (installdir in the appmanifest)."""
    for lib in library_folders(steam_root):
        data = _read_vdf(lib / "steamapps" / f"appmanifest_{app_id}.acf")
        name = vdf_get(data, "AppState", "installdir")
        if isinstance(name, str) and name:
            d = lib / "steamapps" / "common" / name
            if d.is_dir():
                return d
    return None


_TOKEN_POS = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])|(//[^\n]*)')
LAUNCH_OPTIONS_PATH = ("UserLocalConfigStore", "Software", "Valve", "Steam", "apps")


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def set_vdf_value(text: str, path: tuple[str, ...], key: str, value: str) -> str | None:
    """Replaces the value of `key` in section `path` in place (appends it to the section if missing);
    the rest of the file (order, indentation, other keys) is left untouched. None if the section is missing."""
    want = [p.casefold() for p in path]
    stack: list[str] = []
    pending: str | None = None
    for m in _TOKEN_POS.finditer(text):
        s, brace, comment = m.groups()
        if comment is not None:
            continue
        if brace == "{":
            stack.append((pending or "").casefold())
            pending = None
        elif brace == "}":
            if stack == want:  # key not in section: insert before the closing brace line
                line_start = text.rfind("\n", 0, m.start()) + 1
                indent = text[line_start:m.start()]
                line = f'{indent}\t"{key}"\t\t"{_escape(value)}"\n'
                return text[:line_start] + line + text[line_start:]
            if stack:
                stack.pop()
            pending = None
        elif pending is None:
            pending = _unescape(s)
        else:
            if stack == want and pending.casefold() == key.casefold():
                return text[:m.start()] + f'"{_escape(value)}"' + text[m.end():]
            pending = None
    return None


BACKUP_SUFFIX = ".asuragate.bak"


def localconfig_files(steam_root: Path) -> list[Path]:
    return sorted((steam_root / "userdata").glob("*/config/localconfig.vdf"))


def launch_options_backups(steam_root: Path) -> list[Path]:
    """The copies of localconfig.vdf that write_launch_options made before its first change."""
    return [b for b in (c.with_name(c.name + BACKUP_SUFFIX) for c in localconfig_files(steam_root)) if b.is_file()]


def write_launch_options(steam_root: Path, app_id: int, value: str) -> list[Path]:
    """Writes the launch options for every Steam user that has the game; returns the changed files.
    Call only while Steam is closed: a running Steam writes its own copy back when it exits."""
    changed = []
    for cfg in localconfig_files(steam_root):
        try:
            text = cfg.read_text(encoding="utf-8")
        except OSError:
            continue
        new = set_vdf_value(text, (*LAUNCH_OPTIONS_PATH, str(app_id)), "LaunchOptions", value)
        if new is None or new == text:
            continue
        backup = cfg.with_name(cfg.name + BACKUP_SUFFIX)
        if not backup.exists():
            backup.write_text(text, encoding="utf-8")
        tmp = cfg.with_name(cfg.name + ".tmp")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, cfg)
        changed.append(cfg)
    return changed
