"""Core tests: no real Steam/Proton/Wine needed, they use a fake directory tree.

Run:  PYTHONPATH=src python3 -m unittest discover -s tests -v
"""

import hashlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from asuragate import blish_settings, overlay, prefix
from asuragate.config import Config
from asuragate.core import Orchestrator, OrchestratorError, uninstall_extra_text
from asuragate.launchopts import build_launch_options
from asuragate.process import find_process, steam_running
from asuragate.proton import default_runner, discover_runners, runner_from_config_info
from asuragate.steam import (
    compat_tool_name,
    find_app_dir,
    find_compatdata,
    library_folders,
    parse_vdf,
    read_launch_options,
    set_vdf_value,
    write_launch_options,
)


def touch(p: Path, text: str = "") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


class FakeSteam:
    """A minimal Steam install under tmp: GW2 and its prefix live in the second library."""

    def __init__(self, tmp: Path, launch_options: str = "gamescope -f -- %command%"):
        self.root = tmp / "Steam"
        self.lib2 = tmp / "games" / "SteamLibrary"
        (self.root / "steamapps").mkdir(parents=True)
        touch(
            self.root / "steamapps/libraryfolders.vdf",
            '"libraryfolders"\n{\n "0" { "path" "%s" }\n "1" { "path" "%s" }\n}\n' % (self.root, self.lib2),
        )
        touch(self.lib2 / "steamapps/appmanifest_1284210.acf",
              '"AppState" { "appid" "1284210" "installdir" "Guild Wars 2" }\n')
        self.game = self.lib2 / "steamapps/common/Guild Wars 2"
        touch(self.game / "Gw2-64.exe")
        self.compat = self.lib2 / "steamapps/compatdata/1284210"
        touch(self.compat / "pfx/user.reg", "WINE REGISTRY Version 2\n")
        touch(blish_settings.settings_path(self.compat / "pfx"), BLISH_SETTINGS)
        self.exp = self.root / "steamapps/common/Proton - Experimental"
        touch(self.exp / "proton")
        touch(self.root / "steamapps/common/Proton 9.0 (Beta)/proton")
        touch(self.root / "compatibilitytools.d/GE-Proton9-5/proton")
        (self.root / "compatibilitytools.d/not-a-runner").mkdir()
        touch(
            self.root / "config/config.vdf",
            '"InstallConfigStore" { "Software" { "Valve" { "Steam" { "CompatToolMapping" {\n'
            ' "0" { "name" "proton_experimental" }\n'
            ' "1284210" { "name" "proton_9" }\n'
            "} } } } }\n",
        )
        self.set_launch_options(launch_options)

    def set_launch_options(self, opts: str) -> None:
        touch(
            self.root / "userdata/1234/config/localconfig.vdf",
            '"UserLocalConfigStore" { "Software" { "Valve" { "Steam" { "apps" {\n'
            ' "1284210" { "LaunchOptions" "%s" }\n'
            "} } } } }\n" % opts.replace("\\", "\\\\").replace('"', '\\"'),
        )


BLISH_SETTINGS = """\ufeff{
  "T": "Blish_HUD.Settings.SettingCollection, Blish HUD",
  "Entries": [
    {
      "T": "Blish_HUD.Settings.SettingCollection, Blish HUD",
      "Key": "GraphicsConfiguration",
      "Value": {
        "Entries": [
          {"T": "Blish_HUD.Graphics.FramerateMethod, Blish HUD", "Key": "FramerateLimiter", "Value": 2},
          {"T": "System.Boolean, mscorlib", "Key": "EnableCharacterPositionBuffer", "Value": true}
        ]
      }
    },
    {"T": "System.String, mscorlib", "Key": "Language", "Value": "Español"}
  ]
}"""


