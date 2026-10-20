"""Settings window: hotkeys, format mappings, engines, wheel appearance, general options.

Runs inside the tray process (so hotkey changes apply instantly). Every change is saved
to config.json right away; the tray app watches that file and reloads.
"""

from __future__ import annotations

import copy
import logging
import math
import os
import sys
import webbrowser
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPointF, QProcess, QRectF, QSize, Qt, QTimer, \
    QVariantAnimation, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox,
                               QColorDialog, QComboBox,
                               QDialog, QDialogButtonBox, QFormLayout, QFrame, QGridLayout,
                               QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit,
                               QPushButton, QRadioButton, QScrollArea, QSizePolicy, QSlider,
                               QSpinBox, QStackedWidget, QTableWidget, QTableWidgetItem,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import APP_NAME, __version__, config, engine, themes
from .formats import CATEGORIES, CATEGORY_COLORS, category
from .wheel import WheelView

log = logging.getLogger(__name__)

_window: "SettingsWindow | None" = None


def settings_open() -> bool:
    return _window is not None and _window.isVisible()


def show_settings(controller=None):
    global _window
    if _window is None:
        _window = SettingsWindow(controller)
    _window.showNormal()
    _window.raise_()
    _window.activateWindow()
    try:
        from .winutil import force_foreground
        force_foreground(int(_window.winId()))
    except Exception:
        pass


# --------------------------------------------------------------------- helpers

def describe_pair(src: str, dst: str) -> str:
    sc, dc = category(src), category(dst)
    if src == "svg":
        return "Renders the vector drawing (2× resolution)" if dst != "pdf" \
            else "Vector drawing saved as a PDF page"
    if sc == "image" and dst == "pdf":
        return "Places the image on a single PDF page (useful for printing, forms, sharing)"
    if src == "pdf" and dc == "image":
        return "Multi-page TIFF" if dst == "tiff" else "One image file per page"
    if src == "pdf" and dst == "txt":
        return "Extracts the text layer"
    if sc == "document" and dst in ("pdf", "txt") and src in ("epub", "mobi", "fb2", "xps", "oxps", "cbz"):
        return "E-book / document → " + ("PDF" if dst == "pdf" else "plain text")
    if sc == "video" and dc == "audio":
        return "Extracts the soundtrack"
    if sc == "video" and dst == "gif":
        return "Animated GIF, up to 640 px wide at 12 fps, no sound"
    if src == "gif" and dc == "video":
        return "Animated GIF → video clip"
    if sc == "video" and dc == "video":
        return "Lossless remux when possible, otherwise re-encode" if dst in ("mp4", "mkv", "mov") \
            else "Re-encodes the video"
    if sc == "audio":
        return "Re-encodes the audio"
    if sc == "data":
        return "Table ↔ records conversion (first sheet for CSV/TSV)"
    if sc == "image" and dc == "image":
        if dst in ("jpg", "bmp"):
            return "Re-encodes; transparency is flattened onto white"
        if dst == "ico":
            return "Windows icon with sizes 16–256 px"
        return "Re-encodes the image (animation kept where supported)"
    return "Document conversion"


VK_NAMES = {0x20: "space", 0x0D: "enter", 0x09: "tab", 0x2D: "insert", 0x2E: "delete",
            0x24: "home", 0x23: "end", 0x21: "pageup", 0x22: "pagedown", 0x13: "pause",
            0xC0: "`", 0xBD: "-", 0xBB: "=", 0xDB: "[", 0xDD: "]", 0xBA: ";", 0xDE: "'",
            0xBC: ",", 0xBE: ".", 0xBF: "/", 0xDC: "\\"}
MODIFIER_VKS = {0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}


def vk_name(vk: int) -> str | None:
    if 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39:
        return chr(vk).lower()
    if 0x70 <= vk <= 0x87:
        return f"f{vk - 0x6F}"
    return VK_NAMES.get(vk)


def hotkey_available(spec: str) -> bool:
    import ctypes
    from .winutil import parse_hotkey
    try:
        mods, vk = parse_hotkey(spec)
    except ValueError:
        return False
    user32 = ctypes.windll.user32
    if user32.RegisterHotKey(None, 0xBFFF, mods, vk):
        user32.UnregisterHotKey(None, 0xBFFF)
        return True
    return False


def theme_icon(theme: themes.Theme, cfg: dict, size: int = 64) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = QPointF(size / 2, size / 2)
    r = size / 2 - 2
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(theme.ring))
    p.drawEllipse(c, r, r)
    cats = list(CATEGORY_COLORS)
    span = 360 / len(cats)
    rect = QRectF(c.x() - r + 3, c.y() - r + 3, 2 * r - 6, 2 * r - 6)
    for i, cat in enumerate(cats):
        col = QColor(themes.segment_color(theme, cfg, cat))
        col.setAlpha(230 if i == 0 else max(90, theme.seg_idle_alpha * 3))
        p.setBrush(col)
        p.drawPie(rect, int((90 - i * span - 4) * 16), int(-(span - 8) * 16))
    p.setBrush(QColor(theme.hub))
    if theme.outline:
        p.setPen(QColor(theme.outline))
    p.drawEllipse(c, r * 0.42, r * 0.42)
    p.end()
    return QIcon(pm)


def labeled_slider(lo: int, hi: int, value: int, fmt, step: int = 1):
    """A slider with a live value label. Returns (container, slider)."""
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    s = QSlider(Qt.Orientation.Horizontal)
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setPageStep(step * 5)
    s.setValue(value)
    lab = QLabel(fmt(value))
    lab.setMinimumWidth(60)
    lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    s.valueChanged.connect(lambda v: lab.setText(fmt(v)))
    lay.addWidget(s, 1)
    lay.addWidget(lab)
    return box, s


def heading(text: str, sub: str = "") -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 8)
    t = QLabel(text)
    f = t.font()
    f.setPointSizeF(f.pointSizeF() * 1.6)
    f.setWeight(QFont.Weight.DemiBold)
    t.setFont(f)
    lay.addWidget(t)
    if sub:
        s = QLabel(sub)
        s.setWordWrap(True)
        s.setStyleSheet("color: palette(placeholder-text);")
        lay.addWidget(s)
    return w


# ------------------------------------------------------------------- preview

