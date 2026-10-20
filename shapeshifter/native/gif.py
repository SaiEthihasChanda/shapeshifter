"""GIF decoder (LZW, transparency, interlacing, animation compositing)."""

from __future__ import annotations

import struct

from ..core import ConversionError
from .raster import Frame, Raster, Unsupported, too_big_for_native


def _lzw(data: bytes, min_size: int, count: int) -> bytearray:
    clear, end = 1 << min_size, (1 << min_size) + 1
    size = min_size + 1
    table: list[bytes] = [bytes([i]) for i in range(clear)] + [b"", b""]
    out = bytearray()
    prev: bytes | None = None
    bitbuf = bits = 0
    pos = 0
    n = len(data)
    while len(out) < count:
        while bits < size and pos < n:
            bitbuf |= data[pos] << bits
            bits += 8
            pos += 1
        if bits < size:
            break
        code = bitbuf & ((1 << size) - 1)
        bitbuf >>= size
        bits -= size
        if code == clear:
            size = min_size + 1
            table = table[:clear + 2]
            prev = None
            continue
        if code == end:
            break
        if code < len(table):
            entry = table[code]
            if prev is not None:
                table.append(prev + entry[:1])
        elif prev is not None:
            entry = prev + prev[:1]
            table.append(entry)
        else:
            raise ConversionError("Corrupt GIF data")
        out += entry
        prev = entry
        if len(table) == (1 << size) and size < 12:
            size += 1
    return out[:count]


def _subblocks(data: bytes, pos: int) -> tuple[bytes, int]:
    parts = []
    while True:
        n = data[pos]
        pos += 1
        if n == 0:
            return b"".join(parts), pos
        parts.append(data[pos:pos + n])
        pos += n


def _deinterlace(idx: bytearray, w: int, h: int) -> bytearray:
    rows = [idx[y * w:(y + 1) * w] for y in range(h)]
    order = list(range(0, h, 8)) + list(range(4, h, 8)) + list(range(2, h, 4)) + list(range(1, h, 2))
    out = [b""] * h
    for src, y in enumerate(order):
        out[y] = rows[src]
    return bytearray(b"".join(out))


def decode(data: bytes, max_frames: int = 1000) -> Raster:
    if data[:6] not in (b"GIF87a", b"GIF89a"):
        raise ConversionError("Not a GIF file")
    width, height, flags, bg_index, _aspect = struct.unpack("<HHBBB", data[6:13])
    if too_big_for_native(width, height):
        raise Unsupported("large GIF")
    pos = 13
    global_pal = None
    if flags & 0x80:
        size = 3 * (2 << (flags & 7))
        global_pal = data[pos:pos + size]
        pos += size
    canvas = bytearray(width * height * 4)       # transparent
    frames: list[Frame] = []
    gce_delay, gce_trans, gce_disposal = 0, None, 0
    while pos < len(data) and len(frames) < max_frames:
        block = data[pos]
        pos += 1
        if block == 0x3B:     # trailer
            break
        if block == 0x21:     # extension
            label = data[pos]
            pos += 1
            body, pos = _subblocks(data, pos)
            if label == 0xF9 and len(body) >= 4:
                packed, gce_delay, ti = struct.unpack("<BHB", body[:4])
                gce_trans = ti if packed & 1 else None
                gce_disposal = (packed >> 2) & 7
            continue
        if block != 0x2C:
            break
        x, y, w, h, iflags = struct.unpack("<HHHHB", data[pos:pos + 9])
        pos += 9
        pal = global_pal
        if iflags & 0x80:
            size = 3 * (2 << (iflags & 7))
            pal = data[pos:pos + size]
            pos += size
        if pal is None:
            raise ConversionError("Corrupt GIF (no palette)")
        min_size = data[pos]
        pos += 1
        lzw, pos = _subblocks(data, pos)
        idx = _lzw(lzw, min_size, w * h)
        idx += bytes(w * h - len(idx))
        if iflags & 0x40:
            idx = _deinterlace(idx, w, h)
        before = bytes(canvas) if gce_disposal == 3 else None
        rgba = [pal[i * 3:i * 3 + 3] + b"\xff" for i in range(len(pal) // 3)]
        for row in range(h):
            cy = y + row
            if cy >= height:
                break
            for col in range(w):
                cx = x + col
                if cx >= width:
                    break
                i = idx[row * w + col]
                if i == gce_trans or i >= len(rgba):
                    continue
                o = (cy * width + cx) * 4
                canvas[o:o + 4] = rgba[i]
        frames.append(Frame(width, height, "RGBA", bytes(canvas), duration=gce_delay * 10 or 100))
        if gce_disposal == 2:      # restore to background (transparent)
            for row in range(min(h, height - y)):
                o = ((y + row) * width + x) * 4
                canvas[o:o + min(w, width - x) * 4] = bytes(min(w, width - x) * 4)
        elif gce_disposal == 3 and before is not None:
            canvas = bytearray(before)
        gce_delay, gce_trans, gce_disposal = 0, None, 0
    if not frames:
        raise ConversionError("The GIF has no images")
    # Opaque GIFs come out as RGB.
    if not any(f.data[3::4].count(255) != len(f.data) // 4 for f in frames[:1]):
        frames = [f.to("RGB") for f in frames]
    return Raster(frames, kind="animation" if len(frames) > 1 else "single")
