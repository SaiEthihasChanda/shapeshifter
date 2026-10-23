"""Read the files selected in Finder (a window or the desktop), and select the item under the cursor.

The selection is asked for with an Apple event, so the first time macOS asks once whether ShapeShifter
may control Finder (Privacy & Security › Automation). Selecting the item under the cursor uses the
Accessibility interface, which the hotkeys need anyway.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

FINDER = "com.apple.finder"
NOT_ALLOWED = -1743            # errAEEventNotPermitted: the user said no (or hasn't been asked yet)

SELECTION_SCRIPT = """
tell application id "com.apple.finder"
    set picked to selection as alias list
end tell
set out to ""
repeat with f in picked
    set out to out & POSIX path of f & linefeed
end repeat
return out
"""

_script = None


class FinderAccessDenied(PermissionError):
    """macOS didn't let ShapeShifter ask Finder for its selection."""


def finder_is_frontmost() -> bool:
    from .appkit import frontmost_bundle_id
    return frontmost_bundle_id() == FINDER


def _compiled():
    global _script
    if _script is None:
        from Foundation import NSAppleScript
        _script = NSAppleScript.alloc().initWithSource_(SELECTION_SCRIPT)
    return _script


def selected_files() -> list[str]:
    """POSIX paths of the items selected in the front Finder window (or on the desktop); folders end
    with "/" and are left to the caller to filter."""
    result, error = _compiled().executeAndReturnError_(None)
    if error is not None:
        code = int(error.get("NSAppleScriptErrorNumber", 0) or 0)
        if code == NOT_ALLOWED:
            raise FinderAccessDenied(
                "ShapeShifter isn't allowed to ask Finder which files are selected. Turn it on in System Settings › "
                "Privacy & Security › Automation › ShapeShifter › Finder.")
        log.warning("Finder selection failed: %s", dict(error))
        return []
    text = str(result.stringValue() or "") if result is not None else ""
    return [p for p in text.splitlines() if p]


# ------------------------------------------------------------- item under the cursor

def _ax_value(el, attr):
    from ApplicationServices import AXUIElementCopyAttributeValue
    err, value = AXUIElementCopyAttributeValue(el, attr, None)
    return value if err == 0 else None


def select_item_at(x: float, y: float) -> bool:
    """If a file is under (x, y) in Finder and isn't selected, select it, as a click would.
    An item that is already part of a multi-selection keeps the selection. Returns True if an item was found."""
    try:
        from ApplicationServices import (AXUIElementCopyElementAtPosition, AXUIElementCreateApplication,
                                         AXUIElementSetAttributeValue)
        from .appkit import finder_pid
        pid = finder_pid()
        if not pid:
            return False
        app = AXUIElementCreateApplication(pid)
        err, el = AXUIElementCopyElementAtPosition(app, float(x), float(y), None)
        for _ in range(5):                           # the hit is often the icon or the name inside the item
            if err != 0 or el is None:
                return False
            role = str(_ax_value(el, "AXRole") or "")
            selected = _ax_value(el, "AXSelected")
            if selected is not None and role in ("AXRow", "AXImage", "AXGroup", "AXCell", "AXStaticText"):
                if not bool(selected):
                    AXUIElementSetAttributeValue(el, "AXSelected", True)
                return True
            el, err = _ax_value(el, "AXParent"), 0
        return False
    except Exception:
        log.exception("Couldn't select the item under the cursor")
        return False
