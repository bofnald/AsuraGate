"""UI without Qt: simple dialogs through the system's dialog tool (KDE: kdialog, GNOME: zenity).

The same actions (Install / Play / Check / Uninstall) are picked from a list; the program installs nothing
for itself. SteamOS, for example, has no PySide6, but its desktop mode is KDE, so kdialog is there.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable

from .config import Config, display_path, state_dir, without_provider
from .core import UNINSTALL_SUMMARY, Orchestrator, OrchestratorError, uninstall_extra_text

TITLE = "AsuraGate"
ACTIONS = [
    ("launch", "Play"),
    ("install", "Install / Update"),
    ("doctor", "Check"),
    ("settings", "Settings"),
    ("uninstall", "Uninstall"),
]
_MARK = {"ok": "✔", "warn": "⚠", "error": "✘"}


class Dialogs:
    """kdialog / zenity calls. `run` can be injected (tests)."""

    def __init__(self, tool: str, run=subprocess.run):
        self.tool = tool
        self._run = run

    def _call(self, args: list[str]) -> tuple[int, str]:
        r = self._run([self.tool, *args], capture_output=True, text=True)
        return r.returncode, (r.stdout or "").strip()

    def _text(self, text: str) -> str:
        # zenity --text uses Pango markup (& < > must be escaped); kdialog shows plain text.
        if self.tool == "kdialog":
            return text
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def menu(self, text: str) -> str | None:
        items = [x for key, label in ACTIONS for x in (key, label)]
        if self.tool == "kdialog":
            code, out = self._call(["--title", TITLE, "--menu", text, *items])
        else:
            code, out = self._call(["--list", "--title", TITLE, "--text", self._text(text), "--column", "id",
                                    "--column", "Action", "--hide-column", "1", "--print-column", "1",
                                    "--height", "320", *items])
        return out if code == 0 and out else None

    def info(self, text: str) -> None:
        self._call(["--title", TITLE, "--msgbox", text] if self.tool == "kdialog"
                   else ["--info", "--title", TITLE, "--no-wrap", "--text", self._text(text)])

    def error(self, text: str) -> None:
        self._call(["--title", TITLE, "--error", text] if self.tool == "kdialog"
                   else ["--error", "--title", TITLE, "--no-wrap", "--text", self._text(text)])

    def ask(self, text: str, default: str) -> str | None:
        code, out = self._call(["--title", TITLE, "--inputbox", text, default] if self.tool == "kdialog"
                               else ["--entry", "--title", TITLE, "--text", self._text(text), "--entry-text", default])
        return out if code == 0 else None

    def yes_no(self, text: str) -> bool:
        code, _ = self._call(["--title", TITLE, "--yesno", text] if self.tool == "kdialog"
                             else ["--question", "--title", TITLE, "--no-wrap", "--text", self._text(text)])
        return code == 0

    def checklist(self, text: str, items: list[tuple[str, str, bool]]) -> list[str] | None:
        """Check boxes (key, label, checked); returns the checked keys, or None if cancelled."""
        if self.tool == "kdialog":
            args = ["--title", TITLE, "--separate-output", "--checklist", text]
            for key, label, on in items:
                args += [key, label, "on" if on else "off"]
        else:
            args = ["--list", "--checklist", "--title", TITLE, "--text", self._text(text), "--column", "",
                    "--column", "id", "--column", "Remove", "--hide-column", "2", "--print-column", "2",
                    "--separator", "\n", "--width", "720", "--height", "560"]
            for key, label, on in items:
                args += ["TRUE" if on else "FALSE", key, label]
        code, out = self._call(args)
        if code != 0:
            return None
        return [line.strip().strip('"') for line in out.splitlines() if line.strip()]

    def busy(self, text: str) -> None:
        """Non-blocking notice when a long action starts (kdialog: corner popup)."""
        if self.tool == "kdialog":
            self._call(["--title", TITLE, "--passivepopup", text, "5"])


def find_tool(environ: dict[str, str], which=shutil.which) -> str | None:
    desktop = environ.get("XDG_CURRENT_DESKTOP", "").upper()
    order = ["zenity", "kdialog"] if "GNOME" in desktop else ["kdialog", "zenity"]
    return next((t for t in order if which(t)), None)


NO_UI = ("No UI available (no PySide6/PyQt6 and no kdialog/zenity). Use the command line:\n"
         "  python3 -m asuragate install | launch | doctor | uninstall")


def report_no_ui(environ: dict[str, str], run=subprocess.run, which=shutil.which) -> Path | None:
    """With no UI at all: started from the desktop icon there is no terminal to show the message, so a
    system notification (notify-send, available on nearly every desktop) is shown and a log file written."""
    log = None
    try:
        log = state_dir() / "ui-error.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + NO_UI + "\n")
    except OSError:
        log = None
    if which("notify-send"):
        body = NO_UI + (f"\nDetails: {display_path(log)}" if log else "")
        try:
            run(["notify-send", "--app-name", TITLE, "--icon", "dialog-error", TITLE, body],
                env=environ, capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass
    return log


def run_dialogs(cfg: Config, config_file: str | None, dialogs: Dialogs,
                make_orch: Callable[[Config, Callable[[str], None]], Orchestrator] = Orchestrator) -> int:
    while True:
        if config_file is None or Path(config_file).is_file():
            cfg = Config.load(config_file)  # may have changed in Settings
        choice = dialogs.menu("What would you like to do?")
        if choice is None:
            return 0
        logs: list[str] = []
        orch = make_orch(cfg, logs.append)
        if choice == "doctor":
            checks = orch.diagnose()
            text = "\n".join(f"{_MARK[c.level]} {c.message}" for c in checks)
            (dialogs.error if any(c.level == "error" for c in checks) else dialogs.info)(text)
            continue
        if choice == "settings":
            args = dialogs.ask("Extra GW2 arguments (usually empty; the login is asked next):", cfg.game_args)
            if args is not None:
                cfg.game_args = without_provider(args)
                cfg.steam_login = dialogs.yes_no("Do you log in with a Steam account?\n\n"
                                                 "Yes: your GW2 account is linked to Steam.\n"
                                                 "No: you log in with an ArenaNet account (e-mail and password).")
                cfg.save(config_file)
                dialogs.info("Saved. Run “Install / Update” to update the Steam launch options.")
            continue
        kwargs = {}
        if choice == "uninstall":
            if not dialogs.yes_no(UNINSTALL_SUMMARY + "\n\nContinue?"):
                continue
            extras = orch.uninstall_extras()
            if extras:
                texts = {key: uninstall_extra_text(key, paths, show_shortcuts=False) for key, paths in extras.items()}
                explained = "\n\n".join(f"• {label}\n{details}" for label, details in texts.values())
                remove = dialogs.checklist("Also remove these leftovers? Nothing is removed unless you tick it.\n\n"
                                           + explained,
                                           [(key, label, False) for key, (label, _) in texts.items()])
                if remove is None:
                    continue
                kwargs["remove"] = remove
        if choice == "install":
            dialogs.busy("Installing… (the package is downloaded the first time)")
        try:
            getattr(orch, choice)(**kwargs)
        except OrchestratorError as e:
            dialogs.error(str(e))
            continue
        if choice == "launch":
            return 0
        dialogs.info("\n".join(logs) or "Done.")
