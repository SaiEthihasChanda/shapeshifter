"""Command line entry point.

  shapeshifter open FILE...              show the wheel for the files (used by the context menu)
  shapeshifter convert --to EXT FILE...  convert without UI
  shapeshifter targets FILE              list the formats FILE can become
  shapeshifter identify FILE             say what FILE really is
  shapeshifter daemon                    run the tray icon + global hotkeys
  shapeshifter settings                  open the settings app
  shapeshifter install / uninstall       add or remove Windows integration
  shapeshifter selftest                  check that every engine works (for builds)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

CLI_COMMANDS = {"convert", "targets", "identify", "fill", "install", "uninstall", "register", "selftest",
                "-h", "--help", "--version"}


def _attach_console() -> None:
    """The packaged app is a windowed exe; when run from a terminal, print there."""
    if not getattr(sys, "frozen", False) or (sys.stdout and sys.stdout.isatty()):
        return
    import ctypes
    import msvcrt
    k32 = ctypes.windll.kernel32
    k32.GetStdHandle.restype = ctypes.c_void_p
    # Output redirected to a file or pipe (e.g. `ShapeShifter.exe selftest > log`): use it.
    streams = []
    for which in (-11, -12):                         # STD_OUTPUT_HANDLE, STD_ERROR_HANDLE
        h = k32.GetStdHandle(which)
        if h and h != ctypes.c_void_p(-1).value and k32.GetFileType(ctypes.c_void_p(h)) in (1, 3):
            streams.append(os.fdopen(msvcrt.open_osfhandle(h, os.O_WRONLY), "w",
                                     encoding="utf-8", buffering=1))
        else:
            streams.append(None)
    if streams[0]:
        sys.stdout = streams[0]
        sys.stderr = streams[1] or streams[0]
        return
    if k32.AttachConsole(-1):    # ATTACH_PARENT_PROCESS
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        print()
    elif sys.stdout is None:
        sys.stdout = sys.stderr = open(os.devnull, "w")


def _abs(paths):
    return [os.path.abspath(p) for p in paths]


def cmd_open(paths: list[str]) -> int:
    from . import ipc

    paths = _abs(paths)
    lock = ipc.LeaderLock()
    if not lock.is_leader:
        lock.release()
        if ipc.send(paths, timeout=8.0):
            return 0
        lock = ipc.LeaderLock()  # leader vanished; take over
    from .app import run_leader
    return run_leader(paths, lock)


def cmd_settings() -> int:
    """Open the settings window inside the tray app, starting the tray app if needed."""
    from . import ipc

    if ipc.daemon_running() and ipc.send_message({"cmd": "settings"}, timeout=5.0):
        return 0
    from .app import run_daemon
    return run_daemon(show_settings=True)


def cmd_convert(paths: list[str], target: str, original: str | None) -> int:
    from . import config, engine
    from .backends import Context, ConversionError
    from .formats import canonical

    cfg = config.load()
    if original:
        cfg["original"] = original
    target = target if ":" in target or target == "extract" else canonical(target)
    failures = 0
    for p in _abs(paths):
        try:
            res = engine.convert_inplace(p, target, Context(cfg=cfg))
            print(f"{os.path.basename(p)} -> {', '.join(o.name for o in res.outputs)}"
                  f" (original {res.original})")
            if res.warning:
                print(f"  warning: {res.warning}")
        except ConversionError as e:
            failures += 1
            print(f"{os.path.basename(p)}: {e}", file=sys.stderr)
    return 1 if failures else 0


def cmd_targets(path: str) -> int:
    from . import engine
    opts = engine.options_for(path)
    if not opts:
        print("(no conversions available)")
    for o in opts:
        note = f"   ⚠ {'; '.join(o.warnings)}" if o.warnings else ""
        print(f"{o.key:14} {o.description if o.kind != 'convert' else o.via}{note}")
    return 0


def cmd_identify(path: str) -> int:
    from .inspector import report
    rep = report(path)
    print(rep["verdict"])
    for fmt, conf, ev in rep["candidates"]:
        print(f"  {fmt:8} {conf:4.0%}  {ev}")
    if rep["conversions"]:
        print("Can become: " + ", ".join(rep["conversions"]))
    return 0


def cmd_fill(args) -> int:
    """List a form picture's fields, or fill it from the command line (the wheel's window does the same)."""
    from . import config, forms
    if args.gui:
        from PySide6.QtWidgets import QApplication
        from .formfill import show_form_filler
        app = QApplication.instance() or QApplication(sys.argv[:1])
        show_form_filler(args.file, config.load())
        return app.exec()
    fields, (w, h) = forms.detect(args.file, config.load())
    if args.list or not (args.set or args.tick or args.sign):
        print(f"{w}x{h}: {len(fields)} field(s)")
        for f in fields:
            x, y, fw, fh = f.box
            print(f"  {f.kind:9} {f.label or '(unlabelled)':32} at {x:.0f},{y:.0f}  {fw:.0f}x{fh:.0f}")
        return 0

    def find(label: str, kinds: tuple[str, ...]):
        want = label.strip().lower()
        cands = [f for f in fields if f.kind in kinds]
        hit = next((f for f in cands if f.label.lower() == want), None)             or next((f for f in cands if want in f.label.lower()), None)
        if hit is None:
            raise SystemExit(f"No {'/'.join(kinds)} field called '{label}'. Found: "
                             + ", ".join(f.label or '(unlabelled)' for f in cands))
        return hit

    for item in args.set or []:
        label, _, value = item.partition("=")
        find(label, ("text",)).value = value
    for label in args.tick or []:
        find(label, ("checkbox",)).checked = True
    if args.sign:
        for f in fields:
            if f.kind == "signature":
                f.sign_path = args.sign
    out = forms.save_filled(args.file, fields, args.style, args.ink)
    print(f"Saved {out}")
    return 0


def cmd_uninstall(remove_files: bool) -> int:
    from . import shell
    if not remove_files:
        shell.uninstall()
        return 0
    return shell.uninstall_everything()


def cmd_selftest() -> int:
    """Convert a few generated files through every engine; prints one line per check."""
    import tempfile
    from pathlib import Path
    from . import config, engine, explorer
    from .backends import Context
    from .backends.media import ffmpeg_path
    from .native import pdfwrite

    cfg = dict(config.load(), original="keep", learn_reverse=False)   # don't teach it test pairs
    d = Path(tempfile.mkdtemp(prefix="shapeshifter-selftest-"))
    ok = True

    def check(name, fn):
        nonlocal ok
        try:
            detail = fn()
            print(f"ok    {name}{': ' + detail if detail else ''}")
        except Exception as e:
            ok = False
            print(f"FAIL  {name}: {e!r}")

    def conv(src, tgt):
        res = engine.convert_inplace(src, tgt, Context(cfg=cfg))
        return ", ".join(o.name for o in res.outputs)

    from PIL import Image
    img = d / "image.png"
    Image.new("RGBA", (64, 48), (200, 50, 50, 128)).save(img)
    check("Pillow PNG -> WEBP", lambda: conv(img, "webp"))
    check("Pillow PNG -> AVIF", lambda: conv(img, "avif"))
    check("pillow-heif PNG -> HEIC", lambda: conv(img, "heic"))
    check("native PNG -> QOI/TGA/PPM", lambda: ", ".join(conv(img, t) for t in ("qoi", "tga", "ppm")))
    pdf = d / "doc.pdf"
    pdf.write_bytes(pdfwrite.document_to_pdf([("h1", "Self test"), ("p", "Hello")]))
    check("PyMuPDF PDF -> PNG", lambda: conv(pdf, "png"))
    md = d / "notes.md"
    md.write_text("# Hi\n\n- a\n- b\n")
    check("native MD -> HTML", lambda: conv(md, "html"))
    csv = d / "t.csv"
    csv.write_text("a,b\n1,2\n")
    check("data CSV -> XLSX", lambda: conv(csv, "xlsx"))
    # Document fidelity: an image + formatting + a table must survive Markdown -> PDF.
    img_png = d / "pic.png"
    Image.new("RGB", (80, 50), (30, 120, 200)).save(img_png)
    doc_md = d / "fidelity.md"
    doc_md.write_text("# Doc\n\n**bold** [link](https://example.com)\n\n![pic](pic.png)\n\n"
                      "| a | b |\n|---|---|\n| 1 | 2 |\n", encoding="utf-8")

    def fidelity():
        import pymupdf
        out = engine.convert_inplace(doc_md, "pdf", Context(cfg=cfg)).outputs[0]
        page = pymupdf.open(out)[0]
        if not page.get_images():
            raise RuntimeError("the picture was lost")
        if not page.get_links():
            raise RuntimeError("the link was lost")
        return f"{out.name}: picture and link kept"
    check("Markdown -> PDF keeps picture, link, table", fidelity)

    from . import ocr as ocr_mod
    if ocr_mod.available():
        def ocr_check():
            from PIL import ImageDraw, ImageFont
            im = Image.new("RGB", (900, 200), "white")
            ImageDraw.Draw(im).text((40, 50), "Hello ShapeShifter", fill="black",
                                    font=ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 64))
            p = d / "ocr.png"
            im.save(p)
            out = engine.convert_inplace(p, "txt", Context(cfg=cfg)).outputs[0]
            text = out.read_text(encoding="utf-8")
            if "shapeshifter" not in text.lower():
                raise RuntimeError(f"unexpected OCR result {text!r}")
            return repr(text.strip())
        check("Windows OCR image -> text", ocr_check)
    else:
        print("skip  Windows OCR not available")
    srt = d / "subs.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n")
    check("native SRT -> VTT (configured kind)", lambda: conv(srt, "vtt"))
    obj = d / "tri.obj"
    obj.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
    check("native OBJ -> STL (configured kind)", lambda: conv(obj, "stl"))
    from . import planner
    check("graph route MD -> PEM (latent)", lambda: planner.route("md", "pem").via)
    zf = d / "files.zip"
    import zipfile
    with zipfile.ZipFile(zf, "w") as z:
        z.writestr("x.txt", "x")
    check("archives ZIP -> 7z", lambda: conv(zf, "7z"))
    if ffmpeg_path():
        import subprocess
        wav = d / "tone.wav"
        subprocess.run([ffmpeg_path(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        "sine=duration=1", str(wav)], check=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        check(f"ffmpeg WAV -> MP3 ({ffmpeg_path()})", lambda: conv(wav, "mp3"))
    else:
        print("skip  ffmpeg not found")
    check("UI Automation (mouse triggers)", lambda: (explorer._automation(), "")[1])
    import shutil
    shutil.rmtree(d, ignore_errors=True)
    print("ALL OK" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    from . import __version__, config
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in CLI_COMMANDS:
        _attach_console()
        for stream in (sys.stdout, sys.stderr):          # piped output on a legacy code page
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass
    config.setup_logging()

    ap = argparse.ArgumentParser(prog="shapeshifter", description="Convert files in place.")
    ap.add_argument("--version", action="version", version=f"ShapeShifter {__version__}")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("open", help="show the conversion wheel")
    p.add_argument("files", nargs="+")
    p = sub.add_parser("convert", help="convert without UI")
    p.add_argument("--to", required=True, help="target, e.g. png, extract, rename:png")
    p.add_argument("--original", choices=["recycle", "keep", "delete"])
    p.add_argument("files", nargs="+")
    p = sub.add_parser("targets", help="list what a file can become")
    p.add_argument("file")
    p = sub.add_parser("identify", help="say what a file really is")
    p.add_argument("file")
    p = sub.add_parser("fill", help="find and fill the blanks of a form picture")
    p.add_argument("file")
    p.add_argument("--list", action="store_true", help="only list the fields found")
    p.add_argument("--set", action="append", metavar="LABEL=TEXT", help="text for a field")
    p.add_argument("--tick", action="append", metavar="LABEL", help="tick a check box")
    p.add_argument("--sign", metavar="PICTURE", help="signature picture for the signature line")
    p.add_argument("--style", default="Typed", choices=["Typed", "Handwriting", "Typewriter"])
    p.add_argument("--ink", default="Blue", choices=["Blue", "Black", "Red"])
    p.add_argument("--gui", action="store_true", help="open the fill window")
    sub.add_parser("daemon", help="tray icon + global hotkeys")
    sub.add_parser("settings", help="open the settings app")
    p = sub.add_parser("install", help="register context menu, shortcuts, autostart")
    p.add_argument("--no-startup", action="store_true")
    p.add_argument("--uninstall-cmd")
    p.add_argument("--install-dir")
    p = sub.add_parser("uninstall", help="remove Windows integration")
    p.add_argument("--remove-files", action="store_true",
                   help="also delete the installed app (packaged build)")
    sub.add_parser("register", help="refresh the right-click menu entry")
    sub.add_parser("selftest", help="check every engine")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "open":
            return cmd_open(args.files)
        if args.cmd == "convert":
            return cmd_convert(args.files, args.to, args.original)
        if args.cmd == "targets":
            return cmd_targets(args.file)
        if args.cmd == "identify":
            return cmd_identify(args.file)
        if args.cmd == "fill":
            return cmd_fill(args)
        if args.cmd == "settings":
            return cmd_settings()
        if args.cmd == "daemon":
            from .app import run_daemon
            return run_daemon()
        if args.cmd == "install":
            from . import shell
            shell.install(args.uninstall_cmd, args.install_dir, startup=not args.no_startup)
            return 0
        if args.cmd == "uninstall":
            return cmd_uninstall(args.remove_files)
        if args.cmd == "register":
            from . import shell
            shell.register_context_menu()
            print("Registered the 'Convert to…' entry for all files")
            return 0
        if args.cmd == "selftest":
            return cmd_selftest()
    except Exception:
        logging.exception("Fatal error in %s", args.cmd)
        raise
    # No command (e.g. launched from the Start Menu without args): run the tray daemon.
    from .app import run_daemon
    return run_daemon()


if __name__ == "__main__":
    sys.exit(main())
