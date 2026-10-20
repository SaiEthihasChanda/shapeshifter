"""Fill-in-a-form: finding blanks, check boxes and signature lines on a picture, and writing on it."""

from pathlib import Path

import pytest
import pymupdf
from PIL import Image, ImageDraw, ImageFont

from shapeshifter import engine, forms, ocr, planner

from test_ocr import CFG, needs_ocr


def _font(size, bold=False):
    name = "arialbd.ttf" if bold else "arial.ttf"
    try:
        return ImageFont.truetype(name, size)
    except OSError:
        return ImageFont.load_default()


def make_form(path: Path) -> Path:
    im = Image.new("RGB", (1240, 1000), "white")
    d = ImageDraw.Draw(im)
    d.text((60, 50), "MEMBERSHIP FORM", font=_font(40, True), fill="black")
    d.line((60, 105, 1180, 105), fill="black", width=3)
    for i, label in enumerate(["Full name:", "Email:"]):
        y = 170 + i * 110
        d.text((60, y), label, font=_font(32), fill="black")
        d.line((360, y + 40, 1150, y + 40), fill="black", width=2)
    for i, t in enumerate(["Student", "Staff"]):
        x = 60 + i * 330
        d.rectangle((x, 500, x + 34, 534), outline="black", width=3)
        d.text((x + 50, 498), t, font=_font(32), fill="black")
    d.text((60, 700), "Signature:", font=_font(32), fill="black")
    d.line((360, 740, 800, 740), fill="black", width=2)
    im.save(path)
    return path


def make_digital_pdf(path: Path, pages: int = 2) -> Path:
    """A PDF whose text is real text: labels with rules, check boxes and a signature line."""
    doc = pymupdf.open()
    pg = doc.new_page(width=595, height=842)
    pg.insert_text((50, 70), "TRAVEL REQUEST", fontsize=20, fontname="hebo")
    for i, lab in enumerate(["Employee name:", "Destination:"]):
        y = 130 + i * 50
        pg.insert_text((50, y), lab, fontsize=13)
        pg.draw_line((190, y + 3), (540, y + 3), width=0.8)
    for i, t in enumerate(["Air", "Rail"]):
        x = 50 + i * 120
        pg.draw_rect(pymupdf.Rect(x, 300, x + 14, 314), width=1.2)
        pg.insert_text((x + 22, 311), t, fontsize=13)
    pg.insert_text((50, 400), "Signature:", fontsize=13)
    pg.draw_line((190, 403), (400, 403), width=0.8)
    for _ in range(pages - 1):
        pg2 = doc.new_page(width=595, height=842)
        pg2.insert_text((50, 100), "Manager approval:", fontsize=13)
        pg2.draw_line((190, 103), (540, 103), width=0.8)
    doc.save(path)
    doc.close()
    return path


def make_fillable_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    pg = doc.new_page(width=595, height=842)
    pg.insert_text((50, 130), "First name", fontsize=12)
    w = pymupdf.Widget()
    w.field_name, w.field_label, w.field_type = "first", "First name", pymupdf.PDF_WIDGET_TYPE_TEXT
    w.rect = pymupdf.Rect(160, 115, 400, 135)
    pg.add_widget(w)
    w = pymupdf.Widget()
    w.field_name, w.field_label, w.field_type = "subscribe", "Subscribe", pymupdf.PDF_WIDGET_TYPE_CHECKBOX
    w.rect = pymupdf.Rect(160, 168, 176, 184)
    pg.add_widget(w)
    doc.save(path)
    doc.close()
    return path


def ink_in(img, box) -> int:
    x, y, w, h = (int(v) for v in box)
    return sum(1 for p in img.crop((x, y, x + w, y + h)).convert("L").getdata() if p < 160)


class TestShapes:
    def test_a_square_outline_is_a_check_box_but_letters_are_not(self):
        im = Image.new("RGB", (300, 120), "white")
        d = ImageDraw.Draw(im)
        d.rectangle((20, 30, 54, 64), outline="black", width=3)
        d.text((100, 30), "Hello eo OD", font=_font(32), fill="black")
        ink = forms._Ink(im, 1.0)
        boxes = [c for c in forms._components(ink) if forms._is_square_box(ink, *c)]
        assert len(boxes) == 1 and boxes[0][0] == 20

    def test_component_pixel_counts_are_exact(self):
        im = Image.new("RGB", (40, 40), "white")
        ImageDraw.Draw(im).rectangle((5, 5, 14, 14), fill="black")      # a 10 x 10 blob
        (blob,) = forms._components(forms._Ink(im, 1.0))
        assert blob == (5, 5, 15, 15, 100)


