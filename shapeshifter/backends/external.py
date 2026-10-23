"""Optional document converters that use external programs: LibreOffice and pandoc."""

from __future__ import annotations

import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from ..config import DATA_DIR
from .base import NO_WINDOW, Backend, Context, ConversionError, find_exe, target_ext

# ---------------------------------------------------------------- LibreOffice

WRITER_IN = {"doc", "docx", "odt", "rtf", "txt", "html"}
WRITER_OUT = {"pdf", "docx", "odt", "rtf", "txt", "html", "doc"}
CALC_IN = {"xls", "xlsx", "ods", "csv"}
CALC_OUT = {"xlsx", "ods", "csv", "pdf", "xls"}
IMPRESS_IN = {"ppt", "pptx", "odp"}
IMPRESS_OUT = {"pptx", "odp", "pdf", "ppt"}

LO_FILTER = {
    "txt": "txt:Text (encoded):UTF8",
    "csv": "csv:Text - txt - csv (StarCalc):44,34,76,1",
    "html": "html:HTML (StarWriter)",
}


@lru_cache(maxsize=1)
def soffice_path() -> str | None:
    dirs = []
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if base:
            dirs.append(Path(base) / "LibreOffice" / "program")
    dirs += [Path("/Applications/LibreOffice.app/Contents/MacOS"),
             Path.home() / "Applications" / "LibreOffice.app" / "Contents" / "MacOS"]
    return find_exe("soffice", dirs)


class LibreOfficeBackend(Backend):
    name = "libreoffice"
    base_cost = 6.0  # starts a whole office suite

    def available(self):
        return soffice_path() is not None

    def sources(self):
        return WRITER_IN | CALC_IN | IMPRESS_IN

    def targets(self, src):
        if src in CALC_IN:
            return CALC_OUT - {src}
        if src in IMPRESS_IN:
            return IMPRESS_OUT - {src}
        if src in WRITER_IN:
            return WRITER_OUT - {src}
        return set()

    def convert(self, src: Path, dst: Path, ctx: Context) -> list[Path]:
        tgt = target_ext(dst)
        outdir = dst.parent / "lo-out"
        outdir.mkdir(exist_ok=True)
        profile = (DATA_DIR / "lo_profile").as_uri()
        cmd = [soffice_path(), "--headless", "--norestore", "--nologo",
               f"-env:UserInstallation={profile}",
               "--convert-to", LO_FILTER.get(tgt, tgt), "--outdir", str(outdir), str(src)]
        ctx.progress(0.1)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, creationflags=NO_WINDOW,
                                  timeout=600)
        except subprocess.TimeoutExpired as e:
            raise ConversionError("LibreOffice timed out.") from e
        produced = [p for p in outdir.iterdir() if p.is_file()]
        if not produced:
            raise ConversionError("LibreOffice failed: " + (proc.stderr or proc.stdout or "no output")
                                  .strip()[:300])
        shutil.move(str(produced[0]), dst)
        ctx.progress(1.0)
        return [dst]


# --------------------------------------------------------------------- pandoc

PANDOC_IN = {"md", "html", "docx", "odt", "rst", "epub", "tex"}
PANDOC_OUT = {"md", "html", "docx", "odt", "rst", "epub", "tex", "txt"}
PANDOC_FMT = {"md": "gfm", "txt": "plain", "tex": "latex"}


@lru_cache(maxsize=1)
def pandoc_path() -> str | None:
    local = os.environ.get("LOCALAPPDATA", "")
    return find_exe("pandoc", [Path(local) / "Pandoc", r"C:\Program Files\Pandoc"])


class PandocBackend(Backend):
    name = "pandoc"
    base_cost = 3.0

    def available(self):
        return pandoc_path() is not None

    def sources(self):
        return PANDOC_IN

    def targets(self, src):
        return PANDOC_OUT - {src}

    def cost(self, src, dst):
        # Markup-only targets can't carry pictures or layout as faithfully as HTML, so a
        # route through HTML wins when costs would otherwise tie.
        return self.base_cost + (0.3 if dst in ("md", "rst", "tex", "txt") else 0.0)

    def convert(self, src: Path, dst: Path, ctx: Context) -> list[Path]:
        s = ctx.src_fmt or target_ext(src)
        t = ctx.dst_fmt or target_ext(dst)
        cmd = [pandoc_path(), "-s", "-f", PANDOC_FMT.get(s, s), "-t", PANDOC_FMT.get(t, t),
               "-o", str(dst), str(src)]
        media_dir = dst.parent / f"{dst.stem}_files"
        if s in ("docx", "odt", "epub"):
            # Pictures inside the source: without one of these flags pandoc writes <img>
            # tags / links to files it never extracts, and every image is silently lost.
            if t == "html":
                cmd.insert(1, "--embed-resources" if _pandoc_has_embed() else "--self-contained")
            elif t in ("md", "rst", "tex"):
                cmd.insert(1, f"--extract-media={media_dir.name}")
        if s in ("md", "html", "rst", "tex") and ctx.resource_dirs:
            # Pictures the document refers to by relative path.
            cmd.insert(1, "--resource-path=" + os.pathsep.join(str(d) for d in ctx.resource_dirs))
            if t in ("docx", "odt", "epub"):
                pass                                   # pandoc embeds them in the output
            elif t == "html":
                cmd.insert(1, "--embed-resources" if _pandoc_has_embed() else "--self-contained")
        ctx.progress(0.2)
        proc = subprocess.run(cmd, capture_output=True, text=True, creationflags=NO_WINDOW,
                              timeout=300, cwd=str(dst.parent))
        if proc.returncode != 0 or not dst.exists():
            raise ConversionError("pandoc failed: " + proc.stderr.strip()[:300])
        ctx.progress(1.0)
        # The extracted pictures travel with the text that points at them.
        return [dst] + ([media_dir] if media_dir.is_dir() else [])


@lru_cache(maxsize=1)
def _pandoc_has_embed() -> bool:
    """--embed-resources replaced --self-contained in pandoc 2.19."""
    try:
        out = subprocess.run([pandoc_path(), "--version"], capture_output=True, text=True,
                             creationflags=NO_WINDOW, timeout=20).stdout.splitlines()[0]
        major, minor = (int(x) for x in out.split()[1].split(".")[:2])
        return (major, minor) >= (2, 19)
    except Exception:
        return True
