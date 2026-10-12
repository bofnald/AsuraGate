"""The Steam > GW2 > Properties > Launch Options line.

Steam's %command% (reaper, Steam Linux Runtime, proton waitforexitandrun .../Gw2-64.exe) is kept as is;
only the argument ending in Gw2-64.exe is replaced with the loader. The loader therefore runs with GW2's
own AppID, prefix, Proton and container, and starts GW2 itself with the same arguments.
"""

from __future__ import annotations

from .config import Config, without_provider
from .overlay import LOADER_EXE

GW2_EXE = "Gw2-64.exe"
PORTAL = "-provider Portal"  # ArenaNet account login in the Steam version


def game_arguments(cfg: Config) -> str:
    """GW2's arguments: the login (ArenaNet: -provider Portal; Steam: none, the loader adds it) + the extras."""
    parts = [] if cfg.steam_login else [PORTAL]
    extra = without_provider(cfg.game_args)
    if extra:
        parts.append(extra)
    return " ".join(parts)


def build_launch_options(cfg: Config) -> str:
    # ${@/%pattern/new}: replaces a trailing "Gw2-64.exe" in every argument.
    swap = f"bash -c 'exec \"${{@/%{GW2_EXE}/{LOADER_EXE}}}\"' --"
    parts = (["USE_STEAM_LOGIN=1"] if cfg.steam_login else []) + [swap, "%command%"]
    args = game_arguments(cfg)
    if args:
        parts.append(args)
    return " ".join(parts)
