"""Hotkey and mouse-trigger specs ("ctrl+alt+c", "ctrl+mouse:middle", "alt+wheel:up"), parsed the same way on
every system. The system-specific parts (registering, hooking) are in mousehook / winutil and mac.events."""

from __future__ import annotations

from .system import MODIFIER_HELP

BUTTONS = {"left", "right", "middle", "x1", "x2"}
WHEEL = {"up", "down"}
# "win" is the Windows key on Windows and ⌘ Command on a Mac; "cmd" is the same thing written for a Mac.
MOD_FLAGS = {"ctrl": 1, "alt": 2, "shift": 4, "win": 8, "cmd": 8}
MODIFIERS = {"ctrl", "control", "alt", "option", "shift", "win", "super", "meta", "cmd", "command"}


def is_mouse_spec(spec: str) -> bool:
    last = spec.rsplit("+", 1)[-1]
    return last.startswith("mouse:") or last.startswith("wheel:")


def parse_mouse_spec(spec: str) -> tuple[int, str]:
    """'ctrl+mouse:middle' -> (modifier flags, 'middle'); 'alt+wheel:up' -> (2, 'wheel:up')."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    mods = 0
    for p in parts[:-1]:
        if p not in MOD_FLAGS:
            raise ValueError(f"Unknown modifier '{p}' in '{spec}'")
        mods |= MOD_FLAGS[p]
    kind, _, name = parts[-1].partition(":")
    if kind == "mouse" and name in BUTTONS:
        if name in ("left", "right") and not mods:
            raise ValueError(f"Left/right click need a modifier ({MODIFIER_HELP})")
        return mods, name
    if kind == "wheel" and name in WHEEL:
        if not mods:
            raise ValueError(f"Scrolling needs a modifier ({MODIFIER_HELP})")
        return mods, f"wheel:{name}"
    raise ValueError(f"Unknown mouse input '{parts[-1]}'")


def split_key_spec(spec: str) -> tuple[int, str]:
    """'ctrl+alt+c' -> (modifier flags, 'c'). Raises ValueError for a spec without a key."""
    mods, key = 0, None
    for part in [x.strip().lower() for x in spec.split("+") if x.strip()]:
        if part in ("ctrl", "control"):
            mods |= 1
        elif part in ("alt", "option"):
            mods |= 2
        elif part == "shift":
            mods |= 4
        elif part in ("win", "super", "meta", "cmd", "command"):
            mods |= 8
        elif key is None:
            key = part
        else:
            raise ValueError(f"Hotkey '{spec}' has two keys")
    if key is None:
        raise ValueError(f"Hotkey '{spec}' has no key")
    return mods, key
