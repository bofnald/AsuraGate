"""CLI and simple dialog (kdialog/zenity) tests + a headless GUI smoke test (if PySide6/PyQt6 exists).

For the GUI test:  QT_QPA_PLATFORM=offscreen python -m unittest tests.test_cli_gui -v
"""

import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from asuragate.cli import main
from asuragate import dialogs
from asuragate.config import Config
from asuragate.core import Check, OrchestratorError
from asuragate.desktop import default_python

HAS_QT = any(importlib.util.find_spec(m) is not None for m in ("PySide6", "PyQt6"))


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
            err.write(str(e.code))
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = str(Path(self._tmp.name) / "c.json")

    def test_config_set_and_show(self):
        code, out, _ = run_cli("--config", self.cfg, "config", "--set", "game_args=-mapLoadinfo",
                               "--set", "steam_login=yes")
        self.assertEqual(code, 0)
        c = Config.load(self.cfg)
        self.assertEqual((c.game_args, c.steam_login), ("-mapLoadinfo", True))
        self.assertIn("steam_login = True", out)

    def test_config_rejects_unknown_key_and_bad_int(self):
        self.assertNotEqual(run_cli("--config", self.cfg, "config", "--set", "nope=1")[0], 0)
        self.assertNotEqual(run_cli("--config", self.cfg, "config", "--set", "app_id=abc")[0], 0)
        self.assertFalse(Path(self.cfg).exists())  # invalid input does not write the file

    def test_launch_options(self):
        run_cli("--config", self.cfg, "config", "--set", "game_args=-provider Portal")
        code, out, _ = run_cli("--config", self.cfg, "launch-options")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "bash -c 'exec \"${@/%Gw2-64.exe/addons/LOADER_public/"
                                      "Gw2-Simple-Addon-Loader.exe}\"' -- %command% -provider Portal")

    def test_doctor_and_install_fail_without_steam(self):
        home = str(Path(self._tmp.name) / "home")
        os.makedirs(home)
        old = os.environ.get("HOME")
        os.environ["HOME"] = home
        try:
            code, out, _ = run_cli("--config", self.cfg, "doctor")
            icode, _, ierr = run_cli("--config", self.cfg, "install")
        finally:
            os.environ["HOME"] = old
        self.assertEqual(code, 1)
        self.assertIn("[ERROR]", out)
        self.assertEqual(icode, 1)
        self.assertIn("Error:", ierr)


    def test_shortcut_add_and_remove(self):
        home = Path(self._tmp.name) / "home"
        (home / "Desktop").mkdir(parents=True)
        old = {k: os.environ.get(k) for k in ("XDG_DATA_HOME", "HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME")}
        os.environ.update(XDG_DATA_HOME=self._tmp.name, HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
                          XDG_STATE_HOME=str(home / ".local/state"))
        try:
            code, out, _ = run_cli("shortcut")
            entry = (Path(self._tmp.name) / "applications/asuragate.desktop").read_text()
            self.assertIn("Name=AsuraGate\n", entry)
            self.assertRegex(entry, r"\nIcon=/.*/src/asuragate/icon\.svg\n")
            on_desktop = home / "Desktop/asuragate.desktop"
            self.assertEqual(on_desktop.read_text(), entry)
            self.assertTrue(os.access(on_desktop, os.X_OK))  # so KDE opens it on double-click
            code2, out2, _ = run_cli("shortcut", "--remove")
            self.assertFalse(on_desktop.exists())
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertEqual((code, code2), (0, 0))
        py = default_python()
        self.assertIn("Exec=" + py + " -m asuragate\n", entry)
        self.assertIn("[Desktop Action play]\nName=Play\nExec=" + py + " -m asuragate launch\n", entry)
        self.assertFalse((Path(self._tmp.name) / "applications/asuragate.desktop").exists())

    def test_register_menu_writes_entry_and_launcher_icon_once(self):
        from asuragate.desktop import ICON, register_menu

        env = {"XDG_DATA_HOME": self._tmp.name, "HOME": self._tmp.name}
        entry = Path(self._tmp.name) / "applications/asuragate.desktop"
        launcher = Path(self._tmp.name) / "AsuraGate"
        launcher.write_text("[Desktop Entry]\nName=AsuraGate\nIcon=applications-games\nExec=x\n")
        launcher.chmod(0o755)
        self.assertEqual(register_menu(env, python="python3", launcher=launcher), [entry, launcher])
        self.assertIn("Exec=python3 -m asuragate\n", entry.read_text())
        self.assertIn(f"Path={self._tmp.name}/src\n", entry.read_text())
        self.assertEqual(launcher.read_text(), f"[Desktop Entry]\nName=AsuraGate\nIcon={ICON}\nExec=x\n")
        self.assertTrue(os.access(launcher, os.X_OK))
        self.assertEqual(register_menu(env, python="python3", launcher=launcher), [])  # unchanged: nothing written
        entry.write_text("[Desktop Entry]\nPath=/old/folder\n")  # project folder was moved
        self.assertEqual(register_menu(env, python="python3", launcher=launcher), [entry])

    def test_project_launcher(self):
        """The AsuraGate launcher in the project folder: Exec unquoted per the Desktop Entry spec, then run by sh."""
        from asuragate.desktop import LAUNCHER as launcher

        self.assertTrue(os.access(launcher, os.X_OK))
        text = launcher.read_text()
        self.assertRegex(text, r"\nIcon=(applications-games|/.*/asuragate/icon\.svg)\n")
        execs = [line[5:] for line in text.splitlines() if line.startswith("Exec=")]

        def unquote(value):
            value = value.replace("\\\\", "\\")  # string escape level
            args, i = [], 0
            while i < len(value):
                if value[i] == " ":
                    i += 1
                elif value[i] == '"':
                    arg, i = "", i + 1
                    while value[i] != '"':
                        if value[i] == "\\":
                            i += 1
                        arg, i = arg + value[i], i + 1
                    args.append(arg)
                    i += 1
                else:
                    end = value.find(" ", i)
                    end = len(value) if end < 0 else end
                    args.append(value[i:end])
                    i = end
            return args

        self.assertEqual(len(execs), 2)
        for value, tail in zip(execs, ("asuragate --register-menu", "asuragate launch")):
            args = unquote(value)
            self.assertEqual(args[:2] + args[3:], ["sh", "-c", "%k"])
            self.assertEqual(args[2], f'cd "$(dirname -- "$0")/src" && exec python3 -m {tail}')
            d = Path(self._tmp.name) / "a folder"
            (d / "src").mkdir(parents=True, exist_ok=True)
            script = args[2].replace("exec python3 -m " + tail, "pwd")
            out = subprocess.run(["sh", "-c", script, str(d / "AsuraGate")], capture_output=True, text=True)
            self.assertEqual(out.stdout.strip(), str(d / "src"))

    def test_desktop_exec_quoting(self):
        from asuragate.desktop import _quote

        self.assertEqual(_quote("/a/b"), "/a/b")
        self.assertEqual(_quote("/x y/$p"), '"/x y/\\$p"')

    def test_falls_back_to_dialogs_without_qt_and_explains_without_both(self):
        import asuragate.cli as cli

        calls = []
        saved = {k: sys.modules.get(k) for k in ("asuragate.gui",)}
        sys.modules["asuragate.gui"] = None  # no Qt: the import raises ImportError
        old_find, old_run, old_report = dialogs.find_tool, dialogs.run_dialogs, dialogs.report_no_ui
        try:
            dialogs.find_tool = lambda env: "kdialog"
            dialogs.run_dialogs = lambda cfg, f, d: calls.append(d.tool) or 0
            self.assertEqual(cli._run_ui(Config(), self.cfg), 0)
            dialogs.find_tool = lambda env: None
            dialogs.report_no_ui = lambda env: calls.append("notification")
            code, _, err = run_cli("--config", self.cfg, "gui")
        finally:
            dialogs.find_tool, dialogs.run_dialogs, dialogs.report_no_ui = old_find, old_run, old_report
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
        self.assertEqual(calls, ["kdialog", "notification"])
        self.assertEqual(code, 2)
        self.assertIn("kdialog/zenity", err)


