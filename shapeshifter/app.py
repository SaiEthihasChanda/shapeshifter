"""Qt application: single-instance leader, tray (menu-bar) daemon and global hotkeys."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from PySide6.QtCore import QAbstractNativeEventFilter, QEvent, QFileSystemWatcher, QObject, Qt, QTimer, \
    Signal
from PySide6.QtGui import QAction, QActionGroup, QIcon
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QApplication, QFileDialog, QLabel, QMenu, QSystemTrayIcon, QWidget

from . import APP_NAME, APP_USER_MODEL_ID, config, ipc
from .icon import ensure_icon
from .system import FILE_MANAGER, IS_MAC, IS_WIN, TRASH, pretty_modifier

log = logging.getLogger(__name__)

HOTKEY_BASE = 0xB000
WM_HOTKEY = 0x0312


def _make_app() -> QApplication:
    if IS_WIN:
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
    def name(p: str) -> str:
        if p in ("ctrl", "alt", "shift", "win", "cmd"):
            return pretty_modifier(p)
        return MOUSE_NAMES.get(p, p.upper() if len(p) <= 3 else p.capitalize())
    parts = [name(p) for p in spec.split("+") if p]
    if IS_MAC:                                       # ⌃⌥C, ⌘ Middle click
        mods = "".join(x for x in parts if x in "⌃⌥⇧⌘")
        rest = [x for x in parts if x not in "⌃⌥⇧⌘"]
        return (mods + (" " if mods and rest and len(rest[0]) > 1 else "") + "+".join(rest)) if rest else mods
    return "+".join(parts)


class _FileOpenFilter(QObject):
    """macOS hands files to the running app as "open" events (Finder's Quick Action, Open With, dropping on the
    app): each becomes a path for the wheel."""

    def __init__(self, controller):
        super().__init__(controller)
        self.controller = controller

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.FileOpen:
            path = ev.file() or ev.url().toLocalFile()
            if path:
                log.info("Finder handed over %s", path)
                self.controller.add_paths([path])
            return True
        return False


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

        if IS_MAC:
            QApplication.instance().installEventFilter(_FileOpenFilter(self))
            QLocalServer.removeServer(ipc.SERVER_NAME)  # a socket left behind by a crash would block listen()
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
        log.info("Showing the wheel for %d file(s)", len(paths))
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
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam >= HOTKEY_BASE:
                hid = int(msg.wParam)
                QTimer.singleShot(0, lambda: self.callback(hid))
                return True, 0
        return False, 0


class MouseBridge(QObject):
    """Carries mouse-hook (and, on a Mac, hotkey) triggers from the hook thread to the Qt thread."""
    triggered = Signal(object)


class Toast(QLabel):
    """A short message in the top-right corner (macOS: notifications from an app that isn't notarized
    may never show, so ShapeShifter shows its own)."""

    def __init__(self, text: str, ms: int):
        super().__init__(text)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWordWrap(True)
        self.setMaximumWidth(360)
        self.setStyleSheet("QLabel { background: rgba(28,29,34,235); color: white; padding: 12px 16px;"
                           " border-radius: 10px; font-size: 13px; }")
        self.adjustSize()
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 16, screen.top() + 16)
        QTimer.singleShot(ms, self.close)
        self.show()


class Daemon(Controller):
    def __init__(self):
        super().__init__(daemon=True)
        self.bindings: dict[int, dict] = {}
        self.binding_status: list[tuple[dict, bool]] = []
        self.hotkeys_suspended = False
        self.needs_accessibility = False
        self._mouse_bridge = MouseBridge()
        self._mouse_bridge.triggered.connect(self.on_mouse_trigger)
        if IS_MAC:
            from .mac.events import EventTap
            self._key_bridge = MouseBridge()
            self._key_bridge.triggered.connect(self.on_mac_hotkey)
            self.tap = EventTap(self._key_bridge.triggered.emit, self._mouse_bridge.triggered.emit)
            self._tap_retry = QTimer(self, interval=3000)
            self._tap_retry.timeout.connect(self._retry_tap)
        else:
            self._hotkey_window = QWidget()
            self._hotkey_hwnd = int(self._hotkey_window.winId())
            self._filter = HotkeyFilter(self.on_hotkey)
            QApplication.instance().installNativeEventFilter(self._filter)
            from .mousehook import MouseHook
            self.mouse = MouseHook(self._mouse_bridge.triggered.emit)
        self.register_hotkeys()
        self.update_available = None
        self._update_worker = None
        self._message_action = None
        self._build_tray()
        self._start_update_checks()

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
        if IS_MAC:
            return self._register_mac()
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

    def _register_mac(self):
        """macOS: every hotkey and mouse trigger goes through one event tap, which needs Accessibility access."""
        from .triggers import is_mouse_spec
        hotkeys = self.cfg.get("hotkeys", [])
        keys, mouse = [], []
        if not self.hotkeys_suspended:
            for i, b in enumerate(hotkeys):
                (mouse if is_mouse_spec(b.get("keys", "")) else keys).append((b.get("keys", ""), i))
        status = dict(self.tap.set_bindings(keys, mouse))
        self.binding_status = [(b, status.get(i, False)) for i, b in enumerate(hotkeys)]
        wanted = bool(keys or mouse)
        self.needs_accessibility = wanted and not self.tap.running
        if self.needs_accessibility:
            from .mac import appkit
            if not getattr(self, "_asked_accessibility", False):
                self._asked_accessibility = True
                appkit.accessibility_trusted(prompt=True)        # macOS shows its own "allow" dialog once
            self._tap_retry.start()
        else:
            self._tap_retry.stop()

    def _retry_tap(self):
        """Accessibility was switched on in System Settings: start the hotkeys without a restart."""
        from .mac import appkit
        if appkit.accessibility_trusted():
            self._tap_retry.stop()
            self.register_hotkeys()
            if hasattr(self, "tray"):
                self._update_tray()
            if not self.needs_accessibility:
                self.notify("Hotkeys are on.")

    def on_mac_hotkey(self, index: int):
        hotkeys = self.cfg.get("hotkeys", [])
        if not 0 <= index < len(hotkeys):
            return
        self.run_binding(hotkeys[index], self._finder_selection())

    def _finder_selection(self) -> list[str]:
        from .mac import finder
        try:
            if not finder.finder_is_frontmost():
                return []
            return [p for p in finder.selected_files() if os.path.isfile(p)]
        except finder.FinderAccessDenied as e:
            self.notify(str(e))
            from .mac import appkit
            appkit.open_pane("automation")
            return None
        except Exception:
            log.exception("Reading the Finder selection failed")
            return []

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
        """A mouse binding fired over Explorer/Finder/the desktop (already filtered by the hook)."""
        hotkeys = self.cfg.get("hotkeys", [])
        if not 0 <= trigger.payload < len(hotkeys):
            return
        b = hotkeys[trigger.payload]
        if IS_MAC:
            from .mac import finder
            finder.select_item_at(trigger.x, trigger.y)
            self.run_binding(b, self._finder_selection())
            return
        from . import explorer
        # Clicking a file acts on that file, like a left click would select it.
        explorer.select_item_at(trigger.x, trigger.y)
        try:
            paths = [p for p in explorer.selected_files(trigger.hwnd) if os.path.isfile(p)]
        except Exception:
            log.exception("Reading Explorer selection failed")
            paths = []
        log.info("Mouse %s (%s): %d file(s) selected", b.get("keys"), b.get("action"), len(paths))
        self.run_binding(b, paths)

    def run_binding(self, b: dict, paths: list[str] | None):
        if paths is None:                            # already explained (e.g. a missing permission)
            return
        if not paths:
            self.notify(f"Select one or more files in {FILE_MANAGER} first.")
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

    def notify(self, text: str, ms: int = 2500):
        if IS_MAC:
            self._toast = Toast(text, max(ms, 3500))
            return
        self.tray.showMessage(APP_NAME, text, QSystemTrayIcon.MessageIcon.Information, ms)

    # ----------------------------------------------------------------- tray

    def _build_tray(self):
        self.tray = QSystemTrayIcon(QApplication.instance().windowIcon(), self)
        self._menu = QMenu()
        self.tray.setContextMenu(self._menu)
        self.tray.activated.connect(
            lambda reason: self.open_settings()
            if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.tray.messageClicked.connect(self._message_clicked)
        self._update_tray()
        self.tray.show()

    def _update_tray(self):
        wheel = next((b for b, ok in self.binding_status if ok and b.get("action") == "wheel"), None)
        if IS_MAC:
            hint = f"press {pretty_keys(wheel['keys'])}" if wheel else "right-click › Quick Actions › Convert with ShapeShifter"
        else:
            hint = f"press {pretty_keys(wheel['keys'])}" if wheel else "right-click › Convert to…"
        self.tray.setToolTip(f"{APP_NAME}\nSelect files in {FILE_MANAGER} and {hint}")

        menu = self._menu
        menu.clear()
        a = menu.addAction("Settings…")
        a.triggered.connect(self.open_settings)
        f = a.font()
        f.setBold(True)
        a.setFont(f)
        menu.addAction("Convert files…").triggered.connect(self._pick_files)
        menu.addSeparator()
        if IS_MAC and self.needs_accessibility:
            acc = menu.addAction("Allow hotkeys (Accessibility)…")
            acc.triggered.connect(self._open_accessibility)
            af = acc.font()
            af.setBold(True)
            acc.setFont(af)
            menu.addSeparator()
        if self.update_available:
            u = menu.addAction(f"Update to {self.update_available.version}…")
            u.triggered.connect(self.open_update)
            uf = u.font()
            uf.setBold(True)
            u.setFont(uf)
        menu.addAction("Check for updates…").triggered.connect(lambda: self.open_update(check=True))
        menu.addSeparator()
        for b, ok in self.binding_status:
            info = QAction(f"{pretty_keys(b['keys'])}   {describe_binding(b)}"
                           + ("" if ok else "  (in use)"), menu)
            info.setEnabled(False)
            menu.addAction(info)
        menu.addSeparator()

        orig = menu.addMenu("Original file after converting")
        group = QActionGroup(orig)
        for key, label in (("recycle", f"Move to {TRASH}"), ("keep", "Keep it"),
                           ("delete", "Delete permanently")):
            act = QAction(label, orig, checkable=True)
            act.setChecked(self.cfg["original"] == key)
            act.triggered.connect(lambda _=False, k=key: self._set_cfg("original", k))
            group.addAction(act)
            orig.addAction(act)
        menu.addSeparator()
        menu.addAction("Quit").triggered.connect(self._quit)

    # -------------------------------------------------------------- updates

    def _start_update_checks(self):
        """Look for a newer release shortly after start, then every few hours (it only asks GitHub once a day)."""
        QTimer.singleShot(45_000, self._check_updates)
        self._update_timer = QTimer(self, interval=6 * 3600 * 1000)
        self._update_timer.timeout.connect(self._check_updates)
        self._update_timer.start()

    def _check_updates(self):
        from . import updates
        if self._update_worker is not None or not updates.due(self.cfg):
            return
        from .updates_dialog import CheckWorker
        self._update_worker = CheckWorker()
        self._update_worker.done.connect(self._update_checked)
        self._update_worker.start()

    def _update_checked(self, up, error):
        from . import updates
        self._update_worker = None
        if error:
            log.info("Update check failed: %s", error)
            return
        updates.remember_check()
        if not up:
            if self.update_available:
                self.update_available = None
                self._update_tray()
            return
        if up.version in (config.load_state().get("update_skipped"),):
            return
        self.update_available = up
        self._update_tray()
        if updates.should_announce(up):
            updates.remember_check(up.version)
            self._message_action = "update"
            self.tray.showMessage(APP_NAME, f"ShapeShifter {up.version} is available. Click to update.",
                                  QSystemTrayIcon.MessageIcon.Information, 8000)

    def _message_clicked(self):
        action, self._message_action = self._message_action, None
        if action == "update":
            self.open_update()

    def open_update(self, check: bool = False):
        from .updates_dialog import show_update_dialog
        show_update_dialog(None if check else self.update_available)

    def _set_cfg(self, key, value):
        cfg = config.load()
        cfg[key] = value
        config.save(cfg)
        self.cfg = cfg

    def _pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(None, "Choose files to convert")
        if files:
            self.add_paths(files, immediate=True)

    def _open_accessibility(self):
        from .mac import appkit
        appkit.accessibility_trusted(prompt=True)
        appkit.open_pane("accessibility")
        self.notify("Turn on ShapeShifter in Privacy & Security › Accessibility. The hotkeys start by themselves "
                    "once it's on.", 8000)

    def _quit(self):
        if IS_MAC:
            self.tap.stop()
        else:
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
    singleton = ipc.NamedLock(ipc.DAEMON_MUTEX_NAME)
    if not singleton.acquired:
        singleton.release()
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
    if IS_MAC:
        show_settings = _first_run_mac() or show_settings
    if show_settings:
        QTimer.singleShot(0, daemon.open_settings)
    try:
        return app.exec()
    finally:
        lock.release()
        singleton.release()


def _first_run_mac() -> bool:
    """The first time the app starts on a Mac: add the Finder Quick Action and start at login, and show
    Settings (with the permissions to grant). Later starts refresh the Quick Action if the app moved."""
    from .mac import integration
    state = config.load_state()
    first = not state.get("mac_setup_done")
    try:
        if first:
            integration.install(startup=True)
        elif integration.context_menu_registered():
            integration.register_context_menu()                 # points at this copy of the app
    except Exception:
        log.exception("Setting up the Finder integration failed")
    if first:
        config.save_state(mac_setup_done=True)
    return first
