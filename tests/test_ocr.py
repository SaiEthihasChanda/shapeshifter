"""OCR: paragraph/heading logic, failure handling, and (where Windows OCR works) real runs."""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pymupdf
import pytest
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shapeshifter import config, engine, ocr, planner  # noqa: E402
from shapeshifter.backends import Context  # noqa: E402
from shapeshifter.backends.ocr import OcrBackend  # noqa: E402
from shapeshifter.native import docs  # noqa: E402
from shapeshifter.native.docx import blocks_to_docx  # noqa: E402

CFG = dict(config.DEFAULTS, original="keep", learn_reverse=False)
ARIAL = Path(r"C:\Windows\Fonts\arial.ttf")


def _windows_ocr_works() -> bool:
    try:
        return ocr.available() and ocr.status()["ok"] and ARIAL.exists()
    except Exception:
        return False


needs_ocr = pytest.mark.skipif(not _windows_ocr_works(), reason="Windows OCR not usable here")


def convert(path: Path, target: str) -> list[Path]:
    return engine.convert_inplace(path, target, Context(cfg=CFG)).outputs


def line(text, x, y, w, h):
    words = [ocr.Word(t, x + i * (w / max(1, len(text.split()))), y, w / max(1, len(text.split())), h)
             for i, t in enumerate(text.split())]
    return ocr.Line(text, words)


# ------------------------------------------------------- structure (no OCR needed)

def test_paragraphs_headings_and_reflow():
    page = ocr.Page(1000, 800, [
        line("Application Form", 50, 30, 400, 60),                       # tall: heading
        line("Name: Jane", 50, 150, 200, 30),
        line("Address: 1 High St", 50, 210, 300, 30),                    # form lines: gaps
        line("This is a long justified line of prose that fills the", 50, 300, 900, 30),
        line("whole width of the page and continues here on the next", 50, 335, 900, 30),
        line("line until it ends.", 50, 370, 400, 30),
    ])
    paras = page.paragraph_texts()
    assert paras[1] == "Name: Jane" and paras[2] == "Address: 1 High St"
    assert paras[3] == ("This is a long justified line of prose that fills the whole width of the "
                        "page and continues here on the next line until it ends.")        # reflowed
    assert [k for k, _ in page.blocks()][:3] == ["h1", "p", "p"]
    close = ocr.Page(500, 300, [line("a b", 10, 10, 100, 30), line("c d", 10, 50, 100, 30)])
    assert close.paragraph_texts() == ["a b\nc d"]               # short lines keep their breaks
    apart = ocr.Page(500, 300, [line("a b", 10, 10, 100, 30), line("c d", 10, 120, 100, 30)])
    assert apart.paragraph_texts() == ["a b", "c d"]             # a clear gap starts a new paragraph


def test_recognised_markdown_characters_stay_literal():
    page = ocr.Page(800, 400, [line("snake_case_name * 2*3 [x](y)", 10, 10, 500, 30)])
    blocks = [docs.Block(k, docs.escape_markdown(t)) for k, t in page.blocks()]
    xml = zipfile.ZipFile(io.BytesIO(blocks_to_docx(blocks))).read("word/document.xml").decode()
    assert "snake_case_name * 2*3 [x](y)" in xml and "<w:i/>" not in xml and "hyperlink" not in xml


def test_powershell_single_item_json_quirk():
    # ConvertTo-Json turns one-element arrays into bare objects.
    page = ocr._parse_page({"width": 10, "height": 10, "lines": {"t": "Hi", "words":
                                                                 {"t": "Hi", "x": 1, "y": 2, "w": 3, "h": 4}}})
    assert page.lines[0].text == "Hi" and page.lines[0].words[0].w == 3


# --------------------------------------------------------------- failure handling

def test_no_ocr_language_gives_clear_message_and_hides_ocr(tmp_path, monkeypatch):
    img = tmp_path / "a.png"
    Image.new("RGB", (200, 100), "white").save(img)
    monkeypatch.setattr(ocr, "_run", lambda *a, **k: {"error": "NO_LANGUAGE", "detail": "no OCR language"})
    with pytest.raises(ocr.OcrUnavailable, match="Language & region"):
        ocr.recognize([img])
    assert config.load_state()["ocr_ok"] is False
    assert not ocr.available() and not OcrBackend().available()          # wheel stops offering it


def test_scanned_pdf_text_without_ocr_explains_instead_of_writing_an_empty_file(tmp_path, monkeypatch):
    doc = pymupdf.open()
    page = doc.new_page()
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), "white").save(buf, "PNG")
    page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(tmp_path / "scan.pdf")
    monkeypatch.setattr(ocr, "available", lambda: False)
    with pytest.raises(Exception, match="scan.*OCR isn't available"):
        convert(tmp_path / "scan.pdf", "txt")
    assert not (tmp_path / "scan.txt").exists()