def make_zip(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return path


PKG = {
    "addons/LOADER_public/Gw2-Simple-Addon-Loader.exe": b"loader",
    "addons/LOADER_public/dlls.txt": b"dlls/external_dx11_overlay.dll",
    "addons/LOADER_public/exes.txt": b"Blish.HUD.1.3.0\\Blish HUD.exe",
    "addons/LOADER_public/dlls/external_dx11_overlay.dll": b"dll",
    "addons/LOADER_public/Blish.HUD.1.3.0/Blish HUD.exe": b"blish",
}


def release_for(zip_path: Path, tag: str = "release-test") -> overlay.Release:
    return overlay.Release(tag, "https://example.invalid/x.zip", hashlib.sha256(zip_path.read_bytes()).hexdigest())


class VdfTests(unittest.TestCase):
    def test_nested_escapes_and_comments(self):
        data = parse_vdf('// yorum\n"a" { "b" "c \\"q\\"" "d" { "e" "f" } }')
        self.assertEqual(data, {"a": {"b": 'c "q"', "d": {"e": "f"}}})

    def test_unbalanced_braces_do_not_crash(self):
        parse_vdf('"a" { "b" "c" } } }')


class SteamTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.s = FakeSteam(Path(self._tmp.name))

    def test_libraries_compatdata_and_game_dir(self):
        self.assertEqual(library_folders(self.s.root), [self.s.root, self.s.lib2])
        self.assertEqual(find_compatdata(self.s.root, 1284210), self.s.compat)
        self.assertEqual(find_app_dir(self.s.root, 1284210), self.s.game)
        self.assertIsNone(find_app_dir(self.s.root, 999))

    def test_tool_mapping_prefers_app_over_global(self):
        self.assertEqual(compat_tool_name(self.s.root, 1284210), "proton_9")
        self.assertEqual(compat_tool_name(self.s.root, 999), "proton_experimental")

    def test_launch_options(self):
        self.assertEqual(read_launch_options(self.s.root, 1284210), ["gamescope -f -- %command%"])


LOCALCONFIG = """"UserLocalConfigStore"
{
\t"Software"
\t{
\t\t"Valve"
\t\t{
\t\t\t"Steam"
\t\t\t{
\t\t\t\t// yorum { "x" }
\t\t\t\t"apps"
\t\t\t\t{
\t\t\t\t\t"440"
\t\t\t\t\t{
\t\t\t\t\t\t"LaunchOptions"\t\t"-novid"
\t\t\t\t\t}
\t\t\t\t\t"1284210"
\t\t\t\t\t{
\t\t\t\t\t\t"LastPlayed"\t\t"1791800000"
%s\t\t\t\t\t}
\t\t\t\t}
\t\t\t}
\t\t}
\t}
}
"""
PATH = ("UserLocalConfigStore", "Software", "Valve", "Steam", "apps", "1284210")


class SteamRootTests(unittest.TestCase):
    def test_finds_steam_in_known_places_including_snap(self):
        from asuragate.steam import find_steam_root

        with tempfile.TemporaryDirectory() as t:
            home = Path(t)
            self.assertIsNone(find_steam_root(home))
            snap = home / "snap/steam/common/.local/share/Steam"
            (snap / "steamapps").mkdir(parents=True)
            self.assertIsNone(find_steam_root(home))  # steamapps alone is a library, not Steam's main folder
            (snap / "config").mkdir()
            self.assertEqual(find_steam_root(home), snap.resolve())

    def test_doctor_explains_a_wrong_steam_directory(self):
        with tempfile.TemporaryDirectory() as t:
            lib = Path(t) / "Games/Steam"
            (lib / "steamapps").mkdir(parents=True)
            checks = Orchestrator(Config(steam_root=str(lib))).diagnose()
            self.assertEqual(checks[0].level, "error")
            self.assertIn("looks like a game library", checks[0].message)
            checks = Orchestrator(Config(steam_root=str(Path(t) / "nothing"))).diagnose()
            self.assertIn("is not a Steam folder", checks[0].message)


class VdfEditTests(unittest.TestCase):
    TRICKY = 'bash -c \'exec "${@/%Gw2-64.exe/a b\\c}"\' -- %command% -provider Portal'

    def test_replaces_only_that_value(self):
        text = LOCALCONFIG % '\t\t\t\t\t\t"LaunchOptions"\t\t"%command% -provider Portal"\n'
        new = set_vdf_value(text, PATH, "LaunchOptions", self.TRICKY)
        self.assertEqual(new, LOCALCONFIG % ('\t\t\t\t\t\t"LaunchOptions"\t\t"'
                                             + self.TRICKY.replace("\\", "\\\\").replace('"', '\\"') + '"\n'))
        apps = parse_vdf(new)["UserLocalConfigStore"]["Software"]["Valve"]["Steam"]["apps"]
        self.assertEqual(apps["1284210"]["LaunchOptions"], self.TRICKY)  # the value Steam will read
        self.assertEqual(apps["440"]["LaunchOptions"], "-novid")  # other games are untouched

    def test_inserts_when_missing_and_none_when_app_unknown(self):
        new = set_vdf_value(LOCALCONFIG % "", PATH, "LaunchOptions", "%command% -x")
        self.assertIn('\t\t\t\t\t\t"LastPlayed"\t\t"1791800000"\n\t\t\t\t\t\t"LaunchOptions"\t\t"%command% -x"\n\t\t\t\t\t}',
                      new)
        self.assertEqual(parse_vdf(new)["UserLocalConfigStore"]["Software"]["Valve"]["Steam"]["apps"]["1284210"],
                         {"LastPlayed": "1791800000", "LaunchOptions": "%command% -x"})
        self.assertIsNone(set_vdf_value(LOCALCONFIG % "", PATH[:-1] + ("999",), "LaunchOptions", "x"))

    def test_write_launch_options_backs_up_once(self):
        with tempfile.TemporaryDirectory() as t:
            s = FakeSteam(Path(t))
            cfg = s.root / "userdata/1234/config/localconfig.vdf"
            original = cfg.read_text()
            self.assertEqual(write_launch_options(s.root, 1284210, "%command% -a"), [cfg])
            self.assertEqual(read_launch_options(s.root, 1284210), ["%command% -a"])
            self.assertEqual(write_launch_options(s.root, 1284210, "%command% -a"), [])  # no change
            write_launch_options(s.root, 1284210, "%command% -b")
            self.assertEqual((cfg.parent / "localconfig.vdf.asuragate.bak").read_text(), original)


class ProtonTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.s = FakeSteam(Path(self._tmp.name))
        self.runners = discover_runners(self.s.root)

    def test_discovery_skips_non_runners(self):
        self.assertEqual(sorted(r.name for r in self.runners),
                         ["GE-Proton9-5", "Proton - Experimental", "Proton 9.0 (Beta)"])

    def test_default_runner_follows_steam_mapping_without_guessing(self):
        self.assertEqual(default_runner(self.s.root, 1284210, self.runners).name, "Proton 9.0 (Beta)")
        self.assertEqual(default_runner(self.s.root, 999, self.runners).name, "Proton - Experimental")
        (self.s.root / "config/config.vdf").unlink()
        self.assertIsNone(default_runner(self.s.root, 1284210, self.runners))

    def test_runner_from_config_info(self):
        self.assertIsNone(runner_from_config_info(self.s.compat, self.runners))
        touch(self.s.compat / "config_info", f"11.0-100\n{self.s.exp}/files/share/fonts/\n")
        self.assertEqual(runner_from_config_info(self.s.compat, self.runners).name, "Proton - Experimental")


class LaunchOptionsTests(unittest.TestCase):
    STEAM_CMD = ["/S/ubuntu12_32/reaper", "SteamLaunch", "AppId=1284210", "--",
                 "/L/SteamLinuxRuntime_4/_v2-entry-point", "--verb=waitforexitandrun", "--",
                 "/L/common/Proton - Experimental/proton", "waitforexitandrun", "/L/common/Guild Wars 2/Gw2-64.exe"]

    def expand(self, opts: str) -> list[str]:
        """Substitutes %command% the way Steam does; bash prints the arguments instead of exec'ing them."""
        line = opts.replace("exec ", 'printf "%s\\\\0" ', 1)
        pre, _, post = line.partition("%command%")
        script = pre + " ".join(f"'{a}'" for a in self.STEAM_CMD) + post
        out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True).stdout
        return out.split("\0")[:-1]

    def test_swaps_only_the_game_exe(self):
        argv = self.expand(build_launch_options(Config(game_args="-provider Portal")))
        self.assertEqual(argv[:-3], self.STEAM_CMD[:-1])
        self.assertEqual(argv[-3:], ["/L/common/Guild Wars 2/addons/LOADER_public/Gw2-Simple-Addon-Loader.exe",
                                     "-provider", "Portal"])

    def test_steam_login(self):
        opts = build_launch_options(Config(steam_login=True))
        self.assertTrue(opts.startswith("USE_STEAM_LOGIN=1 bash -c "))
        self.assertTrue(opts.endswith("%command%"))

    def test_login_setting_owns_the_provider(self):
        anet = build_launch_options(Config(game_args="-mapLoadinfo"))
        self.assertTrue(anet.startswith("bash -c "))
        self.assertTrue(anet.endswith("%command% -provider Portal -mapLoadinfo"))
        steam = build_launch_options(Config(game_args="-provider Portal -mapLoadinfo", steam_login=True))
        self.assertTrue(steam.endswith("%command% -mapLoadinfo"))  # a -provider in the extras is ignored
        self.assertNotIn("Portal", steam)


class OverlayTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.game = self.tmp / "game"
        self.game.mkdir()
        self.zip = make_zip(self.tmp / "pkg.zip", PKG)
        self.rel = release_for(self.zip)

    def test_install_status_and_idempotence(self):
        self.assertFalse(overlay.status(self.game).installed)
        written = overlay.install(self.game, self.zip, self.rel)
        self.assertEqual(len(written), len(PKG))
        base = "Z:" + str(self.game).replace("/", "\\") + "\\addons\\LOADER_public\\"
        self.assertEqual((self.game / "addons/LOADER_public/dlls.txt").read_text(),
                         base + "dlls\\external_dx11_overlay.dll\n")
        self.assertEqual((self.game / "addons/LOADER_public/exes.txt").read_text(),
                         base + "Blish.HUD.1.3.0\\Blish HUD.exe\n")
        st = overlay.status(self.game)
        self.assertEqual((st.installed, st.tag, st.missing, st.modified), (True, "release-test", [], []))
        self.assertEqual(overlay.install(self.game, self.zip, self.rel), [])  # already up to date

    def test_status_reports_missing_and_modified_then_install_repairs(self):
        overlay.install(self.game, self.zip, self.rel)
        (self.game / "addons/LOADER_public/dlls.txt").unlink()
        (self.game / "addons/LOADER_public/exes.txt").write_text("other")
        st = overlay.status(self.game)
        self.assertEqual(st.missing, ["addons/LOADER_public/dlls.txt"])
        self.assertEqual(st.modified, ["addons/LOADER_public/exes.txt"])
        overlay.install(self.game, self.zip, self.rel)
        st = overlay.status(self.game)
        self.assertEqual(st.missing + st.modified, [])

    def test_absolute_entries_are_kept(self):
        z = make_zip(self.tmp / "abs.zip", {**PKG, "addons/LOADER_public/dlls.txt": b"C:\\x\\a.dll\n\nb.dll\n"})
        overlay.install(self.game, z, release_for(z))
        lines = (self.game / "addons/LOADER_public/dlls.txt").read_bytes().decode().split("\n")
        self.assertNotIn("\r", "".join(lines))
        self.assertEqual(lines[0], "C:\\x\\a.dll")
        self.assertEqual(lines[1], "")
        self.assertTrue(lines[2].startswith("Z:\\") and lines[2].endswith("\\LOADER_public\\b.dll"))

    def test_refuses_to_overwrite_foreign_files(self):
        touch(self.game / "addons/LOADER_public/dlls.txt", "the user's own file")
        with self.assertRaisesRegex(overlay.OverlayError, "do not belong"):
            overlay.install(self.game, self.zip, self.rel)
        self.assertFalse((self.game / "addons/LOADER_public/Gw2-Simple-Addon-Loader.exe").exists())

    def test_rejects_unsafe_and_unexpected_paths(self):
        for bad in ("../evil.dll", "addons/LOADER_public/../../evil.dll", "other/x.dll"):
            z = make_zip(self.tmp / "bad.zip", {**PKG, bad: b"x"})
            with self.assertRaises(overlay.OverlayError):
                overlay.install(self.game, z, release_for(z))
        self.assertFalse((self.tmp / "evil.dll").exists())
        self.assertFalse(overlay.status(self.game).installed)

    def test_update_removes_files_dropped_by_new_release(self):
        overlay.install(self.game, self.zip, self.rel)
        new = {k: v for k, v in PKG.items() if "external_dx11_overlay" not in k}
        z2 = make_zip(self.tmp / "pkg2.zip", {**new, "addons/LOADER_public/dlls/new.dll": b"n"})
        overlay.install(self.game, z2, release_for(z2, "release-2"))
        self.assertFalse((self.game / "addons/LOADER_public/dlls/external_dx11_overlay.dll").exists())
        self.assertTrue((self.game / "addons/LOADER_public/dlls/new.dll").exists())
        self.assertEqual(overlay.status(self.game).tag, "release-2")

    def test_uninstall_keeps_modified_and_foreign_files(self):
        overlay.install(self.game, self.zip, self.rel)
        (self.game / "addons/LOADER_public/exes.txt").write_text("changed")
        touch(self.game / "addons/LOADER_public/loader.log", "log")
        kept = overlay.uninstall(self.game)
        self.assertEqual(kept, ["addons/LOADER_public/exes.txt"])
        self.assertFalse((self.game / "addons/LOADER_public/Gw2-Simple-Addon-Loader.exe").exists())
        self.assertTrue((self.game / "addons/LOADER_public/loader.log").exists())
        self.assertFalse(overlay.status(self.game).installed)
        overlay.uninstall(self.game)  # a second run is harmless

    def test_download_verifies_sha256_and_uses_cache(self):
        calls = []

        def opener(url, timeout):
            calls.append(url)
            return io.BytesIO(self.zip.read_bytes())

        cache = self.tmp / "cache"
        p = overlay.download(self.rel, cache, opener)
        self.assertEqual(p.read_bytes(), self.zip.read_bytes())
        overlay.download(self.rel, cache, opener)
        self.assertEqual(len(calls), 1)  # the second time comes from the cache
        bad = overlay.Release("release-bad", "u", "0" * 64)
        with self.assertRaisesRegex(overlay.OverlayError, "sha256"):
            overlay.download(bad, cache, opener)
        self.assertEqual([f.name for f in cache.iterdir()], [p.name])  # no broken download is left behind

    def test_pinned_release_points_to_loader_layout(self):
        self.assertEqual(str(overlay.LOADER_EXE), "addons/LOADER_public/Gw2-Simple-Addon-Loader.exe")
        self.assertRegex(overlay.RELEASE.sha256, "^[0-9a-f]{64}$")


