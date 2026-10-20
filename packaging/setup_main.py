"""ShapeShifter-Setup.exe: per-user installer for the packaged app.

Bundles payload.zip (the PyInstaller build of ShapeShifter.exe, optionally with ffmpeg).
Installs to %LOCALAPPDATA%\\Programs\\ShapeShifter — no admin rights. Run with /S for a
silent install (add /NOSTARTUP to skip autostart).
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

APP = "ShapeShifter"
INSTALL_DIR = Path(os.environ["LOCALAPPDATA"]) / "Programs" / APP
DATA_DIR = Path(os.environ["LOCALAPPDATA"]) / APP
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def _payload() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / "payload.zip"


def payload_version() -> str:
    try:
        with zipfile.ZipFile(_payload()) as z:
            return z.read("version.txt").decode().strip()
    except Exception:
        return ""


def payload_has_ffmpeg() -> bool:
    try:
        with zipfile.ZipFile(_payload()) as z:
            return "ffmpeg/ffmpeg.exe" in z.namelist()
    except Exception:
        return False


def stop_running():
    subprocess.run(["taskkill", "/F", "/IM", f"{APP}.exe"], capture_output=True,
                   creationflags=NO_WINDOW)
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and "
          "$_.CommandLine -match 'shapeshifter|inplace_converter' } | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                   creationflags=NO_WINDOW)
    time.sleep(0.5)


def install(startup: bool, progress=lambda f, msg: None) -> Path:
    progress(0.02, "Closing ShapeShifter if it's running…")
    stop_running()
    progress(0.08, "Removing the previous version…")
    for attempt in range(10):
        try:
            if INSTALL_DIR.exists():
                shutil.rmtree(INSTALL_DIR)
            break
        except OSError:
            time.sleep(0.5)
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(_payload()) as z:
        members = z.infolist()
        total = sum(m.file_size for m in members) or 1
        done = 0
        for m in members:
            z.extract(m, INSTALL_DIR)
            done += m.file_size
            progress(0.1 + 0.75 * done / total, f"Copying files… {done * 100 // total}%")
    # A source (Python) install of ShapeShifter would run a second tray app: retire it.
    shutil.rmtree(DATA_DIR / "venv", ignore_errors=True)
    exe = INSTALL_DIR / f"{APP}.exe"
    progress(0.9, "Registering with Windows…")
    args = [str(exe), "install", "--uninstall-cmd", f'"{exe}" uninstall --remove-files',
            "--install-dir", str(INSTALL_DIR)]
    if not startup:
        args.append("--no-startup")
    r = subprocess.run(args, capture_output=True, text=True, creationflags=NO_WINDOW, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"Registration failed (exit {r.returncode}). See "
                           f"{DATA_DIR / 'shapeshifter.log'}")
    progress(0.97, "Starting ShapeShifter…")
    subprocess.Popen([str(exe), "daemon"], creationflags=DETACHED, close_fds=True)
    progress(1.0, "Done")
    return exe


def install_ffmpeg_with_winget() -> bool:
    if not shutil.which("winget"):
        return False
    r = subprocess.run(["winget", "install", "--id", "Gyan.FFmpeg", "--silent",
                        "--accept-package-agreements", "--accept-source-agreements"],
                       creationflags=NO_WINDOW)
    return r.returncode == 0


def ffmpeg_present() -> bool:
    return payload_has_ffmpeg() or bool(shutil.which("ffmpeg")) or (
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe"
    ).exists()


# ------------------------------------------------------------------------- GUI

def gui():
    import tkinter as tk
    from tkinter import ttk

    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    root = tk.Tk()
    root.title(f"{APP} Setup")
    root.resizable(False, False)
    try:
        root.iconbitmap(str(Path(getattr(sys, "_MEIPASS", ".")) / "ShapeShifter.ico"))
    except Exception:
        pass
    frm = ttk.Frame(root, padding=22)
    frm.grid()
    version = payload_version()
    ttk.Label(frm, text=f"{APP} {version}", font=("Segoe UI", 18, "bold")).grid(sticky="w")
    ttk.Label(frm, text="Convert files in place: select a file, press Ctrl+Alt+C and pick a format\n"
                        "on the wheel. Installs for your account only — no admin rights needed.",
              font=("Segoe UI", 10), justify="left").grid(sticky="w", pady=(4, 12))
    ttk.Label(frm, text=f"Install to:  {INSTALL_DIR}", font=("Segoe UI", 9)).grid(sticky="w")
    startup = tk.BooleanVar(value=True)
    ttk.Checkbutton(frm, text="Start with Windows (needed for hotkeys)", variable=startup).grid(
        sticky="w", pady=(10, 0))
    want_ff = tk.BooleanVar(value=not ffmpeg_present())
    if not ffmpeg_present():
        ttk.Checkbutton(frm, text="Install ffmpeg for audio/video conversion (via winget)",
                        variable=want_ff).grid(sticky="w")
    open_settings = tk.BooleanVar(value=True)
    ttk.Checkbutton(frm, text="Open ShapeShifter settings when done", variable=open_settings).grid(
        sticky="w")
    bar = ttk.Progressbar(frm, length=440, maximum=1.0)
    bar.grid(pady=(16, 4))
    status = ttk.Label(frm, text="", font=("Segoe UI", 9))
    status.grid(sticky="w")
    buttons = ttk.Frame(frm)
    buttons.grid(sticky="e", pady=(12, 0))
    go = ttk.Button(buttons, text="Install")
    cancel = ttk.Button(buttons, text="Cancel", command=root.destroy)
    go.grid(row=0, column=0, padx=6)
    cancel.grid(row=0, column=1)

    def progress(f, msg):
        root.after(0, lambda: (bar.configure(value=f), status.configure(text=msg)))

    def finished(exe, error):
        if error:
            status.configure(text=f"Failed: {error}")
            go.configure(text="Retry", state="normal")
            cancel.configure(state="normal")
            return
        status.configure(text="Installed. Select a file in Explorer and press Ctrl+Alt+C, "
                              "or right-click › Convert to…")
        go.configure(text="Finish", state="normal", command=root.destroy)
        cancel.grid_remove()
        if open_settings.get():
            subprocess.Popen([str(exe), "settings"], creationflags=DETACHED, close_fds=True)

    def run():
        go.configure(state="disabled")
        cancel.configure(state="disabled")

        def work():
            try:
                exe = install(startup.get(), progress)
                if want_ff.get() and not ffmpeg_present():
                    progress(1.0, "Installing ffmpeg with winget…")
                    install_ffmpeg_with_winget()
                root.after(0, lambda: finished(exe, None))
            except Exception as e:
                err = str(e)
                root.after(0, lambda: finished(None, err))
        threading.Thread(target=work, daemon=True).start()

    go.configure(command=run)
    root.mainloop()


def main():
    if "/S" in sys.argv or "--silent" in sys.argv:
        try:
            install(startup="/NOSTARTUP" not in sys.argv)
            return 0
        except Exception:
            return 1
    gui()
    return 0


if __name__ == "__main__":
    sys.exit(main())
