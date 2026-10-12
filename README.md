# AsuraGate

*An Asura Gate from Linux to Blish HUD. Engineered with superior intellect, obviously.*

**Blish HUD for Guild Wars 2 on Linux, inside the game, like on Windows.**

AsuraGate sets up [Blish HUD](https://blishhud.com/) for the Steam version of Guild Wars 2 running with
Proton. Blish HUD is drawn inside the game (fullscreen works) using
[external-dx11-overlay](https://github.com/SorryQuick/external-dx11-overlay). Set it up once, then press
**Play** in Steam as usual.

> ⚠️ **Looking for a maintainer.** I built AsuraGate for myself and it works on my system, but I can't
> develop it further. If you'd like to take it over, fork it or open an issue.

*AsuraGate is an unofficial community tool. It is not affiliated with or endorsed by ArenaNet or the Blish HUD
team. Guild Wars 2 is a trademark of ArenaNet, LLC. Use it at your own risk.*

## Requirements

- Linux (tested on **Bazzite KDE**; other setups untested)
- Guild Wars 2 from **Steam**, started at least once
- Python 3.10+ (preinstalled on most systems); PySide6 for the full window (included in Bazzite)

## Quick start

1. Download and extract this folder anywhere.
2. Double-click **`AsuraGate`** in the folder (confirm KDE's "trust/execute" question the first time).
3. Close Steam and the game, then click **Install / Update**.
4. Press **Play** in Steam. Blish HUD appears in the game.

AsuraGate adds itself to the application menu on first start.

## How it works

AsuraGate doesn't implement the overlay itself. The work is done by SorryQuick's
[external-dx11-overlay](https://github.com/SorryQuick/external-dx11-overlay) project, whose release package
contains three parts:

- **[Gw2-Simple-Addon-Loader](https://github.com/SorryQuick/Gw2-Simple-Addon-Loader)** starts the game,
  injects the overlay dll into it and starts Blish HUD.
- **external_dx11_overlay.dll** is a DX11 render hook. Blish HUD's window stays hidden; its image is shared
  as a texture and drawn onto every game frame, and the dll passes mouse input to Blish HUD.
- **[A Blish HUD fork](https://github.com/SorryQuick/Blish-HUD)** that renders into that shared texture.

Upstream's [Simple User Guide](https://github.com/SorryQuick/external-dx11-overlay/blob/master/Simple-User-Guide.md)
describes the manual setup: extract the release into the GW2 folder, add the loader to Steam as a separate
non-Steam game, and choose a Proton version for it. **AsuraGate automates that setup** and does a few things
differently:

- **No separate Steam entry.** Instead of adding a non-Steam game, AsuraGate changes Guild Wars 2's own
  Steam launch options so that Steam starts the loader in place of `Gw2-64.exe`. The loader then runs with
  the game's own Proton version and prefix.
- **Pinned and verified download.** It downloads one fixed upstream release, checks its sha256 checksum
  and installs it into `addons/LOADER_public/` in the GW2 folder. It records every file it installs, so it
  can update, repair and uninstall cleanly without touching files it doesn't own.
- **Wine fixes.** Blish HUD crashes under Wine when it uses audio, so AsuraGate turns off the audio drivers
  for `Blish HUD.exe` only. It also sets Blish HUD's frame limiter to "Match Monitor Refresh Rate",
  which avoids a crash with Pathing trails
  ([#7](https://github.com/SorryQuick/external-dx11-overlay/issues/7)).
- **Check.** It diagnoses the whole setup (Steam, game folder, prefix, Proton, package, launch options) and
  says what to fix.

The technical details are in the [development notes](docs/DEVELOPMENT.md).

## Credits

- [external-dx11-overlay](https://github.com/SorryQuick/external-dx11-overlay) and
  [Gw2-Simple-Addon-Loader](https://github.com/SorryQuick/Gw2-Simple-Addon-Loader) by **SorryQuick**
  (Apache-2.0)
- The [Blish HUD fork](https://github.com/SorryQuick/Blish-HUD) by **SorryQuick**, based on
  [Blish HUD](https://github.com/blish-hud/Blish-HUD) by the **Blish HUD team** (MIT)
- The window uses [PySide6](https://doc.qt.io/qtforpython-6/) (Qt for Python, LGPL-3.0) or PyQt6 if one is
  installed on the system; AsuraGate does not ship either.

AsuraGate doesn't include any of their code. It downloads the official external-dx11-overlay release from
GitHub when you click Install.

## Documentation

- 📖 **[User Guide](USER_GUIDE.md)**: installation, settings, troubleshooting, uninstalling, FAQ
- 🛠️ **[Development notes](docs/DEVELOPMENT.md)**: how it works, command line, code layout, tests

## Known issues

- Keys can get stuck or react late while Blish HUD is active, especially after alt-tab
  ([#34](https://github.com/SorryQuick/external-dx11-overlay/issues/34),
  [#22](https://github.com/SorryQuick/external-dx11-overlay/issues/22)).
- Low overlay frame rate on KDE Wayland has been reported
  ([#19](https://github.com/SorryQuick/external-dx11-overlay/issues/19)); not seen on the test system.
- Blish HUD's own sounds are turned off (they crash it under Wine). The game's sound is not affected.

## License

Copyright 2026 bofnald. AsuraGate is licensed under the [Apache License 2.0](LICENSE). If you redistribute
AsuraGate or a work based on it, keep the [LICENSE](LICENSE) and [NOTICE](NOTICE) files (Apache-2.0,
section 4).

The upstream projects listed under [Credits](#credits) have their own licenses. AsuraGate does not include or
redistribute any of their files: Install downloads the official release from its GitHub page onto your own
computer.
