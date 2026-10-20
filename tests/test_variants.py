"""One target, several behaviours: the wheel shows DOCX / PDF once and asks *how* only when the
file's contents make the answer a real choice."""

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PIL import Image, ImageDraw

from shapeshifter import engine, ocr

sys.path.insert(0, str(Path(__file__).parent))
from test_forms import make_form                         # noqa: E402
from test_ocr import CFG, needs_ocr                       # noqa: E402


def make_photo(path: Path) -> Path:
    """A picture with no text in it: a gradient with a few shapes."""
    im = Image.new("RGB", (400, 300))
    px = im.load()
    for x in range(400):
        for y in range(300):
            px[x, y] = (x * 255 // 400, y * 255 // 300, 120)
    d = ImageDraw.Draw(im)
    d.ellipse((120, 80, 280, 220), fill=(240, 200, 60))
    im.save(path)
    return path


def make_scanned_pdf(path: Path, image: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(pymupdf.Rect(20, 20, 575, 500), filename=str(image))
    doc.save(path)
    doc.close()
    return path


def make_text_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "This page has a real text layer.", fontsize=14)
    doc.save(path)
    doc.close()
    return path


class TestGrouping:
    def test_docx_and_pdf_are_single_targets_with_variants_for_pictures(self, tmp_path):
        if not ocr.available():
            pytest.skip("Windows OCR not usable here")
        opts = {o.key: o for o in engine.options_for(make_photo(tmp_path / "p.png"))}
        assert "docxpic" not in opts and "pdfocr" not in opts          # no longer separate segments
        assert [v.key for v in opts["docx"].variants] == ["docx", "docxpic"]
        assert [v.key for v in opts["pdf"].variants] == ["pdf", "pdfocr"]
        assert [v.label for v in opts["docx"].variants] == ["Text (OCR)", "Picture"]
        assert [v.label for v in opts["pdf"].variants] == ["Picture", "Searchable"]
        assert opts["docx"].label == "DOCX" and opts["docx"].sublabel == "Asks how"
        assert "text (ocr)" in opts["docx"].description.lower()
        assert not opts["docx"].warnings                                # no blanket OCR warning

    def test_other_sources_keep_plain_targets(self, tmp_path):
        md = tmp_path / "n.md"
        md.write_text("# Title\n\nHello\n")
        opts = {o.key: o for o in engine.options_for(md)}
        assert "docx" in opts and not opts["docx"].variants

    def test_ocr_pdf_is_offered_for_scans_only(self, tmp_path):
        if not ocr.available():
            pytest.skip("Windows OCR not usable here")
        scan = make_scanned_pdf(tmp_path / "scan.pdf", make_photo(tmp_path / "p.png"))
        digital = make_text_pdf(tmp_path / "digital.pdf")
        assert "pdfocr" in {o.key for o in engine.options_for(scan)}
        assert "pdfocr" not in {o.key for o in engine.options_for(digital)}

    def test_the_old_keys_still_work_for_scripts(self, tmp_path):
        from shapeshifter.core import Context
        out = engine.convert_inplace(make_photo(tmp_path / "p.png"), "docxpic", Context(cfg=CFG)).outputs[0]
        assert out.suffix == ".docx"


@needs_ocr
class TestResolving:
    def opt(self, path, key):
        return {o.key: o for o in engine.options_for(path)}[key]

    def test_a_picture_with_text_asks_how(self, tmp_path):
        src = make_form(tmp_path / "form.png")
        for key, expect in (("docx", ["docx", "docxpic"]), ("pdf", ["pdf", "pdfocr"])):
            got = engine.resolve_variants([src], self.opt(src, key), CFG)
            assert [v.key for v in got] == expect

    def test_a_picture_without_text_does_not_ask(self, tmp_path):
        src = make_photo(tmp_path / "photo.png")
        assert [v.key for v in engine.resolve_variants([src], self.opt(src, "docx"), CFG)] == ["docxpic"]
        assert [v.key for v in engine.resolve_variants([src], self.opt(src, "pdf"), CFG)] == ["pdf"]

    def test_one_file_with_text_is_enough_to_ask(self, tmp_path):
        text, photo = make_form(tmp_path / "form.png"), make_photo(tmp_path / "photo.png")
        got = engine.resolve_variants([photo, text], self.opt(photo, "docx"), CFG)
        assert [v.key for v in got] == ["docx", "docxpic"]

    def test_looking_twice_does_not_read_the_picture_twice(self, tmp_path, monkeypatch):
        src = make_form(tmp_path / "form.png")
        ocr._RECENT.clear()
        engine._has_text(src, CFG)
        calls = []
        real = ocr.recognize
        monkeypatch.setattr(ocr, "recognize", lambda *a, **k: calls.append(1) or real(*a, **k))
        assert engine._has_text(src, CFG)
        assert not calls


@needs_ocr
class TestWheelPrompt:
    """The wheel itself: ask for a picture with text, go straight ahead for one without."""

    @staticmethod
    def spin(app, predicate, seconds=40):
        end = time.time() + seconds
        while time.time() < end and not predicate():
            app.processEvents()
            time.sleep(0.02)
        return predicate()

    def wheel(self, path, tmp_path):
        from PySide6.QtWidgets import QApplication
        from shapeshifter.wheel import WheelOverlay
        app = QApplication.instance() or QApplication([])
        cfg = dict(CFG, original="keep", animation_ms=0)
        return app, WheelOverlay([str(path)], cfg)

    def test_asks_then_steps_back(self, tmp_path):
        app, w = self.wheel(make_form(tmp_path / "form.png"), tmp_path)
        before = list(w.targets)
        w._commit("docx")
        assert w.analysing
        assert self.spin(app, lambda: not w.analysing)
        assert w.state == "choose" and w.targets == ["docx", "docxpic"]
        assert [o.label for o in w.options] == ["Text (OCR)", "Picture"]
        w._back()
        assert w.targets == before and not w._variant_stack

    def test_chosen_variant_is_converted(self, tmp_path):
        app, w = self.wheel(make_form(tmp_path / "form.png"), tmp_path)
        w._commit("docx")
        assert self.spin(app, lambda: not w.analysing)
        w._commit("docxpic")
        assert self.spin(app, lambda: w.state == "done")
        assert (tmp_path / "form.docx").exists()

    def test_goes_straight_ahead_without_text(self, tmp_path):
        app, w = self.wheel(make_photo(tmp_path / "photo.png"), tmp_path)
        w._commit("docx")
        assert self.spin(app, lambda: w.state == "done")
        assert not w._variant_stack and (tmp_path / "photo.docx").exists()
