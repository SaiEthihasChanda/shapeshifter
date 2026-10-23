"""The Fill form window: moving and resizing fields with the mouse, and each answer's font and size."""

import os

import pytest
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from shapeshifter import formfill, forms  # noqa: E402


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    src = tmp_path / "form.png"
    Image.new("RGB", (1000, 800), "white").save(src)
    found = [forms.Field("text", "Name", (100, 100, 400, 40), 30),
             forms.Field("checkbox", "Agree", (100, 300, 30, 30)),
             forms.Field("signature", "Signature", (500, 600, 300, 80), 30)]
    monkeypatch.setattr(forms, "detect_document", lambda *a, **k: (found, [(1000, 800)]))
    dlg = formfill.FormFiller(str(src), {})
    dlg.worker.wait(5000)
    app.processEvents()
    dlg.resize(1400, 900)
    dlg.show()
    app.processEvents()
    yield dlg, app, src
    dlg.close()


def _at(preview, x, y) -> QPoint:
    """Screen point inside the preview for an image position."""
    s = preview._screen((x, y, 0, 0))
    return QPoint(round(s.x()), round(s.y()))


def test_a_field_can_be_dragged_somewhere_else(window):
    dlg, app, _ = window
    pv = dlg.preview
    f = dlg.fields[0]
    k = pv._rect().width() / 1000
    start = _at(pv, 300, 120)                     # the middle of "Name"
    QTest.mousePress(pv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(pv, start + QPoint(round(100 * k), round(50 * k)))
    QTest.mouseRelease(pv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                       start + QPoint(round(100 * k), round(50 * k)))
    x, y, w, h = f.box
    assert abs(x - 200) <= 3 and abs(y - 150) <= 3 and (w, h) == (400, 40)
    assert pv.selected == 0


def test_corners_and_edges_resize_and_a_check_box_stays_square(window):
    dlg, app, _ = window
    pv = dlg.preview
    k = pv._rect().width() / 1000
    sig = dlg.fields[2]
    corner = _at(pv, 800, 680)                    # bottom-right of the signature
    QTest.mousePress(pv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, corner)
    QTest.mouseMove(pv, corner + QPoint(round(60 * k), round(40 * k)))
    QTest.mouseRelease(pv, Qt.MouseButton.LeftButton)
    x, y, w, h = sig.box
    assert (x, y) == (500, 600) and abs(w - 360) <= 3 and abs(h - 120) <= 3

    box = dlg.fields[1]
    corner = _at(pv, 130, 330)
    QTest.mousePress(pv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, corner)
    QTest.mouseMove(pv, corner + QPoint(round(30 * k), round(5 * k)))
    QTest.mouseRelease(pv, Qt.MouseButton.LeftButton)
    assert box.box[2] == box.box[3] and box.box[2] > 50


def test_arrow_keys_nudge_and_delete_removes(window):
    dlg, app, _ = window
    pv = dlg.preview
    dlg._select(0)
    pv.setFocus()
    QTest.keyClick(pv, Qt.Key.Key_Right)
    QTest.keyClick(pv, Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier)
    assert dlg.fields[0].box[:2] == (101, 110)
    QTest.keyClick(pv, Qt.Key.Key_Delete)
    assert [f.label for f in dlg.fields] == ["Agree", "Signature"]


def test_each_answer_can_have_its_own_font_and_size(window, tmp_path):
    dlg, app, src = window
    f = dlg.fields[0]
    f.value = "Ada Lovelace"
    dlg._select(0, focus=False)
    assert dlg.field_bar.isEnabled()
    dlg.field_font.setCurrentText("Typewriter")
    dlg.field_size.setValue(150)
    assert f.font == "Typewriter" and f.scale == 1.5
    dlg._select(1, focus=False)                   # a check box has no font
    assert not dlg.field_bar.isEnabled()

    plain = forms.Field("text", "Name", f.box, 30, value="Ada Lovelace")
    a = forms.render(src, [plain])
    b = forms.render(src, [f])
    def written(im):                              # (width, height) of the writing
        x0, y0, x1, y1 = im.convert("L").point(lambda v: 255 if v < 160 else 0).getbbox()
        return x1 - x0, y1 - y0
    assert written(b)[0] > 1.3 * written(a)[0] and written(b)[1] > 1.2 * written(a)[1]
    out = forms.save_filled(src, dlg.fields)
    assert out.exists()


def test_every_installed_font_is_offered_and_found():
    names = forms.font_names()
    assert names[:3] == list(forms.FONTS)
    if len(names) > 3:
        other = names[3]
        assert forms.find_font(other, 30).getname()[0]
