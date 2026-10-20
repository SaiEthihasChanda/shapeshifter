"""Hand file paths (or commands) to an already-running instance over a named pipe.

Explorer starts one process per selected file. The first process becomes the leader
(holding a named mutex and listening on a QLocalServer pipe); the rest forward their
paths and exit, so a multi-selection opens a single wheel. The tray daemon is a
permanent leader. Clients use plain file I/O so they never pay for importing Qt.
"""

from __future__ import annotations

import getpass
import json
import re
import time

_user = re.sub(r"[^A-Za-z0-9_.-]", "_", getpass.getuser())
SERVER_NAME = f"ShapeShifter-{_user}"
PIPE_PATH = rf"\\.\pipe\{SERVER_NAME}"
MUTEX_NAME = f"Local\\ShapeShifter-{_user}"
DAEMON_MUTEX_NAME = MUTEX_NAME + "-daemon"

ERROR_ALREADY_EXISTS = 183
ASFW_ANY = -1


def send_message(msg: dict, timeout: float = 0.0) -> bool:
    """Deliver ``msg`` to a running instance. Returns True on success."""
    import ctypes

    import pywintypes
    import win32pipe

    # We were started by a user action, so we may grant the receiver the right to
    # bring its window to the front.
    ctypes.windll.user32.AllowSetForegroundWindow(ASFW_ANY)
    payload = (json.dumps(msg) + "\n").encode("utf-8")
    deadline = time.monotonic() + timeout
    while True:
        try:
            win32pipe.WaitNamedPipe(PIPE_PATH, 500)
            with open(PIPE_PATH, "wb", buffering=0) as f:
                f.write(payload)
            return True
        except (OSError, pywintypes.error):
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)


def send(paths: list[str], timeout: float = 0.0) -> bool:
    return send_message({"paths": paths}, timeout)


class LeaderLock:
    """Named mutex used to elect a single leader process."""

    def __init__(self):
        import win32api
        import win32event

        self.handle = win32event.CreateMutex(None, False, MUTEX_NAME)
        self.is_leader = win32api.GetLastError() != ERROR_ALREADY_EXISTS

    def release(self):
        import win32api

        if self.handle:
            win32api.CloseHandle(self.handle)
            self.handle = None


def _mutex_exists(name: str) -> bool:
    import pywintypes
    import win32api
    import win32event

    try:
        h = win32event.OpenMutex(0x00100000, False, name)  # SYNCHRONIZE
    except pywintypes.error:
        return False
    win32api.CloseHandle(h)
    return True


def daemon_running() -> bool:
    return _mutex_exists(DAEMON_MUTEX_NAME)
