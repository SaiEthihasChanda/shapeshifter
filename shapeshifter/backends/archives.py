"""Archive conversions (ZIP, 7z, TAR, TAR.GZ/BZ2/XZ) and "Extract here".

Native except 7z, which uses py7zr. Extraction is defensive: archives with unsafe
paths (zip-slip), links, devices, encryption or bomb-like compression are refused.
"""

from __future__ import annotations

import bz2
import gzip
import lzma
import os
import shutil
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

from .base import Backend, Context, ConversionError, RiskyFile, target_ext

PACKABLE = {"zip", "7z", "tar", "tgz", "tbz2", "txz"}
CONTAINERS = PACKABLE | {"cbz", "jar", "ar", "deb"}
SINGLE = {"gz": gzip.open, "bz2": bz2.open, "xz": lzma.open,
          "lzma": lambda p, m: lzma.open(p, m, format=lzma.FORMAT_ALONE)}
try:                                    # Zstandard (ships with py7zr as backports.zstd)
    from backports import zstd as _zstd
    SINGLE["zst"] = _zstd.open
except ImportError:
    pass
TAR_MODES = {"tar": "w", "tgz": "w:gz", "tbz2": "w:bz2", "txz": "w:xz"}

MAX_TOTAL = 8 * 1024 ** 3        # refuse archives that expand beyond 8 GB
MAX_RATIO = 250                  # …or that compress suspiciously well
MAX_ENTRIES = 200_000
EXTRACT = "extract"              # pseudo-target: unpack into a folder


def _has_7z() -> bool:
    from importlib.util import find_spec
    return find_spec("py7zr") is not None


def _safe_name(name: str) -> str:
    """Normalise an archive member name, refusing anything that escapes the folder."""
    n = name.replace("\\", "/")
    p = PurePosixPath(n)
    if n.startswith("/") or (p.parts and ":" in p.parts[0]) or ".." in p.parts:
        raise RiskyFile(f"The archive contains an unsafe path ({name!r}) that would write "
                        "outside its folder.")
    return str(p)


def _check_totals(total: int, compressed: int, count: int):
    if count > MAX_ENTRIES:
        raise RiskyFile(f"The archive has too many entries ({count:,}).")
    if total > MAX_TOTAL:
        raise RiskyFile(f"The archive would expand to {total / 1024 ** 3:.1f} GB.")
    if compressed and total > 100 * 1024 ** 2 and total / compressed > MAX_RATIO:
        raise RiskyFile("The archive compresses suspiciously well (possible zip bomb).")


def read_ar(path: Path) -> list[tuple[str, bytes]]:
    """Unix ar archives (.a, .deb): '!<arch>\\n' then 60-byte headers."""
    data = path.read_bytes()
    if not data.startswith(b"!<arch>\n"):
        raise ConversionError("Not an ar archive")
    pos, out, longnames = 8, [], b""
    while pos + 60 <= len(data):
        name = data[pos:pos + 16].decode("ascii", "replace").strip()
        size = int(data[pos + 48:pos + 58].decode().strip() or 0)
        body = data[pos + 60:pos + 60 + size]
        pos += 60 + size + (size & 1)
        if name == "//":
            longnames = body
            continue
        if name in ("/", "/SYM64/"):
            continue
        if name.startswith("/") and name[1:].isdigit():
            start = int(name[1:])
            name = longnames[start:longnames.index(b"\n", start)].decode().rstrip("/")
        elif name.startswith("#1/"):                       # BSD long name
            n = int(name[3:])
            name, body = body[:n].decode().rstrip("\0"), body[n:]
        out.append((name.rstrip("/"), body))
    return out


def inspect_archive(path: Path, fmt: str) -> None:
    """Raise RiskyFile if the archive looks unsafe. Cheap: reads only the index."""
    size = path.stat().st_size
    if fmt in ("zip", "cbz", "jar"):
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            for i in infos:
                if i.flag_bits & 0x1:
                    raise RiskyFile("The archive is password-protected.")
                _safe_name(i.filename)
            _check_totals(sum(i.file_size for i in infos), size, len(infos))
    elif fmt in TAR_MODES:
        with tarfile.open(path) as tf:
            members = tf.getmembers()
            for m in members:
                _safe_name(m.name)
                if m.issym() or m.islnk() or m.isdev():
                    raise RiskyFile("The archive contains links or device files.")
            _check_totals(sum(m.size for m in members), size, len(members))
    elif fmt == "7z":
        import py7zr
        try:
            with py7zr.SevenZipFile(path) as z:
                if z.needs_password():
                    raise RiskyFile("The archive is password-protected.")
                infos = z.list()
        except py7zr.exceptions.PasswordRequired as e:
            raise RiskyFile("The archive is password-protected.") from e
        for i in infos:
            _safe_name(i.filename)
        _check_totals(sum(i.uncompressed or 0 for i in infos), size, len(infos))


