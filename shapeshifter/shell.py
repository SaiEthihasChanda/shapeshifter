"""Per-user Windows integration: context menu, shortcuts, startup and uninstall entry.

Everything lives under HKCU / the user's profile, so no administrator rights are needed.
Works both from a Python install (pythonw -m shapeshifter) and from the bundled
ShapeShifter.exe (PyInstaller build).
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import winreg
from pathlib import Path

from . import APP_ID, APP_NAME, APP_USER_MODEL_ID, __version__
from .icon import ensure_icon

log = logging.getLogger(__name__)

VERB = "ShapeShifter"
SFA = r"Software\Classes\SystemFileAssociations"
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_ID}"
# The verb is registered for every file: files are identified by their contents, so a
# .dat or extension-less file may well be convertible.
ALL_FILES_KEY = rf"Software\Classes\*\shell\{VERB}"

_PROGRAMS = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
STARTUP_LNK = _PROGRAMS / "Startup" / f"{APP_NAME}.lnk"
STARTMENU_LNK = _PROGRAMS / f"{APP_NAME}.lnk"

FROZEN = bool(getattr(sys, "frozen", False))


def launcher() -> tuple[str, list[str]]:
    """(executable, leading arguments) that start ShapeShifter without a console."""
    if FROZEN:
        return sys.executable, []
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.exists() else exe), ["-m", "shapeshifter"]


def _command(*args: str) -> str:
    exe, lead = launcher()
    return " ".join([f'"{exe}"', *lead, *args])


def _delete_tree(root, path: str) -> None:
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS) as k:
            while True:
                try:
                    child = winreg.EnumKey(k, 0)
                except OSError:
                    break
                _delete_tree(root, f"{path}\\{child}")
        winreg.DeleteKey(root, path)
    except FileNotFoundError:
        pass


def _icon() -> str:
    return sys.executable if FROZEN else str(ensure_icon())


def register_context_menu() -> int:
    unregister_context_menu()
    cmd = _command("open", '"%1"')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, ALL_FILES_KEY) as k:
        winreg.SetValueEx(k, "MUIVerb", 0, winreg.REG_SZ, "Convert to…")
        winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, _icon())
        # "Player" lifts Explorer's 15-item limit for multi-selection.
        winreg.SetValueEx(k, "MultiSelectModel", 0, winreg.REG_SZ, "Player")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, ALL_FILES_KEY + r"\command") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, cmd)
    _notify_assoc_changed()
    from . import engine
    return len(engine.supported_sources())


def context_menu_registered() -> bool:
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, ALL_FILES_KEY).Close()
        return True
    except OSError:
        return False


def _remove_verb_everywhere(verb: str) -> None:
    _delete_tree(winreg.HKEY_CURRENT_USER, rf"Software\Classes\*\shell\{verb}")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SFA) as root:
            names, i = [], 0
            while True:
                try:
                    names.append(winreg.EnumKey(root, i))
                except OSError:
                    break
                i += 1
    except FileNotFoundError:
        return
    for name in names:
        _delete_tree(winreg.HKEY_CURRENT_USER, rf"{SFA}\{name}\shell\{verb}")


def unregister_context_menu() -> None:
    _remove_verb_everywhere(VERB)
    _notify_assoc_changed()


def _notify_assoc_changed():
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED
    except Exception:
        pass


def _shortcut(lnk: Path, args: list[str], description: str) -> None:
    import win32com.client
    exe, lead = launcher()
    lnk.parent.mkdir(parents=True, exist_ok=True)
    sc = win32com.client.Dispatch("WScript.Shell").CreateShortcut(str(lnk))
    sc.TargetPath = exe
    sc.Arguments = " ".join(lead + args)
    sc.WorkingDirectory = str(Path.home())
    sc.IconLocation = _icon()
    sc.Description = description
    sc.Save()
    try:
        # Tag the shortcut with our AppUserModelID so toasts show the app's name and icon.
        import pythoncom
        from win32com.propsys import propsys, pscon
        from win32com.shell import shellcon
        store = propsys.SHGetPropertyStoreFromParsingName(str(lnk), None, shellcon.GPS_READWRITE,
                                                          propsys.IID_IPropertyStore)
        store.SetValue(pscon.PKEY_AppUserModel_ID,
                       propsys.PROPVARIANTType(APP_USER_MODEL_ID, pythoncom.VT_LPWSTR))
        store.Commit()
    except Exception:
        log.exception("Could not set AppUserModelID on %s", lnk)


def startup_enabled() -> bool:
    return STARTUP_LNK.exists()


def set_startup(enabled: bool) -> None:
    if enabled:
        _shortcut(STARTUP_LNK, ["daemon"], f"{APP_NAME} (tray + hotkeys)")
    else:
        STARTUP_LNK.unlink(missing_ok=True)


def register_uninstaller(uninstall_cmd: str, install_dir: str) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
        for name, val in (("DisplayName", APP_NAME), ("DisplayVersion", __version__),
                          ("Publisher", APP_NAME), ("DisplayIcon", _icon()),
                          ("UninstallString", uninstall_cmd), ("InstallLocation", install_dir)):
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)


# ---------------------------------------------------------------- migration

LEGACY_NAME = "Inplace Converter"
LEGACY_ID = "InplaceConverter"


def migrate_legacy() -> list[str]:
    """Move settings over from the old 'Inplace Converter' install and remove its traces."""
    from . import config
    done = []
    appdata = Path(os.environ.get("APPDATA", Path.home()))
    local = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    old_cfg = appdata / LEGACY_ID / "config.json"
    if old_cfg.exists() and not config.CONFIG_FILE.exists():
        config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old_cfg, config.CONFIG_FILE)
        done.append("settings and hotkeys copied from Inplace Converter")
    old_state = local / LEGACY_ID / "state.json"
    if old_state.exists() and not config.STATE_FILE.exists():
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old_state, config.STATE_FILE)
    _remove_verb_everywhere("InplaceConvert")
    if _key_exists(rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{LEGACY_ID}"):
        _delete_tree(winreg.HKEY_CURRENT_USER,
                     rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{LEGACY_ID}")
        done.append("old Inplace Converter entry removed from Installed apps")
    for lnk in (_PROGRAMS / f"{LEGACY_NAME}.lnk", _PROGRAMS / "Startup" / f"{LEGACY_NAME}.lnk"):
        if lnk.exists():
            lnk.unlink()
            done.append(f"removed shortcut {lnk.name}")
    old_dir = local / LEGACY_ID
    if old_dir.exists():
        shutil.rmtree(old_dir, ignore_errors=True)
        done.append(f"removed {old_dir}")
    if (appdata / LEGACY_ID).exists() and config.CONFIG_FILE.exists():
        shutil.rmtree(appdata / LEGACY_ID, ignore_errors=True)
    _notify_assoc_changed()
    return done


def _key_exists(path: str) -> bool:
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, path).Close()
        return True
    except OSError:
        return False


# ---------------------------------------------------------- install / remove

def install(uninstall_cmd: str | None = None, install_dir: str | None = None,
            startup: bool = True) -> None:
    for line in migrate_legacy():
        print(f"Migrated: {line}")
    n = register_context_menu()
    print(f"Context menu 'Convert to…' registered for all files ({n} formats understood)")
    _shortcut(STARTMENU_LNK, ["settings"], f"{APP_NAME} settings")
    print(f"Start Menu shortcut: {STARTMENU_LNK}")
    set_startup(startup)
    if startup:
        print("Starts automatically with Windows")
    if uninstall_cmd:
        register_uninstaller(uninstall_cmd, install_dir or "")
        print("Added to Settings > Apps > Installed apps")
    else:
        # Installed by the Inno Setup installer, which has its own uninstall entry: drop
        # the one an earlier (self-extracting) installer registered, so there's only one.
        _delete_tree(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)


def uninstall() -> None:
    unregister_context_menu()
    STARTUP_LNK.unlink(missing_ok=True)
    STARTMENU_LNK.unlink(missing_ok=True)
    _delete_tree(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    print("Shell integration removed")


def stop_other_instances() -> None:
    """End every other running ShapeShifter process (tray app, wheels)."""
    import subprocess
    me = os.getpid()
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if FROZEN:
        subprocess.run(["taskkill", "/F", "/FI", f"PID ne {me}", "/IM", Path(sys.executable).name],
                       capture_output=True, creationflags=no_window)
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne %d -and "
          "$_.Name -match 'python' -and $_.CommandLine -match 'shapeshifter|inplace_converter' } | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" % me)
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                   creationflags=no_window)


def uninstall_everything() -> int:
    """Packaged-app uninstaller (Settings > Apps): remove integration, data and the app."""
    import ctypes
    import subprocess
    from . import config
    MB_YESNOCANCEL, MB_ICONQUESTION, IDYES, IDNO = 0x3, 0x20, 6, 7
    answer = ctypes.windll.user32.MessageBoxW(
        None, "Uninstall ShapeShifter?\n\nYes: remove the app and your settings\n"
              "No: remove the app but keep your settings and hotkeys", f"Uninstall {APP_NAME}",
        MB_YESNOCANCEL | MB_ICONQUESTION)
    if answer not in (IDYES, IDNO):
        return 1
    stop_other_instances()
    uninstall()
    shutil.rmtree(config.DATA_DIR, ignore_errors=True)
    if answer == IDYES:
        shutil.rmtree(config.CONFIG_DIR, ignore_errors=True)
    if FROZEN:
        # We can't delete our own folder while running: let cmd do it once we've exited.
        app_dir = Path(sys.executable).parent
        subprocess.Popen(f'cmd /c ping 127.0.0.1 -n 3 > nul & rmdir /s /q "{app_dir}"',
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                         | getattr(subprocess, "DETACHED_PROCESS", 0), cwd=str(app_dir.parent))
    ctypes.windll.user32.MessageBoxW(None, f"{APP_NAME} has been removed.", APP_NAME, 0x40)
    return 0
