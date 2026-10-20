"""Qt application: single-instance leader, tray daemon and global hotkeys."""

from __future__ import annotations

import json
import logging
import os
import sys
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QAbstractNativeEventFilter, QFileSystemWatcher, QObject, Qt, QTimer, \
    Signal
from PySide6.QtGui import QAction, QActionGroup, QIcon
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QSystemTrayIcon, QWidget

from . import APP_NAME, APP_USER_MODEL_ID, config, ipc
from .icon import ensure_icon

log = logging.getLogger(__name__)

HOTKEY_BASE = 0xB000
WM_HOTKEY = 0x0312


def _make_app() -> QApplication:
    try:
        # Attributes notifications/taskbar to us instead of "Python".
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    try:
        app.setWindowIcon(QIcon(str(ensure_icon())))
    except Exception:
        log.exception("Icon generation failed")
    return app


def describe_binding(b: dict) -> str:
    action = b.get("action")
    if action == "wheel":
        return "Open the conversion wheel"
    if action == "repeat":
        return "Repeat last conversion"
    if action == "convert":
        return f"Convert to {str(b.get('target', '?')).upper()}"
    return str(action)


MOUSE_NAMES = {
    "mouse:left": "Left click", "mouse:right": "Right click", "mouse:middle": "Middle click",
    "mouse:x1": "Mouse 4 (Back)", "mouse:x2": "Mouse 5 (Forward)",
    "wheel:up": "Scroll up", "wheel:down": "Scroll down",
}


def pretty_keys(spec: str) -> str:
    names = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win", **MOUSE_NAMES}
    return "+".join(names.get(p, p.upper() if len(p) <= 3 else p.capitalize())
                    for p in spec.split("+") if p)


class Controller(QObject):
    """Receives file paths (argv, pipe, hotkey) and shows one wheel at a time."""

    def __init__(self, daemon: bool):
        super().__init__()
        self.daemon = daemon
        self.cfg = config.load()
        self.wheel = None
        self.pending: list[str] = []
        self.pending_target: str | None = None
        self.debounce = QTimer(self, singleShot=True, interval=250)
        self.debounce.timeout.connect(self._flush)

        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self._on_connection)
        if not self.server.listen(ipc.SERVER_NAME):
            log.warning("Could not listen on %s: %s", ipc.SERVER_NAME, self.server.errorString())

    # -------------------------------------------------------------- input

    def _on_connection(self):
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            buf = bytearray()

            def read(sock=sock, buf=buf):
                buf.extend(bytes(sock.readAll()))

            def done(sock=sock, buf=buf):
                buf.extend(bytes(sock.readAll()))
                sock.deleteLater()
                for line in bytes(buf).decode("utf-8", "replace").splitlines():
                    try:
                        self.handle_message(json.loads(line))
                    except Exception:
                        log.exception("Bad IPC message")

            sock.readyRead.connect(read)
            sock.disconnected.connect(done)

    def handle_message(self, msg: dict):
        if msg.get("cmd") == "settings":
            self.open_settings()
        elif msg.get("paths"):
            self.add_paths(msg["paths"])

    def open_settings(self):
        from .settings_app import show_settings
        show_settings(self)

    def add_paths(self, paths: list[str], immediate: bool = False, target: str | None = None):
        paths = [str(Path(p)) for p in paths if p]
        if not paths:
            return
        if target is None and self.wheel is not None and self.wheel.state == "choose" \
                and not self.wheel.auto_target:
            self.wheel.add_paths(paths)
            return
        self.pending.extend(paths)
        self.pending_target = target
        if immediate:
            self._flush()
        else:
            self.debounce.start()

    def _flush(self):
        if self.wheel is not None:
            return  # shown after the current wheel closes
        paths = list(dict.fromkeys(self.pending))
        target, self.pending_target = self.pending_target, None
        self.pending.clear()
        if not paths:
            self._maybe_quit()
            return
        from .wheel import WheelOverlay
        self.cfg = config.load()
        self.wheel = WheelOverlay(paths, self.cfg, auto_target=target)
        self.wheel.closed.connect(self._on_wheel_closed)
        self.wheel.show()

    def _on_wheel_closed(self):
        self.wheel = None
        if self.pending:
            QTimer.singleShot(0, self._flush)
        else:
            self._maybe_quit()

    def _maybe_quit(self):
        from .settings_app import settings_open
        if not self.daemon and self.wheel is None and not self.pending and not settings_open():
            self.server.close()
            QApplication.quit()


# ------------------------------------------------------------------- daemon

class HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam >= HOTKEY_BASE:
                hid = int(msg.wParam)
                QTimer.singleShot(0, lambda: self.callback(hid))
                return True, 0
        return False, 0


class MouseBridge(QObject):
    """Carries mouse-hook triggers from the hook thread to the Qt thread."""
    triggered = Signal(object)


class Daemon(Controller):
    def __init__(self):
        super().__init__(daemon=True)
        self._hotkey_window = QWidget()
        self._hotkey_hwnd = int(self._hotkey_window.winId())
        self._filter = HotkeyFilter(self.on_hotkey)
        QApplication.instance().installNativeEventFilter(self._filter)
        self.bindings: dict[int, dict] = {}
        self.binding_status: list[tuple[dict, bool]] = []
        self.hotkeys_suspended = False
        from .mousehook import MouseHook
        self._mouse_bridge = MouseBridge()
        self._mouse_bridge.triggered.connect(self.on_mouse_trigger)
        self.mouse = MouseHook(self._mouse_bridge.triggered.emit)
        self.register_hotkeys()
        self._build_tray()

        # Reload when the settings app (or a text editor) changes config.json.
        config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if not config.CONFIG_FILE.exists():
            config.save(self.cfg)
        self._watcher = QFileSystemWatcher([str(config.CONFIG_DIR)], self)
        self._watcher.directoryChanged.connect(lambda _: self._reload_soon.start())
        self._reload_soon = QTimer(self, singleShot=True, interval=300)
        self._reload_soon.timeout.connect(self.reload_config)

    # -------------------------------------------------------------- hotkeys

    def register_hotkeys(self):
        from .mousehook import is_mouse_spec
        from .winutil import register_hotkey, unregister_hotkey
        for hid in list(self.bindings):
            unregister_hotkey(self._hotkey_hwnd, hid)
        self.bindings.clear()
        self.binding_status = []
        if self.hotkeys_suspended:
            self.mouse.set_bindings([])
            return
        status: dict[int, bool] = {}
        mouse_bindings = []
        hotkeys = self.cfg.get("hotkeys", [])
        for i, b in enumerate(hotkeys):
            spec = b.get("keys", "")
            if is_mouse_spec(spec):
                mouse_bindings.append((spec, i))
                continue
            hid = HOTKEY_BASE + i
            try:
                ok = register_hotkey(self._hotkey_hwnd, hid, spec)
            except ValueError:
                log.exception("Invalid hotkey %s", b)
                ok = False
            if ok:
                self.bindings[hid] = b
            else:
                log.warning("Hotkey %s could not be registered", spec)
            status[i] = ok
        for i, ok in self.mouse.set_bindings(mouse_bindings):
            status[i] = ok
        if mouse_bindings:
            from . import explorer
            QTimer.singleShot(1000, explorer.warm_up)
        self.binding_status = [(b, status.get(i, False)) for i, b in enumerate(hotkeys)]

    def suspend_hotkeys(self, suspended: bool):
        """Let the settings app capture key combos that are currently registered."""
        if suspended != self.hotkeys_suspended:
            self.hotkeys_suspended = suspended
            self.register_hotkeys()

    def reload_config(self):
        new = config.load()
        hotkeys_changed = new.get("hotkeys") != self.cfg.get("hotkeys")
        if new.get("prefer_native") != self.cfg.get("prefer_native"):
            from . import planner
            planner.reset()
        self.cfg = new
        if hotkeys_changed:
            self.register_hotkeys()
            self._update_tray()

    def on_hotkey(self, hid: int):
        b = self.bindings.get(hid)
        if not b:
            return
        from . import explorer
        import win32gui
        hwnd = win32gui.GetForegroundWindow()
        try:
            paths = [p for p in explorer.selected_files(hwnd) if os.path.isfile(p)]
        except Exception:
            log.exception("Reading Explorer selection failed")
            paths = []
        log.info("Hotkey %s (%s): foreground %s, %d file(s) selected", b.get("keys"),
                 b.get("action"), win32gui.GetClassName(hwnd) if hwnd else "-", len(paths))
        self.run_binding(b, paths)

    def on_mouse_trigger(self, trigger):
        """A mouse binding fired over Explorer/the desktop (already filtered by the hook)."""
        from . import explorer
        hotkeys = self.cfg.get("hotkeys", [])
        if not 0 <= trigger.payload < len(hotkeys):
            return
        b = hotkeys[trigger.payload]
        # Clicking a file acts on that file, like a left click would select it.
        explorer.select_item_at(trigger.x, trigger.y)
        try:
            paths = [p for p in explorer.selected_files(trigger.hwnd) if os.path.isfile(p)]
        except Exception:
            log.exception("Reading Explorer selection failed")
            paths = []
        log.info("Mouse %s (%s): %d file(s) selected", b.get("keys"), b.get("action"), len(paths))
        self.run_binding(b, paths)

    def run_binding(self, b: dict, paths: list[str]):
        if not paths:
            self.notify("Select one or more files in File Explorer first.")
            return
        action = b.get("action")
        if action == "repeat":
            target = config.load_state().get("last_target")
            if not target:
                self.notify("Nothing to repeat yet — convert something with the wheel first.")
                return
            self.add_paths(paths, immediate=True, target=target)
        elif action == "convert" and b.get("target"):
            self.add_paths(paths, immediate=True, target=b["target"])
        else:
            self.add_paths(paths, immediate=True)

    def notify(self, text: str):
        self.tray.showMessage(APP_NAME, text, QSystemTrayIcon.MessageIcon.Information, 2500)

    # ----------------------------------------------------------------- tray

    def _build_tray(self):
        self.tray = QSystemTrayIcon(QApplication.instance().windowIcon(), self)
        self._menu = QMenu()
        self.tray.setContextMenu(self._menu)
        self.tray.activated.connect(
            lambda reason: self.open_settings()
            if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self._update_tray()
        self.tray.show()

    def _update_tray(self):
        wheel = next((b for b, ok in self.binding_status if ok and b.get("action") == "wheel"), None)
        hint = f"press {pretty_keys(wheel['keys'])}" if wheel else "right-click › Convert to…"
        self.tray.setToolTip(f"{APP_NAME}\nSelect files in Explorer and {hint}")

        menu = self._menu
        menu.clear()
        a = menu.addAction("Settings…")
        a.triggered.connect(self.open_settings)
        f = a.font()
        f.setBold(True)
        a.setFont(f)
        menu.addAction("Convert files…").triggered.connect(self._pick_files)
        menu.addSeparator()
        for b, ok in self.binding_status:
            info = QAction(f"{pretty_keys(b['keys'])}   {describe_binding(b)}"
                           + ("" if ok else "  (in use)"), menu)
            info.setEnabled(False)
            menu.addAction(info)
        menu.addSeparator()

        orig = menu.addMenu("Original file after converting")
        group = QActionGroup(orig)
        for key, label in (("recycle", "Move to Recycle Bin"), ("keep", "Keep it"),
                           ("delete", "Delete permanently")):
            act = QAction(label, orig, checkable=True)
            act.setChecked(self.cfg["original"] == key)
            act.triggered.connect(lambda _=False, k=key: self._set_cfg("original", k))
            group.addAction(act)
            orig.addAction(act)
        menu.addSeparator()
        menu.addAction("Quit").triggered.connect(self._quit)

    def _set_cfg(self, key, value):
        cfg = config.load()
        cfg[key] = value
        config.save(cfg)
        self.cfg = cfg

    def _pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(None, "Choose files to convert")
        if files:
            self.add_paths(files, immediate=True)

    def _quit(self):
        from .winutil import unregister_hotkey
        for hid in list(self.bindings):
            unregister_hotkey(self._hotkey_hwnd, hid)
        self.mouse.stop()
        self.tray.hide()
        self.server.close()
        QApplication.quit()


# --------------------------------------------------------------- entry points

def run_leader(paths: list[str], lock: ipc.LeaderLock, settings: bool = False) -> int:
    app = _make_app()
    ctl = Controller(daemon=False)
    if settings:
        ctl.open_settings()
    ctl.add_paths(paths)
    try:
        return app.exec()
    finally:
        lock.release()


def run_daemon(show_settings: bool = False) -> int:
    import win32api
    import win32event

    singleton = win32event.CreateMutex(None, False, ipc.DAEMON_MUTEX_NAME)
    if win32api.GetLastError() == ipc.ERROR_ALREADY_EXISTS:
        log.info("Daemon already running")
        if show_settings:
            ipc.send_message({"cmd": "settings"}, timeout=5)
        return 0

    # Become the IPC leader so context-menu launches are forwarded here.
    lock = ipc.LeaderLock()
    if not lock.is_leader:
        lock.release()
        import time
        for _ in range(100):  # wait for a short-lived leader to finish
            time.sleep(0.1)
            lock = ipc.LeaderLock()
            if lock.is_leader:
                break
            lock.release()

    app = _make_app()
    daemon = Daemon()
    if show_settings:
        QTimer.singleShot(0, daemon.open_settings)
    try:
        return app.exec()
    finally:
        lock.release()
        win32api.CloseHandle(singleton)
