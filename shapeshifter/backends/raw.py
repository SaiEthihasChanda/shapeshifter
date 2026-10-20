"""Camera RAW files (CR2, CR3, NEF, ARW, DNG, ORF, RW2, RAF…) -> JPEG, by taking the full-size preview
JPEG that cameras store inside every RAW file. No raw development (demosaicing) is done, so this is what a
quick viewer shows, not the sensor data. Other formats are reached through the JPEG (RAW -> JPG -> PNG…)."""

from __future__ import annotations

import io
import logging
import struct
from pathlib import Path

from ..core import Backend, Context, ConversionError, target_ext

log = logging.getLogger(__name__)

RAW_FORMATS = {"dng", "cr2", "nef", "arw", "orf", "rw2", "raf"}
MAX_FILE = 400 * 1024 * 1024
SOI = b"\xff\xd8\xff"


def jpeg_end(data: bytes, start: int) -> int | None:
    """Offset just past the end of the JPEG that begins at ``start`` (walks the marker segments)."""
    i, n = start + 2, len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker == 0xD9:
            return i + 2
        if marker == 0xDA:                                   # image data: runs to the next real marker
            i += 2 + struct.unpack_from(">H", data, i + 2)[0]
            while True:
                j = data.find(b"\xff", i)
                if j < 0 or j + 1 >= n:
                    return None
                nxt = data[j + 1]
                if nxt == 0 or 0xD0 <= nxt <= 0xD7:
                    i = j + 2
                    continue
                i = j
                break
            continue
        if marker in (0x01,) or 0xD0 <= marker <= 0xD8:
            i += 2
            continue
        i += 2 + struct.unpack_from(">H", data, i + 2)[0]
    return None


def preview_candidates(data: bytes) -> list[tuple[int, int, int]]:
    """[(pixels, start, end)] for every decodable JPEG inside the file, biggest first."""
    from PIL import Image
    found = []
    pos, tries = 0, 0
    while tries < 60:
        pos = data.find(SOI, pos)
        if pos < 0:
            break
        tries += 1
        end = jpeg_end(data, pos)
        if end and end - pos > 2000:
            try:
                with Image.open(io.BytesIO(data[pos:end])) as im:
                    found.append((im.width * im.height, pos, end))
            except Exception:                                # noqa: BLE001 - not a picture after all
                pass
        pos += 3
    return sorted(found, reverse=True)


def tiff_orientation(data: bytes) -> int:
    """EXIF orientation from the first IFD of a TIFF-based RAW file (1 if there is none)."""
    try:
        if data[:2] == b"II":
            e = "<"
        elif data[:2] == b"MM":
            e = ">"
        else:
            return 1
        ifd = struct.unpack_from(e + "I", data, 4)[0]
        n = struct.unpack_from(e + "H", data, ifd)[0]
        for k in range(min(n, 200)):
            tag, typ, count = struct.unpack_from(e + "HHI", data, ifd + 2 + k * 12)
            if tag == 0x0112:
                return struct.unpack_from(e + "H", data, ifd + 2 + k * 12 + 8)[0]
    except (struct.error, IndexError):
        pass
    return 1


class RawBackend(Backend):
    name = "raw"
    base_cost = 2.0

    def available(self):
        from importlib.util import find_spec
        return find_spec("PIL") is not None

    def sources(self):
        return set(RAW_FORMATS)

    def targets(self, src):
        return {"jpg"}

    def convert(self, src: Path, dst: Path, ctx: Context) -> list[Path]:
        from PIL import Image, ImageOps
        if src.stat().st_size > MAX_FILE:
            raise ConversionError("The RAW file is too large to read safely.")
        data = src.read_bytes()
        ctx.progress(0.3)
        cands = preview_candidates(data)
        if not cands:
            raise ConversionError("No preview picture was found inside this RAW file.")
        pixels, start, end = cands[0]
        blob = data[start:end]
        with Image.open(io.BytesIO(blob)) as im:
            own = im.getexif().get(0x0112, 1)
            orient = own if own in (2, 3, 4, 5, 6, 7, 8) else tiff_orientation(data)
            if own not in (2, 3, 4, 5, 6, 7, 8) and orient in (2, 3, 4, 5, 6, 7, 8):
                # The orientation is only in the RAW container: turn the preview the right way up.
                im.getexif()[0x0112] = orient
                im.load()
                fixed = ImageOps.exif_transpose(im)
                kw = {"quality": 95, "subsampling": 0}
                if im.info.get("icc_profile"):
                    kw["icc_profile"] = im.info["icc_profile"]
                fixed.convert("RGB").save(dst, "JPEG", **kw)
                ctx.progress(1.0)
                return [dst]
        dst.write_bytes(blob)                                # the camera's own JPEG, byte for byte
        ctx.progress(1.0)
        return [dst]


__all__ = ["RawBackend", "RAW_FORMATS"]