class PreviewWheel(WheelView):
    """Interactive preview: hover works; clicking plays a fake conversion."""

    SAMPLES = {
        "Photo (JPG)": "holiday.jpg",
        "Video (MP4)": "clip.mp4",
        "Song (MP3)": "song.mp3",
        "Spreadsheet (CSV)": "table.csv",
        "Document (PDF)": "report.pdf",
        "Data, many targets (JSON)": "data.json",
        "Subtitles (SRT)": "movie.srt",
    }

    def replay(self):
        """Play the open animation (rings rotate in one after another)."""
        anim_ms = int(self.cfg.get("animation_ms", 170)) or 1
        rings = max(1, len(self.rings))
        self._reveal = QVariantAnimation(self, startValue=0.0, endValue=1.0,
                                         duration=int(anim_ms * (1 + 0.45 * (rings - 1)) * 1.4))
        self._reveal.valueChanged.connect(lambda v: (setattr(self, "_t", float(v)), self.update()))
        self._reveal.start()

    def __init__(self, cfg: dict, parent=None):
        super().__init__(cfg, parent)
        self.setMinimumSize(380, 430)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.sample = "holiday.jpg"
        self._anim = None
        self.refresh()

    def apply_cfg(self, cfg: dict):
        super().apply_cfg(cfg)
        if hasattr(self, "sample"):
            self.refresh()

    def refresh(self):
        path = f"C:\\demo\\{self.sample}"
        self.set_content([path], engine.visible_options([path], self.cfg))

    def set_sample(self, name: str):
        self.sample = name
        self.reset_state()
        self.refresh()

    def reset_state(self):
        if self._anim:
            self._anim.stop()
        self.state, self.chosen, self.progress, self.message = "choose", None, 0.0, ""
        self.update()

    def paint_background(self, p: QPainter):
        g = QLinearGradient(0, 0, self.width(), self.height())
        g.setColorAt(0, QColor("#5b6b8c"))
        g.setColorAt(1, QColor("#2a3142"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 12, 12)

    def mouseReleaseEvent(self, e):
        if self.state == "choose" and self.hover >= 0:
            self.on_select(self.targets[self.hover])
        elif self.state in ("done", "error"):
            self.reset_state()

    def on_select(self, target: str):
        self.show_state("busy", target)

    def show_state(self, state: str, target: str | None = None):
        self.reset_state()
        if state == "choose":
            return
        target = target or (self.targets[0] if self.targets else "png")
        self.chosen = target
        stem = self.sample.rsplit(".", 1)[0]
        if state == "busy":
            self.state = "busy"
            self._anim = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=1400,
                                           easingCurve=QEasingCurve.Type.InOutQuad)
            self._anim.valueChanged.connect(lambda v: (setattr(self, "progress", float(v)),
                                                       self.update()))
            self._anim.finished.connect(lambda: self.show_state("done", target))
            self._anim.start()
        elif state == "done":
            self.state = "done"
            self.message, self.message_color = f"{stem}.{target}", QColor(self.theme.text)
            QTimer.singleShot(int(self.cfg.get("done_delay_ms", 900)) + 600,
                              lambda: self.state == "done" and self.reset_state())
        elif state == "error":
            self.state = "error"
            self.message = "Example error: the file is damaged or unsupported"
            self.message_color = QColor("#e5484d")
        self.update()


# ---------------------------------------------------------------------- pages

class Page(QScrollArea):
    def __init__(self, win: "SettingsWindow"):
        super().__init__()
        self.win = win
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        self.body = QVBoxLayout(inner)
        self.body.setContentsMargins(28, 22, 28, 22)
        self.body.setSpacing(14)
        self.setWidget(inner)

    @property
    def cfg(self) -> dict:
        return self.win.cfg

    def set(self, key, value):
        self.win.set_value(key, value)

    def refresh(self):
        pass


class GeneralPage(Page):
    def __init__(self, win):
        super().__init__(win)
        b = self.body
        b.addWidget(heading("General", "What happens to files, conversion quality and Windows integration."))

        g = QGroupBox("After a successful conversion, the original file…")
        lay = QVBoxLayout(g)
        self.orig = QButtonGroup(self)
        for key, label in (("recycle", "Moves to the Recycle Bin (recommended — easy to undo)"),
                           ("keep", "Stays where it is (you get both files)"),
                           ("delete", "Is deleted permanently")):
            r = QRadioButton(label)
            r.setChecked(self.cfg["original"] == key)
            r.toggled.connect(lambda on, k=key: on and self.set("original", k))
            self.orig.addButton(r)
            lay.addWidget(r)
        mt = QCheckBox("Converted file keeps the original's modified date")
        mt.setChecked(self.cfg.get("preserve_mtime", True))
        mt.toggled.connect(lambda v: self.set("preserve_mtime", v))
        lay.addWidget(mt)
        nat = QCheckBox("Prefer ShapeShifter's own converters (PNG, BMP, TGA, QOI, ICO, GIF/JPEG "
                        "decoding, PDF, Markdown/HTML/text, WAV/AIFF, XLSX) over libraries")
        nat.setToolTip("Native converters need no third-party code but are slower on big files. "
                       "Anything they can't handle still falls back to the libraries.")
        nat.setChecked(bool(self.cfg.get("prefer_native", False)))
        nat.toggled.connect(self._toggle_native)
        lay.addWidget(nat)
        b.addWidget(g)

        g = QGroupBox("Quality")
        form = QFormLayout(g)
        for key, label, lo in (("jpeg_quality", "JPEG quality", 50), ("webp_quality", "WEBP quality", 50),
                               ("avif_quality", "AVIF / HEIC quality", 30)):
            w, s = labeled_slider(lo, 100, int(self.cfg[key]), lambda v: f"{v}")
            s.valueChanged.connect(lambda v, k=key: self.set(k, v))
            form.addRow(label, w)
        dpi = QComboBox()
        for v in (72, 96, 150, 200, 300, 600):
            dpi.addItem(f"{v} DPI", v)
        dpi.setCurrentIndex(max(0, dpi.findData(int(self.cfg["render_dpi"]))))
        dpi.currentIndexChanged.connect(lambda _: self.set("render_dpi", dpi.currentData()))
        form.addRow("PDF → image resolution", dpi)
        b.addWidget(g)

        g = QGroupBox("Text recognition (OCR)")
        form = QFormLayout(g)
        self.ocr_lang = QComboBox()
        self._fill_ocr_languages()
        self.ocr_lang.currentIndexChanged.connect(
            lambda _: self.set("ocr_language", self.ocr_lang.currentData() or "auto"))
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.ocr_lang, 1)
        detect = QPushButton("Detect languages")
        detect.clicked.connect(self._detect_ocr_languages)
        rl.addWidget(detect)
        form.addRow("Language", row)
        self.ocr_note = QLabel("Uses Windows' built-in OCR; add languages in Windows Settings › Time "
                               "& language › Language & region. “Automatic” uses your Windows languages.")
        self.ocr_note.setWordWrap(True)
        self.ocr_note.setStyleSheet("color: palette(placeholder-text);")
        form.addRow(self.ocr_note)
        b.addWidget(g)

        g = QGroupBox("Windows integration")
        lay = QVBoxLayout(g)
        from . import shell
        st = QCheckBox("Start automatically when I sign in to Windows (needed for hotkeys)")
        st.setChecked(shell.startup_enabled())
        st.toggled.connect(shell.set_startup)
        lay.addWidget(st)
        self.ctx = QCheckBox("Show “Convert to…” in File Explorer's right-click menu")
        self.ctx.setChecked(shell.context_menu_registered())
        self.ctx.toggled.connect(self._toggle_ctx)
        lay.addWidget(self.ctx)
        note = QLabel("On Windows 11 the entry is under “Show more options” (or Shift+right-click).")
        note.setStyleSheet("color: palette(placeholder-text);")
        lay.addWidget(note)
        b.addWidget(g)
        b.addStretch(1)

    def _fill_ocr_languages(self):
        self.ocr_lang.blockSignals(True)
        self.ocr_lang.clear()
        self.ocr_lang.addItem("Automatic (Windows languages)", "auto")
        for tag, name in config.load_state().get("ocr_langs", []):
            self.ocr_lang.addItem(f"{name} ({tag})", tag)
        current = self.cfg.get("ocr_language", "auto")
        i = self.ocr_lang.findData(current)
        if i < 0:                                   # a language we haven't detected: keep it visible
            self.ocr_lang.addItem(f"{current} (not detected)", current)
            i = self.ocr_lang.count() - 1
        self.ocr_lang.setCurrentIndex(i)
        self.ocr_lang.blockSignals(False)

    def _detect_ocr_languages(self):
        from PySide6.QtGui import QCursor
        from . import ocr
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            info = ocr.status()
        finally:
            QApplication.restoreOverrideCursor()
        config.save_state(ocr_langs=[list(t) for t in info["languages"]])
        self._fill_ocr_languages()
        self.ocr_note.setText(
            f"Found {len(info['languages'])} OCR language(s): "
            + ", ".join(n for _, n in info["languages"]) if info["ok"] else info["error"])

    def _toggle_native(self, on: bool):
        from . import planner
        self.set("prefer_native", on)
        self.win.flush()
        planner.reset()

    def _toggle_ctx(self, on: bool):
        from . import shell
        if on:
            shell.register_context_menu()
        else:
            shell.unregister_context_menu()


