"""The "Fill form" window: shows the picture of a form, the fields found in it, and lets the
person type answers, tick boxes and add a signature. The picture on the left is a live preview
of exactly what will be written to the new image.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from . import config, forms
from .system import reveal
from .core import ConversionError

log = logging.getLogger(__name__)

_open: list[QDialog] = []
PREVIEW_SIDE = 1500
KIND_COLORS = {"text": "#4a8fff", "checkbox": "#46c06b", "signature": "#f08a3c"}
SIGNATURE_FILE = config.DATA_DIR / "signature.png"


def show_form_filler(path: str, cfg: dict | None = None) -> None:
    dlg = FormFiller(path, cfg or config.load())
    _open.append(dlg)
    dlg.finished.connect(lambda _: _open.remove(dlg) if dlg in _open else None)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()


class _Detect(QThread):
    done = Signal(list, list)
    failed = Signal(str)
    progress = Signal(int, int)

    def __init__(self, path: str, cfg: dict):
        super().__init__()
        self.path, self.cfg = path, cfg

    def run(self):
        try:
            fields, sizes = forms.detect_document(self.path, self.cfg, progress=self.progress.emit)
            self.done.emit(fields, sizes)
        except ConversionError as e:
            self.failed.emit(str(e))
        except Exception as e:                      # noqa: BLE001 - shown to the user
            log.exception("Form detection crashed")
            self.failed.emit(f"Couldn't read this form: {e}")


# ---------------------------------------------------------------- signature pad

class SignaturePad(QDialog):
    """Draw a signature with the mouse (or a pen or finger)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Draw your signature")
        self.canvas = QImage(640, 220, QImage.Format.Format_RGB32)
        self.canvas.fill(Qt.GlobalColor.white)
        self._last: QPointF | None = None
        self._strokes = 0
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Sign inside the box:"))
        self.area = QLabel()
        self.area.setFixedSize(640, 220)
        self.area.setFrameShape(QFrame.Shape.Box)
        self.area.setCursor(Qt.CursorShape.CrossCursor)
        self.area.mousePressEvent = self._press
        self.area.mouseMoveEvent = self._move
        self.area.mouseReleaseEvent = lambda e: setattr(self, "_last", None)
        lay.addWidget(self.area)
        row = QHBoxLayout()
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear)
        ok = QPushButton("Use this signature")
        ok.setDefault(True)
        ok.clicked.connect(self._accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addWidget(clear)
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)
        self._refresh()

    def _refresh(self):
        self.area.setPixmap(QPixmap.fromImage(self.canvas))

    def _clear(self):
        self.canvas.fill(Qt.GlobalColor.white)
        self._strokes = 0
        self._refresh()

    def _press(self, e):
        self._last = e.position()
        self._strokes += 1
        self._line(self._last, self._last)

    def _move(self, e):
        if self._last is not None:
            self._line(self._last, e.position())
            self._last = e.position()

    def _line(self, a: QPointF, b: QPointF):
        p = QPainter(self.canvas)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(10, 10, 10), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        p.drawLine(a, b)
        p.end()
        self._refresh()

    def _accept(self):
        if not self._strokes:
            QMessageBox.information(self, "Signature", "Draw your signature first.")
            return
        SIGNATURE_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.canvas.save(str(SIGNATURE_FILE), "PNG")
        self.accept()


# ---------------------------------------------------------------- preview widget

GRIP = 6                                    # screen pixels around a frame's edge that resize it
CURSORS = {"move": Qt.CursorShape.SizeAllCursor, "l": Qt.CursorShape.SizeHorCursor,
           "r": Qt.CursorShape.SizeHorCursor, "t": Qt.CursorShape.SizeVerCursor,
           "b": Qt.CursorShape.SizeVerCursor, "tl": Qt.CursorShape.SizeFDiagCursor,
           "br": Qt.CursorShape.SizeFDiagCursor, "tr": Qt.CursorShape.SizeBDiagCursor,
           "bl": Qt.CursorShape.SizeBDiagCursor}


