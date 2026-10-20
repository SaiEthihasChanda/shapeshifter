"""Small Win32 helpers."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY = 0x0312

_VK_NAMES = {
    "space": 0x20, "enter": 0x0D, "tab": 0x09, "insert": 0x2D, "delete": 0x2E, "home": 0x24,
    "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "pause": 0x13,
    "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, ";": 0xBA, "'": 0xDE,
    ",": 0xBC, ".": 0xBE, "/": 0xBF, "\\": 0xDC,
}


def parse_hotkey(spec: str) -> tuple[int, int]:
    """'ctrl+alt+c' -> (modifiers, virtual key). Raises ValueError for bad specs."""
    mods, vk = MOD_NOREPEAT, None
    for part in [x.strip().lower() for x in spec.split("+") if x.strip()]:
        if part in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif part == "alt":
            mods |= MOD_ALT
        elif part == "shift":
            mods |= MOD_SHIFT
        elif part in ("win", "super", "meta"):
            mods |= MOD_WIN
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            vk = 0x70 + int(part[1:]) - 1
        elif part in _VK_NAMES:
            vk = _VK_NAMES[part]
        else:
            raise ValueError(f"Unknown key '{part}' in hotkey '{spec}'")
    if vk is None:
        raise ValueError(f"Hotkey '{spec}' has no key")
    return mods, vk


def register_hotkey(hwnd: int, hotkey_id: int, spec: str) -> bool:
    mods, vk = parse_hotkey(spec)
    return bool(user32.RegisterHotKey(wintypes.HWND(hwnd), hotkey_id, mods, vk))


def unregister_hotkey(hwnd: int, hotkey_id: int) -> None:
    user32.UnregisterHotKey(wintypes.HWND(hwnd), hotkey_id)


def force_foreground(hwnd: int) -> None:
    """Bring a window to the foreground even if Windows' focus-stealing guard objects."""
    hwnd = wintypes.HWND(hwnd)
    if user32.GetForegroundWindow() == hwnd.value:
        return
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None)
    me = kernel32.GetCurrentThreadId()
    attached = fg_tid and fg_tid != me and user32.AttachThreadInput(me, fg_tid, True)
    try:
        user32.BringWindowToTop(hwnd)
        ok = user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(me, fg_tid, False)
    if not ok:
        # Tapping Alt lifts the foreground lock for the calling process.
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 2, 0)
        user32.SetForegroundWindow(hwnd)
