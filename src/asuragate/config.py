"""User configuration (JSON, in the XDG directories)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path

GW2_APP_ID = 1284210
APP_DIR = "asuragate"


def display_path(path: Path | str, home: Path | None = None) -> str:
    """A path as shown on screen and in logs: the home folder as ~ and the Steam account number in
    userdata/ hidden, so screenshots and pasted logs don't reveal the user name or the Steam account."""
    text = str(path)
    home = home or Path.home()
    for h in sorted({str(home), str(home.resolve())}, key=len, reverse=True):  # Bazzite: /home -> /var/home
        h = h.rstrip("/")
        if h and (text == h or text.startswith(h + "/")):
            text = "~" + text[len(h):]
            break
    return re.sub(r"(^|/)userdata/\d+(?=/|$)", r"\1userdata/<account>", text)


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_DIR


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / APP_DIR


def config_path() -> Path:
    return config_dir() / "config.json"


def without_provider(args: str) -> str:
    """GW2 arguments without "-provider <x>": the login setting (steam_login) decides the login provider."""
    tokens = args.split()
    if not any(t.casefold() == "-provider" for t in tokens):
        return args.strip()
    out, i = [], 0
    while i < len(tokens):
        if tokens[i].casefold() == "-provider":
            i += 2
            continue
        out.append(tokens[i])
        i += 1
    return " ".join(out)


@dataclass
class Config:
    steam_root: str = ""  # empty = detect automatically
    app_id: int = GW2_APP_ID
    game_args: str = ""  # extra GW2 arguments (appended to the Steam launch options); never "-provider"
    # The login: False = ArenaNet account ("-provider Portal"), True = Steam account (the loader gets
    # USE_STEAM_LOGIN=1 and passes "-provider Steam").
    steam_login: bool = False
    # Wine does not implement IAudioSessionManager2::UnregisterSessionNotification, which NAudio calls;
    # Blish HUD crashes as soon as it finds GW2. When false, the audio drivers are disabled in the prefix
    # for "Blish HUD.exe" only (Wine AppDefaults). GW2's own sound is not affected.
    blish_audio: bool = False
    # Set Blish HUD's frame limiter to "Match Monitor Refresh Rate" (Pathing crashes at the 60 FPS lock).
    blish_match_refresh: bool = True

    @classmethod
    def load(cls, path: Path | str | None = None) -> "Config":
        p = Path(path) if path else config_path()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        defaults = cls()
        kwargs = {}
        for f in fields(cls):
            if f.name in data and isinstance(data[f.name], type(getattr(defaults, f.name))):
                kwargs[f.name] = data[f.name]
        # Older versions kept the login in game_args ("-provider Portal" / "-provider Steam").
        args = kwargs.get("game_args", "")
        if "steam_login" not in kwargs and "-provider steam" in " ".join(args.casefold().split()):
            kwargs["steam_login"] = True
        if args:
            kwargs["game_args"] = without_provider(args)
        return cls(**kwargs)

    def save(self, path: Path | str | None = None) -> Path:
        p = Path(path) if path else config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, p)
        return p