class PrefixTests(unittest.TestCase):
    def test_audio_disabled_reads_user_reg(self):
        with tempfile.TemporaryDirectory() as t:
            pfx = Path(t)
            self.assertFalse(prefix.audio_disabled(pfx))
            touch(pfx / "user.reg", 'WINE REGISTRY Version 2\n\n'
                  '[Software\\\\Wine\\\\AppDefaults\\\\Blish HUD.exe\\\\DllOverrides] 1791800000\n'
                  '#time=1dd\n"winepulse.drv"="disabled"\n\n[Software\\\\Other] 1\n"winealsa.drv"="disabled"\n')
            self.assertFalse(prefix.audio_disabled(pfx))  # alsa is under another key
            touch(pfx / "user.reg", 'WINE REGISTRY Version 2\n\n'
                  '[Software\\\\Wine\\\\AppDefaults\\\\Blish HUD.exe\\\\DllOverrides] 1791800000\n'
                  '"winealsa.drv"="disabled"\n"winepulse.drv"="disabled"\n')
            self.assertTrue(prefix.audio_disabled(pfx))

    def test_reg_commands(self):
        add = prefix.audio_reg_commands(True)
        self.assertEqual(add[0], ["reg", "add", r"HKCU\Software\Wine\AppDefaults\Blish HUD.exe\DllOverrides",
                                  "/v", "winepulse.drv", "/d", "disabled", "/f"])
        self.assertEqual(prefix.audio_reg_commands(False)[0][:2], ["reg", "delete"])


class BlishSettingsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.pfx = Path(self._tmp.name)

    def test_set_frame_limiter_keeps_everything_else(self):
        touch(blish_settings.settings_path(self.pfx), BLISH_SETTINGS)
        self.assertEqual(blish_settings.frame_limiter(self.pfx), 2)
        self.assertTrue(blish_settings.set_frame_limiter(self.pfx, 4))
        self.assertEqual(blish_settings.frame_limiter(self.pfx), 4)
        self.assertFalse(blish_settings.set_frame_limiter(self.pfx, 4))  # already set
        data = json.loads(blish_settings.settings_path(self.pfx).read_text(encoding="utf-8"))
        old = json.loads(BLISH_SETTINGS.lstrip("\ufeff"))
        old["Entries"][0]["Value"]["Entries"][0]["Value"] = 4
        self.assertEqual(data, old)

    def test_missing_entry_is_added_and_missing_file_is_left_alone(self):
        self.assertIsNone(blish_settings.frame_limiter(self.pfx))
        self.assertFalse(blish_settings.set_frame_limiter(self.pfx, 4))
        self.assertFalse(blish_settings.settings_path(self.pfx).exists())
        touch(blish_settings.settings_path(self.pfx), BLISH_SETTINGS.replace(
            '{"T": "Blish_HUD.Graphics.FramerateMethod, Blish HUD", "Key": "FramerateLimiter", "Value": 2},', ""))
        self.assertIsNone(blish_settings.frame_limiter(self.pfx))
        self.assertTrue(blish_settings.set_frame_limiter(self.pfx, 4))
        self.assertEqual(blish_settings.frame_limiter(self.pfx), 4)


