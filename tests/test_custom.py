"""User-described formats, the 'Treat as' choice for ambiguous files, and legacy migration."""

from __future__ import annotations

import io
import json
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shapeshifter import config, custom, engine, planner  # noqa: E402
from shapeshifter.backends import Context  # noqa: E402
from shapeshifter.identify import identify  # noqa: E402

CFG = dict(config.DEFAULTS, original="keep")


@pytest.fixture
def formats_dir(tmp_path, monkeypatch):
    d = tmp_path / "formats"
    d.mkdir()
    monkeypatch.setattr(custom, "FORMATS_DIR", d)
    custom.reload()
    yield d
    monkeypatch.undo()
    custom.reload()


def write_spec(d: Path, name: str, spec: dict):
    import yaml
    (d / f"{name}.yaml").write_text(yaml.safe_dump(spec))
    custom.reload()


def test_records_format(tmp_path, formats_dir):
    write_spec(formats_dir, "slog", {
        "id": "slog", "name": "Sensor log", "extensions": ["slog"], "category": "data",
        "match": [{"ascii": "SLOG"}],
        "decode": {"type": "records", "header": 8, "endian": "little",
                   "fields": [{"name": "time", "type": "u32"}, {"name": "temp", "type": "f32"},
                              {"name": "tag", "type": "str", "size": 4}]}})
    p = tmp_path / "reading.bin"                 # wrong extension on purpose
    p.write_bytes(b"SLOG\x00\x00\x00\x02" + struct.pack("<If4s", 10, 21.5, b"ab")
                  + struct.pack("<If4s", 20, 22.25, b"cd"))
    ident = identify(p)
    assert ident.fmt == "slog" and ident.mismatch
    keys = [o.key for o in engine.options_for(p)]
    assert {"csv", "json", "xlsx"} <= set(keys) and "rename:slog" in keys
    res = engine.convert_inplace(p, "json", Context(cfg=CFG))
    assert json.loads(res.outputs[0].read_text()) == [
        {"time": 10, "temp": 21.5, "tag": "ab"}, {"time": 20, "temp": 22.25, "tag": "cd"}]


def test_embedded_format_chains_into_image_conversions(tmp_path, formats_dir):
    from PIL import Image
    write_spec(formats_dir, "skin", {
        "id": "skin", "name": "Game skin", "extensions": ["skin"], "category": "image",
        "match": [{"magic": "534B494E"}],
        "decode": {"type": "embedded", "as": "png", "search": "89504E470D0A1A0A"}})
    b = io.BytesIO()
    Image.new("RGB", (12, 9), "purple").save(b, "PNG")
    p = tmp_path / "hero.skin"
    p.write_bytes(b"SKIN" + b"\x00" * 60 + b.getvalue())
    assert identify(p).fmt == "skin"
    opts = {o.key: o for o in engine.options_for(p)}
    assert "png" in opts and "webp" in opts and len(opts["webp"].path) == 2   # skin -> png -> webp
    res = engine.convert_inplace(p, "webp", Context(cfg=CFG))
    assert Image.open(res.outputs[0]).size == (12, 9)


def test_text_table_format(tmp_path, formats_dir):
    write_spec(formats_dir, "plog", {
        "id": "plog", "name": "Ping log", "extensions": ["plog"], "category": "data",
        "match": [{"text_regex": "^PING "}],
        "decode": {"type": "text-table",
                   "regex": r"time=(?P<ms>[\d.]+) ms ttl=(?P<ttl>\d+)"}})
    p = tmp_path / "net.txt"
    p.write_text("PING host\nreply time=12.5 ms ttl=56\nreply time=13 ms ttl=56\n")
    assert identify(p).fmt == "plog"
    res = engine.convert_inplace(p, "csv", Context(cfg=CFG))
    assert res.outputs[0].read_text().splitlines() == ["ms,ttl", "12.5,56", "13,56"]


def test_bad_spec_reports_error(formats_dir):
    (formats_dir / "broken.yaml").write_text("id: Bad Id!\nmatch: [{magic: zz}]\n")
    custom.reload()
    spec = custom.load_specs()[0]
    assert spec.error and "id" in spec.error


def test_template_from_sample(tmp_path, formats_dir):
    sample = tmp_path / "thing.xyz"
    sample.write_bytes(b"\xCA\xFE\xBA\xBE1234")
    path = custom.create_template(sample)
    text = path.read_text()
    assert 'magic: "CAFEBABE31323334"' in text and "extensions: [xyz]" in text
    custom.reload()
    assert not custom.load_specs()[0].error        # the template is valid as written


# ------------------------------------------------------------------ ambiguity

def test_ambiguous_file_asks_first(tmp_path):
    p = tmp_path / "notes.dat"
    p.write_text("# Shopping\n\n- milk\n- eggs\n")
    ident = identify(p)
    assert ident.confidence < 0.6
    keys = [o.key for o in engine.options_for(p)]
    assert keys[:2] == ["assume:md", "assume:txt"] and keys[-1] == "inspect"
    second = [o.key for o in engine.options_for(p, fmt="md")]
    assert "html" in second and "rename:md" in second
    res = engine.convert_inplace(p, "html", Context(cfg=CFG), src_fmt="md")
    html = res.outputs[0].read_text()
    assert "<h1>Shopping</h1>" in html and "<li>milk</li>" in html


# ------------------------------------------------------------------ migration

def test_legacy_settings_migrate(tmp_path, monkeypatch):
    from shapeshifter import shell
    appdata, local = tmp_path / "Roaming", tmp_path / "Local"
    (appdata / "InplaceConverter").mkdir(parents=True)
    (appdata / "InplaceConverter" / "config.json").write_text(json.dumps({"theme": "neon"}))
    (local / "InplaceConverter" / "venv").mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(config, "CONFIG_DIR", appdata / "ShapeShifter")
    monkeypatch.setattr(config, "CONFIG_FILE", appdata / "ShapeShifter" / "config.json")
    monkeypatch.setattr(config, "DATA_DIR", local / "ShapeShifter")
    monkeypatch.setattr(config, "STATE_FILE", local / "ShapeShifter" / "state.json")
    done = shell.migrate_legacy()
    assert json.loads(config.CONFIG_FILE.read_text())["theme"] == "neon"
    assert not (local / "InplaceConverter").exists()
    assert any("settings" in d for d in done)
