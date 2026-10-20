"""PNG decoder/encoder (stdlib zlib only).

Decodes every colour type at 1–16 bits, palette transparency and Adam7 interlacing.
Encodes 8-bit L / LA / RGB / RGBA with adaptive (per-row Sub/None) filtering.
"""

from __future__ import annotations

import struct
import zlib

from ..core import ConversionError
from .raster import CHANNELS, Frame, Raster, Unsupported, too_big_for_native

SIGNATURE = b"\x89PNG\r\n\x1a\n"
COLOR_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
ADAM7 = [(0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2),
         (0, 1, 1, 2)]


def _chunks(data: bytes):
    pos = 8
    while pos + 8 <= len(data):
        length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        yield ctype, body
        pos += 12 + length
        if ctype == b"IEND":
            break


def _unfilter(raw: bytes, width: int, height: int, bpp: int, bits_per_row: int) -> bytearray:
    stride = (bits_per_row + 7) // 8
    out = bytearray(stride * height)
    prev = bytearray(stride)
    pos = 0
    for y in range(height):
        ftype = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        if ftype == 1:      # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ftype == 2:    # Up
            line = bytearray((a + b) & 0xFF for a, b in zip(line, prev))
        elif ftype == 3:    # Average
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:    # Paeth
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
        elif ftype != 0:
            raise ConversionError(f"Corrupt PNG (filter type {ftype})")
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return out


def _samples(packed: bytes, width: int, height: int, depth: int, chans: int) -> list[bytes]:
    """Expand packed rows (any bit depth) to one byte per sample, per row."""
    stride = (width * chans * depth + 7) // 8
    rows = []
    for y in range(height):
        row = packed[y * stride:(y + 1) * stride]
        if depth == 8:
            rows.append(bytes(row))
        elif depth == 16:
            rows.append(bytes(row[0::2]))   # keep the high byte
        else:
            n = width * chans
            mask = (1 << depth) - 1
            vals = bytearray(n)
            per_byte = 8 // depth
            for i in range(n):
                byte = row[i // per_byte]
                shift = 8 - depth * (i % per_byte + 1)
                vals[i] = (byte >> shift) & mask
            rows.append(bytes(vals))
    return rows


def decode(data: bytes) -> Raster:
    if not data.startswith(SIGNATURE):
        raise ConversionError("Not a PNG file")
    ihdr = plte = trns = None
    idat = []
    for ctype, body in _chunks(data):
        if ctype == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body[:13])
        elif ctype == b"PLTE":
            plte = body
        elif ctype == b"tRNS":
            trns = body
        elif ctype == b"IDAT":
            idat.append(body)
        elif ctype == b"acTL":
            pass   # APNG: we decode the default image only
    if ihdr is None:
        raise ConversionError("Corrupt PNG (no header)")
    width, height, depth, ctype, _comp, _filt, interlace = ihdr
    if too_big_for_native(width, height):
        raise Unsupported("large PNG")
    chans = COLOR_CHANNELS.get(ctype)
    if chans is None:
        raise ConversionError(f"Corrupt PNG (colour type {ctype})")
    raw = zlib.decompress(b"".join(idat))
    bpp = max(1, chans * depth // 8)

    if interlace:
        full = [bytearray(width * chans) for _ in range(height)]
        pos = 0
        for x0, y0, dx, dy in ADAM7:
            pw, ph = (width - x0 + dx - 1) // dx, (height - y0 + dy - 1) // dy
            if pw <= 0 or ph <= 0:
                continue
            stride = (pw * chans * depth + 7) // 8
            size = (stride + 1) * ph
            packed = _unfilter(raw[pos:pos + size], pw, ph, bpp, pw * chans * depth)
            pos += size
            for j, row in enumerate(_samples(packed, pw, ph, depth, chans)):
                y = y0 + j * dy
                for i in range(pw):
                    x = x0 + i * dx
                    full[y][x * chans:(x + 1) * chans] = row[i * chans:(i + 1) * chans]
        rows = [bytes(r) for r in full]
    else:
        packed = _unfilter(raw, width, height, bpp, width * chans * depth)
        rows = _samples(packed, width, height, depth, chans)

    pixels = b"".join(rows)
    if ctype == 3:   # palette
        if plte is None:
            raise ConversionError("Corrupt PNG (no palette)")
        alpha = trns or b""
        if alpha:
            pal = [plte[i * 3:i * 3 + 3] + bytes([alpha[i] if i < len(alpha) else 255])
                   for i in range(len(plte) // 3)]
            mode = "RGBA"
        else:
            pal = [plte[i * 3:i * 3 + 3] for i in range(len(plte) // 3)]
            mode = "RGB"
        pixels = b"".join(pal[i] if i < len(pal) else pal[0] for i in pixels)
        return Raster([Frame(width, height, mode, pixels)])
    if depth < 8 and ctype in (0, 4):
        scale = 255 // ((1 << depth) - 1)
        pixels = bytes(v * scale for v in pixels)
    mode = {0: "L", 2: "RGB", 4: "LA", 6: "RGBA"}[ctype]
    frame = Frame(width, height, mode, pixels)
    if trns and ctype in (0, 2):   # single transparent colour
        frame = _apply_colorkey(frame, trns, depth)
    return Raster([frame])


def _apply_colorkey(frame: Frame, trns: bytes, depth: int) -> Frame:
    if frame.mode == "L":
        key = bytes([struct.unpack(">H", trns[:2])[0] >> (8 if depth == 16 else 0)])
        f = frame.to("LA")
        data = bytearray(f.data)
        for i in range(0, len(data), 2):
            if data[i:i + 1] == key:
                data[i + 1] = 0
        return Frame(f.width, f.height, "LA", bytes(data))
    r, g, b = (v >> (8 if depth == 16 else 0) for v in struct.unpack(">HHH", trns[:6]))
    key = bytes([r, g, b])
    f = frame.to("RGBA")
    data = bytearray(f.data)
    for i in range(0, len(data), 4):
        if data[i:i + 3] == key:
            data[i + 3] = 0
    return Frame(f.width, f.height, "RGBA", bytes(data))


def _chunk(ctype: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", zlib.crc32(ctype + body))


def encode(raster: Raster, dpi: float | None = None) -> bytes:
    frame = raster.first
    if frame.mode not in CHANNELS:
        frame = frame.to("RGBA")
    ctype = {"L": 0, "LA": 4, "RGB": 2, "RGBA": 6}[frame.mode]
    bpp = CHANNELS[frame.mode]
    stride = frame.width * bpp
    raw = bytearray()
    for row in frame.rows():
        # Sub filter usually compresses photos better; plain rows win for flat art.
        sub = bytearray(row)
        sub[bpp:] = bytes((row[i] - row[i - bpp]) & 0xFF for i in range(bpp, stride))
        if sum(1 for v in sub if v == 0) > sum(1 for v in row if v == 0):
            raw += b"\x01" + sub
        else:
            raw += b"\x00" + row
    out = SIGNATURE + _chunk(b"IHDR", struct.pack(">IIBBBBB", frame.width, frame.height, 8,
                                                     ctype, 0, 0, 0))
    if dpi:
        ppm = int(round(dpi / 0.0254))
        out += _chunk(b"pHYs", struct.pack(">IIB", ppm, ppm, 1))
    out += _chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + _chunk(b"IEND", b"")
    return out
