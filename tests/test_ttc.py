"""TrueType collections (.ttc) <-> separate .ttf fonts."""

import struct
import sys
from pathlib import Path

import pytest

from shapeshifter import combine, engine, planner
from shapeshifter.core import Context
from shapeshifter.native import fonts

sys.path.insert(0, str(Path(__file__).parent))
from test_ocr import CFG                                  # noqa: E402


def name_table(full_name: str) -> bytes:
    raw = full_name.encode("utf-16-be")
    # format 0, one record: platform 3, encoding 1, language 0x409, name id 4 (full name)
    return struct.pack(">HHH", 0, 1, 6 + 12) + struct.pack(">HHHHHH", 3, 1, 0x409, 4, len(raw), 0) + raw


def make_ttf(name: str, glyph_bytes: bytes = b"\x01\x02\x03") -> bytes:
    return fonts._pack_sfnt(fonts.TTF_FLAVOR, [(b"head", bytes(54), 0), (b"glyf", glyph_bytes, 0), (b"name", name_table(name), 0)])


def run(path, target):
    return engine.convert_inplace(path, target, Context(cfg=dict(CFG, original="keep"))).outputs


def test_build_and_split_round_trip():
    a, b = make_ttf("Alpha Regular", b"AAAA"), make_ttf("Alpha Bold", b"BBBBBB")
    ttc = fonts.build_ttc([a, b])
    assert ttc[:4] == b"ttcf" and struct.unpack_from(">I", ttc, 8)[0] == 2
    parts = fonts.split_ttc(ttc)
    assert [n for n, _ in parts] == ["Alpha Regular", "Alpha Bold"]
    for (_, got), want in zip(parts, (a, b)):
        _, tables_got = fonts._read_tables(got)
        _, tables_want = fonts._read_tables(want)
        assert {t: p for t, p, _ in tables_got} == {t: p for t, p, _ in tables_want}


def test_conversion_to_separate_files(tmp_path):
    (tmp_path / "family.ttc").write_bytes(fonts.build_ttc([make_ttf("Alpha Regular"), make_ttf("Alpha Bold", b"XY")]))
    outs = run(tmp_path / "family.ttc", "ttf")
    assert sorted(o.name for o in outs) == ["family - Alpha Bold.ttf", "family - Alpha Regular.ttf"]
    flavor, tables = fonts._read_tables(outs[0].read_bytes())
    assert flavor == fonts.TTF_FLAVOR and {t for t, _, _ in tables} == {b"head", b"glyf", b"name"}


def test_fonts_selected_together_become_a_collection(tmp_path):
    for n in ("Alpha", "Beta", "Gamma"):
        (tmp_path / f"{n}.ttf").write_bytes(make_ttf(n))
    paths = [tmp_path / f"{n}.ttf" for n in ("Alpha", "Beta", "Gamma")]
    assert combine.targets_for(paths) == ["ttc"]
    res = engine.combine_inplace([str(p) for p in paths], "combine:ttc", Context(cfg=CFG))
    out = res.outputs[0]
    assert out.suffix == ".ttc" and [n for n, _ in fonts.split_ttc(out.read_bytes())] == ["Alpha", "Beta", "Gamma"]
    assert all(p.exists() for p in paths)


def test_not_a_collection_and_cff_members():
    with pytest.raises(Exception, match="Not a TrueType collection"):
        fonts.split_ttc(b"nonsense" * 10)
    otf = fonts._pack_sfnt(b"OTTO", [(b"CFF ", b"x" * 8, 0), (b"head", bytes(54), 0)])
    with pytest.raises(Exception):
        fonts.build_ttc([otf])


def test_the_graph_knows_the_collection():
    assert planner.route("ttc", "ttf") is not None            # one file per font, so nothing can follow
