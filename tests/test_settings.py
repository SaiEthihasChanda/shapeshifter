"""Tests for wheel filtering, config migration and themes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shapeshifter import config, engine, themes  # noqa: E402
from shapeshifter.winutil import parse_hotkey  # noqa: E402


def test_hidden_pairs_remove_targets():
    cfg = dict(config.DEFAULTS, hidden_pairs=["jpg>pdf"])
    assert "pdf" in engine.visible_targets(["a.jpg"], config.DEFAULTS)
    assert "pdf" not in engine.visible_targets(["a.jpg"], cfg)
    assert "pdf" in engine.visible_targets(["a.png"], cfg)  # only that pair is hidden


def test_hidden_pair_applies_to_mixed_selection():
    cfg = dict(config.DEFAULTS, hidden_pairs=["png>webp"])
    assert "webp" not in engine.visible_targets(["a.jpg", "b.png"], cfg)


def test_max_segments():
    cfg = dict(config.DEFAULTS, max_segments=5)
    assert len(engine.visible_targets(["a.jpg"], cfg)) == 5


def test_legacy_hotkey_migrates(tmp_path, monkeypatch):
    f = tmp_path / "config.json"
    f.write_text(json.dumps({"hotkey": "ctrl+shift+k", "original": "keep"}))
    monkeypatch.setattr(config, "CONFIG_FILE", f)
    cfg = config.load()
    assert cfg["hotkeys"][0] == {"keys": "ctrl+shift+k", "action": "wheel"}
    assert "hotkey" not in cfg and cfg["original"] == "keep"


def test_themes_resolve_and_colors():
    for key in themes.THEMES:
        th = themes.resolve({"theme": key})
        assert th.key == key
        assert themes.segment_color(th, {}, "image").startswith("#")
    mono = {"theme": "midnight", "color_by_category": False, "accent_color": "#123456"}
    assert themes.segment_color(themes.resolve(mono), mono, "video") == "#123456"
    assert themes.resolve({"theme": "nope"}).key == themes.DEFAULT_THEME


def test_mouse_specs():
    import pytest
    from shapeshifter.mousehook import is_mouse_spec, parse_mouse_spec
    assert is_mouse_spec("mouse:middle") and is_mouse_spec("ctrl+wheel:up")
    assert not is_mouse_spec("ctrl+alt+c")
    assert parse_mouse_spec("mouse:middle") == (0, "middle")
    assert parse_mouse_spec("ctrl+shift+mouse:x2") == (1 | 4, "x2")
    assert parse_mouse_spec("alt+wheel:down") == (2, "wheel:down")
    for bad in ("mouse:left", "mouse:right", "wheel:up", "mouse:nope"):
        with pytest.raises(ValueError):
            parse_mouse_spec(bad)


def test_parse_hotkey():
    mods, vk = parse_hotkey("ctrl+alt+p")
    assert vk == ord("P") and mods & 0x3 == 0x3
    assert parse_hotkey("win+f5")[1] == 0x74
