"""Wine settings specific to Blish HUD in the GW2 prefix.

Wine does not implement IAudioSessionManager2::UnregisterSessionNotification, which NAudio uses
(E_NOTIMPL -> NotImplementedException); Blish HUD crashes right after it finds GW2. Wine's per-application
settings (AppDefaults) disable the audio drivers for the "Blish HUD.exe" process only: Blish HUD sees no
audio device and the game's sound is unaffected. Since the loader starts Blish HUD from inside the game,
a per-process environment variable (WINEDLLOVERRIDES) cannot be used.
"""

from __future__ import annotations

from pathlib import Path

BLISH_EXE_NAME = "Blish HUD.exe"
AUDIO_DRIVERS = ("winepulse.drv", "winealsa.drv")
AUDIO_KEY = rf"HKCU\Software\Wine\AppDefaults\{BLISH_EXE_NAME}\DllOverrides"
# In user.reg keys are relative to HKCU and backslashes are doubled.
_AUDIO_SECTION = "[" + AUDIO_KEY.removeprefix("HKCU\\").replace("\\", "\\\\") + "]"


def audio_disabled(pfx: Path) -> bool:
    try:
        text = (pfx / "user.reg").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(_AUDIO_SECTION):
            body = []
            for nxt in lines[i + 1:]:
                if nxt.startswith("["):
                    break
                body.append(nxt.strip())
            return all(f'"{d}"="disabled"' in body for d in AUDIO_DRIVERS)
    return False


def audio_reg_commands(disable: bool) -> list[list[str]]:
    """reg.exe commands to run through `proton run`."""
    if disable:
        return [["reg", "add", AUDIO_KEY, "/v", d, "/d", "disabled", "/f"] for d in AUDIO_DRIVERS]
    return [["reg", "delete", AUDIO_KEY, "/f"]]