def test_ocr_routes_on_the_wheel(monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda: True)
    planner.reset()
    try:
        png = planner.plan("png")
        assert {"txt", "md", "html", "docx", "pdfocr"} <= set(png)
        assert all(png[k].path[0].backend.name == "ocr" for k in ("txt", "md", "html", "docx", "pdfocr"))
        assert any("OCR" in w for w in png["docx"].warnings)
        assert not any("OCR" in w for w in png["pdfocr"].warnings)      # the page isn't changed
        assert png["pdfocr"].label == "OCR PDF" and "pdfocr" in planner.plan("pdf")
        # OCR is for the user's own scans, not for PDFs we generate from text:
        assert "pdfocr" not in planner.plan("md") and "pdfocr" not in planner.plan("docx")
    finally:
        monkeypatch.undo()
        planner.reset()


# ----------------------------------------------------------- real recognition

def make_form(path: Path) -> Path:
    big, body = ImageFont.truetype(str(ARIAL), 44), ImageFont.truetype(str(ARIAL), 26)
    im = Image.new("RGB", (1100, 700), "white")
    d = ImageDraw.Draw(im)
    d.text((60, 50), "Application Form", font=big, fill="black")
    y = 150
    for ln in ("Name: Jane Q. Public", "Address: 221B Baker Street, London", "Date of birth: 14 March 1988"):
        d.text((60, y), ln, font=body, fill="black")
        y += 60
    y += 30
    for ln in ("This agreement is made between the parties named above and",
               "shall remain in force until terminated in writing by either",
               "party with thirty days notice."):
        d.text((60, y), ln, font=body, fill="black")
        y += 38
    im.save(path)
    return path


@needs_ocr
def test_image_to_text_markdown_html_docx(tmp_path):
    form = make_form(tmp_path / "scan.png")
    text = convert(form, "txt")[0].read_text(encoding="utf-8")
    assert "Application Form" in text and "Jane Q. Public" in text and "Baker Street" in text
    assert "shall remain in force" in text and "\nshall remain" not in text        # prose reflowed
    assert convert(form, "md")[0].read_text(encoding="utf-8").startswith("# Application Form")
    html = convert(form, "html")[0].read_text(encoding="utf-8")
    assert "<h1>Application Form</h1>" in html
    with zipfile.ZipFile(convert(form, "docx")[0]) as z:
        xml = z.read("word/document.xml").decode()
    assert 'w:val="Heading1"' in xml and "Baker Street" in xml


@needs_ocr
def test_image_to_searchable_pdf(tmp_path):
    form = make_form(tmp_path / "scan.png")
    Image.open(form).convert("RGB").save(tmp_path / "scan.jpg", quality=90)
    out = convert(tmp_path / "scan.jpg", "pdfocr")[0]
    assert out.suffix == ".pdf"
    page = pymupdf.open(out)[0]
    assert len(page.get_images()) == 1 and "Baker Street" in page.get_text()
    hits = page.search_for("Baker")
    assert len(hits) == 1
    # the hit lies where the word is printed on the page (third text line, left part)
    assert hits[0].x0 < page.rect.width * 0.5 and page.rect.height * 0.15 < hits[0].y0 < page.rect.height * 0.5
    jpeg_in = (tmp_path / "scan.jpg").read_bytes()
    assert page.parent.extract_image(page.get_images()[0][0])["image"] == jpeg_in    # untouched JPEG


@needs_ocr
def test_scanned_pdf_text_html_and_searchable(tmp_path):
    form = make_form(tmp_path / "scan.png")
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=421)
    page.insert_image(page.rect, filename=str(form))
    doc.save(tmp_path / "scanned.pdf")
    assert pymupdf.open(tmp_path / "scanned.pdf")[0].get_text().strip() == ""
    assert "Baker Street" in convert(tmp_path / "scanned.pdf", "txt")[0].read_text(encoding="utf-8")
    html = convert(tmp_path / "scanned.pdf", "html")[0].read_text(encoding="utf-8")
    assert "Baker Street" in html and "data:image" not in html          # text instead of a picture
    searchable = convert(tmp_path / "scanned.pdf", "pdfocr")[0]
    assert "Baker Street" in pymupdf.open(searchable)[0].get_text()
    with pytest.raises(Exception, match="already has searchable text"):
        convert(searchable, "pdfocr")


@needs_ocr
def test_scan_to_word_fill_in_and_back_to_an_image(tmp_path):
    """The whole workflow: scan -> Word -> fill the form -> image, text still readable."""
    form = make_form(tmp_path / "scan.png")
    docx = convert(form, "docx")[0]
    with zipfile.ZipFile(docx) as z:
        files = {n: z.read(n) for n in z.namelist()}
    files["word/document.xml"] = files["word/document.xml"].replace(
        b"Jane Q. Public", b"Jane Q. Public FILLED-IN")
    filled = tmp_path / "filled.docx"
    with zipfile.ZipFile(filled, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in files.items():
            z.writestr(n, b)
    image = convert(filled, "png")[0]
    text = ocr.recognize_file(image)[0].text()
    assert "FILLED-IN" in text.upper() and "Baker" in text
