"""Command line."""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict, fields

from .config import Config, config_path, display_path
from .core import UNINSTALL_EXTRAS, Orchestrator, OrchestratorError
from .launchopts import build_launch_options

_MARK = {"ok": "[OK]    ", "warn": "[WARN]  ", "error": "[ERROR] "}


def _coerce(cfg: Config, key: str, raw: str):
    names = {f.name for f in fields(Config)}
    if key not in names:
        raise SystemExit(f"Unknown setting: {key} (valid: {', '.join(sorted(names))})")
    cur = getattr(cfg, key)
    if isinstance(cur, bool):
        return raw.strip().casefold() in ("1", "true", "yes", "on")
    if isinstance(cur, int):
        try:
            return int(raw)
        except ValueError:
            raise SystemExit(f"{key} must be an integer: {raw!r}")
    return raw


def _run_ui(cfg: Config, config_file: str | None) -> int:
    """Tiered UI: Qt (PySide6/PyQt6) → kdialog/zenity → notification + log + command line hint."""
    try:
        from .gui import run_gui
    except ImportError:
        pass
    else:
        return run_gui(cfg, config_file)
    import os

    from .dialogs import NO_UI, Dialogs, find_tool, report_no_ui, run_dialogs

    tool = find_tool(dict(os.environ))
    if tool is not None:
        return run_dialogs(cfg, config_file, Dialogs(tool))
    print(NO_UI, file=sys.stderr)  # when started from a terminal
    report_no_ui(dict(os.environ))  # when started from the icon: notification + log
    return 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="asuragate", description="AsuraGate: one-click Blish HUD for Guild Wars 2 on Linux (rendered in-game)")
    ap.add_argument("--config", help="config file (default: %s)" % display_path(config_path()))
    # Set by AsuraGate.desktop in the project folder: also add/update the application menu entry.
    ap.add_argument("--register-menu", action="store_true", help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("gui", help="open the UI (default)")
    sub.add_parser("doctor", help="check the setup")
    sub.add_parser("install", help="install / update / repair the Blish HUD in-game package")
    un = sub.add_parser("uninstall", help="remove the package, the audio setting and the launch options")
    un.add_argument("--remove-logs", action="store_true", help="also remove the overlay's log files")
    un.add_argument("--remove-keybinds", action="store_true",
                    help="also remove the overlay's own shortcut file (not the game's key bindings)")
    un.add_argument("--remove-steam-backup", action="store_true",
                    help="also remove the backup of Steam's localconfig.vdf")
    sub.add_parser("launch", help="start GW2 through Steam")
    sub.add_parser("launch-options", help="print the GW2 launch options to paste into Steam")
    sc = sub.add_parser("shortcut", help="add a desktop and application menu shortcut")
    sc.add_argument("--remove", action="store_true", help="remove the shortcut")
    c = sub.add_parser("config", help="show / change settings")
    c.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    args = ap.parse_args(argv)
    if args.register_menu:
        from .desktop import register_menu

        register_menu()

    cfg = Config.load(args.config)
    cmd = args.cmd or "gui"

    if cmd == "gui":
        return _run_ui(cfg, args.config)

    if cmd == "config":
        for item in args.set:
            key, sep, raw = item.partition("=")
            if not sep:
                raise SystemExit(f"expected KEY=VALUE: {item!r}")
            setattr(cfg, key, _coerce(cfg, key, raw))
        if args.set:
            print(f"Saved: {display_path(cfg.save(args.config))}")
        for k, v in asdict(cfg).items():
            print(f"{k} = {v!r}")
        return 0

    if cmd == "launch-options":
        print(build_launch_options(cfg))
        return 0

    if cmd == "shortcut":
        from .desktop import install_shortcut, remove_shortcut

        if args.remove:
            removed = remove_shortcut()
            print("Removed: " + ", ".join(map(display_path, removed)) if removed else "No shortcut found.")
        else:
            print("Shortcut added: " + ", ".join(map(display_path, install_shortcut())))
        return 0

    orch = Orchestrator(cfg, log=lambda m: print(m, flush=True))
    if cmd == "doctor":
        checks = orch.diagnose()
        for ch in checks:
            print(_MARK[ch.level] + ch.message)
        return 1 if any(ch.level == "error" for ch in checks) else 0
    try:
        if cmd == "uninstall":
            orch.uninstall(remove=[key for key in UNINSTALL_EXTRAS if getattr(args, "remove_" + key)])
        else:
            {"install": orch.install, "launch": orch.launch}[cmd]()
    except OrchestratorError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
