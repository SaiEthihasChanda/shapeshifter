"""RTF, ODT writing, and conversions between Word, OpenDocument, RTF, Markdown, HTML, text and PDF."""

import base64
import io
import sys
import zipfile
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from shapeshifter import engine, planner
from shapeshifter.core import Context
from shapeshifter.native import docread, docs, odtwrite, rtf

sys.path.insert(0, str(Path(__file__).parent))
from test_ocr import CFG                                  # noqa: E402

MD = """# Report

Intro with **bold**, *italic*, `code` and a [link](https://example.com).

## Items

- first
  - nested
- second

1. one
2. two

| Name | Qty |
|---|---|
| pen | 3 |
| ink | 10 |

> a quote

![dot](dot.png)
"""


def run(path, target):
    return engine.convert_inplace(path, target, Context(cfg=dict(CFG, original="keep"))).outputs


@pytest.fixture()
def md(tmp_path):
    Image.new("RGB", (60, 40), (200, 30, 30)).save(tmp_path / "dot.png")
    (tmp_path / "report.md").write_text(MD, encoding="utf-8")
    return tmp_path / "report.md"


def shape(blocks):
    return [(b.kind, b.text if b.kind != "table" else b.rows, b.level) for b in blocks if b.kind != "img"]


class TestRtfWriteRead:
    def test_round_trip(self, md):
        blocks = docs.parse_markdown(md.read_text(encoding="utf-8"))
        data = rtf.write_rtf(blocks, [md.parent], "Report")
        assert data.startswith(b"{\\rtf1") and data.isascii()
        back = rtf.rtf_blocks(data)
        want = [(k, (x.replace("`", "") if isinstance(x, str) else x), lvl) for k, x, lvl in shape(blocks)]
        got = shape(back)
        assert got[:-1] == want[:-1]                       # everything but the quote, which comes back italic
        assert got[-1][1] == "*a quote*"
        kinds = [b.kind for b in back]
        assert kinds.count("li") == 3 and kinds.count("oli") == 2 and "table" in kinds and "img" in kinds
        li = [b for b in back if b.kind == "li"]
        assert [b.level for b in li] == [0, 1, 0]
        assert back[1].text == "Intro with **bold**, *italic*, `code` and a [link](https://example.com)." or "**bold**" in back[1].text
        assert [b.text for b in back if b.kind == "oli"] == ["one", "two"]
        table = next(b for b in back if b.kind == "table")
        assert table.rows == [["Name", "Qty"], ["pen", "3"], ["ink", "10"]]

    def test_unicode_survives(self):
        blocks = [docs.Block("p", "Café ☕ naïve 日本語 😀")]
        assert rtf.rtf_blocks(rtf.write_rtf(blocks))[0].text == "Café ☕ naïve 日本語 😀"

    def test_a_word_style_document(self):
        png = io.BytesIO()
        Image.new("RGB", (8, 8), "blue").save(png, "PNG")
        data = (r"""{\rtf1\ansi\ansicpg1252\deff0{\fonttbl{\f0 Calibri;}}{\colortbl;\red0\green0\blue0;}
{\stylesheet{\s0 Normal;}{\s1 heading 1;}{\s2 heading 2;}{\s3 Title;}}
\pard\s3 Big Title\par
\pard\s1\b Chapter\b0\par
\pard\s2 Section\par
\pard Caf\'e9 and \u8212? dash, \b bold\b0  and \i italic\i0.\par
{\listtext\pard\plain\f0 \u8226\'3f\tab}\pard\ls1 bullet one\par
{\pntext\pard 1.\tab}\pard\ls2 numbered one\par
{\field{\*\fldinst{HYPERLINK "https://example.com"}}{\fldrslt{\ul site}}}\par
\trowd\cellx3000\cellx6000\pard\intbl a\cell b\cell\row
\trowd\cellx3000\cellx6000\pard\intbl 1\cell 2\cell\row
\pard After.\par
{\pict\pngblip\picw8\pich8 HEXPNG}\par}""".replace("HEXPNG", png.getvalue().hex()).replace("\n", "")).encode("ascii")
        blocks = rtf.rtf_blocks(data)
        assert [(b.kind, b.text) for b in blocks if b.kind not in ("table", "img")] == [
            ("h1", "Big Title"), ("h1", "Chapter"), ("h2", "Section"),
            ("p", "Café and — dash, **bold** and *italic*."), ("li", "bullet one"), ("oli", "numbered one"),
            ("p", "[site](https://example.com)"), ("p", "After.")]
        assert next(b for b in blocks if b.kind == "table").rows == [["a", "b"], ["1", "2"]]
        img = next(b for b in blocks if b.kind == "img")
        assert base64.b64decode(img.src.split(",", 1)[1])[:4] == b"\x89PNG"

    def test_not_rtf_is_refused(self):
        with pytest.raises(ValueError):
            rtf.rtf_blocks(b"plain text")


