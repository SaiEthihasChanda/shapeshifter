"""Content-based identification, the conversion planner, archives and risky-file refusal."""

from __future__ import annotations

import io
import json
import shutil
import sqlite3
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shapeshifter import config, engine, planner  # noqa: E402
from shapeshifter.backends import Context, RiskyFile  # noqa: E402
from shapeshifter.identify import identify  # noqa: E402

CFG = dict(config.DEFAULTS, original="keep")


def png_bytes(color="red", size=(24, 16), mode="RGB") -> bytes:
    from PIL import Image
    b = io.BytesIO()
    Image.new(mode, size, color).save(b, "PNG")
    return b.getvalue()


# --------------------------------------------------------------- identification

@pytest.mark.parametrize("name", ["mystery.dat", "noextension", "wrong.jpg", "photo.png"])
def test_png_identified_by_content(tmp_path, name):
    p = tmp_path / name
    p.write_bytes(png_bytes())
    ident = identify(p)
    assert ident.fmt == "png" and ident.confidence >= 0.9
    assert ident.mismatch == (name in ("mystery.dat", "wrong.jpg"))
    assert ident.needs_rename == (name != "photo.png")
    keys = [o.key for o in engine.options_for(p)]
    assert "webp" in keys and "jpg" in keys
    assert ("rename:png" in keys) == (name != "photo.png")


@pytest.mark.parametrize("content,expected", [
    ("name,age\nann,31\nbob,42\n", "csv"),
    ("a\tb\tc\n1\t2\t3\n4\t5\t6\n", "tsv"),
    (json.dumps({"a": [1, 2]}), "json"),
    ('<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>', "svg"),
    ("<!DOCTYPE html><html><body>x</body></html>", "html"),
    ("[server]\nport = 80\nhost = 'x'\n", "toml"),
])
def test_text_formats(tmp_path, content, expected):
    p = tmp_path / "file.bin"
    p.write_text(content)
    assert identify(p).fmt == expected


def test_media_containers(tmp_path):
    cases = {
        b"RIFF\x00\x00\x00\x00WAVEfmt ": "wav",
        b"RIFF\x00\x00\x00\x00WEBPVP8 ": "webp",
        b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic": "heic",
        b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2": "mp4",
        b"\x1a\x45\xdf\xa3\x93\x42\x82\x88matroska": "mkv",
        b"OggS\x00\x02" + b"\x00" * 22 + b"\x01vorbis": "ogg",
        b"fLaC\x00\x00\x00\x22": "flac",
    }
    for data, fmt in cases.items():
        p = tmp_path / f"x_{fmt}.bin"
        p.write_bytes(data + b"\x00" * 64)
        assert identify(p).fmt == fmt, fmt


def test_office_zip_detection(tmp_path):
    p = tmp_path / "report.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w:document/>")
    ident = identify(p)
    assert ident.fmt == "docx" and ident.mismatch


def test_unknown_binary_offers_inspect(tmp_path):
    p = tmp_path / "blob.xyz"
    p.write_bytes(bytes(range(256)) * 64)
    ident = identify(p)
    assert ident.fmt is None
    assert [o.key for o in engine.options_for(p)] == ["inspect"]


# ------------------------------------------------------------------ risky files

def test_refuses_programs_and_disguised_programs(tmp_path):
    (tmp_path / "setup.exe").write_bytes(b"MZ" + b"\x00" * 100)
    (tmp_path / "invoice.pdf").write_bytes(b"MZ\x90\x00" + b"\x00" * 100)
    (tmp_path / "run.bat").write_text("echo hi")
    for name in ("setup.exe", "invoice.pdf", "run.bat"):
        ident = identify(tmp_path / name)
        assert ident.risky, name
        assert engine.options_for(tmp_path / name) == []
        with pytest.raises(RiskyFile):
            engine.convert_inplace(tmp_path / name, "txt", Context(cfg=CFG))
    assert "disguised" in identify(tmp_path / "invoice.pdf").risky


