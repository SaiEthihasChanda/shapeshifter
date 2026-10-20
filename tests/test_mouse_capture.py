"""Mouse-binding capture in the settings dialog and the hook's matching logic (no real input)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from shapeshifter import mousehook  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_capture_mouse_buttons_and_wheel(app):
    from shapeshifter.settings_app import HotkeyEdit
    ed = HotkeyEdit()
    ed.show()
    QTest.mouseClick(ed, Qt.MouseButton.MiddleButton)
    assert ed.spec == "mouse:middle" and ed.text() == "Middle click"
    QTest.mouseClick(ed, Qt.MouseButton.XButton2, Qt.KeyboardModifier.ControlModifier)
    assert ed.spec == "ctrl+mouse:x2"
    QTest.mouseClick(ed, Qt.MouseButton.LeftButton)          # plain left click: just focus
    assert ed.spec == "ctrl+mouse:x2"
    QTest.mouseClick(ed, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.AltModifier)
    assert ed.spec == "alt+mouse:left"
    ev = QWheelEvent(QPointF(5, 5), QPointF(ed.mapToGlobal(QPoint(5, 5))), QPoint(), QPoint(0, -120),
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.ShiftModifier,
                     Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(ed, ev)
    assert ed.spec == "shift+wheel:down"


class FakeInfo:
    def __init__(self, data=0):
        self.mouseData = data
        self.pt = type("P", (), {"x": 10, "y": 20})()


def _hook(monkeypatch, cls="CabinetWClass", mods=0):
    fired = []
    h = mousehook.MouseHook(fired.append)
    h._bindings = [mousehook._Binding(0, "middle", "wheel-binding"),
                   mousehook._Binding(1, "wheel:up", "ctrl-scroll")]
    monkeypatch.setattr(mousehook, "root_window_at", lambda x, y: (1234, cls))
    monkeypatch.setattr(mousehook, "_current_mods", lambda: mods)
    return h, fired


def test_hook_fires_and_swallows_over_explorer(monkeypatch):
    h, fired = _hook(monkeypatch)
    assert h._handle(mousehook.WM_MBUTTONDOWN, FakeInfo()) is True
    assert h._handle(mousehook.WM_MBUTTONUP, FakeInfo()) is True      # matching up is swallowed too
    assert h._handle(mousehook.WM_MBUTTONUP, FakeInfo()) is False     # only once
    assert [t.payload for t in fired] == ["wheel-binding"] and fired[0].hwnd == 1234


def test_hook_ignores_other_apps_and_modifiers(monkeypatch):
    h, fired = _hook(monkeypatch, cls="Chrome_WidgetWin_1")
    assert h._handle(mousehook.WM_MBUTTONDOWN, FakeInfo()) is False   # browser keeps middle click
    h, fired = _hook(monkeypatch, mods=4)
    assert h._handle(mousehook.WM_MBUTTONDOWN, FakeInfo()) is False   # Shift+middle isn't bound
    assert not fired


def test_hook_wheel_debounce(monkeypatch):
    h, fired = _hook(monkeypatch, mods=1)
    up = FakeInfo(120 << 16)
    assert h._handle(mousehook.WM_MOUSEWHEEL, up) is True
    assert h._handle(mousehook.WM_MOUSEWHEEL, up) is True             # swallowed, not re-fired
    assert len(fired) == 1
    down = FakeInfo((0x10000 - 120) << 16)
    assert h._handle(mousehook.WM_MOUSEWHEEL, down) is False          # ctrl+scroll down unbound


def test_real_hook_installs():
    h = mousehook.MouseHook(lambda t: None)
    status = h.set_bindings([("mouse:middle", 0)])
    try:
        assert status == [(0, True)] and h.running
    finally:
        h.stop()