class TestRendering:
    def test_text_tick_and_signature_are_drawn_where_asked(self, tmp_path):
        src = make_form(tmp_path / "form.png")
        sig = Image.new("RGB", (200, 80), "white")
        ImageDraw.Draw(sig).line([(5, 70), (60, 10), (120, 70), (190, 20)], fill="black", width=5)
        sig.save(tmp_path / "sig.png")
        fields = [forms.Field("text", "Full name", (360, 180, 790, 31), 24, value="Ada Lovelace"),
                  forms.Field("checkbox", "Student", (60, 500, 35, 35), checked=True),
                  forms.Field("checkbox", "Staff", (390, 500, 35, 35)),
                  forms.Field("signature", "Signature", (360, 646, 440, 94), 29,
                              sign_path=str(tmp_path / "sig.png"))]
        before = Image.open(src).convert("RGB")
        after = forms.render(src, fields)
        assert after.size == before.size
        assert ink_in(after, (365, 180, 300, 28)) > ink_in(before, (365, 180, 300, 28)) + 100
        assert ink_in(after, (63, 503, 29, 29)) > ink_in(before, (63, 503, 29, 29)) + 20      # the tick
        assert ink_in(after, (393, 503, 29, 29)) == ink_in(before, (393, 503, 29, 29))        # untouched
        assert ink_in(after, (370, 650, 420, 85)) > ink_in(before, (370, 650, 420, 85)) + 200  # signature

    def test_the_original_picture_is_never_modified(self, tmp_path):
        src = make_form(tmp_path / "form.png")
        data = src.read_bytes()
        out = forms.save_filled(src, [forms.Field("text", "x", (360, 180, 790, 31), 24, value="hi")])
        assert src.read_bytes() == data and out != src and out.name == "form (filled).png"
        again = forms.save_filled(src, [])
        assert again.name == "form (filled 1).png"

    def test_long_text_shrinks_to_stay_on_its_line(self, tmp_path):
        src = make_form(tmp_path / "form.png")
        f = forms.Field("text", "Email", (360, 290, 300, 31), 24, value="a" * 60 + "@example.com")
        after = forms.render(src, [f])
        assert ink_in(after, (700, 285, 300, 36)) == ink_in(Image.open(src).convert("RGB"), (700, 285, 300, 36))

    def test_signature_picture_becomes_ink_on_transparent(self, tmp_path):
        sig = Image.new("RGB", (100, 50), "white")
        ImageDraw.Draw(sig).line([(5, 40), (90, 10)], fill="black", width=4)
        sig.save(tmp_path / "s.png")
        out = forms.signature_image(tmp_path / "s.png")
        alphas = list(out.getchannel("A").getdata())
        assert min(alphas) == 0 and max(alphas) == 255


class TestWheelOption:
    def test_fill_form_is_offered_for_images_in_the_inferred_group(self, tmp_path):
        if not ocr.available():
            pytest.skip("Windows OCR not usable here")
        src = make_form(tmp_path / "form.png")
        opts = {o.key: o for o in engine.options_for(src)}
        assert "fill" in opts and opts["fill"].label == "FILL FORM"
        for key in ("fill", "txt", "md", "html"):                 # only reachable by reading the picture
            assert opts[key].category == "inferred", key
            assert opts[key].sublabel == "Inferred", key
        # DOCX and PDF are ordinary targets that ask *how* (see test_variants.py)
        assert opts["docx"].category == "document" and opts["pdf"].category == "document"
        assert opts["jpg"].category == "image"
        keys = list(opts)
        assert keys.index("fill") < keys.index("txt") < keys.index("pdf")

    def test_several_forms_can_be_filled_in_a_row(self, tmp_path):
        if not ocr.available():
            pytest.skip("Windows OCR not usable here")
        a, b = make_form(tmp_path / "a.png"), make_form(tmp_path / "b.png")
        assert any(o.key == "fill" for o in engine.visible_options([a, b], CFG))

    def test_offered_for_pdfs(self, tmp_path):
        if not ocr.available():
            pytest.skip("Windows OCR not usable here")
        pdf = make_digital_pdf(tmp_path / "t.pdf")
        assert any(o.key == "fill" and o.category == "inferred" for o in engine.options_for(pdf))

    def test_not_offered_for_non_pictures(self, tmp_path):
        doc = tmp_path / "n.txt"
        doc.write_text("hello")
        assert not any(o.key == "fill" for o in engine.options_for(doc))

    def test_pseudo_formats_are_inferred(self):
        plan = planner.plan("png")
        assert plan["docxpic"].category == "inferred" and plan["pdfocr"].category == "inferred"


