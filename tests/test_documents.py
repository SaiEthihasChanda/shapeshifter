"""Document fidelity: pictures, formatting, links and tables must survive conversions that
create PDFs / Word documents / HTML (and PDF -> image must stay pixel-faithful)."""

from __future__ import annotations

import io
import subprocess
import sys
import zipfile
from pathlib import Path

import pymupdf
import pytest
from PIL import Image, ImageChops

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shapeshifter import config, engine, planner  # noqa: E402
from shapeshifter.backends import Context  # noqa: E402
from shapeshifter.backends.external import pandoc_path  # noqa: E402
from shapeshifter.native import docs, pdfwrite  # noqa: E402
from shapeshifter.native.docx import blocks_to_docx  # noqa: E402

CFG = dict(config.DEFAULTS, original="keep", learn_reverse=False)
needs_pandoc = pytest.mark.skipif(not pandoc_path(), reason="pandoc not installed")

MD = ("# Annual report\n\nSome **bold**, *italic* and `code` with a [link](https://example.com). "
      "Привет — 你好.\n\n![Company photo](photo.png)\n\n1. one\n2. two\n\n> quoted\n\n"
      "| Item | Qty |\n|---|---|\n| Apples | 3 |\n| Pears | 7 |\n")


def convert(path: Path, target: str, **cfg) -> list[Path]:
    return engine.convert_inplace(path, target, Context(cfg=dict(CFG, **cfg))).outputs


@pytest.fixture
def prefer_native(monkeypatch):
    real = config.load
    monkeypatch.setattr(config, "load", lambda: dict(real(), prefer_native=True))
    planner.reset()
    yield
    monkeypatch.setattr(config, "load", real)
    planner.reset()


@pytest.fixture
def doc_dir(tmp_path):
    photo = Image.new("RGB", (240, 140))
    photo.putdata([(x, (y * 2) % 256, 140) for y in range(140) for x in range(240)])
    photo.save(tmp_path / "photo.png")
    (tmp_path / "report.md").write_text(MD, encoding="utf-8")
    return tmp_path


def pdf_page(path: Path):
    return pymupdf.open(path)[0]


# ----------------------------------------------------------- Markdown/HTML/text -> PDF

def test_markdown_to_pdf_keeps_picture_link_styles_and_scripts(doc_dir):
    page = pdf_page(convert(doc_dir / "report.md", "pdf")[0])
    text = page.get_text()
    assert len(page.get_images()) == 1                       # the picture (was dropped before)
    assert any(l.get("uri") == "https://example.com" for l in page.get_links())
    fonts = " ".join(f[3] for f in page.get_fonts())
    assert "Bold" in fonts and "Italic" in fonts and "Mono" in fonts
    assert "Привет" in text and "你好" in text and "Apples" in text


def test_html_with_data_uri_image_to_pdf(tmp_path):
    import base64
    buf = io.BytesIO()
    Image.new("RGB", (50, 30), "red").save(buf, "PNG")
    uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    (tmp_path / "p.html").write_text(f'<html><body><h1>Hi</h1><img src="{uri}"></body></html>')
    assert len(pdf_page(convert(tmp_path / "p.html", "pdf")[0]).get_images()) == 1


def test_text_with_blank_line_paragraphs_to_pdf(tmp_path):
    (tmp_path / "n.txt").write_text("first paragraph\nsecond line\n\nsecond paragraph\n")
    page = pdf_page(convert(tmp_path / "n.txt", "pdf")[0])
    text = page.get_text()
    assert "first paragraph" in text and "second paragraph" in text


def test_pdf_text_has_no_ligature_characters(tmp_path):
    """Copy/search must see 'first office affluent', not 'ﬁrst oﬃce aﬄuent'."""
    (tmp_path / "n.txt").write_text("first office affluent flat waffle difficult\n")
    page = pdf_page(convert(tmp_path / "n.txt", "pdf")[0])
    assert page.get_text().split() == "first office affluent flat waffle difficult".split()
    assert len(page.search_for("office")) == 1 and len(page.search_for("first")) == 1


def test_native_pdf_writer_embeds_pictures_tables_numbers(doc_dir, prefer_native):
    Image.new("RGBA", (60, 60), (200, 30, 30, 140)).save(doc_dir / "logo.png")      # transparent
    (doc_dir / "report.md").write_text(MD + "\n![Logo](logo.png)\n", encoding="utf-8")
    assert planner.plan("md")["pdf"].path[0].backend.name == "native"
    page = pdf_page(convert(doc_dir / "report.md", "pdf")[0])
    assert len(page.get_images()) == 2
    text = page.get_text()
    assert "1. one" in text and "2. two" in text and "Apples" in text
    assert page.get_drawings()                                   # table borders were drawn


# ------------------------------------------------------------------- DOCX

@needs_pandoc
def test_docx_pictures_survive_pandoc_routes(doc_dir):
    subprocess.run([pandoc_path(), "-f", "gfm", "-t", "docx", "-o", "in.docx", "report.md"],
                   cwd=doc_dir, check=True, capture_output=True)
    html = convert(doc_dir / "in.docx", "html")[0].read_text(encoding="utf-8")
    assert html.count("data:image") == 1                          # was an unresolvable media/ path
    assert len(pdf_page(convert(doc_dir / "in.docx", "pdf")[0]).get_images()) == 1
    md = convert(doc_dir / "in.docx", "md")
    assert any(p.is_dir() and list(p.rglob("*.png")) for p in md)  # extracted pictures travel along