class HotkeyEdit(QLineEdit):
    """Captures a key combination and stores it as e.g. 'ctrl+alt+c'."""

    changed = Signal(str)

    def __init__(self, spec: str = ""):
        super().__init__()
        self.setReadOnly(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.setPlaceholderText("Click here, then press keys — or use a mouse button here")
        self.setMinimumHeight(34)
        self.spec = spec
        self.setText(_pretty(spec))

    @staticmethod
    def _mods(mods) -> list[str]:
        parts = []
        if mods & Qt.KeyboardModifier.ControlModifier:
            parts.append("ctrl")
        if mods & Qt.KeyboardModifier.AltModifier:
            parts.append("alt")
        if mods & Qt.KeyboardModifier.ShiftModifier:
            parts.append("shift")
        if mods & Qt.KeyboardModifier.MetaModifier:
            parts.append("win")
        return parts

    def _set_spec(self, spec: str):
        self.spec = spec
        self.setText(_pretty(spec))
        self.changed.emit(spec)

    def mousePressEvent(self, e):
        parts = self._mods(e.modifiers())
        btn = e.button()
        names = {Qt.MouseButton.MiddleButton: "middle", Qt.MouseButton.XButton1: "x1",
                 Qt.MouseButton.XButton2: "x2", Qt.MouseButton.LeftButton: "left",
                 Qt.MouseButton.RightButton: "right"}
        name = names.get(btn)
        if name in ("left", "right") and not parts:
            # A plain left click just focuses the box; plain left/right can't be a trigger.
            super().mousePressEvent(e)
            self.setFocus()
            if name == "right":
                self.setText("Hold Ctrl, Alt, Shift or Win with left/right click")
            return
        if name:
            self.setFocus()
            self._set_spec("+".join(parts + [f"mouse:{name}"]))
            e.accept()

    def mouseReleaseEvent(self, e):
        e.accept()

    def mouseDoubleClickEvent(self, e):
        self.mousePressEvent(e)

    def wheelEvent(self, e):
        parts = self._mods(e.modifiers())
        if not parts:
            self.setText("Hold Ctrl, Alt, Shift or Win while scrolling")
            e.accept()
            return
        delta = e.angleDelta().y() or e.angleDelta().x()  # Alt+wheel reports horizontally
        if delta:
            self._set_spec("+".join(parts + ["wheel:up" if delta > 0 else "wheel:down"]))
        e.accept()

    def keyPressEvent(self, e):
        vk = e.nativeVirtualKey()
        parts = self._mods(e.modifiers())
        if vk in MODIFIER_VKS:
            self.setText(_pretty("+".join(parts)) + "+…")
            return
        if not parts and e.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            self.spec = ""
            self.setText("")
            self.changed.emit("")
            return
        if not parts and e.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            e.ignore()  # let the dialog accept / cancel
            return
        name = vk_name(vk)
        if not name:
            return
        if not parts and not name.startswith("f"):
            self.setText("Add Ctrl, Alt, Shift or Win")
            return
        self.spec = "+".join(parts + [name])
        self.setText(_pretty(self.spec))
        self.changed.emit(self.spec)

    def keyReleaseEvent(self, e):
        if self.text().endswith("…") or self.text().startswith(("Add ", "Hold ")):
            self.setText(_pretty(self.spec))


def _pretty(spec: str) -> str:
    from .app import pretty_keys
    return pretty_keys(spec) if spec else ""


ACTIONS = [("wheel", "Open the conversion wheel"), ("repeat", "Repeat last conversion"),
           ("convert", "Convert straight to a format…")]


class HotkeyDialog(QDialog):
    def __init__(self, parent, binding: dict, others: list[str]):
        super().__init__(parent)
        self.setWindowTitle("Hotkey")
        self.setMinimumWidth(420)
        self.others = others
        form = QFormLayout(self)
        self.keys = HotkeyEdit(binding.get("keys", ""))
        form.addRow("Shortcut", self.keys)
        self.status = QLabel(" ")
        self.status.setWordWrap(True)
        form.addRow("", self.status)
        self.action = QComboBox()
        for key, label in ACTIONS:
            self.action.addItem(label, key)
        self.action.setCurrentIndex(max(0, self.action.findData(binding.get("action", "wheel"))))
        form.addRow("Action", self.action)
        self.target = QComboBox()
        all_targets = sorted({t for ts in engine.all_pairs().values() for t, *_ in ts
                              if t != "extract"})
        for t in all_targets:
            self.target.addItem(t.upper(), t)
        self.target.setCurrentIndex(max(0, self.target.findData(binding.get("target", "png"))))
        form.addRow("Target format", self.target)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                        | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        form.addRow(self.buttons)
        self.keys.changed.connect(self._validate)
        self.action.currentIndexChanged.connect(self._validate)
        self._validate()

    def _validate(self, *_):
        self.target.setEnabled(self.action.currentData() == "convert")
        from .mousehook import is_mouse_spec, parse_mouse_spec
        spec = self.keys.spec
        ok = bool(spec)
        if not spec:
            msg = "Click the box above, then press keys — or middle-click / side-button / Ctrl+scroll on it."
        elif spec in self.others:
            msg, ok = "⚠ Already used by another binding.", False
        elif is_mouse_spec(spec):
            try:
                parse_mouse_spec(spec)
                msg = ("✓ Mouse trigger — works over File Explorer and the desktop; "
                       "other apps keep their normal behaviour.")
            except ValueError as e:
                msg, ok = f"⚠ {e}", False
        elif not hotkey_available(spec):
            msg = "⚠ Windows or another app already uses this combination — it may not work."
        else:
            msg = "✓ Available"
        self.status.setText(msg)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)

    def binding(self) -> dict:
        b = {"keys": self.keys.spec, "action": self.action.currentData()}
        if b["action"] == "convert":
            b["target"] = self.target.currentData()
        return b


