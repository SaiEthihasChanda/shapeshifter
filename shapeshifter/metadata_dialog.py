"""The Properties window: shows a file's metadata (EXIF, author, tags …) and saves the edits."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from . import config, metadata
from .core import ConversionError
from .identify import identify

log = logging.getLogger(__name__)
_open: list[QDialog] = []


def show_metadata_editor(paths: list[str], cfg: dict | None = None) -> None:
    dlg = MetadataEditor(list(paths), cfg or config.load())
    _open.append(dlg)
    dlg.finished.connect(lambda _: _open.remove(dlg) if dlg in _open else None)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()


class _Save(QThread):
    done = Signal(list, list)

    def __init__(self, jobs, cfg):
        super().__init__()
        self.jobs, self.cfg = jobs, cfg

    def run(self):
        saved, errors = [], []
        for path, fmt, changes, strip in self.jobs:
            try:
                saved.append(str(metadata.apply(path, fmt, changes, strip, self.cfg)))
            except ConversionError as e:
                errors.append(f"{Path(path).name}: {e}")
            except Exception as e:                      # noqa: BLE001 - shown to the user
                log.exception("Saving properties crashed")
                errors.append(f"{Path(path).name}: {e}")
        self.done.emit(saved, errors)


class MetadataEditor(QDialog):
    def __init__(self, paths: list[str], cfg: dict):
        super().__init__()
        self.cfg = cfg
        self.paths = paths
        self.fmts = [identify(p).fmt or "" for p in paths]
        self.multi = len(paths) > 1
        self.edits: dict[str, QLineEdit] = {}
        self.original: dict[str, str] = {}
        self.setWindowTitle(("Properties — " + Path(paths[0]).name) if not self.multi else f"Properties — {len(paths)} files")
        self.resize(560, 700)
        root = QVBoxLayout(self)

        try:
            fields = (metadata.common_fields(paths, self.fmts) if self.multi else metadata.read(paths[0], self.fmts[0]))
            error = ""
        except ConversionError as e:
            fields, error = [], str(e)
        intro = QLabel(error or (f"Editing {len(paths)} files at once. Only what you type is changed; empty boxes keep each file's own value."
                                 if self.multi else
                                 "Change a value and press Save. Empty a box to remove that detail. The picture, text and sound are not touched."))
        intro.setWordWrap(True)
        root.addWidget(intro)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        holder = QWidget()
        col = QVBoxLayout(holder)
        col.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(holder)
        root.addWidget(scroll, 1)
        for group in metadata.GROUPS:
            rows = [f for f in fields if f.group == group and (not self.multi or f.writable)]
            if not rows:
                continue
            box = QGroupBox(group)
            form = QFormLayout(box)
            for f in rows:
                if f.writable:
                    edit = QLineEdit(f.value)
                    edit.setPlaceholderText(f.hint or ("keep each file's own" if self.multi else ""))
                    edit.setToolTip(f.hint)
                    form.addRow(f.label, edit)
                    self.edits[f.key] = edit
                    self.original[f.key] = f.value
                else:
                    label = QLabel(f.value)
                    label.setWordWrap(True)
                    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                    form.addRow(f.label, label)
            col.addWidget(box)

        self.strip = QCheckBox("Remove all personal details (author, location, camera, dates …) when saving")
        root.addWidget(self.strip)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        buttons = QHBoxLayout()
        if "gps" in self.edits:
            where = QPushButton("Remove location")
            where.clicked.connect(lambda: self.edits["gps"].clear())
            buttons.addWidget(where)
        buttons.addStretch(1)
        self.save_btn = QPushButton("Save")
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self._save)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        buttons.addWidget(self.save_btn)
        root.addLayout(buttons)
        if error:
            self.save_btn.setEnabled(False)

    def _changes(self) -> dict[str, str]:
        out = {}
        for key, edit in self.edits.items():
            text = edit.text().strip()
            if text != self.original.get(key, "").strip() and (text or not self.multi):
                out[key] = text
        return out

    def _save(self):
        changes, strip = self._changes(), self.strip.isChecked()
        if not changes and not strip:
            self.status.setText("Nothing changed.")
            return
        self.save_btn.setEnabled(False)
        self.status.setText("Saving…")
        jobs = [(p, f, changes if not self.multi else {k: v for k, v in changes.items()}, strip) for p, f in zip(self.paths, self.fmts)]
        self.worker = _Save(jobs, self.cfg)
        self.worker.done.connect(self._saved)
        self.worker.start()

    def _saved(self, saved, errors):
        self.save_btn.setEnabled(True)
        if errors:
            self.status.setText("Couldn't save: " + "; ".join(errors[:3]))
            return
        self.status.setText(f"Saved {len(saved)} file{'s' if len(saved) != 1 else ''}.")
        if not self.multi:
            self.paths = saved
            self.close()
