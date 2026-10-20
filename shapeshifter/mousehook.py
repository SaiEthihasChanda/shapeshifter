"""Mouse-button and scroll triggers via a low-level mouse hook (WH_MOUSE_LL).

RegisterHotKey only understands keyboard keys, so mouse bindings ("mouse:middle",
"ctrl+wheel:up", …) are matched here. The hook runs on its own thread with its own
message loop so it never waits on the Qt event loop (Windows drops slow LL hooks).

Bindings only fire over File Explorer windows and the desktop; everywhere else the
input passes through untouched. A matched click is swallowed (down *and* up) so
Explorer doesn't also act on it.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_MOUSE_LL = 14
WM_QUIT = 0x0012
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0207, 0x0208
WM_MOUSEWHEEL = 0x020A
WM_XBUTTONDOWN, WM_XBUTTONUP = 0x020B, 0x020C
GA_ROOT = 2

EXPLORER_CLASSES = {"CabinetWClass", "ExploreWClass", "Progman", "WorkerW"}

BUTTONS = {"left", "right", "middle", "x1", "x2"}
WHEEL = {"up", "down"}
MOD_FLAGS = {"ctrl": 1, "alt": 2, "shift": 4, "win": 8}


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


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
            raise ValueError("Left/right click need a modifier (Ctrl, Alt, Shift or Win)")
        return mods, name
    if kind == "wheel" and name in WHEEL:
        if not mods:
            raise ValueError("Scrolling needs a modifier (Ctrl, Alt, Shift or Win)")
        return mods, f"wheel:{name}"
    raise ValueError(f"Unknown mouse input '{parts[-1]}'")


def _current_mods() -> int:
    down = lambda vk: user32.GetAsyncKeyState(vk) & 0x8000  # noqa: E731
    mods = 0
    if down(0x11):
        mods |= 1
    if down(0x12):
        mods |= 2
    if down(0x10):
        mods |= 4
    if down(0x5B) or down(0x5C):
        mods |= 8
    return mods


def root_window_at(x: int, y: int) -> tuple[int, str]:
    hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
    root = user32.GetAncestor(hwnd, GA_ROOT) if hwnd else None
    if not root:
        return 0, ""
    buf = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(root, buf, 64)
    return int(root), buf.value


@dataclass
class Trigger:
    payload: object       # whatever the owner wants back (the binding dict)
    hwnd: int             # Explorer/desktop window under the cursor
    x: int
    y: int


@dataclass
class _Binding:
    mods: int
    button: str
    payload: object


class MouseHook:
    """Owns the hook thread. ``on_trigger(Trigger)`` is called from that thread."""

    def __init__(self, on_trigger):
        self.on_trigger = on_trigger
        self._bindings: list[_Binding] = []
        self._swallow_up: set[str] = set()
        self._last_wheel = 0.0
        self._thread: threading.Thread | None = None
        self._tid = 0
        self._hook = None
        self._proc = HOOKPROC(self._callback)  # keep a reference: ctypes callbacks must not be GC'd
        self.running = False

    def set_bindings(self, bindings: list[tuple[str, object]]) -> list[tuple[object, bool]]:
        """Install ``[(spec, payload)]``; returns ``[(payload, ok)]``. Starts the hook if needed."""
        parsed, status = [], []
        for spec, payload in bindings:
            try:
                mods, button = parse_mouse_spec(spec)
                parsed.append(_Binding(mods, button, payload))
                status.append((payload, True))
            except ValueError:
                log.exception("Bad mouse binding %s", spec)
                status.append((payload, False))
        self._bindings = parsed
        if parsed and not self.running:
            self.start()
        if not self.running:
            status = [(p, False) for p, _ in status]
        return status

    def start(self):
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready,), name="mouse-hook", daemon=True)
        self._thread.start()
        ready.wait(2)

    def stop(self):
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)

    def _run(self, ready: threading.Event):
        self._tid = kernel32.GetCurrentThreadId()
        self._hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc, None, 0)
        self.running = bool(self._hook)
        if not self._hook:
            log.error("SetWindowsHookEx failed: %s", ctypes.get_last_error())
        ready.set()
        if not self._hook:
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass
        user32.UnhookWindowsHookEx(self._hook)
        self.running = False

    def _callback(self, n_code, w_param, l_param):
        try:
            if n_code == 0 and self._bindings:
                if self._handle(w_param, MSLLHOOKSTRUCT.from_address(l_param)):
                    return 1  # swallow
        except Exception:
            log.exception("mouse hook error")
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _handle(self, msg: int, info: MSLLHOOKSTRUCT) -> bool:
        button, down = None, True
        if msg in (WM_LBUTTONDOWN, WM_LBUTTONUP):
            button, down = "left", msg == WM_LBUTTONDOWN
        elif msg in (WM_RBUTTONDOWN, WM_RBUTTONUP):
            button, down = "right", msg == WM_RBUTTONDOWN
        elif msg in (WM_MBUTTONDOWN, WM_MBUTTONUP):
            button, down = "middle", msg == WM_MBUTTONDOWN
        elif msg in (WM_XBUTTONDOWN, WM_XBUTTONUP):
            button = "x1" if (info.mouseData >> 16) == 1 else "x2"
            down = msg == WM_XBUTTONDOWN
        elif msg == WM_MOUSEWHEEL:
            delta = ctypes.c_short(info.mouseData >> 16).value
            button = "wheel:up" if delta > 0 else "wheel:down"
        if button is None:
            return False
        if not down:
            if button in self._swallow_up:
                self._swallow_up.discard(button)
                return True
            return False

        mods = _current_mods()
        matches = [b for b in self._bindings if b.button == button and b.mods == mods]
        if not matches:
            return False
        hwnd, cls = root_window_at(info.pt.x, info.pt.y)
        if cls not in EXPLORER_CLASSES:
            return False
        if button.startswith("wheel:"):
            now = time.monotonic()
            if now - self._last_wheel < 0.6:   # one trigger per scroll gesture
                self._last_wheel = now
                return True
            self._last_wheel = now
        else:
            self._swallow_up.add(button)
        self.on_trigger(Trigger(matches[0].payload, hwnd, info.pt.x, info.pt.y))
        return True
