# AsuraGate: development notes

How AsuraGate works under the hood, why it is built this way, and how to work on it. For using the program,
see the [User Guide](../USER_GUIDE.md).

## Approach

Blish HUD is **not** a separate transparent window; it is rendered inside the game
([external-dx11-overlay](https://github.com/SorryQuick/external-dx11-overlay)):

- `addons/LOADER_public/Gw2-Simple-Addon-Loader.exe` starts GW2 with the same arguments, injects
  `external_dx11_overlay.dll` into the game and runs the bundled (forked) Blish HUD; when the game exits it
  closes Blish HUD as well.
- Blish HUD's window stays hidden; its image is drawn onto every game frame through a shared texture. The dll
  handles mouse input. Fullscreen works; there are no window manager, panel or transparency issues.
- The Steam launch options replace `Gw2-64.exe` inside `%command%` with the loader. Everything runs with GW2's
  own Steam entry (AppID 1284210), prefix (`compatdata/1284210`) and Proton.

Why not a separate window: the WinAPI Blish HUD uses for transparency and click-through
(`WS_EX_LAYERED` / `WS_EX_TRANSPARENT`) is missing in Wine; KWin puts the focused fullscreen window above
everything, and Gamescope only draws the focused application.

The folder name `addons/LOADER_public` comes from the upstream package and cannot be changed: the dll has
`addons/LOADER_public/keybinds.conf` and `addons/LOADER_public/logs` built in.

## What `install` / `uninstall` do

- `install` downloads the pinned release (sha256 verified), installs it into the GW2 folder and records the
  installed files in `addons/LOADER_public/asuragate.json`; it never overwrites files it does not own.
  Running it again repairs/updates.
- `uninstall` only removes files it installed and that are unchanged, re-enables Blish HUD's audio in the
  prefix and restores the launch options (only if they are still AsuraGate's).
- Optional uninstall leftovers (`UNINSTALL_EXTRAS` in `core.py`): the overlay's `logs/` folder and
  `keybinds.conf` (the dll creates both while the game runs; they are not in the manifest) and the
  `localconfig.vdf.asuragate.bak` backups. They are removed only when asked: the UI shows a dialog with a
  tick box per existing leftover (all off by default; details as tooltips, text from `uninstall_extra_text`), kdialog/zenity
  show a checklist, the CLI takes `--remove-logs`, `--remove-keybinds`, `--remove-steam-backup`.
- `install` writes the Steam launch options in place into `userdata/*/config/localconfig.vdf` (only that value;
  a `.asuragate.bak` backup the first time). It does not write while Steam is running (Steam would
  overwrite them on exit); then the `launch-options` output can be pasted manually.
- `install` sets Blish HUD's frame limiter to "Match Monitor Refresh Rate": at the 60 FPS lock Blish HUD
  crashes when Pathing trails are enabled ([#7](https://github.com/SorryQuick/external-dx11-overlay/issues/7)).
  Disable with `blish_match_refresh=false`.
- Login (`steam_login`): false = ArenaNet account, the launch options get `-provider Portal`; true = Steam
  account, they get `USE_STEAM_LOGIN=1` (the loader then passes `-provider Steam`). The login setting owns
  `-provider`: any `-provider` in `game_args` is dropped (`without_provider`), and older configs that kept the
  login in `game_args` are migrated on load.

### Wine audio crash

Wine does not implement `IAudioSessionManager2::UnregisterSessionNotification`; Blish HUD crashes in NAudio as
soon as it finds GW2. `install` disables the audio drivers in the prefix for `Blish HUD.exe` only
(`HKCU\Software\Wine\AppDefaults\Blish HUD.exe\DllOverrides`, via `reg add` with GW2's Proton). The game's
sound is unaffected; Blish HUD's UI sounds don't play. To re-enable: `blish_audio=true` + `install`.

## Command line

Run from the `src` folder (or set `PYTHONPATH=src`):

```bash
python3 -m asuragate               # the UI (same as `gui`)
python3 -m asuragate doctor        # check the setup
python3 -m asuragate install       # install / update / repair
python3 -m asuragate uninstall [--remove-logs] [--remove-keybinds] [--remove-steam-backup]
python3 -m asuragate launch        # start GW2 through Steam
python3 -m asuragate launch-options
python3 -m asuragate config [--set KEY=VALUE ...]
python3 -m asuragate shortcut [--remove]   # desktop + application menu icon
```

Settings (`~/.config/asuragate/config.json`): `steam_root`, `app_id`, `game_args`, `steam_login`,
`blish_audio`, `blish_match_refresh`.

## UI tiers

With **PySide6** (part of the Bazzite image) or PyQt6 you get the windowed UI; otherwise simple dialogs via
**kdialog** (KDE, SteamOS desktop mode) / **zenity** (GNOME); with neither, a desktop notification
(`notify-send`) and a log file (`~/.local/state/asuragate/ui-error.log`) point to the command line.

## Launcher

`AsuraGate` in the project folder is a `.desktop` file without the extension (KDE detects it by content and
shows the name without `.desktop`). Its `Exec` finds its own folder through `%k`, so the folder can live
anywhere. It runs `python3 -m asuragate --register-menu` in `src/`, which adds/updates the application menu entry and
rewrites the launcher's `Icon=` to the absolute path of `src/asuragate/icon.svg` (the spec does not allow
relative icon paths; before the first run it shows `applications-games`).

Because of that rewrite, the launcher in your clone contains your home folder path after you run it. Keep
it out of commits (the committed version must keep `Icon=applications-games`):

```bash
git update-index --skip-worktree AsuraGate
```

## Paths on screen

Every path shown in the UI, the log or the CLI goes through `display_path()` (`config.py`): the home folder
becomes `~` and the Steam account number in `userdata/` becomes `<account>`, so screenshots and pasted
output don't reveal the user name or Steam account.

## Layout

```
AsuraGate            double-click launcher (finds its own folder; registers the menu entry, sets its icon)
src/asuragate/
  config.py          settings (~/.config/asuragate/config.json)
  steam.py           Steam/library/game folder/compatdata discovery, VDF parser and in-place editor
  proton.py          GW2's Proton (config_info / Steam assignment)
  overlay.py         package: pinned release, download, safe extraction, manifest, install/uninstall/status
  prefix.py          Wine setting specific to Blish HUD (audio)
  blish_settings.py  Blish HUD settings.json (frame limiter)
  launchopts.py      Steam launch options
  process.py         is GW2 / Steam running (/proc)
  desktop.py         desktop and application menu shortcut, launcher icon
  core.py            doctor / install / uninstall / launch
  cli.py             commands + tiered UI selection
  gui.py             Qt UI (PySide6 / PyQt6)
  dialogs.py         kdialog / zenity dialogs when Qt is missing
  icon.svg           application icon
```

## Updating the pinned package

`RELEASE` in `src/asuragate/overlay.py` pins the external-dx11-overlay release (tag, URL, sha256). To move to a
new release, update all three, run the tests, then `install` on a real system and check in game.

## Tests

The core uses only the standard library; tests use `unittest` (no pytest needed).

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v   # the GUI test runs if PySide6/PyQt6 is available
```

## Technical known issues

- The loader connects Blish HUD's stdout/stderr to a pipe it never reads; if Blish HUD crashes, look in
  `Documents/Guild Wars 2/addons/blishhud/logs/` (in the prefix) and `addons/LOADER_public/logs/`.
