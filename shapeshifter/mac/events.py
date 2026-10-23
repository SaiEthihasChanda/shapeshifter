"""Hotkeys and mouse triggers on macOS, with one Quartz event tap.

The tap needs Accessibility access (System Settings › Privacy & Security › Accessibility). It runs on its own
thread with its own run loop, so it never waits for the Qt event loop (macOS turns slow taps off).

Key combinations work in every app, like the Windows hotkeys. Mouse triggers only fire over Finder windows and
the desktop; everywhere else the input passes through untouched. A matched click is swallowed (down *and* up)
so Finder doesn't also act on it.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from ..triggers import parse_mouse_spec, split_key_spec

log = logging.getLogger(__name__)

# Virtual key codes of the keys on an ANSI (US) keyboard: they name positions, so on other layouts the letters
# are the ones printed in the same place on a US keyboard.
KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12,
    "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23,
    "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34,
    "p": 35, "enter": 36, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45,
    "m": 46, ".": 47, "tab": 48, "space": 49, "`": 50, "backspace": 51, "escape": 53,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100, "f9": 101,
    "f10": 109, "f11": 103, "f12": 111, "f13": 105, "f14": 107, "f15": 113, "f16": 106, "f17": 64,
    "f18": 79, "f19": 80, "f20": 90, "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "delete": 117, "insert": 114, "left": 123, "right": 124, "down": 125, "up": 126,
}
NAMES = {v: k for k, v in KEYCODES.items()}

# kCGEventFlagMask… -> our modifier bits (ctrl 1, alt/option 2, shift 4, cmd/win 8)
FLAG_BITS = ((0x40000, 1), (0x80000, 2), (0x20000, 4), (0x100000, 8))


def parse_key(spec: str) -> tuple[int, int]:
    """'ctrl+alt+c' -> (modifier bits, key code). Raises ValueError for an unknown key."""
    mods, key = split_key_spec(spec)
    if key not in KEYCODES:
        raise ValueError(f"Unknown key '{key}' in hotkey '{spec}'")
    return mods, KEYCODES[key]


def mods_from_flags(flags: int) -> int:
    return sum(bit for mask, bit in FLAG_BITS if flags & mask)


@dataclass
class Trigger:
    payload: object       # whatever the owner wants back (the binding's index)
    hwnd: int             # the Finder window number under the cursor (0 for the desktop)
    x: float
    y: float


@dataclass
class _Mouse:
    mods: int
    button: str
    payload: object


def finder_window_at(x: float, y: float) -> int | None:
    """The number of the Finder window under (x, y), 0 for the desktop, None for any other app."""
    import Quartz
    infos = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID) or []
    desktop_level = Quartz.CGWindowLevelForKey(Quartz.kCGDesktopIconWindowLevelKey)
    for info in infos:                               # front to back
        layer = int(info.get("kCGWindowLayer", 0))
        b = info.get("kCGWindowBounds") or {}
        if not (b.get("X", 0) <= x < b.get("X", 0) + b.get("Width", 0)
                and b.get("Y", 0) <= y < b.get("Y", 0) + b.get("Height", 0)):
            continue
        if float(info.get("kCGWindowAlpha", 1)) <= 0.01:
            continue
        owner = str(info.get("kCGWindowOwnerName", ""))
        if layer == 0:                               # the first ordinary window under the cursor decides
            return int(info.get("kCGWindowNumber", 0)) if owner == "Finder" else None
        if layer == desktop_level:
            return 0 if owner == "Finder" else None
        # menus, the Dock, overlays: look further down only for panels Finder itself shows
    return None


class EventTap:
    """Owns the tap thread. ``on_key(payload)`` and ``on_mouse(Trigger)`` are called from that thread."""

    def __init__(self, on_key, on_mouse):
        self.on_key, self.on_mouse = on_key, on_mouse
        self._keys: dict[tuple[int, int], object] = {}
        self._mouse: list[_Mouse] = []
        self._swallow_up: set[str] = set()
        self._last_wheel = 0.0
        self._thread: threading.Thread | None = None
        self._loop = None
        self._tap = None
        self.running = False

    # ------------------------------------------------------------------ bindings

    def set_bindings(self, keys: list[tuple[str, object]], mouse: list[tuple[str, object]]) -> list[tuple[object, bool]]:
        """Install hotkeys and mouse triggers ``[(spec, payload)]``; returns ``[(payload, ok)]`` for both."""
        status, parsed_keys, parsed_mouse = [], {}, []
        for spec, payload in keys:
            try:
                parsed_keys[parse_key(spec)] = payload
                status.append((payload, True))
            except ValueError:
                log.exception("Bad hotkey %s", spec)
                status.append((payload, False))
        for spec, payload in mouse:
            try:
                mods, button = parse_mouse_spec(spec)
                parsed_mouse.append(_Mouse(mods, button, payload))
                status.append((payload, True))
            except ValueError:
                log.exception("Bad mouse binding %s", spec)
                status.append((payload, False))
        self._keys, self._mouse = parsed_keys, parsed_mouse
        if (parsed_keys or parsed_mouse) and not self.running:
            self.start()
        if not self.running:
            status = [(p, False) for p, _ in status]
        return status

    # ------------------------------------------------------------------ thread

    def start(self) -> bool:
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready,), name="event-tap", daemon=True)
        self._thread.start()
        ready.wait(3)
        return self.running

    def stop(self):
        if self._loop is not None:
            import Quartz
            Quartz.CFRunLoopStop(self._loop)

    def _run(self, ready: threading.Event):
        import Quartz
        mask = 0
        for t in (Quartz.kCGEventKeyDown, Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp,
                  Quartz.kCGEventRightMouseDown, Quartz.kCGEventRightMouseUp, Quartz.kCGEventOtherMouseDown,
                  Quartz.kCGEventOtherMouseUp, Quartz.kCGEventScrollWheel):
            mask |= Quartz.CGEventMaskBit(t)
        self._tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                                            Quartz.kCGEventTapOptionDefault, mask, self._callback, None)
        if not self._tap:
            log.warning("No event tap: ShapeShifter doesn't have Accessibility access")
            self.running = False
            ready.set()
            return
        source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        self._loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(self._loop, source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)
        self.running = True
        ready.set()
        Quartz.CFRunLoopRun()
        self.running = False
        self._loop = None

    def _callback(self, proxy, etype, event, refcon):
        import Quartz
        try:
            if etype in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
                Quartz.CGEventTapEnable(self._tap, True)        # macOS switched us off: switch back on
                return event
            if etype == Quartz.kCGEventKeyDown:
                return None if self._key(event) else event
            return None if self._mouse_event(etype, event) else event
        except Exception:
            log.exception("event tap error")
            return event

    def _key(self, event) -> bool:
        import Quartz
        if not self._keys:
            return False
        code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
        mods = mods_from_flags(Quartz.CGEventGetFlags(event))
        payload = self._keys.get((mods, int(code)))
        if payload is None:
            return False
        if not Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat):
            self.on_key(payload)
        return True

    def _mouse_event(self, etype, event) -> bool:
        import Quartz
        if not self._mouse:
            return False
        button, down = None, True
        if etype in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
            button, down = "left", etype == Quartz.kCGEventLeftMouseDown
        elif etype in (Quartz.kCGEventRightMouseDown, Quartz.kCGEventRightMouseUp):
            button, down = "right", etype == Quartz.kCGEventRightMouseDown
        elif etype in (Quartz.kCGEventOtherMouseDown, Quartz.kCGEventOtherMouseUp):
            n = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGMouseEventButtonNumber)
            button = {2: "middle", 3: "x1", 4: "x2"}.get(int(n))
            down = etype == Quartz.kCGEventOtherMouseDown
        elif etype == Quartz.kCGEventScrollWheel:
            delta = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGScrollWheelEventDeltaAxis1)
            if not delta:
                return False
            button = "wheel:up" if delta > 0 else "wheel:down"
        if button is None:
            return False
        if not down:
            if button in self._swallow_up:
                self._swallow_up.discard(button)
                return True
            return False
        mods = mods_from_flags(Quartz.CGEventGetFlags(event))
        matches = [b for b in self._mouse if b.button == button and b.mods == mods]
        if not matches:
            return False
        loc = Quartz.CGEventGetLocation(event)
        window = finder_window_at(loc.x, loc.y)
        if window is None:
            return False
        if button.startswith("wheel:"):
            now = time.monotonic()
            if now - self._last_wheel < 0.6:            # one trigger per scroll gesture
                self._last_wheel = now
                return True
            self._last_wheel = now
        else:
            self._swallow_up.add(button)
        self.on_mouse(Trigger(matches[0].payload, window, loc.x, loc.y))
        return True
