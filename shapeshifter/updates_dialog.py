"""The "Check for updates" window, and the background threads it and the tray use."""

from __future__ import annotations

import logging
import os

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout

from . import __version__, updates

log = logging.getLogger(__name__)
_open: list[QDialog] = []


class CheckWorker(QThread):
    """Asks GitHub for the latest release. Emits (update or None, error text)."""
    done = Signal(object, str)

    def run(self):
        try:
            self.done.emit(updates.available(), "")
        except updates.UpdateError as e:
            self.done.emit(None, str(e))
        except Exception as e:                                # noqa: BLE001
            log.exception("Update check crashed")
            self.done.emit(None, str(e))


class DownloadWorker(QThread):
    progress = Signal(float)
    done = Signal(str, str)

    def __init__(self, up: updates.Update):
        super().__init__()
        self.up, self._cancel = up, False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            path = updates.download(self.up, self.progress.emit, lambda: self._cancel)
            self.done.emit(str(path), "")
        except updates.UpdateError as e:
            self.done.emit("", str(e))
        except Exception as e:                                # noqa: BLE001
            log.exception("Update download crashed")
            self.done.emit("", str(e))


def show_update_dialog(up: updates.Update | None = None) -> None:
    """Open the window; with no update given it looks for one first."""
    for d in _open:
        d.raise_()
        d.activateWindow()
        return
    dlg = UpdateDialog(up)
    _open.append(dlg)
    dlg.finished.connect(lambda _: _open.remove(dlg) if dlg in _open else None)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()


class UpdateDialog(QDialog):
    def __init__(self, up: updates.Update | None):
        super().__init__()
        self.up = up
        self.worker = self.downloader = None
        self.setWindowTitle("ShapeShifter updates")
        self.resize(480, 360)
        root = QVBoxLayout(self)
        self.title = QLabel()
        f = self.title.font()
        f.setPointSizeF(f.pointSizeF() * 1.25)
        f.setBold(True)
        self.title.setFont(f)
        self.title.setWordWrap(True)
        root.addWidget(self.title)
        self.text = QLabel()
        self.text.setWordWrap(True)
        root.addWidget(self.text)
        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        root.addWidget(self.notes, 1)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.hide()
        root.addWidget(self.bar)
        row = QHBoxLayout()
        self.skip_btn = QPushButton("Skip this version")
        self.skip_btn.clicked.connect(self._skip)
        self.page_btn = QPushButton("Release notes")
        self.page_btn.clicked.connect(lambda: os.startfile(self.up.page if self.up else updates.PAGE))
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.close)
        self.go = QPushButton("Update now")
        self.go.setDefault(True)
        self.go.clicked.connect(self._update)
        for b in (self.skip_btn, self.page_btn):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.close_btn)
        row.addWidget(self.go)
        root.addLayout(row)
        if up:
            self._show_update(up)
        else:
            self._check()

    # ---------------------------------------------------------------- states

    def _check(self):
        self.title.setText("Looking for updates…")
        self.text.setText(f"You have version {__version__}.")
        self.notes.hide()
        for b in (self.go, self.skip_btn, self.page_btn):
            b.hide()
        self.worker = CheckWorker()
        self.worker.done.connect(self._checked)
        self.worker.start()

    def _checked(self, up, error):
        updates.remember_check(up.version if up else None)
        if error:
            self.title.setText("Couldn't check for updates")
            self.text.setText(error)
            self.page_btn.show()
        elif up:
            self._show_update(up)
        else:
            self.title.setText("You're up to date")
            self.text.setText(f"ShapeShifter {__version__} is the newest version.")

    def _show_update(self, up: updates.Update):
        self.up = up
        self.title.setText(f"ShapeShifter {up.version} is available")
        size = f" ({up.size / 1048576:.0f} MB)" if up.size else ""
        self.text.setText(f"You have {__version__}. The update{size} is downloaded, checked, and installed over the current version; "
                          "your settings and hotkeys are kept. ShapeShifter restarts by itself.")
        self.notes.setPlainText(up.notes or "No release notes.")
        self.notes.show()
        self.go.setText("Update now" if updates.installed_per_user() else "Open download page")
        for b in (self.go, self.skip_btn, self.page_btn):
            b.show()

    # --------------------------------------------------------------- actions

    def _skip(self):
        if self.up:
            updates.skip(self.up.version)
        self.close()

    def _update(self):
        if not self.up:
            return
        if not updates.installed_per_user():
            os.startfile(self.up.page)
            return
        self.go.setEnabled(False)
        self.skip_btn.setEnabled(False)
        self.title.setText(f"Downloading {self.up.version}…")
        self.bar.setValue(0)
        self.bar.show()
        self.downloader = DownloadWorker(self.up)
        self.downloader.progress.connect(lambda f: self.bar.setValue(int(f * 1000)))
        self.downloader.done.connect(self._downloaded)
        self.downloader.start()

    def _downloaded(self, path, error):
        self.bar.hide()
        if error:
            self.title.setText("The update didn't install")
            self.text.setText(error)
            self.go.setEnabled(True)
            self.skip_btn.setEnabled(True)
            return
        self.title.setText("Installing…")
        self.text.setText("ShapeShifter is closing to update and will start again in a moment.")
        try:
            updates.install(__import__("pathlib").Path(path))
        except updates.UpdateError as e:
            self.title.setText("The update didn't install")
            self.text.setText(str(e))
            self.go.setEnabled(True)
            return
        self.go.hide()
        self.skip_btn.hide()

    def closeEvent(self, event):
        if self.downloader and self.downloader.isRunning():
            self.downloader.cancel()
            self.downloader.wait(3000)
        super().closeEvent(event)