class TestOdtWriteRead:
    def test_round_trip_through_the_reader(self, md):
        blocks = docs.parse_markdown(md.read_text(encoding="utf-8"))
        data = odtwrite.blocks_to_odt(blocks, [md.parent], "Report")
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            assert z.namelist()[0] == "mimetype" and z.read("mimetype") == b"application/vnd.oasis.opendocument.text"
            assert any(n.startswith("Pictures/") for n in z.namelist())
        path = md.parent / "w.odt"
        path.write_bytes(data)
        back, title = docread.odt_blocks(path)
        assert title == "Report"
        assert [(b.kind, b.level) for b in back if b.kind in ("h1", "h2", "li", "oli")] == [
            ("h1", 0), ("h2", 0), ("li", 0), ("li", 1), ("li", 0), ("oli", 0), ("oli", 0)]
        assert "**bold**" in back[1].text and "[link](https://example.com)" in back[1].text
        assert next(b for b in back if b.kind == "table").rows == [["Name", "Qty"], ["pen", "3"], ["ink", "10"]]
        assert any(b.kind == "img" for b in back)


class TestConversions:
    def test_every_pair_works_through_the_native_readers_and_writers(self, md):
        (docx,) = run(md, "docx")
        chain = [("docx", docx)]
        for fmt in ("odt", "rtf"):
            src = chain[-1][1]
            (out,) = run(src, fmt)
            chain.append((fmt, out))
        # docx -> odt -> rtf, then back along every edge
        (rtf_file,) = [p for f, p in chain if f == "rtf"]
        (odt_file,) = [p for f, p in chain if f == "odt"]
        for src, targets in ((rtf_file, ("md", "html", "txt", "pdf", "docx", "odt")), (odt_file, ("md", "html", "txt", "pdf", "docx", "rtf"))):
            for t in targets:
                out = run(src, t)[0]                           # (pandoc, when present, may add a media folder)
                assert out.stat().st_size > 0, (src.suffix, t)
        back_md = run(rtf_file, "md")[0]
        text = back_md.read_text(encoding="utf-8")
        assert "## Items" in text and "- first" in text and "| pen | 3 |" in text and "**bold**" in text
        pdf = run(odt_file, "pdf")[0]
        with pymupdf.open(pdf) as doc:
            assert "Report" in doc[0].get_text() and "pen" in "".join(p.get_text() for p in doc)

    def test_markdown_html_and_text_become_odt_and_rtf(self, md):
        for t in ("odt", "rtf"):
            (out,) = run(md, t)
            assert out.suffix == f".{t}" and out.stat().st_size > 200
        (html,) = run(md, "html")
        assert run(html, "rtf")[0].stat().st_size > 200

    def test_routes(self):
        for src, dst in (("rtf", "epub"), ("rtf", "pptx"), ("odt", "rtf"), ("csv", "rtf"), ("rtf", "csv"), ("rtf", "png"),
                         ("pdf", "rtf"), ("docx", "rtf"), ("xlsx", "odt"), ("pptx", "rtf")):
            assert planner.route(src, dst) is not None, (src, dst)