class HotkeysPage(Page):
    def __init__(self, win):
        super().__init__(win)
        b = self.body
        b.addWidget(heading("Hotkeys", "Select files in File Explorer (or on the desktop), then press "
                                       "a shortcut. Shortcuts can be key combinations or mouse "
                                       "buttons — e.g. middle-click a file to open the wheel for it. "
                                       "Double-click a row to change it."))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Shortcut", "Action", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(lambda _: self._edit())
        self.table.setMinimumHeight(220)
        b.addWidget(self.table)

        row = QHBoxLayout()
        for text, fn in (("Add…", self._add), ("Edit…", self._edit), ("Remove", self._remove),
                         ("Restore defaults", self._defaults)):
            btn = QPushButton(text)
            btn.clicked.connect(fn)
            row.addWidget(btn)
        row.addStretch(1)
        b.addLayout(row)

        g = QGroupBox("Keys on the wheel")
        grid = QGridLayout(g)
        for i, (k, v) in enumerate([("Mouse move + click", "Choose a format"),
                                    ("← → ↑ ↓ / Tab", "Move between formats"),
                                    ("Letter key", "Jump to a format starting with it"),
                                    ("Enter / Space", "Convert to the highlighted format"),
                                    ("Esc / click centre", "Cancel (Esc also stops a running conversion)")]):
            kl = QLabel(k)
            f = kl.font()
            f.setWeight(QFont.Weight.DemiBold)
            kl.setFont(f)
            grid.addWidget(kl, i, 0)
            grid.addWidget(QLabel(v), i, 1)
        grid.setColumnStretch(1, 1)
        b.addWidget(g)
        b.addStretch(1)
        self.refresh()

    def refresh(self):
        from .app import describe_binding, pretty_keys
        status = {}
        ctl = self.win.controller
        if ctl is not None and hasattr(ctl, "binding_status"):
            for bd, ok in ctl.binding_status:
                status[bd.get("keys")] = ok
        hotkeys = self.cfg.get("hotkeys", [])
        self.table.setRowCount(len(hotkeys))
        for i, bd in enumerate(hotkeys):
            self.table.setItem(i, 0, QTableWidgetItem(pretty_keys(bd.get("keys", ""))))
            self.table.setItem(i, 1, QTableWidgetItem(describe_binding(bd)))
            if not hasattr(ctl, "binding_status"):
                st = "Tray app not running"
            elif bd.get("keys") in status:
                st = "✓ Active" if status[bd["keys"]] else "⚠ In use by another app"
            else:
                st = "…"
            self.table.setItem(i, 2, QTableWidgetItem(st))

    def _with_suspended(self, fn):
        ctl = self.win.controller
        if hasattr(ctl, "suspend_hotkeys"):
            ctl.suspend_hotkeys(True)
        try:
            return fn()
        finally:
            if hasattr(ctl, "suspend_hotkeys"):
                ctl.suspend_hotkeys(False)

    def _save(self, hotkeys):
        self.set("hotkeys", hotkeys)
        self.win.flush()
        ctl = self.win.controller
        if hasattr(ctl, "reload_config"):
            ctl.reload_config()
        self.refresh()

    def _open_dialog(self, binding, others):
        dlg = HotkeyDialog(self, binding, others)
        return self._with_suspended(lambda: dlg.binding() if dlg.exec() else None)

    def _add(self):
        hotkeys = list(self.cfg.get("hotkeys", []))
        b = self._open_dialog({"action": "convert", "target": "png"}, [h["keys"] for h in hotkeys])
        if b:
            self._save(hotkeys + [b])

    def _edit(self):
        row = self.table.currentRow()
        hotkeys = list(self.cfg.get("hotkeys", []))
        if not 0 <= row < len(hotkeys):
            return
        others = [h["keys"] for i, h in enumerate(hotkeys) if i != row]
        b = self._open_dialog(hotkeys[row], others)
        if b:
            hotkeys[row] = b
            self._save(hotkeys)

    def _remove(self):
        row = self.table.currentRow()
        hotkeys = list(self.cfg.get("hotkeys", []))
        if 0 <= row < len(hotkeys):
            del hotkeys[row]
            self._save(hotkeys)

    def _defaults(self):
        self._save(copy.deepcopy(config.DEFAULT_HOTKEYS))


class AppearancePage(Page):
    def __init__(self, win):
        super().__init__(win)
        b = self.body
        b.addWidget(heading("Wheel", "Theme, size and behaviour. The preview is live — hover and click it."))
        split = QHBoxLayout()
        split.setSpacing(24)
        b.addLayout(split, 1)
        left = QVBoxLayout()
        split.addLayout(left, 5)

        # Themes
        g = QGroupBox("Theme")
        gl = QVBoxLayout(g)
        self.theme_list = QListWidget()
        self.theme_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.theme_list.setIconSize(QSize(56, 56))
        self.theme_list.setGridSize(QSize(96, 92))
        self.theme_list.setMovement(QListWidget.Movement.Static)
        self.theme_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.theme_list.setFixedHeight(200)
        self.theme_list.setWordWrap(True)
        self.theme_list.currentItemChanged.connect(self._theme_changed)
        gl.addWidget(self.theme_list)
        row = QHBoxLayout()
        self.by_cat = QCheckBox("Colour segments by file category")
        self.by_cat.setChecked(self.cfg.get("color_by_category", True))
        self.by_cat.toggled.connect(self._by_cat_changed)
        row.addWidget(self.by_cat)
        row.addStretch(1)
        self.accent_btn = QPushButton("Accent colour…")
        self.accent_btn.clicked.connect(self._pick_accent)
        row.addWidget(self.accent_btn)
        gl.addLayout(row)
        left.addWidget(g)

        # Size & look
        g = QGroupBox("Size and look")
        form = QFormLayout(g)
        self._slider(form, "Wheel size", "wheel_scale", 60, 160, 100, lambda v: f"{v}%", scale=100)
        self._slider(form, "Text size", "label_scale", 70, 150, 100, lambda v: f"{v}%", scale=100)
        self._slider(form, "Opacity", "wheel_opacity", 40, 100, 100, lambda v: f"{v}%", scale=100)
        self._slider(form, "Animation", "animation_ms", 0, 400, 170,
                     lambda v: "Off" if v == 0 else f"{v} ms", step=10)
        sp = QSpinBox()
        sp.setRange(4, 100)
        sp.setToolTip("Up to 12 fit one ring; more spill into outer rings (10, 16, 22, 28 …)")
        sp.setValue(int(self.cfg.get("max_segments", 16)))
        sp.valueChanged.connect(lambda v: self.set("max_segments", v))
        form.addRow("Most formats shown", sp)
        left.addWidget(g)

        # Behaviour
        g = QGroupBox("Behaviour")
        form = QFormLayout(g)
        pos = QComboBox()
        pos.addItem("At the mouse cursor", "cursor")
        pos.addItem("Centre of the screen", "center")
        pos.setCurrentIndex(max(0, pos.findData(self.cfg.get("position", "cursor"))))
        pos.currentIndexChanged.connect(lambda _: self.set("position", pos.currentData()))
        form.addRow("Open", pos)
        for key, label in (("show_category_labels", "Show the category name on the hovered format"),
                           ("show_caption", "Show the file name below the wheel"),
                           ("close_on_focus_loss", "Close when I click somewhere else")):
            cb = QCheckBox(label)
            cb.setChecked(bool(self.cfg.get(key, True)))
            cb.toggled.connect(lambda v, k=key: self.set(k, v))
            form.addRow(cb)
        dwell_on = QCheckBox("Convert by hovering (no click needed)")
        dwell_on.setChecked(int(self.cfg.get("dwell_select_ms", 0)) > 0)
        form.addRow(dwell_on)
        dwell_w, self.dwell = labeled_slider(300, 2000, max(300, int(self.cfg.get("dwell_select_ms", 0))
                                                            or 700), lambda v: f"{v} ms", step=50)
        self.dwell.setEnabled(dwell_on.isChecked())
        form.addRow("Hover time", dwell_w)

        def dwell_changed(*_):
            self.dwell.setEnabled(dwell_on.isChecked())
            self.set("dwell_select_ms", self.dwell.value() if dwell_on.isChecked() else 0)
        dwell_on.toggled.connect(dwell_changed)
        self.dwell.valueChanged.connect(dwell_changed)
        self._slider(form, "Show result for", "done_delay_ms", 300, 3000, 900,
                     lambda v: f"{v / 1000:.1f} s", step=100)
        left.addWidget(g)
        reset = QPushButton("Reset wheel settings")
        reset.clicked.connect(self._reset)
        left.addWidget(reset, 0, Qt.AlignmentFlag.AlignLeft)
        left.addStretch(1)

        # Preview
        right = QVBoxLayout()
        split.addLayout(right, 6)
        row = QHBoxLayout()
        row.addWidget(QLabel("Preview with"))
        self.sample = QComboBox()
        for label, name in PreviewWheel.SAMPLES.items():
            self.sample.addItem(label, name)
        self.sample.currentIndexChanged.connect(lambda _: self.preview.set_sample(self.sample.currentData()))
        row.addWidget(self.sample, 1)
        right.addLayout(row)
        self.preview = PreviewWheel(self.cfg)
        right.addWidget(self.preview, 1)
        row = QHBoxLayout()
        replay = QPushButton("Replay animation")
        replay.clicked.connect(lambda: self.preview.replay())
        row.addWidget(replay)
        for label, st in (("Choosing", "choose"), ("Converting", "busy"), ("Done", "done"),
                          ("Error", "error")):
            btn = QPushButton(label)
            btn.clicked.connect(lambda _=False, s=st: self.preview.show_state(s))
            row.addWidget(btn)
        right.addLayout(row)
        self._fill_themes()

    def _slider(self, form, label, key, lo, hi, default, fmt, step=1, scale=1):
        value = round(float(self.cfg.get(key, default / scale)) * scale)
        w, s = labeled_slider(lo, hi, value, fmt, step)
        s.valueChanged.connect(lambda v: self.set(key, v / scale if scale != 1 else v))
        form.addRow(label, w)
        return s

    def _fill_themes(self):
        self.theme_list.blockSignals(True)
        self.theme_list.clear()
        for t in themes.THEMES.values():
            item = QListWidgetItem(theme_icon(t, self.cfg), t.name)
            item.setData(Qt.ItemDataRole.UserRole, t.key)
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            self.theme_list.addItem(item)
            if t.key == self.cfg.get("theme", "midnight"):
                self.theme_list.setCurrentItem(item)
        self.theme_list.blockSignals(False)
        self._update_accent()

    def _update_accent(self):
        th = themes.resolve(self.cfg)
        self.by_cat.setEnabled(th.accent is None)
        enabled = th.accent is None and not self.cfg.get("color_by_category", True)
        self.accent_btn.setEnabled(enabled)
        col = self.cfg.get("accent_color", "#7c8cff")
        self.accent_btn.setStyleSheet(f"QPushButton {{ border-left: 14px solid {col}; padding-left: 8px; }}"
                                      if enabled else "")

    def _theme_changed(self, item, _prev):
        if item:
            self.set("theme", item.data(Qt.ItemDataRole.UserRole))
            self._update_accent()

    def _by_cat_changed(self, v):
        self.set("color_by_category", v)
        self._fill_themes()

    def _pick_accent(self):
        c = QColorDialog.getColor(QColor(self.cfg.get("accent_color", "#7c8cff")), self, "Accent colour")
        if c.isValid():
            self.set("accent_color", c.name())
            self._fill_themes()

    def _reset(self):
        keys = ["theme", "color_by_category", "accent_color", "wheel_scale", "label_scale",
                "wheel_opacity", "animation_ms", "position", "show_category_labels", "show_caption",
                "dwell_select_ms", "close_on_focus_loss", "done_delay_ms", "max_segments"]
        for k in keys:
            self.cfg[k] = copy.deepcopy(config.DEFAULTS[k])
        self.win.flush()
        self.win.rebuild_page(self)

    def refresh(self):
        self.preview.apply_cfg(self.cfg)


