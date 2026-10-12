"""Proton runner discovery: GW2's own Proton, used to run reg.exe in GW2's prefix."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .steam import compat_tool_name, library_folders


@dataclass(frozen=True)
class Runner:
    name: str
    path: Path

    @property
    def proton(self) -> Path:
        return self.path / "proton"


def _natural_key(s: str):
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", s)]


def _version_key(name: str) -> list[int]:
    """"Proton-8.0-5" -> [8, 0, 5]; names without a version ("Proton - Experimental") rank lowest."""
    return [int(n) for n in re.findall(r"\d+", name)]


def _newest(runners: list["Runner"]) -> "Runner":
    return max(runners, key=lambda r: (_version_key(r.name), _natural_key(r.name)))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.casefold())


def discover_runners(steam_root: Path) -> list[Runner]:
    """Runners in compatibilitytools.d and under steamapps/common/Proton* of every library."""
    found: dict[str, Runner] = {}

    def add(p: Path) -> None:
        if (p / "proton").is_file():
            found.setdefault(p.name, Runner(p.name, p))

    tools = steam_root / "compatibilitytools.d"
    if tools.is_dir():
        for sub in tools.iterdir():
            add(sub)
    for lib in library_folders(steam_root):
        common = lib / "steamapps" / "common"
        if common.is_dir():
            for sub in common.iterdir():
                if sub.name.startswith("Proton"):
                    add(sub)
    return sorted(found.values(), key=lambda r: _natural_key(r.name))


def default_runner(steam_root: Path, app_id: int, runners: list[Runner]) -> Runner | None:
    """The runner Steam assigned to GW2 (or the global default); None without an assignment.

    No guessing: a "newest" runner that differs from GW2's would downgrade the prefix to its own version.
    """
    tool = compat_tool_name(steam_root, app_id)
    if not tool or not runners:
        return None
    for r in runners:  # custom tools: directory name = internal name
        if r.name == tool:
            return r
    n = _norm(tool)  # "proton_9" -> "Proton 9.0 (Beta)", "proton_experimental" -> "Proton - Experimental"
    matches = [r for r in runners if _norm(r.name).startswith(n)]
    return _newest(matches) if matches else None


def runner_from_config_info(compatdata: Path, runners: list[Runner]) -> Runner | None:
    """The runner that last ran the prefix: line 2 of config_info is a path inside that runner
    (".../Proton - Experimental/files/share/fonts/")."""
    try:
        lines = (compatdata / "config_info").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    if len(lines) < 2:
        return None
    path = lines[1]
    for r in sorted(runners, key=lambda r: len(str(r.path)), reverse=True):
        if path.startswith(str(r.path) + "/"):
            return r
    return None


def proton_env(steam_root: Path, compatdata: Path, app_id: int) -> dict[str, str]:
    return {
        "STEAM_COMPAT_DATA_PATH": str(compatdata),
        "STEAM_COMPAT_CLIENT_INSTALL_PATH": str(steam_root),
        "SteamAppId": str(app_id),
        "SteamGameId": str(app_id),
    }