class ConfigTests(unittest.TestCase):
    def test_display_path_hides_user_name_and_steam_account(self):
        from asuragate.config import display_path

        home = Path("/home/alice")
        self.assertEqual(display_path("/home/alice/.local/share/Steam/userdata/123456/config/localconfig.vdf", home),
                         "~/.local/share/Steam/userdata/<account>/config/localconfig.vdf")
        self.assertEqual(display_path("/home/alice", home), "~")
        self.assertEqual(display_path("/home/alicex/a", home), "/home/alicex/a")  # another user's folder
        self.assertEqual(display_path("/mnt/games/steamapps/common/Guild Wars 2", home),
                         "/mnt/games/steamapps/common/Guild Wars 2")

    def test_roundtrip_and_bad_input(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "c.json"
            Config(game_args="-mapLoadinfo", steam_login=True).save(p)
            c = Config.load(p)
            self.assertEqual((c.game_args, c.steam_login, c.blish_audio), ("-mapLoadinfo", True, False))
            p.write_text(json.dumps({"steam_login": "yes", "obsolete_setting": 1, "game_args": "-x"}))
            c = Config.load(p)  # wrong types and obsolete settings are ignored
            self.assertEqual((c.steam_login, c.game_args), (False, "-x"))
            p.write_text("{bozuk")
            self.assertEqual(Config.load(p), Config())

    def test_old_login_in_game_args_is_migrated(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "c.json"
            p.write_text(json.dumps({"game_args": "-provider Portal"}))
            self.assertEqual(Config.load(p), Config())  # ArenaNet login is the default
            p.write_text(json.dumps({"game_args": "-provider Steam -mapLoadinfo"}))
            self.assertEqual(Config.load(p), Config(game_args="-mapLoadinfo", steam_login=True))
            p.write_text(json.dumps({"game_args": "-provider Steam", "steam_login": False}))
            self.assertEqual(Config.load(p), Config())  # an explicit login setting wins


def make_proc(proc_root: Path, pid: int, argv: list[str]) -> None:
    d = proc_root / str(pid)
    d.mkdir(parents=True)
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")


class ProcessTests(unittest.TestCase):
    def test_only_the_wine_process_counts(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            make_proc(root, 10, ["python3", "/P/proton", "waitforexitandrun", "/g/Gw2-64.exe"])
            self.assertIsNone(find_process("Gw2-64.exe", root))
            make_proc(root, 20, ["S:\\g\\Guild Wars 2\\Gw2-64.exe -provider Portal"])  # single-string argv[0]
            self.assertEqual(find_process("Gw2-64.exe", root), 20)

    def test_steam_running(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            make_proc(root, 5, ["c:\\windows\\system32\\steam.exe", "/g/Gw2-64.exe"])  # Proton's doesn't count
            self.assertFalse(steam_running(root))
            make_proc(root, 6, ["/home/u/.local/share/Steam/ubuntu12_32/steam", "-silent"])
            self.assertTrue(steam_running(root))


class FakeRun:
    """Stands in for `proton run reg ...`: applies reg add/delete to the fake user.reg."""

    SECTION = "[Software\\\\Wine\\\\AppDefaults\\\\Blish HUD.exe\\\\DllOverrides] 1\n"

    def __init__(self, pfx: Path, returncode: int = 0):
        self.pfx, self.returncode, self.calls = pfx, returncode, []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        reg = self.pfx / "user.reg"
        if self.returncode == 0 and cmd[2] == "reg":
            text = reg.read_text()
            if cmd[3] == "add":
                if self.SECTION not in text:
                    text += "\n" + self.SECTION
                text = text.replace(self.SECTION, self.SECTION + f'"{cmd[6]}"="disabled"\n')
            else:
                text = text.split("\n" + self.SECTION)[0]
            reg.write_text(text)
        return subprocess.CompletedProcess(cmd, self.returncode, "", "boom" if self.returncode else "")


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.s = FakeSteam(self.tmp)
        touch(self.s.compat / "config_info", f"11.0-100\n{self.s.exp}/files/share/fonts/\n")
        self.proc = self.tmp / "proc"
        self.proc.mkdir()
        self.zip = make_zip(self.tmp / "pkg.zip", PKG)
        self.popened = []
        self.logs = []
        # In tests the downloaded package is the fake zip
        orig = overlay.RELEASE
        overlay.RELEASE = release_for(self.zip, orig.tag)
        self.addCleanup(setattr, overlay, "RELEASE", orig)

    def orch(self, run=None, **cfg):
        cfg = Config(**{"steam_root": str(self.s.root), "game_args": "-provider Portal", **cfg})
        return Orchestrator(cfg, self.logs.append,
                            run=run or FakeRun(self.s.compat / "pfx"),
                            popen=lambda cmd, **kw: self.popened.append(cmd),
                            which=lambda n: f"/usr/bin/{n}" if n == "steam" else None,
                            opener=lambda url, timeout: io.BytesIO(self.zip.read_bytes()),
                            proc_root=self.proc, cache_dir=self.tmp / "cache", environ={"HOME": "/h"})

    def problems(self, o, level="error"):
        return [c.message for c in o.diagnose() if c.level == level]

    def test_fresh_system_reports_what_to_do(self):
        errors = self.problems(self.orch())
        self.assertTrue(any("not installed" in m for m in errors))
        self.assertTrue(any("Launch Options:  bash -c" in m and "gamescope" in m for m in errors))

    def test_install_then_paste_options_makes_doctor_clean(self):
        o = self.orch()
        o.install()
        self.assertTrue((self.s.game / overlay.LOADER_EXE).is_file())
        self.assertTrue(prefix.audio_disabled(self.s.compat / "pfx"))
        self.assertEqual(blish_settings.frame_limiter(self.s.compat / "pfx"), blish_settings.MATCH_MONITOR)
        cmd, kw = o._run.calls[0]
        self.assertEqual(cmd[:3], [str(self.s.exp / "proton"), "run", "reg"])  # GW2's own Proton
        self.assertEqual(kw["env"]["STEAM_COMPAT_DATA_PATH"], str(self.s.compat))
        self.assertTrue(any("launch options written" in m for m in self.logs))
        self.assertEqual([c.message for c in o.diagnose() if c.level != "ok"], [])  # no manual pasting needed

    def test_launch_options_not_written_while_steam_runs(self):
        make_proc(self.proc, 7, ["/S/ubuntu12_32/steam"])
        self.orch().install()
        self.assertEqual(read_launch_options(self.s.root, 1284210), ["gamescope -f -- %command%"])
        self.assertTrue(any("Steam is running" in m and "bash -c" in m for m in self.logs))
        self.assertTrue(any("`install` writes them while Steam" in m for m in self.problems(self.orch())))

    def test_install_refuses_while_gw2_runs(self):
        make_proc(self.proc, 42, ["S:\\g\\Gw2-64.exe"])
        with self.assertRaisesRegex(OrchestratorError, "is running"):
            self.orch().install()
        self.assertFalse((self.s.game / overlay.LOADER_EXE).exists())

    def test_install_reports_failed_reg(self):
        with self.assertRaisesRegex(OrchestratorError, "reg add .* failed"):
            self.orch(run=FakeRun(self.s.compat / "pfx", returncode=1)).install()

    def test_blish_audio_toggle_and_uninstall_cleans_up(self):
        self.orch().install()
        self.orch(blish_audio=True).install()
        self.assertFalse(prefix.audio_disabled(self.s.compat / "pfx"))
        self.orch().install()
        self.orch().uninstall()
        self.assertFalse((self.s.game / "addons").exists())
        self.assertFalse(prefix.audio_disabled(self.s.compat / "pfx"))
        self.assertEqual(read_launch_options(self.s.root, 1284210), ["%command% -provider Portal"])

    def _overlay_ran(self):
        """What the overlay creates while the game runs."""
        loader = self.s.game / overlay.LOADER_DIR
        (loader / "logs").mkdir(parents=True, exist_ok=True)
        (loader / "logs/dll-1.log").write_text("log")
        (loader / "keybinds.conf").write_text("Ctrl+Alt+O restart_blish\nCtrl+Alt+B toggle_rendering\n")

    def test_uninstall_keeps_leftovers_unless_asked(self):
        self.orch().install()
        self._overlay_ran()
        self.assertEqual(sorted(self.orch().uninstall_extras()), ["keybinds", "logs", "steam_backup"])
        self.orch().uninstall()
        self.assertTrue((self.s.game / overlay.KEYBINDS).is_file())
        self.assertTrue((self.s.game / overlay.LOADER_DIR / "logs/dll-1.log").is_file())
        self.assertEqual(sorted(self.orch().uninstall_extras()), ["keybinds", "logs", "steam_backup"])

    def test_uninstall_removes_chosen_leftovers(self):
        self.orch().install()
        self._overlay_ran()
        (self.s.game / "addons/arcdps").mkdir()  # someone else's add-on stays
        self.orch().uninstall(remove=["logs", "keybinds", "steam_backup"])
        self.assertFalse((self.s.game / overlay.LOADER_DIR).exists())
        self.assertTrue((self.s.game / "addons/arcdps").is_dir())
        self.assertEqual(self.orch().uninstall_extras(), {})
        self.assertIn("Also removed: ", self.logs[-1])
        with self.assertRaises(ValueError):
            self.orch().uninstall(remove=["everything"])

    def test_uninstall_keeps_only_what_was_unticked(self):
        self.orch().install()
        self._overlay_ran()
        self.orch().uninstall(remove=["logs", "steam_backup"])
        self.assertEqual(list(self.orch().uninstall_extras()), ["keybinds"])

    def test_keybinds_text_says_it_is_not_the_games_controls(self):
        self.orch().install()
        self._overlay_ran()
        paths = self.orch().uninstall_extras()["keybinds"]
        label, details = uninstall_extra_text("keybinds", paths)
        self.assertNotIn(str(self.s.root), uninstall_extra_text("steam_backup", self.orch().uninstall_extras()["steam_backup"])[1])
        self.assertIn("NOTHING to do with Guild Wars 2's key bindings", details)
        self.assertIn("Ctrl+Alt+O restart_blish", details)  # the file's shortcuts are listed
        self.assertNotIn("restart_blish", uninstall_extra_text("keybinds", paths, show_shortcuts=False)[1])

    def test_uninstall_restores_launch_options_for_the_chosen_login(self):
        self.orch(steam_login=True, game_args="-mapLoadinfo").install()
        self.assertTrue(read_launch_options(self.s.root, 1284210)[0].startswith("USE_STEAM_LOGIN=1 "))
        self.orch(steam_login=True, game_args="-mapLoadinfo").uninstall()
        self.assertEqual(read_launch_options(self.s.root, 1284210), ["%command% -mapLoadinfo"])

    def test_launch_requires_clean_doctor_then_uses_steam(self):
        o = self.orch()
        with self.assertRaises(OrchestratorError):
            o.launch()
        o.install()
        o.launch()
        self.assertEqual(self.popened, [["steam", "-applaunch", "1284210"]])

    def test_frame_limiter_can_be_left_alone(self):
        self.orch(blish_match_refresh=False).install()
        self.assertEqual(blish_settings.frame_limiter(self.s.compat / "pfx"), 2)
        self.assertTrue(any("frame limiter" in m for m in self.problems(self.orch(), "warn")))

    def test_without_prefix(self):
        shutil.rmtree(self.s.compat)
        self.assertTrue(any("prefix" in m for m in self.problems(self.orch())))
        with self.assertRaisesRegex(OrchestratorError, "prefix"):
            self.orch().install()


if __name__ == "__main__":
    unittest.main()