def test_refuses_zip_slip_and_macros(tmp_path):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("../../outside.txt", "x")
    assert "unsafe path" in identify(evil).risky
    macro = tmp_path / "budget.xlsx"
    with zipfile.ZipFile(macro, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/workbook.xml", "<w/>")
        z.writestr("xl/vbaProject.bin", b"\x00")
    assert "macros" in identify(macro).risky


def test_refuses_zip_bomb(tmp_path):
    bomb = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("zeros.bin", b"\x00" * (120 * 1024 ** 2))
    assert "suspiciously" in identify(bomb).risky


# --------------------------------------------------------------- conversions

def test_renamed_png_converts_and_fix_name(tmp_path):
    p = tmp_path / "picture.dat"
    p.write_bytes(png_bytes(mode="RGBA"))
    res = engine.convert_inplace(p, "webp", Context(cfg=CFG))
    assert res.outputs[0].name == "picture.webp" and identify(res.outputs[0]).fmt == "webp"
    res = engine.convert_inplace(p, "rename:png", Context(cfg=CFG))
    assert res.outputs[0].name == "picture.png" and not p.exists()


def test_multi_step_cbz_to_png(tmp_path):
    p = tmp_path / "comic.cbz"
    with zipfile.ZipFile(p, "w") as z:
        for i in range(2):
            z.writestr(f"page{i}.png", png_bytes("blue"))
    opt = planner.plan("cbz")["png"]
    assert [e.dst for e in opt.path] == ["pdf", "png"]
    assert any("2-step" in w for w in opt.warnings)
    res = engine.convert_inplace(p, "png", Context(cfg=CFG))
    assert sorted(o.name for o in res.outputs) == ["comic-1.png", "comic-2.png"]


def test_planner_has_no_nonsense_chains():
    assert "mp4" not in planner.plan("png")           # still image -> video
    assert "txt" not in planner.plan("cbz")           # comics have no text
    assert "bmp" not in planner.plan("cbz")           # no long image chains
    jpg = planner.plan("png")["jpg"]
    assert len(jpg.path) == 1 and "Transparency becomes white" in jpg.warnings


def test_archive_round_trip_and_extract(tmp_path):
    src = tmp_path / "bundle.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("a.txt", "hello")
        z.writestr("sub/b.txt", "world")
    res = engine.convert_inplace(src, "tgz", Context(cfg=CFG))
    tgz = res.outputs[0]
    assert tgz.name == "bundle.tar.gz" and identify(tgz).fmt == "tgz"
    with tarfile.open(tgz) as t:
        assert sorted(t.getnames()) == ["a.txt", "sub", "sub/b.txt"]
    res = engine.convert_inplace(tgz, "7z", Context(cfg=CFG))
    assert res.outputs[0].name == "bundle.7z" and identify(res.outputs[0]).fmt == "7z"
    res = engine.convert_inplace(res.outputs[0], "extract", Context(cfg=CFG))
    folder = res.outputs[0]
    assert folder.is_dir() and (folder / "sub" / "b.txt").read_text() == "world"


def test_sqlite_to_spreadsheet(tmp_path):
    db = tmp_path / "app.db"
    con = sqlite3.connect(db)
    con.execute("create table people(name, age)")
    con.executemany("insert into people values (?, ?)", [("ann", 31), ("bob", 42)])
    con.commit()
    con.close()
    assert identify(db).fmt == "sqlite"
    res = engine.convert_inplace(db, "json", Context(cfg=CFG))
    assert json.loads(res.outputs[0].read_text()) == [{"name": "ann", "age": 31},
                                                     {"name": "bob", "age": 42}]
    assert db.exists()   # opened read-only; original untouched (original="keep")


def test_gzip_single_file_extract(tmp_path):
    import gzip
    p = tmp_path / "notes.txt.gz"
    with gzip.open(p, "wb") as f:
        f.write(b"hello there")
    assert identify(p).fmt == "gz"
    res = engine.convert_inplace(p, "extract", Context(cfg=CFG))
    assert res.outputs[0].name == "notes.txt" and res.outputs[0].read_text() == "hello there"


def test_inspector_report(tmp_path):
    from shapeshifter.inspector import report
    p = tmp_path / "x.dat"
    p.write_bytes(png_bytes())
    rep = report(str(p))
    assert rep["format"] == "png" and rep["mismatch"] and "webp" in rep["conversions"]
