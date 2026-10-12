"""Blish HUD's own settings (settings.json in the prefix).

Frame limiter: the default is "Locked to 60 FPS" (2). With the overlay, Blish HUD crashes when Pathing
trails are enabled; "Match Monitor Refresh Rate" (4, unlimited with vsync) avoids it
(https://github.com/SorryQuick/external-dx11-overlay/issues/7).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

GRAPHICS = "GraphicsConfiguration"
FRAME_LIMITER = "FramerateLimiter"
FRAME_LIMITER_TYPE = "Blish_HUD.Graphics.FramerateMethod, Blish HUD"
MATCH_MONITOR = 4  # FramerateMethod.Unlimited, shown as "Match Monitor Refresh Rate" in Blish HUD


def settings_path(pfx: Path) -> Path:
    return pfx / "drive_c/users/steamuser/Documents/Guild Wars 2/addons/blishhud/settings.json"


def _entry(entries: list, key: str) -> dict | None:
    for e in entries:
        if isinstance(e, dict) and e.get("Key") == key:
            return e
    return None


def _graphics_entries(data) -> list | None:
    g = _entry(data.get("Entries", []), GRAPHICS) if isinstance(data, dict) else None
    entries = g.get("Value", {}).get("Entries") if g and isinstance(g.get("Value"), dict) else None
    return entries if isinstance(entries, list) else None


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def frame_limiter(pfx: Path) -> int | None:
    """The configured value; None if the file/setting is missing (Blish HUD has never run)."""
    entries = _graphics_entries(_load(settings_path(pfx)))
    if entries is None:
        return None
    e = _entry(entries, FRAME_LIMITER)
    return e.get("Value") if e else None


def set_frame_limiter(pfx: Path, value: int) -> bool:
    """Writes the value; True if it changed. False if there is no settings file (Blish HUD has never run)."""
    path = settings_path(pfx)
    data = _load(path)
    entries = _graphics_entries(data)
    if entries is None:
        return False
    e = _entry(entries, FRAME_LIMITER)
    if e is not None and e.get("Value") == value:
        return False
    if e is None:
        entries.insert(0, {"T": FRAME_LIMITER_TYPE, "Key": FRAME_LIMITER, "Value": value})
    else:
        e["Value"] = value
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return True
