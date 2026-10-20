"""Read the files currently selected in the foreground Explorer window (or on the desktop)."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

SWC_DESKTOP = 0x8
SWFO_NEEDDISPATCH = 0x1


def _class_name(hwnd: int) -> str:
    import win32gui
    try:
        return win32gui.GetClassName(hwnd)
    except Exception:
        return ""


def _tab_hwnd(window) -> int | None:
    """HWND of the tab (ShellTabWindowClass) hosting a ShellWindows entry, on tabbed Explorer."""
    import pythoncom
    from win32com.shell import shell

    try:
        sp = window._oleobj_.QueryInterface(pythoncom.IID_IServiceProvider)
        browser = sp.QueryService(shell.SID_STopLevelBrowser, shell.IID_IShellBrowser)
        return browser.GetWindow()
    except Exception:
        return None


def _paths_from(window) -> list[str]:
    try:
        items = window.Document.SelectedItems()
        return [items.Item(i).Path for i in range(items.Count)]
    except Exception:
        log.exception("Could not read selection")
        return []


_uia = None
UIA_SELECTION_ITEM_PATTERN = 10010
UIA_LIST_ITEM = 50007
UIA_DATA_ITEM = 50029


def _automation():
    global _uia
    if _uia is None:
        import comtypes.client
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as uia
        _uia = (comtypes.client.CreateObject(uia.CUIAutomation, interface=uia.IUIAutomation), uia)
    return _uia


def warm_up() -> None:
    """Generate the UI Automation wrappers ahead of the first mouse trigger (~1 s once)."""
    try:
        _automation()
    except Exception:
        log.exception("UI Automation unavailable")


def select_item_at(x: int, y: int) -> bool:
    """If a file icon/row is under (x, y) and isn't selected, select it (like a left click).

    Clicking an item that's already part of a multi-selection keeps the selection.
    Returns True if an item was found under the point.
    """
    try:
        import ctypes
        from ctypes import wintypes
        automation, uia = _automation()
        pt = uia.tagPOINT(x, y)
        el = automation.ElementFromPoint(pt)
        walker = automation.ControlViewWalker
        for _ in range(4):  # the hit is often the item's text or icon child
            if el is None:
                return False
            if el.CurrentControlType in (UIA_LIST_ITEM, UIA_DATA_ITEM):
                break
            el = walker.GetParentElement(el)
        else:
            return False
        if el is None or el.CurrentControlType not in (UIA_LIST_ITEM, UIA_DATA_ITEM):
            return False
        pattern = el.GetCurrentPattern(UIA_SELECTION_ITEM_PATTERN)
        if not pattern:
            return False
        sel = pattern.QueryInterface(uia.IUIAutomationSelectionItemPattern)
        if not sel.CurrentIsSelected:
            sel.Select()
        return True
    except Exception:
        log.exception("Could not select the item under the cursor")
        return False


def selected_files(hwnd: int | None = None) -> list[str]:
    """Selected file paths in the given (default: foreground) Explorer window."""
    import win32com.client
    import win32gui

    hwnd = hwnd or win32gui.GetForegroundWindow()
    cls = _class_name(hwnd)
    shell_app = win32com.client.Dispatch("Shell.Application")

    if cls in ("CabinetWClass", "ExploreWClass"):
        windows = []
        for w in shell_app.Windows():
            try:
                if int(w.HWND) == hwnd:
                    windows.append(w)
            except Exception:
                continue
        if not windows:
            return []
        if len(windows) > 1:
            # Windows 11 tabs: all tabs share the frame HWND. The active tab is the
            # first ShellTabWindowClass child in z-order.
            active = win32gui.FindWindowEx(hwnd, 0, "ShellTabWindowClass", None)
            for w in windows:
                if active and _tab_hwnd(w) == active:
                    return _paths_from(w)
        return _paths_from(windows[0])

    if cls in ("Progman", "WorkerW"):
        try:
            sw = win32com.client.Dispatch("{9BA05972-F6A8-11CF-A442-00A0C90A8F39}")
            desktop = sw.FindWindowSW(0, 0, SWC_DESKTOP, 0, SWFO_NEEDDISPATCH)
            return _paths_from(win32com.client.Dispatch(desktop))
        except Exception:
            log.exception("Could not read desktop selection")
    return []
