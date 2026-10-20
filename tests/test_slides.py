"""Presentations (PPTX / ODP) and the native Word / OpenDocument readers."""

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pymupdf
import pytest
from PIL import Image, ImageDraw

from shapeshifter import engine, planner
from shapeshifter.backends import external
from shapeshifter.backends.slides import DocReadBackend
from shapeshifter.core import Context
from shapeshifter.native import docread, docs, slides, slides_out, slides_write

sys.path.insert(0, str(Path(__file__).parent))
from test_ocr import CFG                                  # noqa: E402

MD = """# Quarterly Review

Prepared by the team

## Highlights

- Revenue up **12%**
- New *customers*: 340
  - Enterprise: 40
- See [the report](https://example.com/report)

## Plan

1. Hire two engineers
2. Launch the beta

## Numbers

| Region | Q1 | Q2 |
|---|---|---|
| North | 10 | 12 |
| South | 8 | 9 |

## Picture

![Sun](sun.png)
"""


def run(path, target):
    return engine.convert_inplace(path, target, Context(cfg=dict(CFG, original="keep"))).outputs


@pytest.fixture()
def deck_md(tmp_path):
    im = Image.new("RGB", (400, 260), (60, 120, 200))
    ImageDraw.Draw(im).ellipse((120, 50, 280, 210), fill=(250, 210, 60))
    im.save(tmp_path / "sun.png")
    (tmp_path / "deck.md").write_text(MD, encoding="utf-8")
    return tmp_path / "deck.md"


def texts(deck):
    return [[p.text for sh in s.shapes for p in sh.paras] for s in deck.slides]


class TestWriteThenRead:
    def test_markdown_becomes_slides(self, deck_md):
        (pptx,) = run(deck_md, "pptx")
        deck = slides.read_pptx(pptx)
        assert [s.title for s in deck.slides] == ["Quarterly Review", "Highlights", "Plan", "Numbers", "Picture"]
        highlights = [p for sh in deck.slides[1].shapes if sh.role == "body" for p in sh.paras]
        assert [(p.text, p.level, p.bullet) for p in highlights] == [
            ("Revenue up 12%", 0, "•"), ("New customers: 340", 0, "•"), ("Enterprise: 40", 1, "•"),
            ("See the report", 0, "•")]
        assert highlights[0].runs[1].bold and highlights[1].runs[1].italic
        assert highlights[3].runs[-1].link == "https://example.com/report"
        plan = [p for sh in deck.slides[2].shapes if sh.role == "body" for p in sh.paras]
        assert [p.bullet for p in plan] == ["#", "#"]
        table = next(sh for sh in deck.slides[3].shapes if sh.kind == "table")
        assert table.rows == [["Region", "Q1", "Q2"], ["North", "10", "12"], ["South", "8", "9"]]
        assert any(sh.kind == "picture" and sh.image for sh in deck.slides[4].shapes)

    @pytest.mark.skipif(not external.pandoc_path(), reason="pandoc not installed")
    def test_an_independent_reader_opens_our_pptx(self, deck_md):
        (pptx,) = run(deck_md, "pptx")
        out = subprocess.run([external.pandoc_path(), "-f", "pptx", "-t", "plain", str(pptx)],
                             capture_output=True, text=True, encoding="utf-8")
        assert out.returncode == 0
        for word in ("Quarterly Review", "Highlights", "Enterprise: 40", "Hire two engineers", "North"):
            assert word in out.stdout

    def test_package_structure_is_complete(self, deck_md):
        (pptx,) = run(deck_md, "pptx")
        with zipfile.ZipFile(pptx) as z:
            names = set(z.namelist())
            for part in ("[Content_Types].xml", "_rels/.rels", "ppt/presentation.xml", "ppt/slideMasters/slideMaster1.xml",
                         "ppt/slideLayouts/slideLayout1.xml", "ppt/theme/theme1.xml", "ppt/slides/slide5.xml"):
                assert part in names
            assert any(n.startswith("ppt/media/") for n in names)
            ct = z.read("[Content_Types].xml").decode()
            assert ct.count("/ppt/slides/slide") == 5

    def test_odp_round_trip_keeps_text_pictures_and_tables(self, deck_md):
        (pptx,) = run(deck_md, "pptx")
        (odp,) = run(pptx, "odp")
        with zipfile.ZipFile(odp) as z:
            assert z.namelist()[0] == "mimetype"
            assert z.read("mimetype") == b"application/vnd.oasis.opendocument.presentation"
        deck = slides_write.read_odp(odp)
        assert len(deck.slides) == 5
        assert deck.slides[0].title == "Quarterly Review"
        body = [p for sh in deck.slides[1].shapes if sh.role == "body" for p in sh.paras]
        assert [p.text for p in body] == ["Revenue up 12%", "New customers: 340", "Enterprise: 40", "See the report"]
        assert body[2].level == 1 and body[0].bullet == "•"
        assert any(sh.kind == "table" and sh.rows[1] == ["North", "10", "12"] for sh in deck.slides[3].shapes)
        assert any(sh.kind == "picture" for sh in deck.slides[4].shapes)
        (back,) = run(odp, "pptx")
        assert [s.title for s in slides.read_pptx(back).slides][:3] == ["Quarterly Review", "Highlights", "Plan"]

    def test_speaker_notes_survive_odp(self, tmp_path):
        deck = slides.Deck(slides=[slides.Slide(shapes=[slides.Shape(kind="text", x=0, y=0, w=5e6, h=1e6, role="title",
                                                                    paras=[slides.Para([slides.Run("Hi")])])],
                                                  notes="Remember to smile")])
        slides_write.write_odp(deck, tmp_path / "n.odp")
        assert slides_write.read_odp(tmp_path / "n.odp").slides[0].notes == "Remember to smile"


