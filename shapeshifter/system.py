"""The few things ShapeShifter does differently on Windows and on macOS, behind one set of names.

Everything else (converting, the wheel, Fill form, settings) is the same code on both.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# Words the interface uses for the system's own things.
FILE_MANAGER = "Finder" if IS_MAC else "File Explorer"
TRASH = "Trash" if IS_MAC else "Recycle Bin"
SYSTEM = "macOS" if IS_MAC else "Windows"


def app_dirs(app_id: str) -> tuple[Path, Path]:
    """(settings folder, data folder) for the current user."""
    if IS_MAC:
        base = Path.home() / "Library" / "Application Support" / app_id
        return base, base / "Data"
    return (Path(os.environ.get("APPDATA", Path.home())) / app_id,
            Path(os.environ.get("LOCALAPPDATA", Path.home())) / app_id)


def tool_dirs() -> list[Path]:
    """Folders where command-line tools usually live but which an app started from the Dock / Finder
    doesn't have on its PATH (Homebrew, MacPorts)."""
    if IS_MAC:
        return [Path("/opt/homebrew/bin"), Path("/usr/local/bin"), Path("/opt/local/bin")]
    return []


def exe_name(name: str) -> str:
    return f"{name}.exe" if IS_WIN else name


def open_path(path) -> None:
    """Open a file, folder or web address with its default app."""
    target = str(path)
    if IS_WIN:
        os.startfile(target)                                   # noqa: S606 - the user asked to open it
    elif IS_MAC:
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])


def reveal(path) -> None:
    """Show a file selected in Finder / File Explorer."""
    p = Path(path)
    if IS_MAC:
        subprocess.Popen(["open", "-R", str(p)])
    elif IS_WIN:
        subprocess.Popen(["explorer", "/select,", str(p)])
    else:
        open_path(p.parent)


def bring_to_front(widget) -> None:
    """Give a window the keyboard focus even though ShapeShifter wasn't the active app."""
    try:
        if IS_WIN:
            from .winutil import force_foreground
            force_foreground(int(widget.winId()))
        elif IS_MAC:
            from .mac import appkit
            appkit.activate()
    except Exception:
        log.exception("Couldn't bring the window to the front")


def set_hidden(path) -> None:
    """Hide a helper file from the folder view."""
    try:
        if IS_WIN:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02)   # FILE_ATTRIBUTE_HIDDEN
        elif IS_MAC:
            import stat
            os.chflags(str(path), os.stat(str(path)).st_flags | stat.UF_HIDDEN)
    except Exception:
        log.exception("Couldn't hide %s", path)


def set_created_time(path, timestamp: float) -> None:
    """Change a file's creation date (the modified date is os.utime)."""
    if IS_WIN:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        k32.CreateFileW.restype = wintypes.HANDLE
        handle = k32.CreateFileW(str(path), 0x100, 0, None, 3, 0x80, None)   # FILE_WRITE_ATTRIBUTES, OPEN_EXISTING
        if handle in (wintypes.HANDLE(-1).value, None):
            raise OSError("Couldn't open the file to change its dates.")
        try:
            value = int((timestamp + 11644473600) * 10_000_000)
            ft = wintypes.FILETIME(value & 0xFFFFFFFF, value >> 32)
            if not k32.SetFileTime(handle, ctypes.byref(ft), None, None):
                raise OSError("Couldn't change the file's creation date.")
        finally:
            k32.CloseHandle(handle)
    elif IS_MAC:
        from Foundation import NSDate, NSFileCreationDate, NSFileManager
        ok, err = NSFileManager.defaultManager().setAttributes_ofItemAtPath_error_(
            {NSFileCreationDate: NSDate.dateWithTimeIntervalSince1970_(timestamp)}, str(path), None)
        if not ok:
            raise OSError(f"Couldn't change the file's creation date: {err}")
    else:
        raise OSError("Changing the creation date isn't supported here.")


def font_dirs() -> list[Path]:
    if IS_MAC:
        return [Path("/System/Library/Fonts"), Path("/System/Library/Fonts/Supplemental"),
                Path("/Library/Fonts"), Path.home() / "Library" / "Fonts"]
    if IS_WIN:
        return [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"]
    return [Path("/usr/share/fonts"), Path.home() / ".fonts"]


def pretty_modifier(name: str) -> str:
    """How a modifier in a hotkey spec ("ctrl", "alt", "shift", "win"/"cmd") is shown."""
    if IS_MAC:
        return {"ctrl": "⌃", "alt": "⌥", "shift": "⇧", "win": "⌘", "cmd": "⌘"}.get(name, name)
    return {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win", "cmd": "Win"}.get(name, name)


MODIFIER_HELP = "⌃ Control, ⌥ Option, ⇧ Shift or ⌘ Command" if IS_MAC else "Ctrl, Alt, Shift or Win"
