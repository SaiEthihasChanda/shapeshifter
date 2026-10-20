"""Third-party licence notices for what is actually inside the frozen ShapeShifter app.

    python packaging/collect_licenses.py --exe .build/dist/ShapeShifter/ShapeShifter.exe \
        --out .build/licenses --md THIRD-PARTY-NOTICES.md

Reads the module list out of the frozen executable (so only bundled packages are listed),
copies each package's licence files into <out>/licenses/<package>/, and writes a plain-text
notice for the installer plus a Markdown copy for the repository.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, distribution, packages_distributions
from pathlib import Path

LICENSE_FILE = re.compile(r"(^|/)(licen[cs]e|copying|notice|authors|copyright)[^/]*$", re.I)
SKIP = {"shapeshifter"}                        # that's us
# Packages PyInstaller's hooks drag in that are not part of ShapeShifter's own dependencies.
BUILD_ONLY = {"setuptools", "packaging", "pip", "wheel", "pyinstaller", "altgraph", "pefile"}

KNOWN_LICENSES = {                             # when package metadata is vague
    "pillow": "MIT-CMU (HPND)", "pywin32": "PSF-2.0", "psutil": "BSD-3-Clause",
    "pycryptodomex": "BSD-2-Clause / Public Domain", "pymupdf": "AGPL-3.0 (or Artifex commercial)",
    "PySide6_Essentials": "LGPL-3.0-only (Qt for Python)", "shiboken6": "LGPL-3.0-only",
}

STATIC = [
    ("Python", "3.12", "PSF-2.0", "https://docs.python.org/3/license.html",
     "The Python runtime is bundled in the application folder."),
    ("Qt 6 (via PySide6)", "6.x", "LGPL-3.0-only",
     "https://www.qt.io/licensing/open-source-lgpl-obligations",
     "Qt is used as dynamic libraries (separate .dll files in the application folder), so you may "
     "replace them with your own build. Source: https://download.qt.io/official_releases/qt/"),
    ("MuPDF (inside PyMuPDF)", "", "AGPL-3.0 (or Artifex commercial)",
     "https://github.com/ArtifexSoftware/mupdf",
     "PDF engine. ShapeShifter itself is also AGPL-3.0-or-later; see SOURCE.txt."),
    ("Windows OCR (Windows.Media.Ocr)", "", "Part of Windows",
     "https://learn.microsoft.com/uwp/api/windows.media.ocr",
     "Called through Windows PowerShell on your PC. Not distributed with ShapeShifter."),
    ("pandoc, LibreOffice", "", "GPL-2.0-or-later / MPL-2.0",
     "https://pandoc.org and https://www.libreoffice.org",
     "Optional: ShapeShifter uses them if you install them yourself. Not distributed with it."),
]


def bundled_distributions(exe: Path) -> list[str]:
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(str(exe))
    modules: set[str] = set()
    for name, entry in archive.toc.items():
        if entry[4] == "z":
            modules |= set(archive.open_embedded_archive(name).toc)
    tops = {m.split(".")[0] for m in modules}
    internal = exe.parent / "_internal"
    if internal.is_dir():
        tops |= {p.name.split(".")[0] for p in internal.iterdir()}
    mapping = packages_distributions()
    found = {d for t in tops for d in mapping.get(t, [])}
    return sorted(found - SKIP - BUILD_ONLY, key=str.lower)


def describe(name: str) -> dict:
    d = distribution(name)
    meta = d.metadata
    lic = meta.get("License-Expression") or ""
    if not lic:
        raw = (meta.get("License") or "").strip()
        lic = raw.splitlines()[0][:80] if raw and len(raw) < 200 else ""
    if not lic:
        lic = "; ".join(c.split("::")[-1].strip() for c in (meta.get_all("Classifier") or [])
                        if c.startswith("License"))
    lic = KNOWN_LICENSES.get(name, lic) or "see licence files"
    home = meta.get("Home-page") or ""
    if not home:
        for u in meta.get_all("Project-URL") or []:
            home = u.split(",", 1)[-1].strip()
            if "source" in u.lower() or "home" in u.lower():
                break
    files = [f for f in (d.files or []) if LICENSE_FILE.search(str(f).replace("\\", "/"))]
    return {"name": meta["Name"], "version": meta["Version"], "license": lic, "home": home,
            "files": [(f, Path(d.locate_file(f))) for f in files]}


def ffmpeg_info(ffmpeg_dir: Path | None) -> tuple[str, str] | None:
    exe = ffmpeg_dir / "ffmpeg.exe" if ffmpeg_dir else None
    if not exe or not exe.is_file():
        return None
    try:
        out = subprocess.run([str(exe), "-version"], capture_output=True, text=True, timeout=20,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:
        return ("FFmpeg", "")
    first = out.splitlines()[0] if out else "ffmpeg"
    gpl = "--enable-gpl" in out
    return first, "GPL-3.0-or-later (this build enables GPL components)" if gpl else "LGPL-2.1-or-later"


def collect(exe: Path, out: Path, ffmpeg_dir: Path | None) -> list[dict]:
    records = []
    lic_root = out / "licenses"
    if lic_root.exists():
        shutil.rmtree(lic_root)
    for name in bundled_distributions(exe):
        try:
            rec = describe(name)
        except PackageNotFoundError:
            continue
        target = lic_root / re.sub(r"[^\w.-]", "_", rec["name"])
        copied = []
        for rel, src in rec["files"]:
            if src.is_file():
                target.mkdir(parents=True, exist_ok=True)
                dst = target / Path(str(rel)).name
                shutil.copyfile(src, dst)
                copied.append(f"licenses/{target.name}/{dst.name}")
        rec["copied"] = copied
        records.append(rec)
    return records


def write_text(records, static, ffmpeg, path: Path, version: str) -> None:
    lines = [f"ShapeShifter {version} - third-party software notices", "=" * 60, "",
             "ShapeShifter is free software released under the GNU AGPL v3 or later (see LICENSE).",
             "It includes and/or uses the components below, under their own licences.",
             "Full licence texts are in the 'licenses' folder next to this file.", ""]
    for title, ver, lic, url, note in static:
        lines += [f"{title} {ver}".strip(), f"  Licence: {lic}", f"  {url}", f"  {note}", ""]
    if ffmpeg:
        lines += [ffmpeg[0], f"  Licence: {ffmpeg[1]}", "  https://ffmpeg.org  (build: https://www.gyan.dev/ffmpeg/builds/)",
                  "  Runs as a separate program (ffmpeg\\ffmpeg.exe). Its licence text is ffmpeg\\LICENSE.txt;",
                  "  its source code is available from the links above and in ffmpeg\\SOURCE.txt.", ""]
    lines += ["Python packages", "-" * 40]
    for r in records:
        lines += [f"{r['name']} {r['version']}", f"  Licence: {r['license']}"]
        if r["home"]:
            lines.append(f"  {r['home']}")
        if r["copied"]:
            lines.append("  Licence text: " + ", ".join(r["copied"]))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_markdown(records, static, ffmpeg, path: Path, version: str) -> None:
    md = ["# Third-party notices", "",
          f"ShapeShifter {version} is released under the **GNU AGPL v3 or later** (see [LICENSE](LICENSE)). "
          "The Windows installer bundles the components below, each under its own licence. "
          "This file is generated from the contents of the built application by "
          "`packaging/collect_licenses.py`; the installer also ships the full licence texts.", "",
          "## Components", "", "| Component | Licence | Notes |", "|---|---|---|"]
    for title, ver, lic, url, note in static:
        md.append(f"| [{f'{title} {ver}'.strip()}]({re.split(r'  |·', url)[0].strip()}) | {lic} | {note} |")
    if ffmpeg:
        md.append(f"| [{ffmpeg[0].split(' Copyright')[0]}](https://ffmpeg.org) | {ffmpeg[1]} | "
                  "Runs as a separate program. Source: <https://ffmpeg.org/download.html#get-sources>; "
                  "build: <https://www.gyan.dev/ffmpeg/builds/> |")
    md += ["", "## Python packages", "", "| Package | Version | Licence |", "|---|---|---|"]
    for r in records:
        name = f"[{r['name']}]({r['home']})" if r["home"].startswith("http") else r["name"]
        md.append(f"| {name} | {r['version']} | {r['license']} |")
    md += ["", "## Source code", "",
           "The complete corresponding source of ShapeShifter is at "
           "<https://github.com/SaiEthihasChanda/shapeshifter>. For components that are only "
           "re-distributed (Qt, FFmpeg, Python), use the links above; a copy of ffmpeg's licence is "
           "included with the installer.", ""]
    path.write_text("\n".join(md), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--md", type=Path)
    ap.add_argument("--ffmpeg-dir", type=Path)
    ap.add_argument("--version", default="")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    ff = ffmpeg_info(a.ffmpeg_dir)
    records = collect(a.exe, a.out, a.ffmpeg_dir)
    write_text(records, STATIC, ff, a.out / "THIRD-PARTY-NOTICES.txt", a.version)
    if a.md:
        write_markdown(records, STATIC, ff, a.md, a.version)
    print(f"{len(records)} bundled Python packages documented"
          + (", plus ffmpeg" if ff else "") + f"; {sum(len(r['copied']) for r in records)} licence files copied")
    missing = [r["name"] for r in records if not r["copied"]]
    if missing:
        print("note: no licence file shipped by:", ", ".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
