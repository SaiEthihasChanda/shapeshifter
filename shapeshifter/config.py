"""User configuration stored in %APPDATA%\\ShapeShifter\\config.json."""

from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path

from . import APP_ID

CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / APP_ID
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_ID
CONFIG_FILE = CONFIG_DIR / "config.json"
STATE_FILE = DATA_DIR / "state.json"
LOG_FILE = DATA_DIR / "shapeshifter.log"

# Hotkey actions: "wheel" (pick a format), "repeat" (last used format),
# "convert" (always convert to "target").
DEFAULT_HOTKEYS = [
    {"keys": "ctrl+alt+c", "action": "wheel"},
    {"keys": "ctrl+alt+shift+c", "action": "repeat"},
]

DEFAULTS = {
    # ---- conversion
    # What happens to the source file after a successful conversion:
    # "recycle" (send to Recycle Bin), "keep" (leave it), "delete" (permanently remove).
    "original": "recycle",
    "jpeg_quality": 92,
    "webp_quality": 90,
    "avif_quality": 75,
    "render_dpi": 150,            # PDF/SVG -> image resolution
    "preserve_mtime": True,       # converted file keeps the source's modified time
    "prefer_native": False,       # use ShapeShifter's own converters before libraries
    "learn_reverse": True,        # after A -> B, offer B -> A on B files (best effort)
    "ocr_language": "auto",       # "auto" = Windows profile languages, or a tag like "en-US"
    # ---- hotkeys
    "hotkeys": DEFAULT_HOTKEYS,
    # ---- formats shown on the wheel
    "hidden_pairs": [],           # "src>dst", e.g. "jpg>pdf"
    "max_segments": 48,           # spread over up to 3 concentric rings
    # ---- wheel appearance & behaviour
    "theme": "midnight",
    "color_by_category": True,
    "accent_color": "#7c8cff",
    "wheel_scale": 1.0,           # overall size
    "label_scale": 1.0,           # text size relative to the wheel
    "wheel_opacity": 1.0,
    "animation_ms": 170,          # 0 = no animation
    "position": "cursor",         # cursor | center
    "show_category_labels": True,
    "show_caption": True,
    "dwell_select_ms": 0,         # >0: hovering a segment this long converts (no click)
    "close_on_focus_loss": True,
    "done_delay_ms": 900,
    "config_version": 2,
}


def load() -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if "hotkey" in data and "hotkeys" not in data:  # v1.0 single-hotkey format
            data["hotkeys"] = [{"keys": data.pop("hotkey"), "action": "wheel"},
                               *DEFAULT_HOTKEYS[1:]]
        data.pop("hotkey", None)
        if data.get("config_version", 1) < 2:
            # 2.1 shows several rings; the old one-ring default of 16 would hide them.
            if data.get("max_segments") == 16:
                data["max_segments"] = DEFAULTS["max_segments"]
            data["config_version"] = 2
        cfg.update(data)
    except FileNotFoundError:
        pass
    except Exception:
        logging.exception("Could not read config, using defaults")
    return cfg


def save(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_FILE)


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(**values) -> None:
    state = load_state()
    state.update(values)
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        logging.exception("Could not save state")


def setup_logging() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.INFO,
        format="%(asctime)s %(process)d %(levelname)s %(name)s: %(message)s",
    )
