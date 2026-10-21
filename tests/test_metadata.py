import shutil
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from shapeshifter import metadata as md
from shapeshifter.core import ConversionError


def by_key(fields):
    return {f.key: f.value for f in fields}


@pytest.fixture
def jpg(tmp_path):
    p = tmp_path / "a.jpg"
    Image.new("RGB", (64, 48), (200, 40, 40)).save(p, "JPEG", quality=90)
    return p


def test_jpeg_edit_is_lossless(jpg):
    before = jpg.read_bytes()
    out = md.apply(jpg, "jpg", {"author": "Ada", "title": "Sunset", "gps": "13.0827, 80.2707", "taken": "2020-05-17 10:30:00",
                                 "copyright": "(c) Ada", "comments": "café ☕"}, cfg={"original": "keep"})
    assert out.name == "a (edited).jpg"
    v = by_key(md.read(out, "jpg"))
    assert v["author"] == "Ada" and v["title"] == "Sunset" and v["copyright"] == "(c) Ada"
    assert v["taken"] == "2020-05-17 10:30:00"
    assert v["comments"] == "café ☕"
    lat, lon = (float(x) for x in v["gps"].split(","))
    assert abs(lat - 13.0827) < 1e-3 and abs(lon - 80.2707) < 1e-3
    # compressed picture data is byte-identical
    assert before[before.index(b"\xff\xda"):] == out.read_bytes()[out.read_bytes().index(b"\xff\xda"):]
    assert jpg.read_bytes() == before


def test_jpeg_remove_location_keeps_rest(jpg):
    e1 = md.apply(jpg, "jpg", {"author": "Ada", "gps": "1.5, 2.5"}, cfg={"original": "keep"})
    e2 = md.apply(e1, "jpg", {"gps": ""}, cfg={"original": "keep"})
    v = by_key(md.read(e2, "jpg"))
    assert v["gps"] == "" and v["author"] == "Ada"


def test_jpeg_strip_all(jpg):
    e1 = md.apply(jpg, "jpg", {"author": "Ada", "gps": "1.5, 2.5"}, cfg={"original": "keep"})
    e2 = md.apply(e1, "jpg", {}, strip_all=True, cfg={"original": "keep"})
    v = by_key(md.read(e2, "jpg"))
    assert v["author"] == "" and v["gps"] == ""
    assert Image.open(e2).size == (64, 48)


def test_png_text_and_pixels(tmp_path):
    p = tmp_path / "a.png"
    Image.new("RGBA", (10, 10), (1, 2, 3, 4)).save(p)
    out = md.apply(p, "png", {"author": "Ada", "title": "Pic"}, cfg={"original": "keep"})
    v = by_key(md.read(out, "png"))
    assert v["author"] == "Ada" and v["title"] == "Pic"
    assert list(Image.open(out).getdata()) == list(Image.open(p).getdata())
    out2 = md.apply(out, "png", {"author": ""}, cfg={"original": "keep"})
    assert by_key(md.read(out2, "png"))["author"] == ""


def test_webp_resave(tmp_path):
    p = tmp_path / "a.webp"
    Image.new("RGB", (16, 16), (9, 9, 9)).save(p, "WEBP")
    out = md.apply(p, "webp", {"author": "Ada"}, cfg={"original": "keep"})
    assert by_key(md.read(out, "webp"))["author"] == "Ada"


def test_bad_inputs(jpg):
    with pytest.raises(ConversionError):
        md.apply(jpg, "jpg", {"gps": "nowhere"}, cfg={"original": "keep"})
    with pytest.raises(ConversionError):
        md.apply(jpg, "jpg", {"taken": "yesterday"}, cfg={"original": "keep"})


def test_pdf(tmp_path):
    import pymupdf
    p = tmp_path / "a.pdf"
    d = pymupdf.open()
    d.new_page().insert_text((50, 50), "hello")
    d.save(str(p))
    d.close()
    out = md.apply(p, "pdf", {"author": "Ada", "title": "T", "keywords": "a, b"}, cfg={"original": "keep"})
    v = by_key(md.read(out, "pdf"))
    assert v["author"] == "Ada" and v["title"] == "T" and v["keywords"] == "a, b"
    out2 = md.apply(out, "pdf", {}, strip_all=True, cfg={"original": "keep"})
    assert by_key(md.read(out2, "pdf"))["author"] == ""
    with pymupdf.open(str(out2)) as d2:
        assert "hello" in d2[0].get_text()


def make_docx(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>')
        z.writestr("word/document.xml", "<doc/>")


def test_docx_without_core_part(tmp_path):
    p = tmp_path / "a.docx"
    make_docx(p)
    out = md.apply(p, "docx", {"author": "Ada", "title": "Report", "company": "ACME", "created": "2021-01-02 03:04:05"}, cfg={"original": "keep"})
    v = by_key(md.read(out, "docx"))
    assert v["author"] == "Ada" and v["title"] == "Report" and v["created"].startswith("2021-01-0")
    with zipfile.ZipFile(out) as z:
        assert "docProps/core.xml" in z.namelist() and z.read("word/document.xml") == b"<doc/>"
    out2 = md.apply(out, "docx", {"author": ""}, cfg={"original": "keep"})
    assert by_key(md.read(out2, "docx"))["author"] == ""


def test_odt(tmp_path):
    p = tmp_path / "a.odt"
    meta = ('<?xml version="1.0"?><office:document-meta xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<office:meta><dc:title>Old</dc:title></office:meta></office:document-meta>')
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        z.writestr("meta.xml", meta)
    out = md.apply(p, "odt", {"title": "New", "author": "Ada", "keywords": "x, y"}, cfg={"original": "keep"})
    v = by_key(md.read(out, "odt"))
    assert v["title"] == "New" and v["author"] == "Ada" and v["keywords"] == "x, y"


def test_original_recycled_and_mtime_kept(jpg, monkeypatch):
    import os
    import sys
    import types
    gone = []
    monkeypatch.setitem(sys.modules, "send2trash", types.SimpleNamespace(send2trash=lambda p: (gone.append(p), os.remove(p))))
    os.utime(jpg, (1_500_000_000, 1_500_000_000))
    out = md.apply(jpg, "jpg", {"author": "Ada"}, cfg={"original": "recycle"})
    assert out == jpg and gone and abs(out.stat().st_mtime - 1_500_000_000) < 2


def test_file_dates(jpg):
    out = md.apply(jpg, "jpg", {"file_modified": "2019-03-04 05:06:07", "file_created": "2018-01-01 00:00:00"}, cfg={"original": "keep"})
    v = by_key(md.read(out, "jpg"))
    assert v["file_modified"] == "2019-03-04 05:06:07" and v["file_created"] == "2018-01-01 00:00:00"


def test_assignments_and_common(jpg, tmp_path):
    f = md.read(jpg, "jpg")
    assert md.parse_assignments(["Author=Ada", "gps=1,2"], f) == {"author": "Ada", "gps": "1,2"}
    with pytest.raises(ConversionError):
        md.parse_assignments(["Nope=1"], f)
    p2 = tmp_path / "b.png"
    Image.new("RGB", (4, 4)).save(p2)
    keys = {x.key for x in md.common_fields([jpg, p2], ["jpg", "png"])}
    assert "author" in keys and "title" in keys and "gps" not in keys or "author" in keys


def test_unsupported():
    assert not md.supports("zip") and md.supports("jpg") and md.supports("docx")
