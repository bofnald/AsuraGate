"""Qt UI. All logic lives in core.py; this is only a settings form + buttons + log.

Uses the system's PySide6 first (ships with the Bazzite image), else PyQt6; with neither, the import
fails and cli.py falls back to simple dialogs (dialogs.py).
"""

from __future__ import annotations

import html
import os
import sys
from dataclasses import fields, replace
from pathlib import Path

try:
    import PySide6 as _binding
    from PySide6.QtCore import QThread, Signal
    from PySide6.QtGui import QIcon, QTextOption
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QDialog,
        QDialogButtonBox,
        QFileDialog,
        QFormLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QPlainTextEdit,
        QPushButton,
        QRadioButton,
        QVBoxLayout,
        QWidget,
    )
except ImportError:
    import PyQt6 as _binding
    from PyQt6.QtCore import QThread
    from PyQt6.QtCore import pyqtSignal as Signal
    from PyQt6.QtGui import QIcon, QTextOption
    from PyQt6.QtWidgets import (
        QApplication,
        QCheckBox,
        QDialog,
        QDialogButtonBox,
        QFileDialog,
        QFormLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QPlainTextEdit,
        QPushButton,
        QRadioButton,
        QVBoxLayout,
        QWidget,
    )

from .config import Config, config_path, without_provider
from .core import UNINSTALL_SUMMARY, Orchestrator, OrchestratorError, uninstall_extra_text
from .launchopts import build_launch_options


class Worker(QThread):
    """install / uninstall / launch in a thread so downloads and Wine calls don't freeze the UI."""

    log = Signal(str)
    failed = Signal(str)

    def __init__(self, cfg: Config, action: str, **kwargs):
        super().__init__()
        self.cfg = cfg
        self.action = action
        self.kwargs = kwargs

    def run(self) -> None:
        try:
            getattr(Orchestrator(self.cfg, log=self.log.emit), self.action)(**self.kwargs)
        except OrchestratorError as e:
            self.failed.emit(str(e))
        except Exception as e:  # don't let the UI thread crash
            self.failed.emit(f"Unexpected error: {e!r}")


def _rich(text: str) -> str:
    """Plain text as rich text: Qt wraps long tooltips only when they are rich text."""
    body = html.escape(text).replace("\n\n", "</p><p>").replace("\n", "<br>").replace("  ", "&nbsp;&nbsp;")
    return f"<qt><p>{body}</p></qt>"


