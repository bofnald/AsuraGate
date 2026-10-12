"""Shortcuts: application menu (~/.local/share/applications) and desktop (opens on double-click)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

FILE_NAME = "asuragate.desktop"
ICON = Path(__file__).resolve().parent / "icon.svg"
SRC_DIR = ICON.parent.parent  # src/: the working directory for `python3 -m asuragate`
LAUNCHER = SRC_DIR.parent / "AsuraGate"  # double-click launcher in the project folder


def applications_dir(environ: dict[str, str]) -> Path:
    base = environ.get("XDG_DATA_HOME") or str(Path(environ.get("HOME", str(Path.home()))) / ".local/share")
    return Path(base) / "applications"


def desktop_dir(environ: dict[str, str]) -> Path | None:
    """The XDG desktop folder (with its localized name, e.g. ~/Schreibtisch)."""
    try:
        out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, env=environ, timeout=5)
        d = Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None
    except (OSError, subprocess.SubprocessError):
        d = None
    home = Path(environ.get("HOME", str(Path.home())))
    d = d or home / "Desktop"
    return d if d.is_dir() and d != home else None


def _quote(arg: str) -> str:
    """Desktop Entry Exec rules: arguments with spaces/special characters are double-quoted."""
    if arg and not any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`'):
        return arg
    return '"' + "".join("\\" + c if c in '"`$\\' else c for c in arg) + '"'


def desktop_entry(python: str, project_dir: Path, icon: Path = ICON) -> str:
    base = [python, "-m", "asuragate"]
    run = " ".join(_quote(a) for a in base)
    play = " ".join(_quote(a) for a in [*base, "launch"])
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=AsuraGate\n"
        "Comment=Install and launch Guild Wars 2 with Blish HUD\n"
        f"Exec={run}\n"
        f"Path={project_dir}\n"
        f"Icon={icon if icon.is_file() else 'applications-games'}\n"
        "Terminal=false\n"
        "Categories=Game;\n"
        "Actions=play;\n"
        "\n"
        "[Desktop Action play]\n"
        "Name=Play\n"
        f"Exec={play}\n"
    )


def default_python() -> str:
    """The venv's Python when running in one; otherwise the system python3 from PATH (distro-independent)."""
    return sys.executable if sys.prefix != sys.base_prefix else "python3"


def _targets(environ: dict[str, str]) -> list[Path]:
    out = [applications_dir(environ) / FILE_NAME]
    d = desktop_dir(environ)
    if d is not None:
        out.append(d / FILE_NAME)
    return out


def install_shortcut(environ: dict[str, str] | None = None, python: str | None = None) -> list[Path]:
    environ = dict(os.environ if environ is None else environ)
    entry = desktop_entry(python or default_python(), SRC_DIR)
    written = []
    for path in _targets(environ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(entry, encoding="utf-8")
        path.chmod(0o755)  # KDE asks before opening a non-executable .desktop file
        written.append(path)
    return written


def launcher_with_icon(text: str, icon: Path = ICON) -> str:
    """The project folder's AsuraGate launcher with an absolute Icon= (the spec does not allow relative paths)."""
    return re.sub(r"(?m)^Icon=.*$", lambda _: f"Icon={icon}", text, count=1)


def register_menu(environ: dict[str, str] | None = None, python: str | None = None,
                  launcher: Path = LAUNCHER) -> list[Path]:
    """Started from the AsuraGate launcher in the project folder: adds/updates the application menu entry and
    points the launcher's icon at icon.svg (both again after the folder was moved). Writes only what differs;
    returns the files written."""
    environ = dict(os.environ if environ is None else environ)
    written = []
    for path in (applications_dir(environ) / FILE_NAME, launcher):
        try:
            old = path.read_text(encoding="utf-8") if path.is_file() else None
            if path == launcher:
                if old is None:
                    continue
                new = launcher_with_icon(old)
            else:
                new = desktop_entry(python or default_python(), launcher.parent / "src")
            if new == old:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(new, encoding="utf-8")  # keeps the launcher's executable bit
        except OSError:
            continue  # never keeps the UI from opening
        written.append(path)
    return written


def remove_shortcut(environ: dict[str, str] | None = None) -> list[Path]:
    environ = dict(os.environ if environ is None else environ)
    removed = []
    for path in _targets(environ):
        if path.exists():
            path.unlink()
            removed.append(path)
    return removed
