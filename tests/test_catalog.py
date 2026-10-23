"""Configured file types, their native converters, learned reverse conversions, the graph
and the multi-ring wheel."""

from __future__ import annotations

import base64
import json
import plistlib
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shapeshifter import catalog, config, engine, learned, planner  # noqa: E402
from shapeshifter.backends import Context, RiskyFile  # noqa: E402
from shapeshifter.identify import identify  # noqa: E402

CFG = dict(config.DEFAULTS, original="keep")


def convert(p: Path, target: str, **kw) -> list[Path]:
    return engine.convert_inplace(p, target, Context(cfg=CFG), **kw).outputs


# ------------------------------------------------------------- ASN.1 builders

def tlv(tag: int, content: bytes) -> bytes:
    n = len(content)
    if n < 128:
        length = bytes([n])
    else:
        b = n.to_bytes((n.bit_length() + 7) // 8, "big")
        length = bytes([0x80 | len(b)]) + b
    return bytes([tag]) + length + content


def oid(dotted: str) -> bytes:
    parts = [int(x) for x in dotted.split(".")]
    body = bytes([parts[0] * 40 + parts[1]])
    for v in parts[2:]:
        enc = [v & 0x7F]
        v >>= 7
        while v:
            enc.insert(0, 0x80 | (v & 0x7F))
            v >>= 7
        body += bytes(enc)
    return tlv(0x06, body)


SEQ = lambda *xs: tlv(0x30, b"".join(xs))  # noqa: E731
INT = lambda v: tlv(0x02, v.to_bytes(max(1, (v.bit_length() + 8) // 8), "big"))  # noqa: E731
NAME = lambda cn: SEQ(tlv(0x31, SEQ(oid("2.5.4.3"), tlv(0x0C, cn.encode()))))  # noqa: E731
RSA_ALG = SEQ(oid("1.2.840.113549.1.1.1"), tlv(0x05, b""))
MODULUS = int("C" + "5" * 255, 16)


def make_cert() -> bytes:
    spki = SEQ(RSA_ALG, tlv(0x03, b"\x00" + SEQ(INT(MODULUS), INT(65537))))
    san = SEQ(SEQ(oid("2.5.29.17"), tlv(0x04, SEQ(tlv(0x82, b"example.com")))))
    tbs = SEQ(tlv(0xA0, INT(2)), INT(4242), SEQ(oid("1.2.840.113549.1.1.11"), tlv(0x05, b"")),
              NAME("Test CA"), SEQ(tlv(0x17, b"250101000000Z"), tlv(0x17, b"350101000000Z")),
              NAME("example.com"), spki, tlv(0xA3, san))
    return SEQ(tbs, SEQ(oid("1.2.840.113549.1.1.11"), tlv(0x05, b"")), tlv(0x03, b"\x00" + b"\x11" * 64))


def make_private_key() -> bytes:
    rsa = SEQ(INT(0), INT(MODULUS), INT(65537), INT(12345), INT(3), INT(5), INT(7), INT(9), INT(11))
    return SEQ(INT(0), RSA_ALG, tlv(0x04, rsa))


def pem(der: bytes, label: str) -> str:
    b64 = base64.b64encode(der).decode()
    return f"-----BEGIN {label}-----\n" + "\n".join(b64[i:i + 64] for i in range(0, len(b64), 64)) \
        + f"\n-----END {label}-----\n"


# ----------------------------------------------------------- certificates

def test_pem_certificate_identified_and_converted(tmp_path):
    p = tmp_path / "server.crt"
    p.write_text(pem(make_cert(), "CERTIFICATE"))
    ident = identify(p)
    assert ident.fmt == "pem" and ident.confidence >= 0.9 and not ident.details["sensitive"]
    keys = {o.key for o in engine.options_for(p)}
    assert {"der", "txt", "json"} <= keys and "md" not in keys
    info = json.loads(convert(p, "json")[0].read_text())
    assert info["subject"] == "CN=example.com" and info["issuer"] == "CN=Test CA"
    assert info["subject_alt_names"] == ["example.com"] and info["key_bits"] == 1024
    der = convert(p, "der")[0]
    assert der.read_bytes() == make_cert() and identify(der).fmt == "der"
    back = convert(der, "pem")[0]
    assert "BEGIN CERTIFICATE" in back.read_text()


def test_private_key_is_restricted(tmp_path):
    p = tmp_path / "server.key"
    p.write_text(pem(make_private_key(), "PRIVATE KEY"))
    ident = identify(p)
    assert ident.fmt == "pem" and "private key" in ident.details["sensitive"]
    keys = {o.key for o in engine.options_for(p)}
    assert keys <= {"der", "inspect"} and "der" in keys
    with pytest.raises(RiskyFile):
        convert(p, "txt")
    der = convert(p, "der")[0]
    assert identify(der).details["sensitive"]          # still recognised as a key


# --------------------------------------------------------- text-based kinds

SRT = "1\n00:00:01,000 --> 00:00:02,500\nHello\n\n2\n00:00:03,000 --> 00:00:04,000\nWorld\n"


@pytest.mark.parametrize("name,content,fmt", [
    ("a.dat", SRT, "srt"),
    ("b.dat", "WEBVTT\n\n00:01.000 --> 00:02.000\nHi\n", "vtt"),
    ("c.dat", "BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n", "ics"),
    ("d.dat", "BEGIN:VCARD\nFN:Ann\nEND:VCARD\n", "vcf"),
    ("e.dat", "From: a@b.c\nTo: d@e.f\nSubject: Hi\nDate: Mon, 1 Jan 2024 10:00:00 +0000\n\nBody\n", "eml"),
    ("f.dat", "solid cube\n  facet normal 0 0 1\n    outer loop\n", "stl"),
    ("g.dat", "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", "obj"),
    ("h.dat", '{"type": "FeatureCollection", "features": []}', "geojson"),
    ("i.dat", '{"nbformat": 4, "cells": []}', "ipynb"),
    ("j.dat", '{"a": 1}\n{"a": 2}\n{"a": 3}\n', "jsonl"),
    ("k.dat", "#!/usr/bin/env python3\nprint('hi')\n", "py"),
    ("l.dat", "\\documentclass{article}\n\\begin{document}x\\end{document}\n", "tex"),
    ("m.dat", "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMq user@host\n", "sshpub"),
    ("n.dat", '<?xml version="1.0"?><gpx version="1.1"><wpt lat="1" lon="2"/></gpx>', "gpx"),
    ("o.rs", "fn main() { println!(\"hi\"); }\n", "rs"),
])
def test_configured_text_kinds(tmp_path, name, content, fmt):
    p = tmp_path / name
    p.write_text(content)
    assert identify(p).fmt == fmt


def test_binary_kinds(tmp_path):
    cases = {"x1": (b"wOFF" + b"\x00" * 60, "woff"), "x2": (b"glTF\x02\x00\x00\x00" + b"\x00" * 20, "glb"),
             "x3": (b"bplist00" + b"\x00" * 30, "bplist"), "x4": (b"\x28\xb5\x2f\xfd" + b"\x00" * 20, "zst"),
             "x5": (b"!<arch>\ndebian-binary   " + b"\x00" * 40, "deb"), "x6": (b"\x00asm\x01\x00\x00\x00", "wasm"),
             "x7": (b"PAR1" + b"\x00" * 20, "parquet"), "x8": (b"MThd\x00\x00\x00\x06", "mid")}
    for name, (data, fmt) in cases.items():
        p = tmp_path / name
        p.write_bytes(data)
        assert identify(p).fmt == fmt, fmt
    assert identify(tmp_path / "x6").risky                   # WebAssembly is a program


def test_subtitles(tmp_path):
    p = tmp_path / "movie.srt"
    p.write_text(SRT)
    vtt = convert(p, "vtt")[0].read_text()
    assert vtt.startswith("WEBVTT") and "00:00:01.000 --> 00:00:02.500" in vtt
    ass = convert(p, "ass")[0]
    back = convert(ass, "srt")[0].read_text()
    assert "Hello" in back and "00:00:03,000 --> 00:00:04,000" in back
    assert convert(p, "lrc")[0].read_text().startswith("[00:01.00]Hello")


def test_geodata_round_trip(tmp_path):
    p = tmp_path / "trip.gpx"
    p.write_text('<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">'
                 '<wpt lat="51.5" lon="-0.12"><name>London</name></wpt>'
                 '<trk><name>Walk</name><trkseg><trkpt lat="1" lon="2"/><trkpt lat="3" lon="4"/></trkseg></trk></gpx>')
    gj = json.loads(convert(p, "geojson")[0].read_text())
    assert gj["features"][0]["geometry"] == {"type": "Point", "coordinates": [-0.12, 51.5]}
    assert gj["features"][1]["geometry"]["type"] == "LineString"
    kml = convert(tmp_path / "trip.geojson", "kml")[0]
    assert "London" in kml.read_text() and identify(kml).fmt == "kml"
    rows = convert(p, "csv")[0].read_text().splitlines()
    assert rows[0].startswith("name,kind,longitude") and len(rows) == 4


def test_calendar_and_contacts(tmp_path):
    ics = tmp_path / "cal.ics"
    ics.write_text("BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:Lunch\\, with team\nDTSTART:20240101T120000\n"
                   "END:VEVENT\nEND:VCALENDAR\n")
    rows = json.loads(convert(ics, "json")[0].read_text())
    assert rows[0]["summary"] == "Lunch, with team" and rows[0]["start"] == "20240101T120000"
    csv = tmp_path / "people.csv"
    csv.write_text("Name,Email,Phone\nAnn Lee,ann@x.org,123\n")
    vcf = convert(csv, "vcf")[0]
    assert "FN:Ann Lee" in vcf.read_text() and identify(vcf).fmt == "vcf"
    back = json.loads(convert(vcf, "json")[0].read_text())
    assert back[0]["email"] == "ann@x.org"


def test_email_and_mailbox(tmp_path):
    eml = tmp_path / "m.eml"
    eml.write_text("From: a@b.c\nTo: d@e.f\nSubject: Hello\nDate: Mon, 1 Jan 2024 10:00:00 +0000\n"
                   "Content-Type: text/plain\n\nHi there\n")
    assert "Subject: Hello" in convert(eml, "txt")[0].read_text()
    assert convert(eml, "md")[0].read_text().startswith("# Hello")
    box = tmp_path / "inbox.mbox"
    box.write_text("From a@b.c Mon Jan  1 10:00:00 2024\nSubject: One\n\nfirst\n\n"
                   "From a@b.c Mon Jan  1 11:00:00 2024\nSubject: Two\n\nsecond\n")
    assert identify(box).fmt == "mbox"
    assert len(convert(box, "eml")) == 2


def test_notebook_torrent_plist_env(tmp_path):
    nb = tmp_path / "n.ipynb"
    nb.write_text(json.dumps({"nbformat": 4, "metadata": {}, "cells": [
        {"cell_type": "markdown", "source": ["# Title"]},
        {"cell_type": "code", "source": ["x = 1\n", "print(x)"], "outputs": [
            {"output_type": "stream", "text": ["1\n"]}]}]}))
    md = convert(nb, "md")[0].read_text()
    assert "# Title" in md and "```python\nx = 1" in md
    assert "print(x)" in convert(nb, "py")[0].read_text()

    t = tmp_path / "f.torrent"
    t.write_bytes(b"d8:announce14:http://tracker4:infod4:name5:a.txt6:lengthi12eee")
    obj = json.loads(convert(t, "json")[0].read_text())
    assert obj["announce"] == "http://tracker" and obj["info"]["length"] == 12

    pl = tmp_path / "Info.plist"
    pl.write_bytes(plistlib.dumps({"CFBundleName": "App", "Version": 3}))
    assert json.loads(convert(pl, "json")[0].read_text()) == {"CFBundleName": "App", "Version": 3}
    bp = convert(pl, "bplist")[0]
    assert identify(bp).fmt == "bplist" and plistlib.loads(bp.read_bytes())["Version"] == 3

    env = tmp_path / "app.env"
    env.write_text('export DB_HOST="localhost"\nPORT=5432\n# comment\n')
    assert json.loads(convert(env, "json")[0].read_text()) == {"DB_HOST": "localhost", "PORT": "5432"}


def test_json_family(tmp_path):
    jl = tmp_path / "rows.jsonl"
    jl.write_text('{"a": 1, "b": "x"}\n{"a": 2}\n')
    assert convert(jl, "csv")[0].read_text().splitlines() == ["a,b", "1,x", "2,"]
    j5 = tmp_path / "c.json5"
    j5.write_text("{\n  // comment\n  name: 'demo, a: b',\n  list: [1, 2,],\n}\n")
    assert json.loads(convert(j5, "json")[0].read_text()) == {"name": "demo, a: b", "list": [1, 2]}


def test_meshes_round_trip(tmp_path):
    obj = tmp_path / "quad.obj"
    obj.write_text("v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nf 1 2 3 4\n")
    stl = convert(obj, "stl")[0]
    from shapeshifter.native import mesh
    v, f = mesh.read_stl(stl.read_bytes())
    assert len(f) == 2 and len(v) == 4                 # quad -> 2 triangles, shared vertices
    ply = convert(stl, "ply")[0]
    assert identify(ply).fmt == "ply"
    back = convert(ply, "obj")[0]
    assert back.read_text().count("\nf ") == 2


def test_fonts_round_trip(tmp_path):
    from test_ocr import ARIAL as src
    if not src.exists():
        pytest.skip("no system font to test with")
    ttf = tmp_path / "font.ttf"
    shutil.copy(src, ttf)
    woff = convert(ttf, "woff")[0]
    assert identify(woff).fmt == "woff" and woff.stat().st_size < ttf.stat().st_size
    shutil.move(ttf, tmp_path / "orig.ttf")
    back = convert(woff, "ttf")[0]
    from PIL import ImageFont
    assert ImageFont.truetype(str(back), 20).getname()[0] == "Arial"


def test_code_rendering_and_family(tmp_path):
    py = tmp_path / "hello.py"
    py.write_text("def hi():\n    return '<b>'\n")
    html = convert(py, "html")[0].read_text()
    assert 'class="language-python"' in html and "&lt;b&gt;" in html
    assert convert(py, "md")[0].read_text().startswith("```python\n")
    assert convert(py, "pdf")[0].read_bytes().startswith(b"%PDF")
    assert convert(py, "txt")[0].read_text() == py.read_text()
    apng = tmp_path / "anim.apng"
    from PIL import Image
    Image.new("RGB", (4, 4), "red").save(apng, "PNG")
    assert "png" in {o.key for o in engine.options_for(apng)}


def test_single_file_and_ar_archives(tmp_path):
    from backports import zstd
    z = tmp_path / "notes.txt.zst"
    with zstd.open(z, "wb") as f:
        f.write(b"compressed text")
    assert identify(z).fmt == "zst"
    assert convert(z, "extract")[0].read_bytes() == b"compressed text"
    ar = tmp_path / "lib.a"
    body = b"hello\n"
    ar.write_bytes(b"!<arch>\n" + b"hello.txt/".ljust(16) + b"0".ljust(12) + b"0".ljust(6) * 2
                   + b"644".ljust(8) + str(len(body)).encode().ljust(10) + b"`\n" + body)
    out = convert(ar, "extract")[0]
    assert (out / "hello.txt").read_bytes() == body


# ------------------------------------------------------ learned + graph

@pytest.fixture
def learn_store(tmp_path, monkeypatch):
    monkeypatch.setattr(learned, "FILE", tmp_path / "learned.json")
    yield


def test_learned_reverse_conversion(tmp_path, learn_store):
    # The user's experiment: something -> MD once; afterwards MD files offer the way back.
    src = tmp_path / "server.pem"
    src.write_text("just some text that happens to be named .pem\n")
    ident = identify(src)
    md = tmp_path / "notes.md"
    md.write_text("# Notes\n\nhello\n")
    assert "pem" not in {o.key for o in engine.options_for(md)}
    learned.record("pem", "md")                         # as recorded after a PEM -> MD conversion
    opts = {o.key: o for o in engine.options_for(md)}
    assert "pem" in opts and opts["pem"].learned
    assert any("Learned" in w for w in opts["pem"].warnings)
    out = convert(md, "pem")[0]
    assert out.suffix == ".pem" and "Notes" in out.read_text()
    assert ident.fmt  # (sanity)


def test_conversion_records_reverse(tmp_path, learn_store):
    srt = tmp_path / "a.srt"
    srt.write_text(SRT)
    convert(srt, "vtt")
    assert ("vtt", "srt") in [(a, b) for a, b, _ in learned.pairs()]


def test_graph_route_and_export(tmp_path):
    opt = planner.route("md", "pem")
    assert opt is not None and [e.dst for e in opt.path][-1] == "pem"
    assert any(e.latent for e in opt.path)
    assert "pem" not in planner.plan("md")              # latent: not on the wheel by default
    n = planner.export_graph(tmp_path / "g.dot")
    assert n > 1000 and (tmp_path / "g.dot").read_text().startswith("digraph")
    data = json.loads((tmp_path / "g.json").write_text("") or "{}") if False else None
    planner.export_graph(tmp_path / "g.json", "json")
    g = json.loads((tmp_path / "g.json").read_text())
    assert len(g["nodes"]) > 200 and any(e["latent"] for e in g["edges"])


def test_conditional_routes(tmp_path):
    # Strict planning never puts a "treat as" step in the middle…
    assert planner.route("txt", "der") is None
    # …but the route finders can show it, clearly marked.
    opt = planner.route("txt", "der", conditional=True)
    assert [e.dst for e in opt.path] == ["pem", "der"] and planner.is_conditional(opt)
    assert "only works if the TXT content really is" in opt.warnings[0]
    assert not planner.is_conditional(planner.route("md", "pem"))
    # A "treat as" claim is only allowed about the user's own file, never mid-route:
    # …so never DOCX -> TXT -> SVG -> PNG. The route it does find keeps the pictures
    # (HTML -> PDF -> PNG), not the text-only one:
    docx_png = planner.route("docx", "png", conditional=True)
    assert docx_png is None or [e.dst for e in docx_png.path] == ["html", "pdf", "png"]
    assert not any(e.latent for e in (docx_png.path if docx_png else ()))
    # And a real .txt holding a certificate is simply identified as PEM.
    p = tmp_path / "cert.txt"
    p.write_text(pem(make_cert(), "CERTIFICATE"))
    keys = [o.key for o in engine.options_for(p)]
    assert "der" in keys and "rename:pem" in keys
    assert convert(p, "der")[0].read_bytes() == make_cert()


@pytest.mark.parametrize("name,content,fmt", [
    (".gitignore", "node_modules/\n*.log\n", "gitignore"),
    (".env", "A=1\n", "env"),
    (".editorconfig", "root = true\n", "editorconfig"),
    ("Makefile", "all:\n\tcc x.c\n", "makefile"),
    ("Dockerfile", "FROM alpine\nRUN echo\n", "dockerfile"),
    (".bashrc", "alias ll=ls\n", "bash"),
    ("LICENSE", "MIT License\n", "txt"),
])
def test_dotfiles_and_known_names(tmp_path, name, content, fmt):
    p = tmp_path / name
    p.write_text(content)
    ident = identify(p)
    assert ident.fmt == fmt and ident.confidence >= 0.8 and not ident.mismatch
    keys = {o.key for o in engine.options_for(p)}
    assert ("txt" in keys) == (fmt != "txt") and "html" in keys


def test_dotfile_output_keeps_name(tmp_path):
    p = tmp_path / ".gitignore"
    p.write_text("dist/\n")
    assert convert(p, "txt")[0].name == ".gitignore.txt"


def test_catalog_size():
    assert len(catalog.CATALOG) >= 250


# ---------------------------------------------------------------- wheel

def test_ring_layout():
    from shapeshifter.wheel import ring_layout
    assert ring_layout(8) == [(0, 8)]
    assert ring_layout(12) == [(0, 12)]
    assert ring_layout(30) == [(0, 10), (10, 16), (26, 4)]
    assert ring_layout(48) == [(0, 10), (10, 16), (26, 22)]


def test_wheel_rings_hit_and_search():
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QApplication
    from shapeshifter.wheel import WheelView
    app = QApplication.instance() or QApplication([])  # noqa: F841
    w = WheelView(dict(config.DEFAULTS, wheel_scale=1.0))
    w.resize(700, 800)
    fmts = ["png", "jpg", "webp", "gif", "bmp", "tiff", "ico", "tga", "qoi", "pdf",
            "avif", "heic", "ppm", "mp4", "mp3", "csv", "json", "yaml", "xml", "toml"]
    w.set_content(["C:\\x.png"], fmts)
    assert len(w.rings) == 2
    inner, outer = w.rings
    # straight up at the middle of each ring hits that ring's first segment
    up = lambda r: QPointF(w.c.x(), w.c.y() - r * w.s)  # noqa: E731
    assert w._hit(up((inner.ri + inner.ro) / 2)) == (0, False)
    assert w._hit(up((outer.ri + outer.ro) / 2)) == (outer.start, False)
    w.query = "jp"
    assert w.matches()[0] == fmts.index("jpg")
    w.query = "jpeg"
    assert w.matches() == [fmts.index("jpg")]
    w.query = "zzz"
    assert w.matches() == []