class FakeTool:
    """Stands in for kdialog/zenity: returns the given answers in order and records the calls."""

    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        code, out = self.answers.pop(0) if self.answers else (0, "")
        return subprocess.CompletedProcess(cmd, code, out, "")


class FakeOrch:
    def __init__(self, cfg, log, fail=None):
        self.cfg, self.log, self.fail, self.done = cfg, log, fail, []

    def diagnose(self):
        return [Check("ok", "Steam: /s"), Check("error", "Package <installed> not & missing")]

    def _act(self, name):
        if self.fail == name:
            raise OrchestratorError("it failed")
        self.done.append(name)
        self.log(f"{name} done")

    def uninstall_extras(self):
        return {"logs": [Path("/g/logs/a.log"), Path("/g/logs/b.log")], "keybinds": [Path("/g/keybinds.conf")]}

    def install(self): self._act("install")

    def uninstall(self, remove=()):
        self.removed = list(remove)
        self._act("uninstall")

    def launch(self): self._act("launch")


class DialogsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg_file = str(Path(self._tmp.name) / "c.json")
        self.orchs = []

    def make(self, cfg, log):
        o = FakeOrch(cfg, log)
        self.orchs.append(o)
        return o

    def run_with(self, tool, answers):
        fake = FakeTool(answers)
        code = dialogs.run_dialogs(Config(), self.cfg_file, dialogs.Dialogs(tool, run=fake), self.make)
        return code, fake.calls

    def test_report_no_ui_notifies_and_logs(self):
        old = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = self._tmp.name
        try:
            fake = FakeTool([])
            log = dialogs.report_no_ui({"DISPLAY": ":0"}, run=fake, which=lambda n: "/usr/bin/" + n)
            self.assertIn("No UI available", log.read_text())
            self.assertEqual(fake.calls[0][:5], ["notify-send", "--app-name", "AsuraGate", "--icon", "dialog-error"])
            self.assertIn(str(log), fake.calls[0][-1])
            silent = FakeTool([])
            dialogs.report_no_ui({}, run=silent, which=lambda n: None)  # without notify-send only the log
            self.assertEqual(silent.calls, [])
            self.assertEqual(log.read_text().count("No UI available"), 2)
        finally:
            if old is None:
                os.environ.pop("XDG_STATE_HOME", None)
            else:
                os.environ["XDG_STATE_HOME"] = old

    def test_find_tool_prefers_desktop_native(self):
        both = lambda n: f"/usr/bin/{n}"  # noqa: E731
        self.assertEqual(dialogs.find_tool({"XDG_CURRENT_DESKTOP": "KDE"}, both), "kdialog")
        self.assertEqual(dialogs.find_tool({"XDG_CURRENT_DESKTOP": "GNOME"}, both), "zenity")
        self.assertEqual(dialogs.find_tool({}, lambda n: "/z" if n == "zenity" else None), "zenity")
        self.assertIsNone(dialogs.find_tool({}, lambda n: None))

    def test_kdialog_install_then_play(self):
        code, calls = self.run_with("kdialog", [(0, "install"), (0, ""), (0, ""), (0, "launch")])
        self.assertEqual(code, 0)
        self.assertEqual(calls[0][:4], ["kdialog", "--title", "AsuraGate", "--menu"])
        self.assertIn("--passivepopup", calls[1])
        self.assertEqual(calls[2][-1], "install done")  # the install log was shown
        self.assertEqual([o.done for o in self.orchs], [["install"], ["launch"]])

    def test_zenity_doctor_escapes_markup_and_cancel_exits(self):
        code, calls = self.run_with("zenity", [(0, "doctor"), (0, ""), (1, "")])
        self.assertEqual(code, 0)
        self.assertIn("--hide-column", calls[0])
        self.assertEqual(calls[1][1], "--error")  # errors -> error dialog
        self.assertIn("Package &lt;installed&gt; not &amp; missing", calls[1][-1])
        k_code, k_calls = self.run_with("kdialog", [(0, "doctor"), (0, ""), (1, "")])
        self.assertIn("Package <installed> not & missing", k_calls[1][-1])  # kdialog shows plain text

    def test_uninstall_needs_confirmation_and_errors_are_shown(self):
        _, calls = self.run_with("kdialog", [(0, "uninstall"), (1, ""), (1, "")])
        self.assertEqual(self.orchs[0].done, [])
        self.assertIn("--yesno", calls[1])
        self.orchs.clear()
        fake = FakeTool([(0, "install"), (0, ""), (0, ""), (1, "")])
        dialogs.run_dialogs(Config(), self.cfg_file, dialogs.Dialogs("kdialog", run=fake),
                            lambda c, log: FakeOrch(c, log, fail="install"))
        self.assertEqual(fake.calls[2][-2:], ["--error", "it failed"])

    def test_kdialog_uninstall_explains_and_asks_for_each_leftover(self):
        _, calls = self.run_with("kdialog", [(0, "uninstall"), (0, ""), (0, '"logs"\n'), (0, ""), (1, "")])
        self.assertIn("Not touched: the game itself", calls[1][-1])  # the yes/no explains what is removed
        checklist = calls[2]
        self.assertIn("--checklist", checklist)
        self.assertIn("NOTHING to do with Guild Wars 2's key bindings", checklist[checklist.index("--checklist") + 1])
        self.assertIn("Overlay log files (2 files)", checklist)
        self.assertEqual(checklist[checklist.index("Overlay log files (2 files)") + 1], "off")  # opt-in
        self.assertEqual(self.orchs[0].removed, ["logs"])  # only the ticked one
        self.assertEqual(self.orchs[0].done, ["uninstall"])

    def test_zenity_uninstall_checklist_cancel_removes_nothing(self):
        _, calls = self.run_with("zenity", [(0, "uninstall"), (0, ""), (1, ""), (1, "")])
        self.assertIn("--checklist", calls[2])
        self.assertEqual(self.orchs[0].done, [])

    def test_settings_saves_config(self):
        self.run_with("kdialog", [(0, "settings"), (0, "-provider Portal -mapLoadinfo"), (0, ""), (0, ""), (1, "")])
        c = Config.load(self.cfg_file)
        self.assertEqual((c.game_args, c.steam_login), ("-mapLoadinfo", True))  # the login is its own question


