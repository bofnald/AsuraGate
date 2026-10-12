"""Process lookup via /proc."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator


def iter_processes(proc_root: Path = Path("/proc")) -> Iterator[tuple[int, list[str]]]:
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        yield int(entry.name), [a.decode(errors="replace") for a in raw.split(b"\0") if a]


def _basename(arg: str) -> str:
    return arg.replace("\\", "/").rsplit("/", 1)[-1].casefold()


def _is_exe(arg: str, needle: str) -> bool:
    # Wine sometimes puts the whole Windows command line into argv[0]: "Z:\\...\\Gw2-64.exe -provider Steam"
    base = _basename(arg)
    return base == needle or base.startswith(needle + " ")


def find_process(exe_name: str, proc_root: Path = Path("/proc")) -> int | None:
    """Finds the Wine process itself (argv[0] = ...\\Gw2-64.exe); wrappers that merely mention the exe on
    their command line (reaper, proton, the loader) do not count."""
    needle = exe_name.casefold()
    me = os.getpid()
    for pid, argv in iter_processes(proc_root):
        if pid != me and argv and _is_exe(argv[0], needle):
            return pid
    return None


def steam_running(proc_root: Path = Path("/proc")) -> bool:
    """Whether the Steam client (…/ubuntu12_32/steam) is running."""
    return any(argv and _basename(argv[0]) == "steam" for _pid, argv in iter_processes(proc_root))
