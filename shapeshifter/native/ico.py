"""Windows ICO reader (PNG and BMP/DIB entries) and writer (PNG-compressed entries)."""

from __future__ import annotations

import struct

from ..core import ConversionError
from . import png
from .raster import Frame, Raster, Unsupported, pad_square, resize_box

SIZES = (16, 24, 32, 48, 64, 128, 256)


def decode(data: bytes) -> Raster:
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    if reserved != 0 or kind not in (1, 2) or not count:
        raise ConversionError("Not an ICO file")
    entries = []
    for i in range(count):
        w, h, _cc, _r, _planes, bits, size, offset = struct.unpack(
            "<BBBBHHII", data[6 + i * 16:22 + i * 16])
        entries.append((w or 256, h or 256, bits, size, offset))
    w, h, bits, size, offset = max(entries, key=lambda e: (e[0] * e[1], e[2]))
    blob = data[offset:offset + size]
    if blob.startswith(png.SIGNATURE):
        return png.decode(blob)
    return _decode_dib(blob, w, h)


def _decode_dib(blob: bytes, w: int, h: int) -> Raster:
    hsize, bw, bh2, _planes, bits = struct.unpack("<IiiHH", blob[:16])
    if bits != 32:
        raise Unsupported(f"{bits}-bit icon image")
    stride = bw * 4
    body = blob[hsize:hsize + stride * w]
    rows = []
    for y in range(h):
        row = bytearray(body[y * stride:(y + 1) * stride])
        row[0::4], row[2::4] = row[2::4], row[0::4]
        rows.append(bytes(row))
    rows.reverse()
    return Raster([Frame(w, h, "RGBA", b"".join(rows))])


def encode(raster: Raster) -> bytes:
    src = pad_square(raster.first)
    if src.width > 1024:
        raise Unsupported("large source for an icon")
    sizes = [s for s in SIZES if s <= src.width] or [src.width]
    images = []
    for s in sizes:
        frame = src if s == src.width else resize_box(src, s, s)
        images.append((s, png.encode(Raster([frame]))))
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    directory = b""
    for s, blob in images:
        directory += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    return header + directory + b"".join(b for _, b in images)