class TestDrawing:
    def test_pdf_has_one_page_per_slide_with_text_and_picture(self, deck_md):
        (pptx,) = run(deck_md, "pptx")
        (pdf,) = run(pptx, "pdf")
        with pymupdf.open(pdf) as doc:
            assert len(doc) == 5
            assert abs(doc[0].rect.width / doc[0].rect.height - 16 / 9) < 0.01
            text = [p.get_text() for p in doc]
            assert "Quarterly Review" in text[0] and "Revenue up 12%" in text[1] and "Enterprise: 40" in text[1]
            assert "1." in text[2] and "North" in text[3]
            assert doc[4].get_images()

    def test_text_that_does_not_fit_is_shrunk_not_lost(self, tmp_path):
        paras = [slides.Para([slides.Run(f"Line number {i} of a very long list", size=28)]) for i in range(40)]
        deck = slides.Deck(slides=[slides.Slide(shapes=[slides.Shape(kind="text", x=457200, y=457200, w=8e6, h=1.5e6, paras=paras)])])
        slides_out.deck_to_pdf(deck, tmp_path / "x.pdf")
        with pymupdf.open(tmp_path / "x.pdf") as doc:
            assert "Line number 39" in doc[0].get_text()

    def test_colours_and_backgrounds_are_drawn(self, tmp_path):
        deck = slides.Deck(slides=[slides.Slide(bg="1f3a93", shapes=[
            slides.Shape(kind="rect", x=1e6, y=1e6, w=3e6, h=2e6, fill="ffd60a")])])
        slides_out.deck_to_pdf(deck, tmp_path / "c.pdf")
        with pymupdf.open(tmp_path / "c.pdf") as doc:
            pix = doc[0].get_pixmap(dpi=36)
            assert pix.pixel(2, 2) == (0x1f, 0x3a, 0x93)
            cx, cy = int(2.5e6 / 12700 * 0.5), int(2e6 / 12700 * 0.5)
            assert pix.pixel(cx, cy) == (0xff, 0xd6, 0x0a)


