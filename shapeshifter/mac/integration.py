"""Per-user macOS integration: the Finder Quick Action, start at login, and removing ShapeShifter.

The same functions as ``shell`` (the Windows integration), so Settings and the command line use either one.

* Right-click › Quick Actions › "Convert with ShapeShifter": a small Automator service in ~/Library/Services
  that hands the selected files to the app with ``open -b <bundle id>``. macOS delivers them to the running
  app as "open" events (starting it first if needed), so one selection always opens one wheel.
* Start at login: a LaunchAgent in ~/Library/LaunchAgents that starts the menu-bar app.
"""

from __future__ import annotations

import logging
import os
import plistlib
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from .. import APP_NAME
from . import BUNDLE_ID

log = logging.getLogger(__name__)

SERVICE_NAME = f"Convert with {APP_NAME}"
SERVICES_DIR = Path.home() / "Library" / "Services"
WORKFLOW = SERVICES_DIR / f"{SERVICE_NAME}.workflow"
AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
AGENT = AGENTS_DIR / f"{BUNDLE_ID}.plist"
FROZEN = bool(getattr(sys, "frozen", False))


def launcher() -> tuple[str, list[str]]:
    """(executable, leading arguments) that start ShapeShifter."""
    if FROZEN:
        return sys.executable, []
    return sys.executable, ["-m", "shapeshifter"]


def _hand_over_command() -> str:
    """The shell command the Quick Action runs with the selected files as "$@"."""
    if FROZEN:
        return f'/usr/bin/open -b {BUNDLE_ID} "$@"'
    exe, lead = launcher()                           # a source checkout: forward through the command line
    quoted = " ".join(f"'{a}'" for a in [exe, *lead])
    return f'{quoted} open "$@" >/dev/null 2>&1 &'


# ------------------------------------------------------------------ Quick Action

def _workflow_plist(command: str) -> dict:
    """document.wflow: one "Run Shell Script" action that receives files and folders in Finder as arguments."""
    ids = [str(uuid.uuid4()).upper() for _ in range(3)]
    action = {
        "AMAccepts": {"Container": "List", "Optional": True, "Types": ["com.apple.cocoa.string"]},
        "AMActionVersion": "2.0.3",
        "AMApplication": ["Automator"],
        "AMParameterProperties": {"COMMAND_STRING": {}, "CheckedForUserDefaultShell": {}, "inputMethod": {},
                                  "shell": {}, "source": {}},
        "AMProvides": {"Container": "List", "Types": ["com.apple.cocoa.string"]},
        "ActionBundlePath": "/System/Library/Automator/Run Shell Script.action",
        "ActionName": "Run Shell Script",
        "ActionParameters": {"COMMAND_STRING": command, "CheckedForUserDefaultShell": True,
                             "inputMethod": 1, "shell": "/bin/zsh", "source": ""},
        "BundleIdentifier": "com.apple.RunShellScript",
        "CFBundleVersion": "2.0.3",
        "CanShowSelectedItemsWhenRun": False,
        "CanShowWhenRun": True,
        "Category": ["AMCategoryUtilities"],
        "Class Name": "RunShellScriptAction",
        "InputUUID": ids[0],
        "Keywords": ["Shell", "Script", "Command", "Run", "Unix"],
        "OutputUUID": ids[1],
        "UUID": ids[2],
        "UnlocalizedApplications": ["Automator"],
        "arguments": {
            "0": {"default value": 0, "name": "inputMethod", "required": "0", "type": "0", "uuid": "0"},
            "1": {"default value": False, "name": "CheckedForUserDefaultShell", "required": "0", "type": "0", "uuid": "1"},
            "2": {"default value": "", "name": "source", "required": "0", "type": "0", "uuid": "2"},
            "3": {"default value": "", "name": "COMMAND_STRING", "required": "0", "type": "0", "uuid": "3"},
            "4": {"default value": "/bin/sh", "name": "shell", "required": "0", "type": "0", "uuid": "4"},
        },
        "isViewVisible": True,
        "location": "309.000000:253.000000",
        "nibPath": "/System/Library/Automator/Run Shell Script.action/Contents/Resources/Base.lproj/main.nib",
    }
    return {
        "AMApplicationBuild": "523",
        "AMApplicationVersion": "2.10",
        "AMDocumentVersion": "2",
        "actions": [{"action": action, "isViewVisible": True}],
        "connectors": {},
        "workflowMetaData": {
            "applicationBundleIDsByPath": {},
            "applicationPaths": [],
            "inputTypeIdentifier": "com.apple.Automator.fileSystemObject",
            "outputTypeIdentifier": "com.apple.Automator.nothing",
            "presentationMode": 15,
            "processesInput": False,
            "serviceApplicationBundleID": "com.apple.finder",
            "serviceApplicationPath": "/System/Library/CoreServices/Finder.app",
            "serviceInputTypeIdentifier": "com.apple.Automator.fileSystemObject",
            "serviceOutputTypeIdentifier": "com.apple.Automator.nothing",
            "serviceProcessesInput": False,
            "systemImageName": "NSActionTemplate",
            "useAutomaticInputType": False,
            "workflowTypeIdentifier": "com.apple.Automator.servicesMenu",
        },
    }


