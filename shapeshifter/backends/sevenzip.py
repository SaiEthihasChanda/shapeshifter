"""More archive formats through 7-Zip, when it is installed: RAR / CBR, ISO, CAB, WIM, DMG, VHD(X), VMDK, QCOW2,
SquashFS, RPM, CPIO, XAR, CHM. They can be unpacked ("Extract here") or re-packed as ZIP / 7z / TAR.

The same safety rules as the built-in archives apply: encrypted archives, unsafe paths (zip-slip), and
bomb-like sizes are refused before anything is written."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from .archives import EXTRACT, MAX_ENTRIES, ArchiveBackend, _check_totals, _safe_name
from .base import NO_WINDOW, Backend, Cancelled, Context, ConversionError, RiskyFile, find_exe, target_ext

log = logging.getLogger(__name__)

SEVENZIP_FORMATS = {"rar", "cbr", "cab", "iso", "wim", "dmg", "vhd", "vhdx", "vmdk", "qcow2", "squashfs",
                    "rpm", "cpio", "xar", "chm"}
REPACK = {"zip", "7z", "tar", "tgz", "tbz2", "txz"}


@lru_cache(maxsize=1)
def sevenzip_path() -> str | None:
    dirs = []
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if base:
            dirs.append(Path(base) / "7-Zip")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / "Programs" / "7-Zip")
    return find_exe("7z", dirs)


def parse_listing(text: str) -> list[dict]:
    """Entries of ``7z l -slt`` output: [{'Path': ..., 'Size': ..., 'Encrypted': ...}]."""
    entries, current, started = [], {}, False
    for line in text.splitlines():
        if line.startswith("----------"):
            started = True
            continue
        if not started:
            continue
        if not line.strip():
            if current.get("Path"):
                entries.append(current)
            current = {}
            continue
        key, sep, value = line.partition(" = ")
        if sep:
            current[key.strip()] = value.strip()
    if current.get("Path"):
        entries.append(current)
    return entries


def check_entries(entries: list[dict], compressed: int) -> None:
    """Refuse archives that are encrypted, escape their folder, or look like bombs."""
    if len(entries) > MAX_ENTRIES:
        raise RiskyFile(f"The archive has too many entries ({len(entries):,}).")
    total = 0
    for e in entries:
        if e.get("Encrypted", "-") == "+":
            raise RiskyFile("The archive is password-protected.")
        _safe_name(e["Path"])
        if e.get("Symbolic Link") or e.get("Link"):
            raise RiskyFile("The archive contains links.")
        try:
            total += int(e.get("Size") or 0)
        except ValueError:
            pass
    _check_totals(total, compressed, len(entries))


class SevenZipBackend(Backend):
    name = "sevenzip"
    base_cost = 3.0

    def available(self):
        return sevenzip_path() is not None

    def sources(self):
        return set(SEVENZIP_FORMATS)

    def targets(self, src):
        out = {EXTRACT} | REPACK
        if src == "cbr":
            out.add("cbz")
        return out - {src}

    def multi_output(self, src, dst):
        return dst == EXTRACT

    def cost(self, src, dst):
        return 3.0 if dst == EXTRACT else 4.5

    # ------------------------------------------------------------------ work

    def _run(self, args: list[str], ctx: Context, timeout: float = 900) -> str:
        proc = subprocess.Popen([sevenzip_path(), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, text=True,
                                encoding="utf-8", errors="replace")
        waited = 0.0
        while True:
            try:
                out, err = proc.communicate(timeout=0.3)
                break
            except subprocess.TimeoutExpired:
                waited += 0.3
                if ctx.cancel_event.is_set():
                    proc.kill()
                    proc.communicate()
                    raise Cancelled() from None
                if waited > timeout:
                    proc.kill()
                    proc.communicate()
                    raise ConversionError("7-Zip took too long.") from None
        if proc.returncode not in (0, 1):                      # 1 = warnings only
            log.info("7z failed (%s): %s", proc.returncode, (err or out)[-500:])
            low = (err + out).lower()
            if "wrong password" in low or "encrypted" in low:
                raise RiskyFile("The archive is password-protected.")
            raise ConversionError("7-Zip couldn't read this archive.")
        return out

    def inspect(self, src: Path, ctx: Context) -> None:
        entries = parse_listing(self._run(["l", "-slt", "-sccUTF-8", str(src)], ctx))
        if not entries:
            raise ConversionError("The archive is empty or couldn't be read.")
        check_entries(entries, src.stat().st_size)

    def convert(self, src: Path, dst: Path, ctx: Context) -> list[Path]:
        t = ctx.dst_fmt or target_ext(dst)
        self.inspect(src, ctx)
        ctx.progress(0.1)
        unpack = dst if t == EXTRACT else dst.parent / "unpacked"
        unpack.mkdir(parents=True, exist_ok=True)
        self._run(["x", "-y", "-bd", "-sccUTF-8", f"-o{unpack}", str(src)], ctx)
        self._verify(unpack)
        ctx.progress(0.7)
        if t == EXTRACT:
            return [unpack]
        pack_fmt = "zip" if t == "cbz" else t
        ArchiveBackend()._pack(unpack, dst, pack_fmt, ctx)
        shutil.rmtree(unpack, ignore_errors=True)
        ctx.progress(1.0)
        return [dst]

    @staticmethod
    def _verify(folder: Path) -> None:
        """Whatever was unpacked must lie inside the folder and contain no links."""
        root = folder.resolve()
        for p in folder.rglob("*"):
            if p.is_symlink():
                raise RiskyFile("The archive contained links.")
            try:
                p.resolve().relative_to(root)
            except ValueError as e:
                raise RiskyFile("The archive tried to write outside its folder.") from e