class TestOutlines:
    def test_markdown_html_text_and_word(self, deck_md):
        (pptx,) = run(deck_md, "pptx")
        (md,) = run(pptx, "md")
        text = md.read_text(encoding="utf-8")
        assert "## Highlights" in text and "- Revenue up **12%**" in text and "| North | 10 | 12 |" in text
        assert "[the report](https://example.com/report)" in text and "  - Enterprise: 40" in text
        (html,) = run(pptx, "html")
        assert "data:image/png;base64" in html.read_text(encoding="utf-8")
        (txt,) = run(pptx, "txt")
        assert "Revenue up 12%" in txt.read_text(encoding="utf-8")
        (docx,) = run(pptx, "docx")
        blocks, _ = docread.docx_blocks(docx)
        assert any(b.kind == "img" for b in blocks) and any(b.kind == "table" for b in blocks)

    def test_tables_in_slides_can_become_spreadsheets(self, deck_md):
        (pptx,) = run(deck_md, "pptx")
        outs = run(pptx, "csv")
        assert outs[0].read_text(encoding="utf-8").splitlines()[:2] == ["Region,Q1,Q2", "North,10,12"]


class TestOtherWaysIn:
    def test_pdf_pages_become_picture_slides(self, deck_md):
        (pptx,) = run(deck_md, "pptx")
        (pdf,) = run(pptx, "pdf")
        (back,) = run(pdf, "pptx")
        deck = slides.read_pptx(back)
        assert len(deck.slides) == 5
        assert all(len(s.shapes) == 1 and s.shapes[0].kind == "picture" for s in deck.slides)
        assert "Revenue up 12%" in deck.slides[1].shapes[0].name                 # the page text is kept as alt text
        assert abs(deck.width / deck.height - 16 / 9) < 0.01
        opt = planner.plan("pdf")["pptx"]
        assert any("picture" in w for w in opt.warnings)

    def test_a_picture_becomes_a_slide(self, deck_md):
        (pptx,) = run(deck_md.parent / "sun.png", "pptx")
        deck = slides.read_pptx(pptx)
        assert len(deck.slides) == 1 and deck.slides[0].shapes[0].kind == "picture"

    def test_word_becomes_slides_by_heading(self, deck_md):
        (docx,) = run(deck_md, "docx")
        (pptx,) = run(docx, "pptx")
        deck = slides.read_pptx(pptx)
        assert [s.title for s in deck.slides][:3] == ["Quarterly Review", "Highlights", "Plan"]

    def test_long_text_is_split_over_several_slides(self):
        blocks = [docs.Block("h2", "Notes")] + [docs.Block("li", "A fairly long bullet point " * 4) for _ in range(20)]
        deck = slides_write.deck_from_blocks(blocks, "T")
        titles = [s.title for s in deck.slides]
        assert len(titles) > 2 and titles[0] == "Notes" and titles[1] == "Notes (cont.)"

    def test_a_presentation_without_slides_says_so(self, tmp_path):
        slides_write.write_pptx(slides.Deck(), tmp_path / "e.pptx")
        with pytest.raises(Exception):
            slides.read_pptx(tmp_path / "e.pptx").slides[0].shapes[0]       # one blank slide, no shapes


class TestRoutes:
    def test_longer_routes_exist_both_ways(self):
        for src, dst in (("pptx", "png"), ("pptx", "jpg"), ("pptx", "epub"), ("csv", "pptx"), ("xlsx", "odp"),
                         ("odp", "docx"), ("pdf", "odp"), ("docx", "odp"), ("pptx", "xlsx"), ("png", "pptx"),
                         ("tsv", "pptx"), ("pptx", "tex")):
            assert planner.route(src, dst) is not None, (src, dst)

    def test_warnings(self):
        assert any("redrawn" in w for w in planner.plan("pptx")["pdf"].warnings)
        assert any("slide layout" in w for w in planner.plan("pptx")["md"].warnings)
        assert not planner.plan("md")["pptx"].warnings


