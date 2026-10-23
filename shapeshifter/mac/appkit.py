"""Small AppKit helpers: bringing the app forward, privacy permissions and System Settings panes."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

PANES = {
    "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    "automation": "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation",
    "files": "x-apple.systempreferences:com.apple.preference.security?Privacy_FilesAndFolders",
    "extensions": "x-apple.systempreferences:com.apple.ExtensionsPreferences",
    "login": "x-apple.systempreferences:com.apple.LoginItems-Settings.extension",
}


def activate() -> None:
    """Make ShapeShifter the active app, so its window gets the keyboard (the wheel, Settings)."""
    from AppKit import NSApplication, NSApplicationActivateIgnoringOtherApps, NSRunningApplication
    NSRunningApplication.currentApplication().activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)


def frontmost_bundle_id() -> str:
    from AppKit import NSWorkspace
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return str(app.bundleIdentifier() or "") if app is not None else ""


def finder_pid() -> int:
    from AppKit import NSRunningApplication
    apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_("com.apple.finder")
    return int(apps[0].processIdentifier()) if apps else 0


def accessibility_trusted(prompt: bool = False) -> bool:
    """Whether macOS lets ShapeShifter see keys and clicks (Privacy & Security › Accessibility).
    ``prompt`` shows the system's "would like to control this computer" dialog when it doesn't."""
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
        return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: bool(prompt)}))
    except Exception:
        log.exception("Couldn't ask macOS about Accessibility access")
        return False


def open_pane(kind: str) -> None:
    subprocess.Popen(["open", PANES[kind]])


def app_bundle() -> Path | None:
    """The ShapeShifter.app this process runs from (packaged app only)."""
    if not getattr(sys, "frozen", False):
        return None
    for p in Path(sys.executable).resolve().parents:
        if p.suffix == ".app":
            return p
    return None


def set_dock_icon(visible: bool) -> None:
    """Show the app in the Dock and the ⌘-Tab switcher while one of its windows (Settings) is open."""
    try:
        from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory,
                            NSApplicationActivationPolicyRegular)
        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyRegular if visible else NSApplicationActivationPolicyAccessory)
    except Exception:
        log.exception("Couldn't change the Dock icon")
