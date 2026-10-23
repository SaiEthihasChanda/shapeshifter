"""Tests for wheel filtering, config migration and themes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from shapeshifter import config, engine, themes  # noqa: E402


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
    from shapeshifter.triggers import is_mouse_spec, parse_mouse_spec
    assert is_mouse_spec("mouse:middle") and is_mouse_spec("ctrl+wheel:up")
    assert not is_mouse_spec("ctrl+alt+c")
    assert parse_mouse_spec("mouse:middle") == (0, "middle")
    assert parse_mouse_spec("ctrl+shift+mouse:x2") == (1 | 4, "x2")
    assert parse_mouse_spec("alt+wheel:down") == (2, "wheel:down")
    for bad in ("mouse:left", "mouse:right", "wheel:up", "mouse:nope"):
        with pytest.raises(ValueError):
            parse_mouse_spec(bad)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows virtual keys")
def test_parse_hotkey():
    from shapeshifter.winutil import parse_hotkey
    mods, vk = parse_hotkey("ctrl+alt+p")
    assert vk == ord("P") and mods & 0x3 == 0x3
    assert parse_hotkey("win+f5")[1] == 0x74


def test_mac_key_codes_and_modifiers():
    """The macOS event tap's key table (pure Python: runs on any system)."""
    from shapeshifter.mac.events import KEYCODES, mods_from_flags, parse_key
    assert parse_key("ctrl+alt+c") == (1 | 2, KEYCODES["c"]) == (3, 8)
    assert parse_key("cmd+shift+f5") == (8 | 4, 96)
    assert parse_key("win+space") == parse_key("cmd+space")          # a Windows "win" binding is ⌘ on a Mac
    assert mods_from_flags(0x40000 | 0x100000) == 1 | 8              # control + command
    with pytest.raises(ValueError):
        parse_key("ctrl+alt")
    with pytest.raises(ValueError):
        parse_key("ctrl+pause")


def test_mac_quick_action_is_a_finder_service(tmp_path, monkeypatch):
    """The Quick Action bundle: a Finder service that runs one shell step with the files as arguments."""
    import plistlib
    from shapeshifter.mac import integration
    monkeypatch.setattr(integration, "WORKFLOW", tmp_path / "Convert with ShapeShifter.workflow")
    monkeypatch.setattr(integration, "_refresh_services", lambda: None)
    integration.register_context_menu()
    contents = integration.WORKFLOW / "Contents"
    info = plistlib.loads((contents / "Info.plist").read_bytes())
    service = info["NSServices"][0]
    assert service["NSMenuItem"]["default"] == "Convert with ShapeShifter"
    assert service["NSRequiredContext"]["NSApplicationIdentifier"] == "com.apple.finder"
    flow = plistlib.loads((contents / "document.wflow").read_bytes())
    step = flow["actions"][0]["action"]
    assert step["BundleIdentifier"] == "com.apple.RunShellScript" and step["ActionParameters"]["inputMethod"] == 1
    assert '"$@"' in step["ActionParameters"]["COMMAND_STRING"]
    assert flow["workflowMetaData"]["workflowTypeIdentifier"] == "com.apple.Automator.servicesMenu"
    assert integration.context_menu_registered()
    integration.unregister_context_menu(refresh=False)
    assert not integration.context_menu_registered()
