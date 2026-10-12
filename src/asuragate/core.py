"""Install, check and launch.

Flow: `install` puts the package into the GW2 folder, applies Blish HUD's settings in the prefix and writes
the Steam launch options (while Steam is closed). After that GW2 starts from Steam's "Play" button: Steam
runs the loader with GW2's own prefix/Proton, and the loader starts GW2 and Blish HUD.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Collection

from . import blish_settings, overlay, prefix
from .config import Config, display_path, state_dir
from .launchopts import GW2_EXE, build_launch_options, game_arguments
from .process import find_process, steam_running
from .proton import (
    Runner,
    default_runner,
    discover_runners,
    proton_env,
    runner_from_config_info,
)
from .steam import (
    find_app_dir,
    find_compatdata,
    find_steam_root,
    is_steam_root,
    launch_options_backups,
    prefix_path,
    read_launch_options,
    write_launch_options,
)


# Optional uninstall leftovers: the overlay's log folder and shortcut file (it creates them while the game
# runs) and the backup of Steam's localconfig.vdf.
UNINSTALL_EXTRAS = ("logs", "keybinds", "steam_backup")

UNINSTALL_SUMMARY = (
    "Uninstall removes Blish HUD from Guild Wars 2:\n"
    "  • the add-on files AsuraGate installed (addons/LOADER_public)\n"
    "  • AsuraGate's Steam launch options (your own GW2 arguments are kept)\n"
    "  • the Blish HUD sound setting in the game's Wine prefix\n"
    "\n"
    "Not touched: the game itself, your Guild Wars 2 settings and key bindings, and Blish HUD's own "
    "settings and modules (a reinstall picks them up again)."
)


def uninstall_extra_text(key: str, paths: list[Path], show_shortcuts: bool = True) -> tuple[str, str]:
    """(label, details) of an optional uninstall leftover, for the uninstall dialogs; with show_shortcuts the
    keybinds details list the shortcuts currently in the file."""
    if key == "logs":
        return (f"Overlay log files ({len(paths)} files)",
                "Text files the overlay writes every time the game runs, in addons/LOADER_public/logs/.\n"
                "They are only useful for troubleshooting. Removing them changes nothing else.")
    if key == "keybinds":
        lines = []
        try:
            if show_shortcuts:
                lines = [ln.strip() for ln in paths[0].read_text(encoding="utf-8", errors="replace").splitlines()
                         if ln.strip()][:12]
        except OSError:
            pass
        current = ("\n\nShortcuts in the file now:\n" + "\n".join("  " + ln for ln in lines)) if lines else ""
        return ("Overlay keyboard shortcuts (keybinds.conf)",
                "This file holds the overlay's OWN shortcuts, for example Ctrl+Alt+O (restart Blish HUD) and "
                "Ctrl+Alt+B (show / hide Blish HUD).\n\n"
                "They have NOTHING to do with Guild Wars 2's key bindings (movement, skills, interface). "
                "The game keeps its controls in its own settings, and uninstall never touches them.\n\n"
                "Keep this file only if you changed these overlay shortcuts and want them back after "
                "reinstalling. Otherwise the overlay creates it again with the defaults." + current)
    if key == "steam_backup":
        return ("Backup of Steam's settings file",
                "The first time AsuraGate changed GW2's launch options, it saved a copy of Steam's settings "
                "file (localconfig.vdf.asuragate.bak, in Steam's userdata folder).\n\n"
                "Uninstall restores the launch options by itself, so this copy is no longer needed. It is an "
                "old copy: don't put it back by hand, it would undo Steam settings changed since then.")
    raise ValueError(key)


class OrchestratorError(Exception):
    pass


@dataclass(frozen=True)
class Check:
    level: str  # "ok" | "warn" | "error"
    message: str


class Orchestrator:
    def __init__(
        self,
        cfg: Config,
        log: Callable[[str], None] | None = None,
        *,
        run=subprocess.run,
        popen=subprocess.Popen,
        which=shutil.which,
        opener=urllib.request.urlopen,
        proc_root: Path = Path("/proc"),
        cache_dir: Path | None = None,
        environ: dict[str, str] | None = None,
    ):
        self.cfg = cfg
        self.log = log or (lambda msg: None)
        self._run = run
        self._popen = popen
        self._which = which
        self._opener = opener
        self._proc_root = proc_root
        self._cache_dir = cache_dir or state_dir() / "downloads"
        self._environ = dict(os.environ if environ is None else environ)

    # ---------- discovery ----------
    def steam_root(self) -> Path | None:
        if self.cfg.steam_root.strip():
            p = Path(self.cfg.steam_root).expanduser()
            return p if is_steam_root(p) else None
        return find_steam_root()

    def game_dir(self) -> Path | None:
        root = self.steam_root()
        d = find_app_dir(root, self.cfg.app_id) if root else None
        return d if d and (d / GW2_EXE).is_file() else None

    def compatdata(self) -> Path | None:
        root = self.steam_root()
        return find_compatdata(root, self.cfg.app_id) if root else None

    def gw2_runner(self) -> Runner | None:
        """GW2's Proton: the one that last ran the prefix (config_info), else Steam's assignment."""
        root, compat = self.steam_root(), self.compatdata()
        if root is None:
            return None
        runners = discover_runners(root)
        r = runner_from_config_info(compat, runners) if compat else None
        return r or default_runner(root, self.cfg.app_id, runners)

    def gw2_running(self) -> bool:
        return find_process(GW2_EXE, self._proc_root) is not None

    # ---------- checks ----------
    def diagnose(self) -> list[Check]:
        out: list[Check] = []

        def add(level, msg):
            out.append(Check(level, msg))

        root = self.steam_root()
        if root is None:
            add("error", self._steam_root_problem())
            return out
        add("ok", f"Steam: {display_path(root)}")

        game = self.game_dir()
        if game is None:
            add("error", f"GW2 folder not found (appmanifest_{self.cfg.app_id}.acf / {GW2_EXE}).")
        else:
            add("ok", f"GW2: {display_path(game)}")

        compat = self.compatdata()
        pfx = prefix_path(compat) if compat else None
        if pfx is None or not pfx.is_dir():
            add("error", f"GW2 prefix missing (compatdata/{self.cfg.app_id}/pfx): start GW2 from Steam once.")
            pfx = None
        else:
            add("ok", f"GW2 prefix: {display_path(pfx)}")
            r = self.gw2_runner()
            if r is None:
                add("warn", "Could not determine GW2's Proton version; start GW2 once (needed for the audio setting).")
            else:
                add("ok", f"GW2 Proton: {r.name}")

        if game is not None:
            self._diagnose_overlay(game, add)
        if pfx is not None:
            self._diagnose_audio(pfx, add)
            self._diagnose_blish_settings(pfx, add)
        self._diagnose_launch_options(root, add)

        if self.gw2_running():
            add("warn", "GW2 is running: close it before install / uninstall.")
        return out

    def _steam_root_problem(self) -> str:
        if not self.cfg.steam_root.strip():
            return "Steam directory not found: choose Steam's main folder in the settings (Steam directory)."
        p = Path(self.cfg.steam_root).expanduser()
        if (p / "steamapps").is_dir():
            return (f"The Steam directory in the settings ({display_path(p)}) looks like a game library, not "
                    "Steam's main folder: choose the folder that also contains config and userdata.")
        return (f"The Steam directory in the settings ({display_path(p)}) is not a Steam folder "
                "(it has no steamapps folder).")

    def _diagnose_overlay(self, game: Path, add) -> None:
        st = overlay.status(game)
        if not st.installed:
            add("error", "Blish HUD in-game package is not installed: run `install`.")
        elif st.missing or st.modified:
            add("error", "Package damaged (missing: %s; modified: %s): repair with `install`."
                % (", ".join(st.missing) or "-", ", ".join(st.modified) or "-"))
        elif st.tag != overlay.RELEASE.tag:
            add("warn", f"Package version {st.tag}, latest {overlay.RELEASE.tag}: update with `install`.")
        else:
            add("ok", f"Blish HUD in-game package installed ({st.tag}).")

    def _diagnose_audio(self, pfx: Path, add) -> None:
        disabled = prefix.audio_disabled(pfx)
        if not self.cfg.blish_audio and not disabled:
            add("warn", "Blish HUD's audio drivers are not disabled: under Wine it crashes once it finds GW2. `install` sets this.")
        elif self.cfg.blish_audio and disabled:
            add("warn", "blish_audio=true but Blish HUD's audio is still disabled in the prefix: `install` re-enables it.")
        elif disabled:
            add("ok", "Blish HUD's audio drivers are disabled (avoids the Wine audio crash).")

    def _diagnose_blish_settings(self, pfx: Path, add) -> None:
        if not self.cfg.blish_match_refresh:
            return
        value = blish_settings.frame_limiter(pfx)
        if value == blish_settings.MATCH_MONITOR:
            add("ok", "Blish HUD frame limiter: Match Monitor Refresh Rate.")
        elif value is None and not blish_settings.settings_path(pfx).is_file():
            add("warn", "Blish HUD has never run yet; after the first game session `install` sets the frame limiter.")
        else:
            add("warn", "Blish HUD frame limiter is not Match Monitor Refresh Rate (Pathing may crash): `install` sets it.")

    def _diagnose_launch_options(self, root: Path, add) -> None:
        want = build_launch_options(self.cfg)
        opts = [o.strip() for o in read_launch_options(root, self.cfg.app_id)]
        if want in opts:
            add("ok", "Steam launch options run the loader.")
        elif any(str(overlay.LOADER_EXE) in o for o in opts):
            add("warn", f"Steam launch options differ from the expected value. Expected:  {want}")
        else:
            current = f" (currently: {opts[0]!r})" if opts else ""
            add("error", f"Steam launch options do not run the loader{current}. `install` writes them while Steam "
                         f"is closed; or set Steam > GW2 > Properties > Launch Options:  {want}")

    # ---------- install ----------
    def _require(self) -> tuple[Path, Path, Path]:
        root, game, compat = self.steam_root(), self.game_dir(), self.compatdata()
        if root is None or game is None:
            raise OrchestratorError("Steam or GW2 folder not found (see `doctor`).")
        if compat is None or not prefix_path(compat).is_dir():
            raise OrchestratorError("GW2 prefix missing: start GW2 from Steam once.")
        if self.gw2_running():
            raise OrchestratorError("GW2 is running: close the game first.")
        return root, game, compat

    def install(self) -> None:
        root, game, compat = self._require()
        rel = overlay.RELEASE
        self.log(f"Preparing package: {rel.tag} ({rel.url})")
        try:
            zip_path = overlay.download(rel, self._cache_dir, self._opener)
            written = overlay.install(game, zip_path, rel)
        except overlay.OverlayError as e:
            raise OrchestratorError(str(e)) from e
        self.log(f"{len(written)} files written: {display_path(game / overlay.LOADER_DIR)}" if written else "Package already up to date.")
        self._set_audio(root, compat, disable=not self.cfg.blish_audio)
        if self.cfg.blish_match_refresh and blish_settings.set_frame_limiter(
                prefix_path(compat), blish_settings.MATCH_MONITOR):
            self.log("Blish HUD frame limiter: Match Monitor Refresh Rate.")
        self._write_launch_options(root, build_launch_options(self.cfg))

    def uninstall_extras(self) -> dict[str, list[Path]]:
        """The optional leftovers (see UNINSTALL_EXTRAS) that exist now; the user picks which to remove."""
        root, game = self.steam_root(), self.game_dir()
        found = {
            "logs": overlay.log_files(game) if game else [],
            "keybinds": [p for p in [overlay.keybinds_file(game) if game else None] if p],
            "steam_backup": launch_options_backups(root) if root else [],
        }
        return {k: v for k, v in found.items() if v}

    def uninstall(self, remove: Collection[str] = ()) -> None:
        """Removes the package, the audio setting and the launch options; `remove` adds UNINSTALL_EXTRAS."""
        unknown = set(remove) - set(UNINSTALL_EXTRAS)
        if unknown:
            raise ValueError(f"unknown uninstall extras: {sorted(unknown)}")
        root, game, compat = self._require()
        kept = overlay.uninstall(game)
        if kept:
            self.log("Files kept because they were modified: " + ", ".join(kept))
        self._set_audio(root, compat, disable=False)
        args = game_arguments(self.cfg)
        self.log("Package removed.")
        if build_launch_options(self.cfg) in [o.strip() for o in read_launch_options(root, self.cfg.app_id)]:
            self._write_launch_options(root, f"%command% {args}" if args else "")
        removed = overlay.remove_runtime_files(game, logs="logs" in remove, keybinds="keybinds" in remove)
        if "steam_backup" in remove:
            for b in launch_options_backups(root):
                b.unlink()
                removed.append(display_path(b))
        if removed:
            self.log("Also removed: " + ", ".join(removed))

    def _write_launch_options(self, root: Path, value: str) -> None:
        manual = "Steam > GW2 > Properties > Launch Options:  " + (value or "(empty)")
        if value in [o.strip() for o in read_launch_options(root, self.cfg.app_id)]:
            return
        if steam_running(self._proc_root):
            self.log("Steam is running: launch options not written (Steam would overwrite them on exit). "
                     "Close Steam and run again, or paste them manually. " + manual)
            return
        changed = write_launch_options(root, self.cfg.app_id, value)
        if changed:
            self.log("Steam launch options written: " + ", ".join(display_path(c) for c in changed))
        else:
            self.log("No GW2 entry found in Steam's config; paste manually. " + manual)

    def _set_audio(self, root: Path, compat: Path, disable: bool) -> None:
        pfx = prefix_path(compat)
        if prefix.audio_disabled(pfx) == disable:
            return
        runner = self.gw2_runner()
        if runner is None:
            raise OrchestratorError("Could not determine GW2's Proton version: start GW2 once and try again.")
        env = dict(self._environ)
        env.update(proton_env(root, compat, self.cfg.app_id))
        for cmd in prefix.audio_reg_commands(disable):
            r = self._run([str(runner.proton), "run", *cmd], env=env, capture_output=True, text=True)
            if r.returncode != 0:
                raise OrchestratorError(f"{' '.join(cmd)} failed: {(r.stderr or '').strip()[-300:]}")
        if prefix.audio_disabled(pfx) != disable:
            raise OrchestratorError("Wine audio setting was written but does not show up in user.reg.")
        self.log("Blish HUD's audio drivers " + ("disabled." if disable else "re-enabled."))

    # ---------- launch ----------
    def steam_command(self) -> list[str] | None:
        aid = str(self.cfg.app_id)
        root = self.steam_root()
        if root and "com.valvesoftware.Steam" in str(root) and self._which("flatpak"):
            return ["flatpak", "run", "com.valvesoftware.Steam", "-applaunch", aid]
        if self._which("steam"):
            return ["steam", "-applaunch", aid]
        if self._which("xdg-open"):
            return ["xdg-open", f"steam://rungameid/{aid}"]
        return None

    def launch(self) -> None:
        errors = [c.message for c in self.diagnose() if c.level == "error"]
        if errors:
            raise OrchestratorError("; ".join(errors))
        cmd = self.steam_command()
        if cmd is None:
            raise OrchestratorError("Cannot start Steam (no steam / xdg-open).")
        self.log("Starting GW2: " + " ".join(cmd))
        self._popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