class Preview(QWidget):
    """The rendered form with a frame around each field. Clicking places a new field; dragging a
    frame moves it, dragging its edges or corners resizes it; arrow keys nudge the selected one."""

    clicked = Signal(float, float)          # position in image pixels
    field_clicked = Signal(int)
    field_moved = Signal(int)               # a field's box changed (while dragging, too)
    delete_requested = Signal(int)

    def __init__(self):
        super().__init__()
        self.setMinimumSize(420, 420)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.pix: QPixmap | None = None
        self.size_px = (1, 1)
        self.fields: list[forms.Field] = []
        self.selected = -1
        self.placing = False
        self._drag = None                   # (index, mode, start x, start y, original box)
        self.setMouseTracking(True)

    def set_pixmap(self, pix: QPixmap | None, size_px: tuple[int, int]):
        self.pix, self.size_px = pix, size_px
        self.update()

    def _rect(self) -> QRectF:
        if self.pix is None:
            return QRectF()
        k = min(self.width() / self.pix.width(), self.height() / self.pix.height())
        w, h = self.pix.width() * k, self.pix.height() * k
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def _screen(self, box) -> QRectF:
        r = self._rect()
        k = r.width() / self.size_px[0]
        x, y, w, h = box
        return QRectF(r.x() + x * k, r.y() + y * k, w * k, h * k)

    def _image_point(self, pos) -> tuple[float, float]:
        r = self._rect()
        k = self.size_px[0] / r.width()
        return (pos.x() - r.x()) * k, (pos.y() - r.y()) * k

    @staticmethod
    def movable(f) -> bool:
        return not f.widget                 # a real PDF field stays where the PDF puts it

    def _hit(self, pos) -> tuple[int, str]:
        """(field index, "move" or the edges being grabbed) under a screen position; selected one first."""
        order = list(range(len(self.fields) - 1, -1, -1))
        if 0 <= self.selected < len(self.fields):
            order.remove(self.selected)
            order.insert(0, self.selected)
        for i in order:
            if not self.movable(self.fields[i]):
                if self._screen(self.fields[i].box).contains(pos):
                    return i, ""
                continue
            s = self._screen(self.fields[i].box)
            if not s.adjusted(-GRIP, -GRIP, GRIP, GRIP).contains(pos):
                continue
            g = min(GRIP, s.width() / 4, s.height() / 4)
            edge = ("t" if pos.y() <= s.top() + g else "b" if pos.y() >= s.bottom() - g else "") + \
                   ("l" if pos.x() <= s.left() + g else "r" if pos.x() >= s.right() - g else "")
            return i, edge or "move"
        return -1, ""

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), self.palette().window())
        if self.pix is None:
            return
        r = self._rect()
        p.drawPixmap(r.toRect(), self.pix)
        for i, f in enumerate(self.fields):
            col = QColor(KIND_COLORS.get(f.kind, "#888"))
            sel = i == self.selected
            col.setAlpha(255 if sel else 150)
            p.setPen(QPen(col, 2.5 if sel else 1.2, Qt.PenStyle.SolidLine if sel else Qt.PenStyle.DashLine))
            p.setBrush(QColor(col.red(), col.green(), col.blue(), 40 if sel else 14))
            s = self._screen(f.box)
            p.drawRect(s)
            if sel and self.movable(f):     # grips on the corners and the middle of each side
                p.setPen(QPen(QColor("white"), 1))
                p.setBrush(col)
                for gx, gy in ((s.left(), s.top()), (s.right(), s.top()), (s.left(), s.bottom()),
                               (s.right(), s.bottom()), (s.center().x(), s.top()), (s.center().x(), s.bottom()),
                               (s.left(), s.center().y()), (s.right(), s.center().y())):
                    p.drawRect(QRectF(gx - 3.5, gy - 3.5, 7, 7))

    def mousePressEvent(self, e):
        r = self._rect()
        if self.pix is None or e.button() != Qt.MouseButton.LeftButton:
            return
        if self.placing:
            if r.contains(e.position()):
                self.clicked.emit(*self._image_point(e.position()))
            return
        i, mode = self._hit(e.position())
        if i < 0:
            return
        self.field_clicked.emit(i)
        if mode:
            self._drag = (i, mode, *self._image_point(e.position()), self.fields[i].box)

    def mouseMoveEvent(self, e):
        if self._drag is None:
            if not self.placing and self.pix is not None:
                i, mode = self._hit(e.position())
                if i >= 0 and mode:
                    self.setCursor(CURSORS[mode])
                elif i >= 0:
                    self.setCursor(Qt.CursorShape.PointingHandCursor)
                else:
                    self.unsetCursor()
            return
        i, mode, sx, sy, (x, y, w, h) = self._drag
        ix, iy = self._image_point(e.position())
        dx, dy = ix - sx, iy - sy
        W, H = self.size_px
        least = 6.0
        if mode == "move":
            x = min(max(0.0, x + dx), W - w)
            y = min(max(0.0, y + dy), H - h)
        else:
            x1, y1 = x + w, y + h
            if "l" in mode:
                x = min(max(0.0, x + dx), x1 - least)
            if "r" in mode:
                x1 = max(min(W, x1 + dx), x + least)
            if "t" in mode:
                y = min(max(0.0, y + dy), y1 - least)
            if "b" in mode:
                y1 = max(min(H, y1 + dy), y + least)
            w, h = x1 - x, y1 - y
            if self.fields[i].kind == "checkbox" and len(mode) == 2:
                w = h = max(w, h)           # a check box stays square when dragged by a corner
        self.fields[i].box = (x, y, w, h)
        self.update()
        self.field_moved.emit(i)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def keyPressEvent(self, e):
        i = self.selected
        if not 0 <= i < len(self.fields):
            return super().keyPressEvent(e)
        if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_requested.emit(i)
            return
        step = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0), Qt.Key.Key_Up: (0, -1),
                Qt.Key.Key_Down: (0, 1)}.get(e.key())
        if step is None or not self.movable(self.fields[i]):
            return super().keyPressEvent(e)
        n = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        x, y, w, h = self.fields[i].box
        W, H = self.size_px
        self.fields[i].box = (min(max(0.0, x + step[0] * n), W - w), min(max(0.0, y + step[1] * n), H - h), w, h)
        self.update()
        self.field_moved.emit(i)