def _workflow_info() -> dict:
    return {
        "NSServices": [{
            "NSBackgroundColorName": "background",
            "NSIconName": "NSActionTemplate",
            "NSMenuItem": {"default": SERVICE_NAME},
            "NSMessage": "runWorkflowAsService",
            "NSRequiredContext": {"NSApplicationIdentifier": "com.apple.finder"},
            "NSSendFileTypes": ["public.item"],
        }],
    }


def _refresh_services() -> None:
    try:
        subprocess.run(["/System/Library/CoreServices/pbs", "-update"], capture_output=True, timeout=20)
    except Exception:
        log.exception("Couldn't refresh the Services menu")


def register_context_menu() -> int:
    unregister_context_menu(refresh=False)
    contents = WORKFLOW / "Contents"
    contents.mkdir(parents=True, exist_ok=True)
    with open(contents / "document.wflow", "wb") as f:
        plistlib.dump(_workflow_plist(_hand_over_command()), f)
    with open(contents / "Info.plist", "wb") as f:
        plistlib.dump(_workflow_info(), f)
    _refresh_services()
    from .. import engine
    return len(engine.supported_sources())


def context_menu_registered() -> bool:
    return (WORKFLOW / "Contents" / "document.wflow").exists()


def unregister_context_menu(refresh: bool = True) -> None:
    shutil.rmtree(WORKFLOW, ignore_errors=True)
    if refresh:
        _refresh_services()


# ------------------------------------------------------------------ start at login

def startup_enabled() -> bool:
    return AGENT.exists()


def set_startup(enabled: bool) -> None:
    if not enabled:
        AGENT.unlink(missing_ok=True)
        return
    if FROZEN:
        args = ["/usr/bin/open", "-g", "-b", BUNDLE_ID]          # follows the app if it's moved
    else:
        exe, lead = launcher()
        args = [exe, *lead, "daemon"]
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(AGENT, "wb") as f:
        plistlib.dump({"Label": BUNDLE_ID, "ProgramArguments": args, "RunAtLoad": True,
                       "LimitLoadToSessionType": "Aqua", "ProcessType": "Interactive"}, f)


# ------------------------------------------------------------------ install / remove

def install(uninstall_cmd: str | None = None, install_dir: str | None = None, startup: bool = True) -> None:
    n = register_context_menu()
    print(f"Quick Action '{SERVICE_NAME}' added to Finder's right-click menu ({n} formats understood)")
    set_startup(startup)
    if startup:
        print("Starts automatically when you log in")


def uninstall() -> None:
    unregister_context_menu()
    AGENT.unlink(missing_ok=True)
    print("Finder integration removed")


def stop_other_instances() -> None:
    me = os.getpid()
    out = subprocess.run(["/usr/bin/pgrep", "-f", "ShapeShifter|shapeshifter"], capture_output=True, text=True)
    for line in out.stdout.split():
        if line.isdigit() and int(line) != me:
            try:
                os.kill(int(line), 15)
            except OSError:
                pass


def uninstall_everything() -> int:
    """Remove the Finder integration, the settings and the app itself (it goes to the Trash)."""
    from .. import config
    from .appkit import app_bundle
    stop_other_instances()
    uninstall()
    shutil.rmtree(config.DATA_DIR, ignore_errors=True)
    shutil.rmtree(config.CONFIG_DIR, ignore_errors=True)
    bundle = app_bundle()
    if bundle is not None:
        try:
            from send2trash import send2trash
            send2trash(str(bundle))
        except Exception:
            log.exception("Couldn't move %s to the Trash", bundle)
    print(f"{APP_NAME} has been removed.")
    return 0


def migrate_legacy() -> list[str]:
    return []