@needs_ocr
class TestDetection:
    def test_finds_every_blank_check_box_and_signature_line(self, tmp_path):
        src = make_form(tmp_path / "form.png")
        fields, size = forms.detect(src, CFG)
        assert size == (1240, 1000)
        by = {(f.kind, f.label): f for f in fields}
        assert ("text", "Full name") in by and ("text", "Email") in by
        assert ("checkbox", "Student") in by and ("checkbox", "Staff") in by
        assert any(f.kind == "signature" for f in fields)
        assert not any("MEMBERSHIP" in f.label for f in fields)          # a heading is not a blank
        name = by[("text", "Full name")]
        assert 340 <= name.box[0] <= 380 and 170 <= name.box[1] <= 200 and name.box[2] > 700
        student = by[("checkbox", "Student")]
        assert abs(student.box[0] - 60) <= 3 and abs(student.box[1] - 500) <= 3

    def test_detect_then_fill_round_trip(self, tmp_path):
        src = make_form(tmp_path / "form.png")
        fields, _ = forms.detect(src, CFG)
        for f in fields:
            if f.label == "Full name":
                f.value = "Ada Lovelace"
            if f.label == "Staff":
                f.checked = True
        out = forms.save_filled(src, fields)
        page, _ = ocr.recognize_file(out, CFG)
        assert "Ada Lovelace" in page.text()


class TestPdfForms:
    def test_real_form_fields_are_found_and_filled_natively(self, tmp_path):
        src = make_fillable_pdf(tmp_path / "f.pdf")
        fields, sizes = forms.detect_document(src, CFG)       # no OCR needed for real fields
        assert len(sizes) == 1
        by = {f.label: f for f in fields}
        assert by["First name"].kind == "text" and by["First name"].widget
        assert by["Subscribe"].kind == "checkbox"
        by["First name"].value = "Ada Lovelace"
        by["Subscribe"].checked = True
        out = forms.save_filled(src, fields)
        assert out.name == "f (filled).pdf" and out != src
        with pymupdf.open(out) as doc:
            values = {w.field_name: w.field_value for w in doc[0].widgets()}
        assert values["first"] == "Ada Lovelace" and values["subscribe"] not in ("Off", False, "")
        with pymupdf.open(src) as doc:                                     # the original is untouched
            assert not [w for w in doc[0].widgets() if w.field_value not in ("", "Off", False)]

    @needs_ocr
    def test_every_page_of_a_pdf_is_read(self, tmp_path):
        src = make_digital_pdf(tmp_path / "t.pdf")
        fields, sizes = forms.detect_document(src, CFG)
        assert len(sizes) == 2
        labels = {(f.page, f.label) for f in fields}
        assert (0, "Employee name") in labels and (0, "Destination") in labels
        assert (1, "Manager approval") in labels
        assert {f.label for f in fields if f.kind == "checkbox"} >= {"Air", "Rail"}
        assert any(f.kind == "signature" and f.page == 0 for f in fields)

    @needs_ocr
    def test_answers_are_laid_over_the_pdf_and_its_text_survives(self, tmp_path):
        src = make_digital_pdf(tmp_path / "t.pdf")
        fields, _ = forms.detect_document(src, CFG)
        for f in fields:
            if f.label == "Employee name":
                f.value = "Ada Lovelace"
            if f.label == "Manager approval":
                f.value = "Approved"
            if f.label == "Rail":
                f.checked = True
        out = forms.save_filled(src, fields)
        assert out.name == "t (filled).pdf"
        with pymupdf.open(out) as doc:
            assert len(doc) == 2
            text = doc[0].get_text() + doc[1].get_text()
            assert "TRAVEL REQUEST" in text and "Manager approval" in text      # still real text
            for i, word in ((0, "Ada Lovelace"), (1, "Approved")):
                page = doc[i]
                found, _ = ocr.recognize_file(_snapshot(page, tmp_path / f"p{i}.png"), CFG)
                assert word in found.text()

    def test_is_pdf_looks_at_the_contents_not_the_name(self, tmp_path):
        pdf = make_fillable_pdf(tmp_path / "x.dat")
        assert forms.is_pdf(pdf)
        assert not forms.is_pdf(make_form(tmp_path / "x.png"))


def _snapshot(page, path):
    page.get_pixmap(dpi=200).save(path)
    return path