class FormatsPage(Page):
    def __init__(self, win):
        super().__init__(win)
        b = self.body
        b.addWidget(heading("Formats & mappings",
                            "Every file type the converter understands and what it can become. "
                            "Untick a conversion to hide it from the wheel (it still works from the "
                            "command line). Right-click a target to hide or show it for every source."))
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter, e.g. “jpg” or “pdf”")
        self.search.textChanged.connect(self._filter)
        row.addWidget(self.search, 2)
        self.cat = QComboBox()
        self.cat.addItem("All categories", "")
        for c in CATEGORIES:
            if c != "program":
                self.cat.addItem(c.capitalize(), c)
        self.cat.currentIndexChanged.connect(self._filter)
        row.addWidget(self.cat, 1)
        expand = QPushButton("Expand all")
        expand.clicked.connect(lambda: self.tree.expandAll())
        row.addWidget(expand)
        show_all = QPushButton("Show everything")
        show_all.clicked.connect(self._show_all)
        row.addWidget(show_all)
        ident = QPushButton("Identify a file…")
        ident.setToolTip("Pick any file to see what it really is and what it can become")
        ident.clicked.connect(self._identify_file)
        row.addWidget(ident)
        b.addLayout(row)
        self.summary = QLabel()
        b.addWidget(self.summary)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Format", "Category / engine", "What it does"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setStretchLastSection(True)
        self.tree.setMinimumHeight(420)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        b.addWidget(self.tree, 1)

        # ---- conversion graph: route finder + export
        g = QGroupBox("Conversion graph")
        gl = QVBoxLayout(g)
        from . import planner
        nodes, edges = planner.graph_nodes_edges()
        self.graph_info = QLabel(f"{len(nodes)} file types connected by {len(edges)} one-way "
                                 f"conversion steps ({sum(e.latent for e in edges)} only used "
                                 "inside routes). Multi-step conversions are found by searching "
                                 "this graph.")
        self.graph_info.setWordWrap(True)
        gl.addWidget(self.graph_info)
        row = QHBoxLayout()
        all_fmts = sorted(nodes - {"extract"})
        self.route_from, self.route_to = QComboBox(), QComboBox()
        for cb, default in ((self.route_from, "md"), (self.route_to, "pem")):
            cb.setEditable(True)
            cb.addItems(all_fmts)
            cb.setCurrentText(default)
            cb.setMinimumWidth(110)
        row.addWidget(QLabel("Find a route from"))
        row.addWidget(self.route_from)
        row.addWidget(QLabel("to"))
        row.addWidget(self.route_to)
        go = QPushButton("Find")
        go.clicked.connect(self._find_route)
        row.addWidget(go)
        row.addStretch(1)
        for text, fmt in (("Export graph (DOT)…", "dot"), ("Export graph (JSON)…", "json")):
            btn = QPushButton(text)
            btn.clicked.connect(lambda _=False, f=fmt: self._export_graph(f))
            row.addWidget(btn)
        gl.addLayout(row)
        self.route_result = QLabel("")
        self.route_result.setWordWrap(True)
        self.route_result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        gl.addWidget(self.route_result)
        b.addWidget(g)

        # ---- learned reverse conversions
        g = QGroupBox("Learned reverse conversions")
        gl = QVBoxLayout(g)
        self.learn_cb = QCheckBox("After converting A → B, offer B → A on B files next time "
                                  "(best effort — may lose data)")
        self.learn_cb.setChecked(bool(self.cfg.get("learn_reverse", True)))
        self.learn_cb.toggled.connect(lambda v: self.set("learn_reverse", v))
        gl.addWidget(self.learn_cb)
        self.learned_list = QListWidget()
        self.learned_list.setMaximumHeight(110)
        gl.addWidget(self.learned_list)
        row = QHBoxLayout()
        forget = QPushButton("Forget selected")
        forget.clicked.connect(self._forget_learned)
        clear = QPushButton("Forget all")
        clear.clicked.connect(self._clear_learned)
        row.addWidget(forget)
        row.addWidget(clear)
        row.addStretch(1)
        gl.addLayout(row)
        b.addWidget(g)

        g = QGroupBox("Custom formats — teach ShapeShifter a format without code")
        gl = QVBoxLayout(g)
        note = QLabel("Describe how to recognise a file (its first bytes or a text pattern) and how "
                      "to read it (it's really another format, has one embedded, or holds "
                      "records/lines that become a table). Its conversions then appear "
                      "automatically. Inspect a file and use “Describe this format…” to start "
                      "from that file.")
        note.setWordWrap(True)
        gl.addWidget(note)
        self.custom_list = QListWidget()
        self.custom_list.setMaximumHeight(120)
        self.custom_list.itemDoubleClicked.connect(
            lambda it: os.startfile(it.data(Qt.ItemDataRole.UserRole)))
        gl.addWidget(self.custom_list)
        row = QHBoxLayout()
        for text, fn in (("New description…", self._new_custom), ("Open folder", self._open_custom),
                         ("Reload", self._reload_custom)):
            btn = QPushButton(text)
            btn.clicked.connect(fn)
            row.addWidget(btn)
        row.addStretch(1)
        gl.addLayout(row)
        b.addWidget(g)
        self.refresh()

    def _fill_custom(self):
        from . import custom
        self.custom_list.clear()
        specs = custom.load_specs()
        if not specs:
            self.custom_list.addItem("(none yet)")
        for s in specs:
            text = (f"⚠ {s.path.name}: {s.error}" if s.error else
                    f"{s.name} (.{s.id}) — {s.kind}"
                    + (f" → {s.decode.get('as', '').upper()}" if s.kind in ("alias", "embedded") else
                       " → table"))
            it = QListWidgetItem(text)
            it.setData(Qt.ItemDataRole.UserRole, str(s.path))
            self.custom_list.addItem(it)

    def _find_route(self):
        from . import planner
        from .formats import canonical
        a, b = canonical(self.route_from.currentText()), canonical(self.route_to.currentText())
        opt = planner.route(a, b, conditional=True)
        if not opt:
            self.route_result.setText(f"No route from {a.upper()} to {b.upper()}.")
            return
        if planner.is_conditional(opt):
            self.route_result.setText(
                " → ".join([a.upper()] + [e.dst.upper() for e in opt.path])
                + f"\n⚠ {'; '.join(opt.warnings)}\nFor an actual file, ShapeShifter checks its "
                  "content: if it really is that format, the direct conversion is offered.")
            return
        steps = " → ".join([a.upper()] + [f"{e.dst.upper()} ({e.backend.name}"
                                          + (", latent" if e.latent else "") + ")" for e in opt.path])
        warn = f"\n⚠ {'; '.join(opt.warnings)}" if opt.warnings else ""
        shown = "" if opt in planner.plan(a).values() else \
            "\n(Not offered on the wheel by default; available once learned or from the command line.)"
        self.route_result.setText(f"{steps}{warn}{shown}")

    def _export_graph(self, fmt: str):
        from PySide6.QtWidgets import QFileDialog
        from . import planner
        path, _ = QFileDialog.getSaveFileName(self, "Export conversion graph",
                                              f"shapeshifter-graph.{fmt}",
                                              "Graphviz (*.dot)" if fmt == "dot" else "JSON (*.json)")
        if path:
            n = planner.export_graph(path, fmt)
            self.route_result.setText(f"Exported {n} conversion steps to {path}")

    def _fill_learned(self):
        from . import learned
        self.learned_list.clear()
        items = learned.pairs()
        if not items:
            self.learned_list.addItem("(nothing learned yet — convert something)")
        for a, b, info in items:
            it = QListWidgetItem(f"{a.upper()} → {b.upper()}   (learned from {b.upper()} → "
                                 f"{a.upper()}, used {info.get('count', 1)}×)")
            it.setData(Qt.ItemDataRole.UserRole, (a, b))
            self.learned_list.addItem(it)

    def _forget_learned(self):
        from . import learned
        it = self.learned_list.currentItem()
        if it and it.data(Qt.ItemDataRole.UserRole):
            learned.forget(*it.data(Qt.ItemDataRole.UserRole))
            self._fill_learned()

    def _clear_learned(self):
        from . import learned
        learned.clear()
        self._fill_learned()

    def _new_custom(self):
        from . import custom
        os.startfile(custom.create_template())
        self._fill_custom()

    def _open_custom(self):
        from . import custom
        custom.FORMATS_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(custom.FORMATS_DIR)

    def _reload_custom(self):
        from . import custom
        custom.reload()
        self.refresh()

    def refresh(self):
        self._fill_custom()
        self._fill_learned()
        hidden = set(self.cfg.get("hidden_pairs", []))
        self.tree.blockSignals(True)
        self.tree.clear()
        pairs = engine.all_pairs()
        n_pairs = n_hidden = 0
        order = {c: i for i, c in enumerate(CATEGORIES)}
        for src in sorted(pairs, key=lambda s: (order.get(category(s), 9), s)):
            targets = pairs[src]
            if not targets:
                continue
            top = QTreeWidgetItem([src.upper(), category(src).capitalize(),
                                   f"{len(targets)} target formats"])
            f = top.font(0)
            f.setWeight(QFont.Weight.DemiBold)
            top.setFont(0, f)
            top.setData(0, Qt.ItemDataRole.UserRole, src)
            top.setForeground(1, QColor(CATEGORY_COLORS.get(category(src), "#888")))
            top.setFlags(top.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
            for tgt, via, warnings in targets:
                what = describe_pair(src, tgt) if tgt != "extract" else "Unpacks into a folder"
                if warnings != "Converts without notable loss":
                    what += "  —  ⚠ " + warnings
                from .formats import label_of
                label = "EXTRACT" if tgt == "extract" else label_of(tgt)
                ch = QTreeWidgetItem([f"→ {label}", via, what])
                ch.setData(0, Qt.ItemDataRole.UserRole, tgt)
                ch.setToolTip(2, what)
                ch.setFlags(ch.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                off = f"{src}>{tgt}" in hidden
                ch.setCheckState(0, Qt.CheckState.Unchecked if off else Qt.CheckState.Checked)
                top.addChild(ch)
                n_pairs += 1
                n_hidden += off
            self.tree.addTopLevelItem(top)
        self.tree.blockSignals(False)
        self.summary.setText(f"{self.tree.topLevelItemCount()} source formats · {n_pairs} conversions"
                             + (f" · {n_hidden} hidden from the wheel" if n_hidden else ""))
        self._filter()

    def _filter(self, *_):
        q = self.search.text().strip().lower().lstrip(".")
        cat = self.cat.currentData()
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            src = top.data(0, Qt.ItemDataRole.UserRole)
            child_match = [q and q in top.child(j).data(0, Qt.ItemDataRole.UserRole)
                           for j in range(top.childCount())]
            visible = (not cat or category(src) == cat) and (not q or q in src or any(child_match))
            top.setHidden(not visible)
            if q and visible and not q in src:
                top.setExpanded(True)

    def _item_changed(self, item, col):
        if col != 0 or item.parent() is None:
            return
        src = item.parent().data(0, Qt.ItemDataRole.UserRole)
        tgt = item.data(0, Qt.ItemDataRole.UserRole)
        hidden = set(self.cfg.get("hidden_pairs", []))
        key = f"{src}>{tgt}"
        if item.checkState(0) == Qt.CheckState.Checked:
            hidden.discard(key)
        else:
            hidden.add(key)
        self.set("hidden_pairs", sorted(hidden))
        self._update_summary(hidden)

    def _update_summary(self, hidden):
        text = self.summary.text().split(" · ")
        n = sum(1 for _ in hidden)
        self.summary.setText(" · ".join(text[:2]) + (f" · {n} hidden from the wheel" if n else ""))

    def _context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None or item.parent() is None:
            return
        tgt = item.data(0, Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        menu.addAction(f"Hide {tgt.upper()} for every source").triggered.connect(
            lambda: self._set_target_everywhere(tgt, False))
        menu.addAction(f"Show {tgt.upper()} for every source").triggered.connect(
            lambda: self._set_target_everywhere(tgt, True))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _set_target_everywhere(self, tgt, show):
        hidden = set(self.cfg.get("hidden_pairs", []))
        for src, targets in engine.all_pairs().items():
            if any(t == tgt for t, *_ in targets):
                (hidden.discard if show else hidden.add)(f"{src}>{tgt}")
        self.set("hidden_pairs", sorted(hidden))
        self.refresh()

    def _show_all(self):
        self.set("hidden_pairs", [])
        self.refresh()

    def _identify_file(self):
        from PySide6.QtWidgets import QFileDialog
        from .inspector import show_inspector
        path, _ = QFileDialog.getOpenFileName(self, "Identify a file")
        if path:
            show_inspector(path)


ENGINE_INFO = {
    "images": ("Pillow", "Raster images: JPG, PNG, WEBP, AVIF, HEIC, GIF, BMP, TIFF, ICO, …", None, None),
    "mupdf": ("PyMuPDF", "PDF, SVG and e-books (EPUB, MOBI, XPS, CBZ)", None, None),
    "data": ("Built-in data converter", "CSV, TSV, XLSX, JSON, YAML, TOML, SQLite databases",
             None, None),
    "ocr": ("Text recognition (Windows OCR)",
            "Reads text from scans and photos: image → text, Markdown, HTML, Word or searchable "
            "PDF, and scanned PDFs → text or searchable PDF. Built into Windows 10/11; needs at "
            "least one OCR language (Settings › Time & language › Language & region).",
            None, "https://support.microsoft.com/windows/language-packs-for-windows-a5094319-a92d-18de-5b53-1cfc697cfca8"),
    "archives": ("Built-in archive converter",
                 "ZIP, 7z, TAR, TAR.GZ/BZ2/XZ and Extract here. Unsafe archives are refused.",
                 None, None),
    "native": ("ShapeShifter native converters",
               "Written from scratch, no third-party code: PNG, BMP, TGA, PPM, QOI, ICO, GIF and "
               "baseline JPEG decoding, PDF writing, Markdown ↔ HTML ↔ text, WAV ↔ AIFF.",
               None, None),
    "custom": ("Your format descriptions",
               "Formats you described in Settings › Formats & mappings › Custom formats.",
               None, None),
    "magika": ("Magika (optional AI file detector)",
               "Google's model for identifying files nothing else recognises. "
               "Used only as a last resort. About a 20 MB download.",
               "pip:magika", "https://github.com/google/magika"),
    "ffmpeg": ("ffmpeg", "All audio and video formats, video → GIF, soundtrack extraction",
               "Gyan.FFmpeg", "https://ffmpeg.org"),
    "pandoc": ("pandoc", "Markdown, HTML, DOCX, ODT, RST, EPUB, LaTeX",
               "JohnMacFarlane.Pandoc", "https://pandoc.org"),
    "libreoffice": ("LibreOffice", "Word, Excel and PowerPoint files ↔ PDF / OpenDocument",
                    "TheDocumentFoundation.LibreOffice", "https://www.libreoffice.org"),
}


class EnginesPage(Page):
    def __init__(self, win):
        super().__init__(win)
        b = self.body
        b.addWidget(heading("Engines", "Programs that do the actual converting. Installing an optional "
                                       "engine adds its formats to the wheel and right-click menu."))
        self.list = QVBoxLayout()
        self.list.setSpacing(10)
        b.addLayout(self.list)
        row = QHBoxLayout()
        btn = QPushButton("Re-detect engines")
        btn.clicked.connect(self._redetect)
        row.addWidget(btn)
        row.addStretch(1)
        b.addLayout(row)
        b.addStretch(1)
        self.refresh()

    def refresh(self):
        while self.list.count():
            w = self.list.takeAt(0).widget()
            if w:
                w.deleteLater()
        from .backends import ALL_BACKENDS
        from .backends.external import pandoc_path, soffice_path
        from .backends.media import ffmpeg_path
        from .identify import magika_available
        paths = {"ffmpeg": ffmpeg_path, "pandoc": pandoc_path, "libreoffice": soffice_path}
        entries = []
        for backend in ALL_BACKENDS:
            try:
                ok = backend.available()
            except Exception:
                ok = False
            entries.append((backend.name, ok))
        entries.append(("magika", magika_available()))
        for name, ok in entries:
            title, desc, winget, url = ENGINE_INFO.get(name, (name, "", None, None))
            card = QFrame()
            card.setFrameShape(QFrame.Shape.StyledPanel)
            lay = QHBoxLayout(card)
            lay.setContentsMargins(14, 10, 14, 10)
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {'#2fbf71' if ok else '#e5484d'}; font-size: 18px;")
            lay.addWidget(dot)
            text = QVBoxLayout()
            t = QLabel(f"<b>{title}</b> — " + ("installed" if ok else "not installed"))
            text.addWidget(t)
            d = QLabel(desc)
            d.setWordWrap(True)
            text.addWidget(d)
            if name == "ocr":
                ocr_status = QLabel("")
                ocr_status.setWordWrap(True)
                text.addWidget(ocr_status)
                test = QPushButton("Test OCR")
                test.clicked.connect(lambda _=False, lab=ocr_status: self._test_ocr(lab))
                text.addWidget(test, 0, Qt.AlignmentFlag.AlignLeft)
            if ok and name in paths:
                full = paths[name]() or ""
                pl = QLabel(self.fontMetrics().elidedText(full, Qt.TextElideMode.ElideMiddle, 520))
                pl.setToolTip(full)
                pl.setStyleSheet("color: palette(placeholder-text);")
                pl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                text.addWidget(pl)
            lay.addLayout(text, 1)
            from .shell import FROZEN
            if not ok and winget and winget.startswith("pip:") and FROZEN:
                d.setText(desc + " (Not available in the packaged app — install ShapeShifter "
                                 "with install.ps1 to use it.)")
            elif not ok and winget:
                btn = QPushButton("Install")
                btn.clicked.connect(lambda _=False, w=winget, n=title: self._install(w, n))
                lay.addWidget(btn)
            if url:
                link = QPushButton("Website")
                link.clicked.connect(lambda _=False, u=url: webbrowser.open(u))
                lay.addWidget(link)
            self.list.addWidget(card)

    def _install(self, package: str, name: str):
        """``package``: a winget id, or 'pip:<name>' for a Python package."""
        import sys
        from pathlib import Path
        if package.startswith("pip:"):
            exe = Path(sys.executable)
            py = exe.with_name("python.exe") if exe.name.lower() == "pythonw.exe" else exe
            program, args = str(py), ["-m", "pip", "install", "--disable-pip-version-check",
                                      package[4:]]
        else:
            program, args = "winget", ["install", "--id", package, "--silent",
                                       "--accept-package-agreements", "--accept-source-agreements"]
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Installing {name}")
        dlg.resize(620, 360)
        lay = QVBoxLayout(dlg)
        out = QPlainTextEdit()
        out.setReadOnly(True)
        lay.addWidget(out)
        close = QPushButton("Close")
        close.setEnabled(False)
        close.clicked.connect(dlg.accept)
        lay.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        proc = QProcess(dlg)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(
            lambda: out.appendPlainText(bytes(proc.readAllStandardOutput()).decode("utf-8", "replace")
                                        .replace("\r", "").strip()))

        def finished(code, _status):
            out.appendPlainText("\nDone." if code == 0 else f"\nThe installer exited with code {code}.")
            close.setEnabled(True)
            self._redetect()
        proc.finished.connect(finished)
        proc.errorOccurred.connect(lambda _: (out.appendPlainText(
            f"Couldn't start {Path(program).name}. Install {name} from its website instead."),
            close.setEnabled(True)))
        out.appendPlainText(f"{Path(program).name} {' '.join(args)}\n")
        proc.start(program, args)
        dlg.exec()

    def _test_ocr(self, label: QLabel):
        """Run a real recognition on a generated sample and report what Windows found."""
        import tempfile
        from pathlib import Path
        from PySide6.QtGui import QCursor
        from . import ocr
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            info = ocr.status()
            config.save_state(ocr_langs=[list(t) for t in info["languages"]])
            if not info["ok"]:
                label.setText("✗ " + info["error"])
                return
            sample = ""
            try:
                from PIL import Image, ImageDraw, ImageFont
                with tempfile.TemporaryDirectory() as tmp:
                    im = Image.new("RGB", (900, 160), "white")
                    ImageDraw.Draw(im).text((30, 40), "ShapeShifter OCR test 12345",
                                            font=ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 56),
                                            fill="black")
                    p = Path(tmp) / "t.png"
                    im.save(p)
                    page, _ = ocr.recognize_file(p, self.win.cfg)
                    sample = page.text().strip()
            except Exception as e:
                sample = f"(sample failed: {e})"
            label.setText(f"✓ Working. Languages: {', '.join(n for _, n in info['languages'])}."
                          f"\nRead back: “{sample}”")
        finally:
            QApplication.restoreOverrideCursor()

    def _redetect(self):
        config.save_state(ocr_ok=None)             # let OCR be re-checked
        engine.reset()
        from . import shell
        if shell.context_menu_registered():
            shell.register_context_menu()
        self.win.refresh_all()


class AboutPage(Page):
    def __init__(self, win):
        super().__init__(win)
        b = self.body
        b.addWidget(heading(APP_NAME, f"Version {__version__}"))
        from . import COPYRIGHT, LICENSE_NAME, SOURCE_URL
        info = QLabel("Convert files in place from File Explorer with a radial wheel.\n\n"
                      f"{COPYRIGHT}\nLicence: {LICENSE_NAME}\nSource code: {SOURCE_URL}\n\n"
                      f"Settings file: {config.CONFIG_FILE}\nLog file: {config.LOG_FILE}")
        info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info.setWordWrap(True)
        b.addWidget(info)
        app_dir = Path(sys.executable).parent

        def open_doc(name):
            p = app_dir / name
            os.startfile(p if p.exists() else SOURCE_URL)

        legal = QHBoxLayout()
        for text, fn in (("Source code", lambda: os.startfile(SOURCE_URL)),
                         ("Licence", lambda: open_doc("LICENSE.txt")),
                         ("Third-party notices", lambda: open_doc("THIRD-PARTY-NOTICES.txt"))):
            btn = QPushButton(text)
            btn.clicked.connect(fn)
            legal.addWidget(btn)
        legal.addStretch(1)
        b.addLayout(legal)
        row = QHBoxLayout()
        for text, fn in (("Open settings file", lambda: os.startfile(config.CONFIG_FILE)),
                         ("Open log", lambda: config.LOG_FILE.exists() and os.startfile(config.LOG_FILE)),
                         ("Reset all settings", self._reset)):
            btn = QPushButton(text)
            btn.clicked.connect(fn)
            row.addWidget(btn)
        row.addStretch(1)
        b.addLayout(row)
        b.addStretch(1)

    def _reset(self):
        if QMessageBox.question(self, APP_NAME, "Reset every setting (including hotkeys) to the defaults?") \
                == QMessageBox.StandardButton.Yes:
            self.win.cfg.clear()
            self.win.cfg.update(copy.deepcopy(config.DEFAULTS))
            self.win.flush()
            ctl = self.win.controller
            if hasattr(ctl, "reload_config"):
                ctl.reload_config()
            self.win.rebuild_all()


# --------------------------------------------------------------------- window

from .graph_view import GraphPage  # noqa: E402

PAGES = [("General", GeneralPage), ("Hotkeys", HotkeysPage), ("Wheel", AppearancePage),
         ("Formats & mappings", FormatsPage), ("Graph", GraphPage), ("Engines", EnginesPage),
         ("About", AboutPage)]


class SettingsWindow(QWidget):
    def __init__(self, controller=None):
        super().__init__()
        self.controller = controller
        self.setWindowTitle(f"{APP_NAME} — Settings")
        self.resize(1120, 760)
        self.cfg = config.load()
        self._save_timer = QTimer(self, singleShot=True, interval=250)
        self._save_timer.timeout.connect(self.flush)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setFixedWidth(200)
        self.nav.setFrameShape(QFrame.Shape.NoFrame)
        self.nav.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.nav.setStyleSheet("QListWidget { padding-top: 16px; background: palette(window); outline: 0; }"
                               "QListWidget::item { padding: 10px 18px; border-radius: 6px; margin: 2px 8px; }"
                               "QListWidget::item:selected { background: palette(highlight);"
                               " color: palette(highlighted-text); }")
        lay.addWidget(self.nav)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        for name, cls in PAGES:
            self.nav.addItem(name)
            self.stack.addWidget(cls(self))
        self.nav.currentRowChanged.connect(self._page_changed)
        self.nav.setCurrentRow(0)

    def _page_changed(self, row):
        self.stack.setCurrentIndex(row)
        page = self.stack.currentWidget()
        if isinstance(page, (HotkeysPage, FormatsPage, AppearancePage)):
            page.refresh()

    def set_value(self, key, value):
        self.cfg[key] = value
        self._save_timer.start()
        for i in range(self.stack.count()):
            page = self.stack.widget(i)
            if isinstance(page, AppearancePage):
                page.preview.apply_cfg(self.cfg)

    def flush(self):
        self._save_timer.stop()
        config.save(self.cfg)

    def refresh_all(self):
        for i in range(self.stack.count()):
            self.stack.widget(i).refresh()

    def rebuild_page(self, page):
        idx = self.stack.indexOf(page)
        cls = type(page)
        new = cls(self)
        self.stack.insertWidget(idx, new)
        self.stack.removeWidget(page)
        page.deleteLater()
        self.stack.setCurrentIndex(idx)

    def rebuild_all(self):
        row = self.nav.currentRow()
        for i in range(self.stack.count()):
            self.rebuild_page(self.stack.widget(i))
        self.nav.setCurrentRow(row)
        self.stack.setCurrentIndex(row)

    def closeEvent(self, e):
        global _window
        self.flush()
        _window = None
        self.deleteLater()
        super().closeEvent(e)
        ctl = self.controller
        if ctl is not None and hasattr(ctl, "_maybe_quit"):
            QTimer.singleShot(0, ctl._maybe_quit)