class TestNativeWordReader:
    def test_word_to_markdown_html_text_and_pdf_without_pandoc(self, deck_md, tmp_path):
        (docx,) = run(deck_md, "docx")
        for target in ("md", "html", "txt", "pdf"):
            dst = tmp_path / f"out.{target}"
            DocReadBackend().convert(docx, dst, Context(cfg=CFG, src_fmt="docx", dst_fmt=target))
            assert dst.stat().st_size > 0
        md = (tmp_path / "out.md").read_text(encoding="utf-8")
        assert "## Highlights" in md and "**12%**" in md and "| North | 10 | 12 |" in md
        assert "[the report](https://example.com/report)" in md
        assert "data:image" in (tmp_path / "out.html").read_text(encoding="utf-8")
        with pymupdf.open(tmp_path / "out.pdf") as doc:
            assert "Revenue up" in doc[0].get_text() or any("Revenue up" in p.get_text() for p in doc)

    def test_lists_styles_links_and_images_in_a_handmade_docx(self, tmp_path):
        W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
        doc = (f'<w:document {W}><w:body>'
               '<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:t>My Title</w:t></w:r></w:p>'
               '<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>Part one</w:t></w:r></w:p>'
               '<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Bold</w:t></w:r><w:r><w:t xml:space="preserve"> and </w:t></w:r>'
               '<w:r><w:rPr><w:i/></w:rPr><w:t>italic</w:t></w:r></w:p>'
               '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>bullet</w:t></w:r></w:p>'
               '<w:p><w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="2"/></w:numPr></w:pPr><w:r><w:t>numbered</w:t></w:r></w:p>'
               '<w:p><w:hyperlink r:id="rId9"><w:r><w:t>site</w:t></w:r></w:hyperlink></w:p>'
               '</w:body></w:document>')
        numbering = (f'<w:numbering {W}><w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/></w:lvl></w:abstractNum>'
                     '<w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="1"><w:numFmt w:val="decimal"/></w:lvl></w:abstractNum>'
                     '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num><w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num></w:numbering>')
        rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId9" Type="x/hyperlink" Target="https://example.com" TargetMode="External"/></Relationships>')
        path = tmp_path / "h.docx"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("word/document.xml", doc)
            z.writestr("word/numbering.xml", numbering)
            z.writestr("word/_rels/document.xml.rels", rels)
        blocks, title = docread.docx_blocks(path)
        assert title == "My Title"
        kinds = [(b.kind, b.text) for b in blocks]
        assert kinds == [("h1", "My Title"), ("h2", "Part one"), ("p", "**Bold** and *italic*"), ("li", "bullet"),
                         ("oli", "numbered"), ("p", "[site](https://example.com)")]
        assert blocks[4].level == 1

    def test_opendocument_text(self, tmp_path):
        ns = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
              'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
              'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" xmlns:xlink="http://www.w3.org/1999/xlink"')
        content = (f'<office:document-content {ns}><office:automatic-styles><style:style style:name="B" style:family="text">'
                   '<style:text-properties fo:font-weight="bold"/></style:style></office:automatic-styles><office:body><office:text>'
                   '<text:h text:outline-level="1">Heading</text:h><text:p>Hello <text:span text:style-name="B">bold</text:span> '
                   '<text:a xlink:href="https://example.com">link</text:a></text:p>'
                   '<text:list><text:list-item><text:p>one</text:p></text:list-item><text:list-item><text:p>two</text:p></text:list-item></text:list>'
                   '<table:table><table:table-row><table:table-cell><text:p>a</text:p></table:table-cell><table:table-cell><text:p>b</text:p></table:table-cell></table:table-row></table:table>'
                   '</office:text></office:body></office:document-content>')
        path = tmp_path / "t.odt"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("mimetype", "application/vnd.oasis.opendocument.text")
            z.writestr("content.xml", content)
        blocks, title = docread.odt_blocks(path)
        assert title == "Heading"
        assert [(b.kind, b.text) for b in blocks if b.kind != "table"] == [
            ("h1", "Heading"), ("p", "Hello **bold** [link](https://example.com)"), ("li", "one"), ("li", "two")]
        assert blocks[-1].rows == [["a", "b"]]


@pytest.mark.skipif(not shutil.which("python"), reason="unused")
def test_ppsx_and_potx_are_read_like_pptx(deck_md, tmp_path):
    (pptx,) = run(deck_md, "pptx")
    copy = tmp_path / "show.ppsx"
    shutil.copy(pptx, copy)
    assert planner.plan("ppsx")["pdf"].path[0].backend.name == "slides"
    (pdf,) = run(copy, "pdf")
    with pymupdf.open(pdf) as doc:
        assert len(doc) == 5