@unittest.skipUnless(HAS_QT, "no PySide6/PyQt6")
class GuiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from asuragate.gui import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_form_updates_launch_options_and_saves(self):
        from asuragate.gui import MainWindow

        with tempfile.TemporaryDirectory() as t:
            cfg_file = str(Path(t) / "c.json")
            win = MainWindow(Config(game_args="-mapLoadinfo"), cfg_file)
            self.assertTrue(win.anet_login.isChecked())
            self.assertTrue(win.launch_opts.toPlainText().endswith("%command% -provider Portal -mapLoadinfo"))
            win.steam_login.setChecked(True)  # switching the login switches the launch options
            self.assertFalse(win.anet_login.isChecked())
            text = win.launch_opts.toPlainText()
            self.assertTrue(text.startswith("USE_STEAM_LOGIN=1 "))
            self.assertTrue(text.endswith("%command% -mapLoadinfo"))
            self.assertNotIn("-provider", text)
            win.close()  # closeEvent saves the config
            self.assertEqual(Config.load(cfg_file), Config(game_args="-mapLoadinfo", steam_login=True))

    def test_uninstall_dialog_has_explained_checkboxes(self):
        from asuragate.gui import UninstallDialog

        with tempfile.TemporaryDirectory() as t:
            kb = Path(t) / "keybinds.conf"
            kb.write_text("Ctrl+Alt+O restart_blish\n")
            dlg = UninstallDialog({"logs": [Path(t) / "a.log"], "keybinds": [kb]})
            self.assertEqual(dlg.selected(), [])  # nothing extra is removed unless ticked
            try:
                from PySide6.QtGui import QTextDocument
            except ImportError:
                from PyQt6.QtGui import QTextDocument
            doc = QTextDocument()
            doc.setHtml(dlg.boxes["keybinds"].toolTip())  # rich text, so Qt wraps it
            shown = doc.toPlainText()
            self.assertIn("NOTHING to do with Guild Wars 2's key bindings", shown)
            self.assertIn("Ctrl+Alt+O restart_blish", shown)
            dlg.boxes["logs"].setChecked(True)
            self.assertEqual(dlg.selected(), ["logs"])
            self.assertEqual(UninstallDialog({}).selected(), [])

    def test_save_keeps_changes_made_outside_the_gui(self):
        from asuragate.gui import MainWindow

        with tempfile.TemporaryDirectory() as t:
            cfg_file = str(Path(t) / "c.json")
            Config(game_args="-a").save(cfg_file)
            win = MainWindow(Config.load(cfg_file), cfg_file)
            Config(game_args="-b", blish_audio=True).save(cfg_file)  # changed via the CLI while the UI was open
            win.steam_login.setChecked(True)  # the user changed only this in the UI
            win.close()
            self.assertEqual(Config.load(cfg_file), Config(game_args="-b", blish_audio=True, steam_login=True))
            self.assertEqual(win.game_args.text(), "-b")  # the form was updated too


if __name__ == "__main__":
    unittest.main()