class UninstallDialog(QDialog):
    """Says what uninstall removes and lets the user pick the optional leftovers (details on hover)."""

    def __init__(self, extras: dict[str, list[Path]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Uninstall")
        v = QVBoxLayout(self)
        summary = QLabel(UNINSTALL_SUMMARY)
        summary.setWordWrap(True)
        v.addWidget(summary)
        self.boxes: dict[str, QCheckBox] = {}
        if extras:
            v.addWidget(QLabel("\nAlso remove these leftovers? Nothing below is removed unless you tick it.\n"
                               "Hover over an option for details."))
            for key, paths in extras.items():
                label, details = uninstall_extra_text(key, paths)
                box = QCheckBox(label)
                box.setChecked(False)  # opt-in: an accidental uninstall loses nothing extra
                box.setToolTip(_rich(details))
                v.addWidget(box)
                self.boxes[key] = box
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.addButton("Uninstall", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

    def selected(self) -> list[str]:
        return [key for key, box in self.boxes.items() if box.isChecked()]


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config, config_file: str | None = None):
        super().__init__()
        self._config_file = config_file
        self._worker: Worker | None = None
        self.setWindowTitle("AsuraGate")
        self.setWindowIcon(QIcon(str(Path(__file__).resolve().parent / "icon.svg")))

        self.steam_root = QLineEdit()
        self.steam_root.setPlaceholderText("automatic")
        self.anet_login = QRadioButton("ArenaNet account")
        self.anet_login.setToolTip("You log in with your ArenaNet e-mail and password.\n"
                                   "AsuraGate puts -provider Portal into the launch options.")
        self.steam_login = QRadioButton("Steam account")
        self.steam_login.setToolTip("Your Guild Wars 2 account is linked to Steam and you log in through Steam.\n"
                                    "AsuraGate puts USE_STEAM_LOGIN=1 into the launch options.")
        self.game_args = QLineEdit()
        self.game_args.setPlaceholderText("usually empty")
        self.game_args.setToolTip("Extra Guild Wars 2 options. The login is chosen above, so -provider is ignored here.")
        self.blish_audio = QCheckBox("Blish HUD sounds (cause a crash under Wine)")
        # Shown in full (wrapped): a one-line field showed only the end, so changes at the start were hidden.
        self.launch_opts = QPlainTextEdit()
        self.launch_opts.setReadOnly(True)
        self.launch_opts.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.launch_opts.setFixedHeight(self.launch_opts.fontMetrics().lineSpacing() * 3 + 12)
        copy_btn = QPushButton("Copy")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(self.launch_opts.toPlainText()))

        form_box = QGroupBox("Settings")
        f = QFormLayout(form_box)
        f.addRow("Steam directory", self._with_browse(self.steam_root))
        login = QWidget()
        lh = QHBoxLayout(login)
        lh.setContentsMargins(0, 0, 0, 0)
        lh.addWidget(self.anet_login)
        lh.addWidget(self.steam_login)
        lh.addStretch(1)
        f.addRow("Log in with", login)
        f.addRow("Extra GW2 arguments", self.game_args)
        f.addRow(self.blish_audio)
        opts = QWidget()
        h = QHBoxLayout(opts)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.launch_opts, 1)
        h.addWidget(copy_btn)
        f.addRow("Steam launch options", opts)

        self.install_btn = QPushButton("Install / Update")
        self.uninstall_btn = QPushButton("Uninstall")
        self.doctor_btn = QPushButton("Check")
        self.launch_btn = QPushButton("Play")
        self.install_btn.clicked.connect(lambda: self._start("install"))
        self.uninstall_btn.clicked.connect(self.uninstall)
        self.launch_btn.clicked.connect(lambda: self._start("launch"))
        self.doctor_btn.clicked.connect(self.doctor)
        buttons = QHBoxLayout()
        for b in (self.install_btn, self.uninstall_btn, self.doctor_btn, self.launch_btn):
            buttons.addWidget(b)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)

        root = QWidget()
        v = QVBoxLayout(root)
        v.addWidget(form_box)
        v.addLayout(buttons)
        v.addWidget(self.log_view, 1)
        self.setCentralWidget(root)
        self.resize(820, 560)

        self._populate(cfg)
        for sig in (self.game_args.textChanged, self.steam_login.toggled):
            sig.connect(self._update_launch_opts)
        self._update_launch_opts()

    # ---------- helpers ----------
    def _with_browse(self, edit: QLineEdit) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        btn = QPushButton("…")
        btn.setFixedWidth(32)

        def pick():
            path = QFileDialog.getExistingDirectory(self, "Steam directory", edit.text())
            if path:
                edit.setText(path)

        btn.clicked.connect(pick)
        h.addWidget(edit, 1)
        h.addWidget(btn)
        return w

    def _populate(self, c: Config) -> None:
        self.steam_root.setText(c.steam_root)
        self.game_args.setText(c.game_args)
        (self.steam_login if c.steam_login else self.anet_login).setChecked(True)
        self.blish_audio.setChecked(c.blish_audio)
        self._base = c  # keeps fields that are not in the form (app_id)

    def _collect(self) -> Config:
        return replace(
            getattr(self, "_base", Config()),
            steam_root=self.steam_root.text().strip(),
            game_args=without_provider(self.game_args.text()),
            steam_login=self.steam_login.isChecked(),
            blish_audio=self.blish_audio.isChecked(),
        )

    def _update_launch_opts(self, *_):
        self.launch_opts.setPlainText(build_launch_options(self._collect()))

    def _log(self, msg: str) -> None:
        self.log_view.appendPlainText(msg)

    def _save(self) -> Config:
        """Writes only the fields changed in the form, so settings changed via the CLI while the UI is open survive."""
        form, base = self._collect(), getattr(self, "_base", Config())
        path = Path(self._config_file) if self._config_file else config_path()
        cfg = Config.load(path) if path.is_file() else replace(base)
        for f in fields(Config):
            if getattr(form, f.name) != getattr(base, f.name):
                setattr(cfg, f.name, getattr(form, f.name))
        cfg.save(path)
        if cfg != form:
            self._populate(cfg)  # show the current values from disk
            self._update_launch_opts()
        else:
            self._base = cfg
        return cfg

    # ---------- actions ----------
    def doctor(self) -> None:
        marks = {"ok": "[OK]", "warn": "[WARN]", "error": "[ERROR]"}
        for c in Orchestrator(self._save(), log=self._log).diagnose():
            self._log(f"{marks[c.level]} {c.message}")

    def uninstall(self) -> None:
        if self._worker is not None:
            return
        dialog = UninstallDialog(Orchestrator(self._save()).uninstall_extras(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._start("uninstall", remove=dialog.selected())

    def _start(self, action: str, **kwargs) -> None:
        if self._worker is not None:
            return
        self._worker = Worker(self._save(), action, **kwargs)
        self._worker.log.connect(self._log)
        self._worker.failed.connect(lambda m: self._log(f"[ERROR] {m}"))
        self._worker.finished.connect(self._on_finished)
        self._set_busy(True)
        self._worker.start()

    def _set_busy(self, busy: bool) -> None:
        for b in (self.install_btn, self.uninstall_btn, self.launch_btn):
            b.setEnabled(not busy)

    def _on_finished(self) -> None:
        self._worker = None
        self._set_busy(False)
        self._log("Done.")

    def closeEvent(self, event) -> None:
        self._save()
        if self._worker is not None:
            self._worker.wait(30000)
        super().closeEvent(event)


def _isolate_bundled_qt() -> None:
    """Keep a binding that bundles its own Qt (pip) from loading plugins of a different Qt version via
    KDE's QT_PLUGIN_PATH. A system package (e.g. Bazzite's PySide6) uses the system Qt and is left alone."""
    root = Path(_binding.__file__).parent
    if (root / "Qt6" / "plugins").is_dir() or (root / "Qt" / "plugins").is_dir():  # PyQt6 / PySide6 wheel
        for var in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
            os.environ.pop(var, None)


def run_gui(cfg: Config, config_file: str | None = None) -> int:
    _isolate_bundled_qt()
    app = QApplication(sys.argv[:1])
    win = MainWindow(cfg, config_file)
    win.show()
    return app.exec()
