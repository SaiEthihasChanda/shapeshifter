"""Hand file paths (or commands) to an already-running instance.

Explorer starts one process per selected file. The first process becomes the leader
(holding a named lock and listening on a QLocalServer); the rest forward their
paths and exit, so a multi-selection opens a single wheel. The tray daemon is a
permanent leader. Clients use plain file / socket I/O so they never pay for importing Qt.

Windows: a named mutex and a named pipe. macOS: lock files (flock) and the Unix socket
QLocalServer creates in the temporary folder. (On a Mac, Finder hands files to the running
app through "open" events instead, so this is mostly used from the command line.)
"""

from __future__ import annotations

import getpass
import json
import os
import re
import sys
import tempfile
import time

_user = re.sub(r"[^A-Za-z0-9_.-]", "_", getpass.getuser())
SERVER_NAME = f"ShapeShifter-{_user}"
PIPE_PATH = rf"\\.\pipe\{SERVER_NAME}"
MUTEX_NAME = f"Local\\ShapeShifter-{_user}"
DAEMON_MUTEX_NAME = MUTEX_NAME + "-daemon"

ERROR_ALREADY_EXISTS = 183
ASFW_ANY = -1
IS_WIN = sys.platform == "win32"


def socket_path() -> str:
    """Where QLocalServer puts its socket on macOS / Linux (QDir::tempPath() + name)."""
    return os.path.join(tempfile.gettempdir().rstrip("/"), SERVER_NAME)


def _lock_path(name: str) -> str:
    return os.path.join(tempfile.gettempdir(), re.sub(r"[^A-Za-z0-9_.-]", "_", name) + ".lock")


def send_message(msg: dict, timeout: float = 0.0) -> bool:
    """Deliver ``msg`` to a running instance. Returns True on success."""
    payload = (json.dumps(msg) + "\n").encode("utf-8")
    deadline = time.monotonic() + timeout
    if IS_WIN:
        import ctypes

        import pywintypes
        import win32pipe

        # We were started by a user action, so we may grant the receiver the right to
        # bring its window to the front.
        ctypes.windll.user32.AllowSetForegroundWindow(ASFW_ANY)
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
    import socket
    while True:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(2)
                s.connect(socket_path())
                s.sendall(payload)
            return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)


def send(paths: list[str], timeout: float = 0.0) -> bool:
    return send_message({"paths": paths}, timeout)


class NamedLock:
    """A lock only one process of this user can hold: a named mutex on Windows, a lock file elsewhere.
    ``acquired`` says whether this process got it."""

    def __init__(self, name: str):
        self.name = name
        self.handle = None
        if IS_WIN:
            import win32api
            import win32event
            self.handle = win32event.CreateMutex(None, False, name)
            self.acquired = win32api.GetLastError() != ERROR_ALREADY_EXISTS
            return
        import fcntl
        fd = os.open(_lock_path(name), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.acquired = True
            self.handle = fd
        except OSError:
            os.close(fd)
            self.acquired = False

    def release(self):
        if not self.handle:
            return
        if IS_WIN:
            import win32api
            win32api.CloseHandle(self.handle)
        else:
            import fcntl
            try:
                fcntl.flock(self.handle, fcntl.LOCK_UN)
            finally:
                os.close(self.handle)
        self.handle = None


class LeaderLock(NamedLock):
    """Elects a single leader process (the one showing the wheel)."""

    def __init__(self):
        super().__init__(MUTEX_NAME)
        self.is_leader = self.acquired


def _held(name: str) -> bool:
    if IS_WIN:
        import pywintypes
        import win32api
        import win32event
        try:
            h = win32event.OpenMutex(0x00100000, False, name)  # SYNCHRONIZE
        except pywintypes.error:
            return False
        win32api.CloseHandle(h)
        return True
    probe = NamedLock(name)
    probe.release()
    return not probe.acquired


def daemon_running() -> bool:
    return _held(DAEMON_MUTEX_NAME)