# ------------------------------------------------------------------- the window

def _font_box(first: str = "") -> QComboBox:
    """Writing styles, then every installed font, each shown in its own typeface."""
    box = QComboBox()
    box.setMaxVisibleItems(18)
    if first:
        box.addItem(first)
    for name in forms.font_names():
        box.addItem(name)
        if name not in forms.FONTS:
            box.setItemData(box.count() - 1, QFont(name), Qt.ItemDataRole.FontRole)
    return box


def _size_box(value: int) -> QSpinBox:
    box = QSpinBox()
    box.setRange(30, 400)
    box.setSingleStep(10)
    box.setSuffix(" %")
    box.setValue(value)
    box.setToolTip("Size of the writing, compared with the size that fits the form's lines")
    return box


class FormFiller(QDialog):
    def __init__(self, path: str, cfg: dict):
        super().__init__()
        self.path, self.cfg = path, cfg
        self.setWindowTitle(f"Fill form — {Path(path).name}")
        self.resize(1180, 760)
        self.fields: list[forms.Field] = []    # every field of every page
        self.sizes: list[tuple[int, int]] = [(1, 1)]
        self.page = 0
        self._bases: dict[int, tuple] = {}     # page -> (downscaled image, scale) for quick previews
        self.rows: list[QWidget] = []
        self._saved_sig = str(SIGNATURE_FILE) if SIGNATURE_FILE.exists() else ""
        self._timer = QTimer(self, singleShot=True, interval=80)
        self._timer.timeout.connect(self._redraw)

        root = QHBoxLayout(self)
        self.preview = Preview()
        self.preview.field_clicked.connect(self._select)
        self.preview.clicked.connect(self._place)
        self.preview.field_moved.connect(self._changed)
        self.preview.delete_requested.connect(lambda i: self._remove(self.cur()[i]))
        root.addWidget(self.preview, 3)

        side = QVBoxLayout()
        root.addLayout(side, 2)
        self.status = QLabel("Reading the form…")
        self.status.setWordWrap(True)
        self.status.setMinimumWidth(200)
        side.addWidget(self.status)

        self.nav = QWidget()
        nav = QHBoxLayout(self.nav)
        nav.setContentsMargins(0, 0, 0, 0)
        self.prev_btn = QPushButton("‹ Previous page")
        self.next_btn = QPushButton("Next page ›")
        self.page_label = QLabel("")
        self.prev_btn.clicked.connect(lambda: self._go(-1))
        self.next_btn.clicked.connect(lambda: self._go(1))
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.page_label, 1, Qt.AlignmentFlag.AlignCenter)
        nav.addWidget(self.next_btn)
        self.nav.hide()
        side.addWidget(self.nav)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.holder = QWidget()
        self.form = QVBoxLayout(self.holder)
        self.form.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.holder)
        side.addWidget(self.scroll, 1)

        add = QHBoxLayout()
        add.addWidget(QLabel("Missed one? Add:"))
        for text, kind in (("Text", "text"), ("Check box", "checkbox"), ("Signature", "signature")):
            b = QPushButton("+ " + text)
            b.clicked.connect(lambda _=False, k=kind: self._start_placing(k))
            add.addWidget(b)
        add.addStretch(1)
        side.addLayout(add)

        # the selected answer: its own font and size, on top of the whole form's
        self.field_bar = QWidget()
        fb = QHBoxLayout(self.field_bar)
        fb.setContentsMargins(0, 0, 0, 0)
        self.field_font = _font_box("Same as the form")
        self.field_size = _size_box(100)
        self.field_font.currentIndexChanged.connect(self._field_look)
        self.field_size.valueChanged.connect(self._field_look)
        self.field_name = QLabel("Selected answer:")
        fb.addWidget(self.field_name)
        fb.addWidget(self.field_font, 1)
        fb.addWidget(QLabel("Size"))
        fb.addWidget(self.field_size)
        self.field_bar.setEnabled(False)
        self.field_bar.setToolTip("Click a text field on the form (or its box on the right) to give that "
                                  "answer its own font or size.")
        side.addWidget(self.field_bar)

        look = QHBoxLayout()
        self.style = _font_box()
        self.ink = QComboBox()
        self.ink.addItems(list(forms.INKS))
        self.size = _size_box(100)
        for w in (self.style, self.ink):
            w.currentIndexChanged.connect(self._changed)
        self.size.valueChanged.connect(self._changed)
        look.addWidget(QLabel("All answers:"))
        look.addWidget(self.style, 1)
        look.addWidget(self.ink)
        look.addWidget(QLabel("Size"))
        look.addWidget(self.size)
        side.addLayout(look)

        bottom = QHBoxLayout()
        self.note = QLabel("")
        self.note.setWordWrap(True)
        bottom.addWidget(self.note, 1)
        cancel = QPushButton("Close")
        cancel.clicked.connect(self.reject)
        self.save_btn = QPushButton("Save filled PDF" if forms.is_pdf(path) else "Save filled image")
        self.save_btn.setDefault(True)
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save)
        bottom.addWidget(cancel)
        bottom.addWidget(self.save_btn)
        side.addLayout(bottom)

        self._placing_kind: str | None = None
        self.worker = _Detect(path, cfg)
        self.worker.done.connect(self._detected)
        self.worker.progress.connect(lambda i, n: self.status.setText(f"Reading the form… page {i} of {n}"))
        self.worker.failed.connect(self._failed)
        self.worker.start()

    # ------------------------------------------------------------- detection

    def _failed(self, message: str):
        self.status.setText(message)
        self.status.setStyleSheet("color: #d9534f")

    def cur(self) -> list:
        """The fields of the page being shown."""
        return [f for f in self.fields if f.page == self.page]

    def _base(self):
        """(downscaled page image, scale) for the page being shown."""
        got = self._bases.get(self.page)
        if got is None:
            from PIL import Image
            full = forms.load_image(self.path, self.page)
            k = min(1.0, PREVIEW_SIDE / max(full.size))
            img = full.resize((max(1, round(full.width * k)), max(1, round(full.height * k))),
                              Image.LANCZOS) if k != 1.0 else full
            got = self._bases[self.page] = (img, k)
        return got

    def _detected(self, fields: list, sizes: list):
        self.fields, self.sizes = fields, [tuple(z) for z in sizes]
        for f in self.fields:
            if f.kind == "signature":
                f.sign_path = self._saved_sig
        n = {k: sum(1 for f in fields if f.kind == k) for k in ("text", "checkbox", "signature")}
        pages = f" on {len(self.sizes)} pages" if len(self.sizes) > 1 else ""
        native = any(f.widget for f in fields)
        self.status.setText(
            f"Found {n['text']} text field(s), {n['checkbox']} check box(es) and {n['signature']} signature "
            f"line(s){pages}. Fill them in on the right; click a frame on the form to jump to it. "
            "Drag a frame to move it, or its edges and corners to resize it (arrow keys nudge, Delete "
            "removes). If one was missed, add it with the buttons below, then click where it goes."
            + (" Fields that are part of the PDF itself stay fillable in the saved copy." if native else ""))
        self.status.setStyleSheet("")
        self.save_btn.setEnabled(True)
        self._update_nav()
        self._rebuild_rows()
        self._redraw()

    def _update_nav(self):
        many = len(self.sizes) > 1
        self.nav.setVisible(many)
        self.page_label.setText(f"Page {self.page + 1} of {len(self.sizes)}")
        self.prev_btn.setEnabled(self.page > 0)
        self.next_btn.setEnabled(self.page < len(self.sizes) - 1)

    def _go(self, delta: int):
        page = max(0, min(len(self.sizes) - 1, self.page + delta))
        if page == self.page:
            return
        self.page = page
        self.preview.selected = -1
        self._show_field_look(None)
        self._placing_kind = None
        self.preview.placing = False
        self._update_nav()
        self._rebuild_rows()
        self._redraw()

    # ---------------------------------------------------------------- rows

    def _rebuild_rows(self):
        for w in self.rows:
            w.setParent(None)
            w.deleteLater()
        self.rows = []
        for i, f in enumerate(self.cur()):
            row = QWidget()
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 2, 0, 2)
            name = QLabel(f.label or {"text": "Text", "checkbox": "Check box", "signature": "Signature"}[f.kind])
            name.setMinimumWidth(110)
            name.setMaximumWidth(150)
            name.setWordWrap(True)
            if f.kind == "text":
                lay.addWidget(name)
                edit = QLineEdit(f.value)
                edit.setPlaceholderText("type here")
                edit.textChanged.connect(lambda t, f=f: self._set(f, "value", t))
                edit.focusInEvent = (lambda ev, i=i, orig=edit.focusInEvent: (self._select(i, focus=False), orig(ev)))
                lay.addWidget(edit, 1)
                f._edit = edit                                     # noqa: SLF001 - kept for focusing
            elif f.kind == "checkbox":
                box = QCheckBox(f.label or "Check box")
                box.setChecked(f.checked)
                box.toggled.connect(lambda v, f=f: self._set(f, "checked", v))
                lay.addWidget(box, 1)
            else:
                lay.addWidget(name)
                info = QLabel(Path(f.sign_path).name if f.sign_path else "no signature yet")
                lay.addWidget(info, 1)
                pick = QPushButton("Choose image…")
                pick.clicked.connect(lambda _=False, f=f, info=info: self._choose_signature(f, info))
                draw = QPushButton("Draw…")
                draw.clicked.connect(lambda _=False, f=f, info=info: self._draw_signature(f, info))
                lay.addWidget(pick)
                lay.addWidget(draw)
            gone = QPushButton("✕")
            gone.setFixedWidth(28)
            gone.setToolTip("Remove this field")
            gone.clicked.connect(lambda _=False, f=f: self._remove(f))
            lay.addWidget(gone)
            self.form.addWidget(row)
            self.rows.append(row)

    def _set(self, f, attr, value):
        setattr(f, attr, value)
        self._changed()

    def _remove(self, f):
        self.fields.remove(f)
        self.preview.selected = -1
        self._show_field_look(None)
        self._rebuild_rows()
        self._changed()

    def _show_field_look(self, f):
        """Point the "Selected answer" font and size at a field (text fields only)."""
        self._looking = None
        ok = f is not None and f.kind == "text" and not f.widget
        self.field_bar.setEnabled(ok)
        for w in (self.field_font, self.field_size):
            w.blockSignals(True)
        self.field_font.setCurrentIndex(max(0, self.field_font.findText(f.font)) if ok and f.font else 0)
        self.field_size.setValue(round((f.scale or 1.0) * 100) if ok else 100)
        for w in (self.field_font, self.field_size):
            w.blockSignals(False)
        self.field_name.setText(f"{(f.label or 'Text')[:22]}:" if ok else "Selected answer:")
        self._looking = f if ok else None

    def _field_look(self, *_):
        f = getattr(self, "_looking", None)
        if f is None:
            return
        f.font = "" if self.field_font.currentIndex() == 0 else self.field_font.currentText()
        f.scale = self.field_size.value() / 100.0
        self._changed()

    def _select(self, i: int, focus: bool = True):
        self.preview.selected = i
        self.preview.update()
        cur = self.cur()
        self._show_field_look(cur[i] if 0 <= i < len(cur) else None)
        if focus and i < len(cur):
            edit = getattr(cur[i], "_edit", None)
            if edit is not None:
                edit.setFocus()
            if i < len(self.rows):
                self.scroll.ensureWidgetVisible(self.rows[i])

    # ------------------------------------------------------------- signatures

    def _remember(self, path: str) -> str:
        try:
            SIGNATURE_FILE.parent.mkdir(parents=True, exist_ok=True)
            if Path(path).resolve() != SIGNATURE_FILE.resolve():
                shutil.copyfile(path, SIGNATURE_FILE)
            self._saved_sig = str(SIGNATURE_FILE)
            return self._saved_sig
        except OSError:
            return path

    def _apply_signature(self, f, path: str, info: QLabel):
        f.sign_path = self._remember(path)
        info.setText("signature added")
        for other in self.fields:                  # one signature, used on every signature line (all pages)
            if other.kind == "signature" and not other.sign_path:
                other.sign_path = f.sign_path
        self._rebuild_rows()
        self._changed()

    def _choose_signature(self, f, info):
        path, _ = QFileDialog.getOpenFileName(self, "Choose your signature picture", "",
                                              "Pictures (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")
        if path:
            self._apply_signature(f, path, info)

    def _draw_signature(self, f, info):
        pad = SignaturePad(self)
        if pad.exec():
            self._apply_signature(f, str(SIGNATURE_FILE), info)

    # ------------------------------------------------------------ add fields

    def _start_placing(self, kind: str):
        self._placing_kind = kind
        self.preview.placing = True
        self.preview.setCursor(Qt.CursorShape.CrossCursor)
        self.note.setText({"text": "Click on the form where the text should be written (on its line).",
                           "checkbox": "Click the centre of the check box.",
                           "signature": "Click on the signature line."}[kind])

    def _place(self, x: float, y: float):
        kind = self._placing_kind or "text"
        W, H = self.sizes[self.page]
        sizes = [f.size for f in self.cur() if f.kind == "text" and f.size]
        size = sorted(sizes)[len(sizes) // 2] if sizes else max(14.0, H * 0.02)
        if kind == "checkbox":
            side = next((f.box[2] for f in self.cur() if f.kind == "checkbox"), max(18.0, W * 0.028))
            f = forms.Field("checkbox", "", (x - side / 2, y - side / 2, side, side), page=self.page)
        elif kind == "signature":
            hbox = max(3.2 * size, H * 0.05)
            f = forms.Field("signature", "", (x, y - hbox, min(W * 0.35, W - x - 8), hbox), size,
                            sign_path=self._saved_sig, page=self.page)
        else:
            f = forms.Field("text", "", (x, y - 1.3 * size, min(W * 0.4, W - x - 8), 1.3 * size), size,
                            page=self.page)
        self.fields.append(f)
        self._placing_kind = None
        self.preview.placing = False
        self.preview.unsetCursor()
        self.note.setText("")
        self._rebuild_rows()
        self._select(len(self.cur()) - 1)
        self._changed()

    # ---------------------------------------------------------------- output

    def _changed(self, *_):
        if not self._timer.isActive():              # at most every 80 ms, so dragging and typing stay live
            self._timer.start()

    def _redraw(self):
        if len(self.sizes) == 1 and self.sizes[0] == (1, 1):
            return                                  # nothing detected yet
        base, k = self._base()
        fields = self.cur()
        img = forms.render(self.path, fields, self.style.currentText(), self.ink.currentText(),
                           self.size.value(), scale=k, base=base, prescaled=True)
        data = img.convert("RGBA").tobytes("raw", "RGBA")
        qimg = QImage(data, img.width, img.height, QImage.Format.Format_RGBA8888).copy()
        self.preview.fields = fields
        self.preview.set_pixmap(QPixmap.fromImage(qimg), self.sizes[self.page])

    def _save(self):
        try:
            out = forms.save_filled(self.path, self.fields, self.style.currentText(),
                                    self.ink.currentText(), self.size.value())
        except Exception as e:                      # noqa: BLE001 - shown to the user
            log.exception("Saving the filled form failed")
            QMessageBox.warning(self, "Fill form", f"Couldn't save: {e}")
            return
        box = QMessageBox(self)
        box.setWindowTitle("Fill form")
        box.setText(f"Saved {out.name}\nnext to the original, which is unchanged.")
        show = box.addButton("Show in folder", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Close", QMessageBox.ButtonRole.AcceptRole)
        box.exec()
        if box.clickedButton() is show:
            reveal(out)
        self.accept()