class ArchiveBackend(Backend):
    name = "archives"
    base_cost = 2.0

    def sources(self):
        s = CONTAINERS | set(SINGLE)
        return s if _has_7z() else s - {"7z"}

    def targets(self, src):
        if src in SINGLE:
            return {EXTRACT}
        packable = PACKABLE if _has_7z() else PACKABLE - {"7z"}
        return (packable | {EXTRACT}) - {src}

    def multi_output(self, src, dst):
        return dst == EXTRACT

    def convert(self, src: Path, dst: Path, ctx: Context) -> list[Path]:
        s = ctx.src_fmt or target_ext(src)
        t = ctx.dst_fmt or target_ext(dst)
        if s in SINGLE:
            return [self._decompress_single(src, dst, s, ctx)]
        inspect_archive(src, s)
        unpack = dst if t == EXTRACT else dst.parent / "unpacked"
        unpack.mkdir(parents=True, exist_ok=True)
        self._extract(src, s, unpack, ctx)
        if t == EXTRACT:
            return [unpack]
        self._pack(unpack, dst, t, ctx)
        shutil.rmtree(unpack, ignore_errors=True)
        ctx.progress(1.0)
        return [dst]

    # ----------------------------------------------------------------- extract

    def _extract(self, src: Path, fmt: str, out: Path, ctx: Context):
        if fmt in ("zip", "cbz", "jar"):
            with zipfile.ZipFile(src) as zf:
                infos = zf.infolist()
                for n, info in enumerate(infos):
                    ctx.check_cancel()
                    target = out / _safe_name(info.filename)
                    if info.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as fi, open(target, "wb") as fo:
                        shutil.copyfileobj(fi, fo, 1024 * 1024)
                    ctx.progress(0.5 * (n + 1) / max(1, len(infos)))
        elif fmt in TAR_MODES:
            with tarfile.open(src) as tf:
                tf.extractall(out, filter="data")
        elif fmt == "7z":
            import py7zr
            with py7zr.SevenZipFile(src) as z:
                z.extractall(path=out)
        elif fmt in ("ar", "deb"):
            for name, blob in read_ar(src):
                target = out / _safe_name(name)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(blob)
        else:
            raise ConversionError(f"Can't extract {fmt.upper()}")
        ctx.progress(0.5)

    def _decompress_single(self, src: Path, dst: Path, fmt: str, ctx: Context) -> Path:
        """notes.txt.gz -> notes.txt (written next to ``dst``)."""
        inner = dst.parent / (src.stem if src.suffix else src.name + ".out")
        total = 0
        with SINGLE[fmt](src, "rb") as fi, open(inner, "wb") as fo:
            while chunk := fi.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_TOTAL:
                    raise RiskyFile("The file decompresses to more than 8 GB.")
                ctx.check_cancel()
                fo.write(chunk)
        ctx.progress(1.0)
        return inner

    # -------------------------------------------------------------------- pack

    def _pack(self, folder: Path, dst: Path, fmt: str, ctx: Context):
        files = [p for p in folder.rglob("*")]
        if fmt == "zip":
            with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                for n, p in enumerate(files):
                    ctx.check_cancel()
                    zf.write(p, p.relative_to(folder).as_posix())
                    ctx.progress(0.5 + 0.5 * (n + 1) / max(1, len(files)))
        elif fmt in TAR_MODES:
            with tarfile.open(dst, TAR_MODES[fmt]) as tf:
                for n, p in enumerate(sorted(folder.iterdir())):
                    ctx.check_cancel()
                    tf.add(p, arcname=p.name)
        elif fmt == "7z":
            import py7zr
            with py7zr.SevenZipFile(dst, "w") as z:
                for p in sorted(folder.iterdir()):
                    z.writeall(p, arcname=p.name) if p.is_dir() else z.write(p, arcname=p.name)
        else:
            raise ConversionError(f"Can't create {fmt.upper()}")
        if not dst.exists():
            raise ConversionError("The archive could not be written.")
        os.utime(dst)