def test_native_docx_writer_structure(doc_dir):
    Image.new("RGBA", (60, 60), (200, 30, 30, 140)).save(doc_dir / "logo.png")
    blocks = docs.parse_markdown(MD + "\n![Logo](logo.png)\n\n```\ncode block\n```\n")
    data = blocks_to_docx(blocks, [doc_dir], "T")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        xml = z.read("word/document.xml").decode("utf-8")
        assert {"word/media/image1.png", "word/media/image2.png", "word/styles.xml"} <= names
        assert z.testzip() is None
    for frag in ('w:val="Heading1"', "<w:b/>", "<w:i/>", "<w:hyperlink", "<w:tbl>", "<wp:inline",
                 "Привет", "1.", "code block"):
        assert frag in xml, frag


@needs_pandoc
def test_native_docx_reads_back_in_pandoc(doc_dir, prefer_native):
    assert planner.plan("md")["docx"].path[0].backend.name == "native"
    docx = convert(doc_dir / "report.md", "docx")[0]
    html = subprocess.run([pandoc_path(), "-f", "docx", "-t", "html", "-s", "--embed-resources",
                           str(docx)], capture_output=True, text=True, encoding="utf-8").stdout
    assert html.count("<img") == 1 and "<strong>bold" in html and "<em>italic" in html
    assert 'href="https://example.com' in html and "<table" in html and "<blockquote" in html


def test_escaped_text_is_not_formatted(tmp_path):
    text = "snake_case_name and 2*3*4 and [not a link](x) and # not a heading"
    blocks = [docs.Block("p", docs.escape_markdown(text))]
    assert docs.plain(blocks[0].text) == text
    xml = zipfile.ZipFile(io.BytesIO(blocks_to_docx(blocks))).read("word/document.xml").decode()
    assert "<w:i/>" not in xml and "snake_case_name" in xml and "hyperlink" not in xml
    assert "snake_case_name" in docs.to_html(blocks)


def test_raw_html_img_in_markdown_becomes_a_picture():
    blocks = docs.parse_markdown('<img src="a/b.png" style="width:2in" alt="A pic" />\n\ntext <img src="c.png"> end')
    assert blocks[0].kind == "img" and blocks[0].src == "a/b.png" and blocks[0].text == "A pic"
    assert any(s.img == "c.png" for s in docs.parse_inline(blocks[1].text))


def test_numbered_lists_in_native_pdf_blocks():
    blocks = docs.pdf_blocks(docs.parse_markdown("1. a\n2. b\n\n- x\n"))
    assert [(k, e) for k, _t, *e in blocks if k in ("oli", "li")] == [("oli", ["1."]), ("oli", ["2."]), ("li", [])]


# --------------------------------------------------------- PDF -> HTML/DOCX/SVG/image

def make_rich_pdf(path: Path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Quarterly report", fontsize=22)
    photo = Image.new("RGB", (300, 200))
    photo.putdata([(x * 255 // 300, y * 255 // 200, 128) for y in range(200) for x in range(300)])
    buf = io.BytesIO()
    photo.save(buf, "JPEG", quality=90)
    page.insert_image(pymupdf.Rect(72, 100, 372, 300), stream=buf.getvalue())
    logo = Image.new("RGBA", (120, 120), (0, 0, 0, 0))
    logo.paste((220, 40, 40, 160), (5, 5, 115, 115))
    lb = io.BytesIO()
    logo.save(lb, "PNG")
    page.insert_image(pymupdf.Rect(300, 250, 420, 370), stream=lb.getvalue())
    page.draw_rect(pymupdf.Rect(72, 400, 300, 450), color=(0, 0.4, 0.9), fill=(0.8, 0.9, 1))
    page.add_highlight_annot(page.search_for("Quarterly")[0])
    page.insert_text((72, 480), "Body text below.", fontsize=12)
    doc.save(path)


def test_pdf_to_image_is_pixel_faithful(tmp_path):
    """Locks in the verified behaviour: rendering equals MuPDF's own full render."""
    make_rich_pdf(tmp_path / "rich.pdf")
    out = Image.open(convert(tmp_path / "rich.pdf", "png")[0]).convert("RGB")
    ref_pix = pymupdf.open(tmp_path / "rich.pdf")[0].get_pixmap(
        matrix=pymupdf.Matrix(150 / 72, 150 / 72), annots=True)
    ref = Image.frombytes("RGB", (ref_pix.width, ref_pix.height), ref_pix.samples)
    assert out.size == ref.size and ImageChops.difference(out, ref).getbbox() is None


def test_pdf_to_html_and_svg_keep_pictures(tmp_path):
    make_rich_pdf(tmp_path / "rich.pdf")
    html = convert(tmp_path / "rich.pdf", "html")[0].read_text(encoding="utf-8")
    assert html.count("data:image") >= 2 and "Quarterly report" in html
    svg = convert(tmp_path / "rich.pdf", "svg")[0].read_text(encoding="utf-8")
    assert "<image" in svg


@needs_pandoc
def test_pdf_to_docx_keeps_pictures(tmp_path):
    make_rich_pdf(tmp_path / "rich.pdf")
    docx = convert(tmp_path / "rich.pdf", "docx")[0]
    with zipfile.ZipFile(docx) as z:
        assert len([n for n in z.namelist() if n.startswith("word/media/")]) >= 2


def test_docx_to_png_route_keeps_pictures_not_text_only():
    opt = planner.plan("docx").get("png")
    if opt is None:
        pytest.skip("no DOCX reader installed")
    assert [e.dst for e in opt.path] == ["html", "pdf", "png"]
    assert "Only the text is kept" not in opt.warnings
