"""Checks of the macOS integration on a real Mac (run by CI on GitHub's Apple Silicon machines).

    python packaging/mac_checks.py [--app /Applications/ShapeShifter.app] [--shots DIR]

Without --app it checks the source: text recognition, the font list, Finder's selection, the Quick Action (run
through Automator), and that the event tap starts or explains why not. With --app it starts the built app,
hands it a file the way Finder's Quick Action does, and takes screenshots of what appears.

Each check prints "ok", "FAIL" or "note" (something CI can't test, e.g. a permission only a person can give).
"""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

failures = 0


def report(name, fn):
    global failures
    try:
        out = fn()
        if isinstance(out, str) and out.startswith("note:"):
            print(f"note  {name}: {out[5:].strip()}")
        else:
            print(f"ok    {name}{': ' + out if out else ''}")
    except Exception as e:                                   # noqa: BLE001
        failures += 1
        print(f"FAIL  {name}: {e!r}")


def osascript(script: str, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)


# ------------------------------------------------------------------ source checks

def check_vision(tmp: Path):
    from PIL import Image, ImageDraw
    from shapeshifter import ocr
    from shapeshifter.forms import find_font
    im = Image.new("RGB", (1200, 300), "white")
    ImageDraw.Draw(im).text((40, 90), "Hello ShapeShifter 2026", fill="black", font=find_font("Typed", 72))
    p = tmp / "vision.png"
    im.save(p)
    page, _ = ocr.recognize_file(p, {"ocr_language": "auto"})
    text = page.text()
    if "shapeshifter" not in text.lower():
        raise RuntimeError(f"read {text!r}")
    words = [w for ln in page.lines for w in ln.words]
    first = words[0]
    if not (20 <= first.x <= 120 and 60 <= first.y <= 200):
        raise RuntimeError(f"first word box looks wrong: {first}")
    langs = ocr.status()["languages"]
    return f"{text.strip()!r}, {len(words)} word boxes, {len(langs)} languages"


def check_fonts():
    from shapeshifter import forms
    names = forms.font_names()
    if len(names) < 30:
        raise RuntimeError(f"only {len(names)} fonts")
    for style in ("Typed", "Handwriting", "Typewriter"):
        if forms.find_font(style, 30).getname()[0] in ("", "Aileron"):
            raise RuntimeError(f"no font for {style}")
    return f"{len(names)} fonts; Typed = {forms.find_font('Typed', 30).getname()[0]}, " \
           f"Handwriting = {forms.find_font('Handwriting', 30).getname()[0]}, " \
           f"Typewriter = {forms.find_font('Typewriter', 30).getname()[0]}"


def check_finder_selection(tmp: Path):
    from shapeshifter.mac import finder
    a, b = tmp / "sel-a.txt", tmp / "sel-b.png"
    a.write_text("a")
    b.write_bytes(b"x")
    r = osascript(f'''
tell application "Finder"
    activate
    open POSIX file "{tmp}"
    delay 1
    select {{POSIX file "{a}" as alias, POSIX file "{b}" as alias}}
end tell''')
    if r.returncode != 0:
        return f"note: couldn't drive Finder from the CI shell ({r.stderr.strip()[:160]})"
    time.sleep(1)
    try:
        got = finder.selected_files()
    except finder.FinderAccessDenied as e:
        return f"note: {e}"
    want = {str(a.resolve()), str(b.resolve())}
    got_set = {str(Path(p).resolve()) for p in got}
    if got_set != want:
        raise RuntimeError(f"selection {got} instead of {sorted(want)}")
    return f"{len(got)} files read from Finder's selection; frontmost = {finder.finder_is_frontmost()}"


def check_quick_action(tmp: Path):
    """Build the Quick Action exactly as ShapeShifter does, but with a command that records its arguments, and run
    it through Automator with two files: proves the bundle is a valid service that passes files as "$@"."""
    from shapeshifter.mac import integration
    out = tmp / "qa-args.txt"
    wf = tmp / "probe.workflow" / "Contents"
    wf.mkdir(parents=True)
    with open(wf / "document.wflow", "wb") as f:
        plistlib.dump(integration._workflow_plist(f'for f in "$@"; do echo "$f" >> "{out}"; done'), f)
    with open(wf / "Info.plist", "wb") as f:
        plistlib.dump(integration._workflow_info(), f)
    files = [tmp / "picked file 1.jpg"]                     # automator -i takes one item
    for p in files:
        p.write_bytes(b"x")
    r = subprocess.run(["automator", "-i", str(files[0]), str(wf.parent)], capture_output=True, text=True,
                       timeout=120)
    if not out.exists():
        raise RuntimeError(f"the workflow didn't run its shell step (exit {r.returncode}): {r.stderr.strip()[:300]}")
    lines = out.read_text().splitlines()
    if sorted(lines) != sorted(str(p) for p in files):
        raise RuntimeError(f"arguments {lines}")
    # The real one, installed for this user, and registered with the Services menu
    n = integration.register_context_menu()
    dump = subprocess.run(["/System/Library/CoreServices/pbs", "-dump_pboard"], capture_output=True, text=True,
                          timeout=60).stdout
    listed = integration.SERVICE_NAME in dump
    return (f"Automator ran it with {lines!r} (a name with spaces arrives whole); installed at {integration.WORKFLOW}"
            f" ({n} formats); listed by pbs: {listed}")


