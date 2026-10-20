"""'What is this file?' window: identification verdict, evidence and raw details."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit,
                               QPushButton, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout)

from . import engine
from .formats import name_of
from .identify import identify, magika_available

_open: list[QDialog] = []


def show_inspector(path: str):
    dlg = Inspector(path)
    _open.append(dlg)
    dlg.finished.connect(lambda _: _open.remove(dlg) if dlg in _open else None)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()


def _hexdump(data: bytes, width: int = 16) -> str:
    lines = []
    for off in range(0, len(data), width):
        chunk = data[off:off + width]
        hexs = " ".join(f"{b:02x}" for b in chunk)
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{off:08x}  {hexs:<{width * 3}} {text}")
    return "\n".join(lines)


def _strings(path: Path, limit: int = 60) -> list[str]:
    with open(path, "rb") as f:
        data = f.read(256 * 1024)
    found = re.findall(rb"[\x20-\x7e]{6,}", data)
    return [s.decode("ascii")[:120] for s in found[:limit]]


def report(path: str) -> dict:
    """Plain-data report (also used by tests and 'Copy report')."""
    p = Path(path)
    ident = identify(p)
    opts = engine.options_for(p) if not ident.risky else []
    rep = {
        "file": p.name,
        "size": ident.details.get("size", 0),
        "verdict": ident.summary,
        "format": ident.fmt,
        "confidence": round(ident.confidence, 2),
        "extension_says": ident.ext_fmt or "(none)",
        "mismatch": ident.mismatch,
        "risky": ident.risky,
        "entropy": round(ident.details.get("entropy", 0.0), 2),
        "candidates": [(c.fmt, round(c.confidence, 2), c.evidence) for c in ident.candidates],
        "conversions": [o.key for o in opts if o.kind in ("convert", "extract")],
    }
    if ident.fmt in ("zip", "cbz", "jar", "apk", "docx", "xlsx", "pptx", "epub", "odt", "ods"):
        try:
            with zipfile.ZipFile(p) as zf:
                rep["contents"] = zf.namelist()[:200]
        except Exception:
            pass
    return rep


class Inspector(QDialog):
    def __init__(self, path: str):
        super().__init__(None, Qt.WindowType.Window)
        self.path = Path(path)
        self.setWindowTitle(f"What is {self.path.name}?")
        self.resize(760, 560)
        rep = report(path)
        self.rep = rep
        lay = QVBoxLayout(self)

        title = QLabel(rep["verdict"])
        f = title.font()
        f.setPointSizeF(f.pointSizeF() * 1.5)
        f.setWeight(QFont.Weight.DemiBold)
        title.setFont(f)
        title.setWordWrap(True)
        lay.addWidget(title)
        conf = f"{rep['confidence']:.0%} confident" if rep["format"] else "not identified"
        facts = (f"{self.path.name} · {rep['size']:,} bytes · extension says "
                 f"“{rep['extension_says']}” · {conf} · entropy {rep['entropy']} bits/byte")
        sub = QLabel(facts)
        sub.setWordWrap(True)
        sub.setStyleSheet("color: palette(placeholder-text);")
        lay.addWidget(sub)
        if rep["risky"]:
            warn = QLabel("⚠  This file won't be converted: " + rep["risky"])
            warn.setWordWrap(True)
            warn.setStyleSheet("color: #e5484d; font-weight: 600;")
            lay.addWidget(warn)
        elif rep["conversions"]:
            conv = QLabel("Can become: " + ", ".join(k.upper() for k in rep["conversions"]))
            conv.setWordWrap(True)
            lay.addWidget(conv)
        else:
            hint = ("No converter understands this file yet."
                    + ("" if magika_available() else
                       " Installing the Magika AI detector (Settings › Engines) may identify it."))
            h = QLabel(hint)
            h.setWordWrap(True)
            lay.addWidget(h)

        tabs = QTabWidget()
        lay.addWidget(tabs, 1)

        table = QTableWidget(len(rep["candidates"]), 3)
        table.setHorizontalHeaderLabels(["Could be", "Confidence", "Evidence"])
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for r, (fmt, c, ev) in enumerate(rep["candidates"]):
            table.setItem(r, 0, QTableWidgetItem(f"{name_of(fmt)} (.{fmt})"))
            table.setItem(r, 1, QTableWidgetItem(f"{c:.0%}"))
            table.setItem(r, 2, QTableWidgetItem(ev))
        table.resizeColumnsToContents()
        tabs.addTab(table, "Evidence")

        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        hexv = QPlainTextEdit(_hexdump(identify(self.path).details.get("head", b"")))
        hexv.setReadOnly(True)
        hexv.setFont(mono)
        tabs.addTab(hexv, "First bytes")
        try:
            strs = QPlainTextEdit("\n".join(_strings(self.path)) or "(no readable text found)")
        except OSError as e:
            strs = QPlainTextEdit(str(e))
        strs.setReadOnly(True)
        strs.setFont(mono)
        tabs.addTab(strs, "Readable text")
        if rep.get("contents"):
            cont = QPlainTextEdit("\n".join(rep["contents"]))
            cont.setReadOnly(True)
            cont.setFont(mono)
            tabs.addTab(cont, f"Contents ({len(rep['contents'])})")

        row = QHBoxLayout()
        copy = QPushButton("Copy report")
        copy.clicked.connect(self._copy)
        row.addWidget(copy)
        if not rep["risky"]:
            describe = QPushButton("Describe this format…")
            describe.setToolTip("Create a format description pre-filled from this file, so "
                                "ShapeShifter recognises and converts files like it")
            describe.clicked.connect(self._describe)
            row.addWidget(describe)
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)

    def _describe(self):
        import os
        from PySide6.QtWidgets import QMessageBox
        from . import custom
        path = custom.create_template(self.path)
        os.startfile(path)
        QMessageBox.information(
            self, "Describe this format",
            f"A description was created at\n{path}\n\nIt's pre-filled with this file's first "
            "bytes. Choose how the file should be read, save it, then press Reload in "
            "Settings › Formats & mappings.")

    def _copy(self):
        r = self.rep
        lines = [f"{k}: {v}" for k, v in r.items() if k not in ("candidates", "contents")]
        lines += [f"candidate: {f} {c:.0%} — {e}" for f, c, e in r["candidates"]]
        QGuiApplication.clipboard().setText("\n".join(lines))