def check_event_tap():
    from shapeshifter.mac import appkit
    from shapeshifter.mac.events import EventTap, finder_window_at
    trusted = appkit.accessibility_trusted()
    tap = EventTap(lambda p: None, lambda t: None)
    status = tap.set_bindings([("ctrl+alt+c", 0)], [("ctrl+mouse:middle", 1)])
    running = tap.running
    tap.stop()
    finder_window_at(10, 10)                                  # the window lookup runs without errors
    if trusted and not running:
        raise RuntimeError("Accessibility is on but the tap didn't start")
    if not trusted:
        return (f"note: no Accessibility access on the CI machine, so the tap correctly reports "
                f"{status} (not running) and the app asks for the permission")
    return f"tap running with {status}"


def _post_key(code: int, flags: int):
    import Quartz
    for down in (True, False):
        ev = Quartz.CGEventCreateKeyboardEvent(None, code, down)
        Quartz.CGEventSetFlags(ev, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        time.sleep(0.05)


def _post_middle_click(x: float, y: float, flags: int):
    import Quartz
    for kind in (Quartz.kCGEventOtherMouseDown, Quartz.kCGEventOtherMouseUp):
        ev = Quartz.CGEventCreateMouseEvent(None, kind, (x, y), Quartz.kCGMouseButtonCenter)
        Quartz.CGEventSetIntegerValueField(ev, Quartz.kCGMouseEventButtonNumber, 2)
        Quartz.CGEventSetFlags(ev, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        time.sleep(0.05)


def check_hotkey_end_to_end(tmp: Path, shots: Path):
    """The whole hotkey path with real system events: files selected in Finder, ⌃⌥C pressed, the menu-bar app
    (run from source, which has Accessibility on the CI machine) reads the selection and opens the wheel. Then the
    same with ⌃ + middle click over the Finder window."""
    from shapeshifter import config
    from shapeshifter.mac import appkit
    from shapeshifter.mac.events import KEYCODES, finder_window_at
    if not appkit.accessibility_trusted():
        return "note: no Accessibility access on this machine; the hotkey path can't be driven"
    files = [tmp / "hot 1.png", tmp / "hot 2.png"]
    from PIL import Image
    for p in files:
        Image.new("RGB", (40, 30), (30, 120, 200)).save(p)
    cfg = config.load()
    cfg["hotkeys"] = [{"keys": "ctrl+alt+c", "action": "wheel"}, {"keys": "ctrl+mouse:middle", "action": "wheel"}]
    cfg["close_on_focus_loss"] = False
    config.save(cfg)
    log = config.LOG_FILE
    start = log.stat().st_size if log.exists() else 0
    daemon = subprocess.Popen([sys.executable, "-m", "shapeshifter", "daemon"], cwd=str(ROOT),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(8)
        sel = ", ".join(f'POSIX file "{p}" as alias' for p in files)
        r = osascript(f'''
tell application "Finder"
    activate
    open POSIX file "{tmp}"
    delay 1
    select {{{sel}}}
end tell''')
        if r.returncode != 0:
            return f"note: couldn't drive Finder ({r.stderr.strip()[:160]})"
        time.sleep(1.5)
        _post_key(KEYCODES["c"], 0x40000 | 0x80000)          # control + option + C
        time.sleep(3)
        print("      hotkey:", screenshot(shots, "4-hotkey-wheel"))
        text = log.read_text(encoding="utf-8", errors="replace")[start:]
        if "Showing the wheel for 2 file(s)" not in text:
            raise RuntimeError("⌃⌥C didn't open the wheel for the two selected files; log:\n" + text[-2500:])
        # close the wheel (Escape), then a mouse trigger over the Finder window
        _post_key(KEYCODES["escape"], 0)
        time.sleep(1.5)
        osascript('tell application "Finder" to activate')
        time.sleep(1)
        b = osascript('tell application "Finder" to get bounds of front window').stdout.strip()
        x0, y0, x1, y1 = (float(v) for v in b.split(", "))
        point = ((x0 + x1) / 2, y1 - 40)                      # an empty spot inside the window
        mid = len(log.read_text(encoding="utf-8", errors="replace"))
        found = finder_window_at(*point)
        _post_middle_click(*point, 0x40000)                   # control + middle click
        for _ in range(20):
            time.sleep(0.5)
            text2 = log.read_text(encoding="utf-8", errors="replace")[mid:]
            if "Showing the wheel" in text2:
                break
        time.sleep(1)
        print("      mouse:", screenshot(shots, "5-mouse-trigger-wheel"))
        text2 = log.read_text(encoding="utf-8", errors="replace")[mid:]
        if "Showing the wheel" not in text2:
            raise RuntimeError(f"⌃+middle click over Finder (window {found}) didn't open the wheel; log:\n"
                               + text2[-2500:])
        return f"⌃⌥C opened the wheel for 2 Finder files; ⌃+middle click over Finder window {found} opened it too"
    finally:
        daemon.terminate()
        try:
            daemon.wait(10)
        except subprocess.TimeoutExpired:
            daemon.kill()


def check_launch_agent(tmp: Path):
    from shapeshifter.mac import integration
    agent = integration.AGENT
    integration.set_startup(True)
    data = plistlib.loads(agent.read_bytes())
    integration.set_startup(False)
    if agent.exists():
        raise RuntimeError("not removed")
    return f"{data['Label']}: {' '.join(data['ProgramArguments'])}"


# ------------------------------------------------------------------ built-app checks

def screenshot(shots: Path, name: str) -> str:
    shots.mkdir(parents=True, exist_ok=True)
    p = shots / f"{name}.png"
    r = subprocess.run(["screencapture", "-x", str(p)], capture_output=True, text=True, timeout=30)
    return str(p) if r.returncode == 0 and p.exists() else f"(no screenshot: {r.stderr.strip()[:100]})"


def check_app(app: Path, shots: Path, tmp: Path):
    from shapeshifter import config
    from shapeshifter.mac import BUNDLE_ID
    log = config.LOG_FILE
    start = log.stat().st_size if log.exists() else 0
    # No hotkeys for this run: the CI machine can't grant the app Accessibility, and the system's prompt would
    # take the focus away from the wheel (which closes when it loses the focus, as it should).
    cfg = config.load()
    cfg["hotkeys"] = []
    config.save(cfg)
    subprocess.run(["/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/"
                    "lsregister", "-f", str(app)], capture_output=True, timeout=60)
    subprocess.run(["open", str(app)], check=True, timeout=60)
    time.sleep(12)                                            # first start: Python, Qt, first-run setup
    print("      first start:", screenshot(shots, "1-first-start"))
    pic = tmp / "handed over.png"
    from PIL import Image
    Image.new("RGB", (64, 48), (200, 60, 60)).save(pic)
    subprocess.run(["open", "-b", BUNDLE_ID, str(pic)], check=True, timeout=60)
    time.sleep(2)
    print("      wheel:", screenshot(shots, "2-wheel-after-quick-action"))
    time.sleep(4)
    print("      later:", screenshot(shots, "3-six-seconds-later"))
    text = log.read_text(encoding="utf-8", errors="replace")[start:] if log.exists() else ""
    running = subprocess.run(["pgrep", "-f", "ShapeShifter.app/Contents/MacOS/ShapeShifter"], capture_output=True,
                             text=True).stdout.split()
    subprocess.run(["osascript", "-e", f'tell application id "{BUNDLE_ID}" to quit'], capture_output=True, timeout=30)
    time.sleep(2)
    subprocess.run(["pkill", "-f", "ShapeShifter.app/Contents/MacOS/ShapeShifter"], capture_output=True)
    if not running:
        raise RuntimeError("the app wasn't running after it was opened; log:\n" + text[-2000:])
    if f"Finder handed over {pic}" not in text:
        raise RuntimeError("the app didn't receive the file; log:\n" + text[-2000:])
    if "Showing the wheel for 1 file(s)" not in text:
        raise RuntimeError("the wheel wasn't shown; log:\n" + text[-2000:])
    services = Path.home() / "Library" / "Services" / "Convert with ShapeShifter.workflow"
    closed = "Wheel closed: another window took the focus" in text
    return (f"the app received the file and showed the wheel{' (then lost the focus)' if closed else ''}; "
            f"first run installed the Quick Action: {services.exists()}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", type=Path)
    ap.add_argument("--shots", type=Path, default=Path("mac-shots"))
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="ss-mac-checks-")).resolve()
    if a.app:
        report("built app: start, receive a file from the Quick Action route", lambda: check_app(a.app, a.shots, tmp))
    else:
        report("Vision text recognition", lambda: check_vision(tmp))
        report("fonts for Fill form", check_fonts)
        report("Finder selection", lambda: check_finder_selection(tmp))
        report("Quick Action", lambda: check_quick_action(tmp))
        report("event tap (hotkeys, mouse triggers)", check_event_tap)
        report("start at login (LaunchAgent)", lambda: check_launch_agent(tmp))
        report("hotkey and mouse trigger, end to end", lambda: check_hotkey_end_to_end(tmp, a.shots))
    print("ALL OK" if not failures else f"{failures} CHECK(S) FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    sys.exit(main())
