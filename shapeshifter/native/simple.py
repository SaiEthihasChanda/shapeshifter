"""Small raster formats: BMP, TGA, Netpbm (PBM/PGM/PPM) and QOI."""

from __future__ import annotations

import struct

from ..core import ConversionError
from .raster import CHANNELS, Frame, Raster, Unsupported, too_big_for_native


def _bgr_to_rgb(data: bytes, step: int) -> bytes:
    out = bytearray(data)
    out[0::step], out[2::step] = data[2::step], data[0::step]
    return bytes(out)


# ------------------------------------------------------------------------- BMP

def decode_bmp(data: bytes) -> Raster:
    if data[:2] != b"BM":
        raise ConversionError("Not a BMP file")
    offset = struct.unpack("<I", data[10:14])[0]
    hsize = struct.unpack("<I", data[14:18])[0]
    if hsize == 12:   # OS/2 BITMAPCOREHEADER
        width, height, _planes, bits = struct.unpack("<HHHH", data[18:26])
        compression, pal_entry = 0, 3
    else:
        width, height, _planes, bits, compression = struct.unpack("<iiHHI", data[18:34])
        pal_entry = 4
    if compression not in (0, 3):
        raise Unsupported("compressed BMP")
    top_down = height < 0
    height = abs(height)
    if too_big_for_native(width, height):
        raise Unsupported("large BMP")
    stride = ((width * bits + 31) // 32) * 4
    rows = []
    if bits in (24, 32):
        step = bits // 8
        mode = "RGB"
        alpha = False
        if bits == 32:
            # BI_BITFIELDS/V4+ headers may declare an alpha mask; plain 32-bit usually
            # leaves alpha at 0, which we treat as opaque.
            body = data[offset:offset + stride * height]
            alpha = any(body[3::4]) and hsize >= 40
            mode = "RGBA" if alpha else "RGB"
        for y in range(height):
            row = data[offset + y * stride:offset + y * stride + width * step]
            if bits == 24:
                rows.append(_bgr_to_rgb(row, 3))
            else:
                rgba = _bgr_to_rgb(row, 4)
                if alpha:
                    rows.append(rgba)
                else:
                    out = bytearray(width * 3)
                    out[0::3], out[1::3], out[2::3] = rgba[0::4], rgba[1::4], rgba[2::4]
                    rows.append(bytes(out))
    elif bits in (1, 4, 8):
        colours = struct.unpack("<I", data[46:50])[0] if hsize >= 40 else 0
        colours = colours or (1 << bits)
        pal_off = 14 + hsize
        palette = [bytes((data[pal_off + i * pal_entry + 2], data[pal_off + i * pal_entry + 1],
                          data[pal_off + i * pal_entry])) for i in range(colours)]
        mode = "RGB"
        per_byte = 8 // bits
        mask = (1 << bits) - 1
        for y in range(height):
            row = data[offset + y * stride:offset + (y + 1) * stride]
            out = bytearray()
            for x in range(width):
                byte = row[x // per_byte]
                idx = (byte >> (8 - bits * (x % per_byte + 1))) & mask
                out += palette[idx] if idx < len(palette) else b"\0\0\0"
            rows.append(bytes(out))
    elif bits == 16:
        raise Unsupported("16-bit BMP")
    else:
        raise ConversionError(f"Unsupported BMP depth {bits}")
    if not top_down:
        rows.reverse()
    return Raster([Frame(width, height, mode, b"".join(rows))])


def encode_bmp(raster: Raster) -> bytes:
    f = raster.first
    alpha = f.has_alpha
    f = f.to("RGBA" if alpha else "RGB")
    step = 4 if alpha else 3
    stride = ((f.width * step * 8 + 31) // 32) * 4
    pad = b"\0" * (stride - f.width * step)
    body = b"".join(_bgr_to_rgb(row, step) + pad for row in reversed(list(f.rows())))
    if alpha:
        # BITMAPV4HEADER with explicit masks so alpha-aware readers keep transparency.
        header = struct.pack("<IiiHHIIiiII", 108, f.width, f.height, 1, 32, 3, len(body),
                             2835, 2835, 0, 0)
        header += struct.pack("<IIII", 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)
        header += b"BGRs" + b"\0" * 48
    else:
        header = struct.pack("<IiiHHIIiiII", 40, f.width, f.height, 1, 24, 0, len(body),
                             2835, 2835, 0, 0)
    offset = 14 + len(header)
    return b"BM" + struct.pack("<IHHI", offset + len(body), 0, 0, offset) + header + body


# ------------------------------------------------------------------------- TGA

def decode_tga(data: bytes) -> Raster:
    id_len, cmap_type, img_type = data[0], data[1], data[2]
    cmap_first, cmap_len, cmap_bits = struct.unpack("<HHB", data[3:8])
    width, height, bits, desc = struct.unpack("<HHBB", data[12:18])
    if too_big_for_native(width, height):
        raise Unsupported("large TGA")
    pos = 18 + id_len
    palette = []
    if cmap_type:
        entry = (cmap_bits + 7) // 8
        for i in range(cmap_len):
            e = data[pos + i * entry:pos + (i + 1) * entry]
            palette.append(bytes((e[2], e[1], e[0])) if entry >= 3 else b"\0\0\0")
        pos += cmap_len * entry
    base = img_type & 7
    rle = img_type & 8
    if base not in (1, 2, 3):
        raise ConversionError(f"Unsupported TGA type {img_type}")
    px = (bits + 7) // 8
    n = width * height
    if rle:
        raw = bytearray()
        while len(raw) < n * px:
            hdr = data[pos]
            pos += 1
            count = (hdr & 0x7F) + 1
            if hdr & 0x80:
                raw += data[pos:pos + px] * count
                pos += px
            else:
                raw += data[pos:pos + px * count]
                pos += px * count
        raw = bytes(raw[:n * px])
    else:
        raw = data[pos:pos + n * px]
    if base == 1:
        raw = b"".join(palette[i - cmap_first] for i in raw)
        mode, step = "RGB", 3
    elif base == 3:
        mode, step = ("L", 1) if px == 1 else ("LA", 2)
    elif px == 2:
        raise Unsupported("16-bit TGA")
    else:
        mode, step = ("RGB", 3) if px == 3 else ("RGBA", 4)
        raw = _bgr_to_rgb(raw, step)
        if mode == "RGBA" and not (desc & 0x0F) and not any(raw[3::4]):
            raw = bytearray(raw)
            raw[3::4] = b"\xff" * n      # alpha bits unused: treat as opaque
            raw = bytes(raw)
    stride = width * CHANNELS[mode]
    rows = [raw[y * stride:(y + 1) * stride] for y in range(height)]
    if not desc & 0x20:   # origin bottom-left
        rows.reverse()
    if desc & 0x10:       # right-to-left
        rows = [b"".join(r[x * step:(x + 1) * step] for x in reversed(range(width))) for r in rows]
    return Raster([Frame(width, height, mode, b"".join(rows))])


def encode_tga(raster: Raster) -> bytes:
    f = raster.first
    f = f.to("RGBA" if f.has_alpha else "RGB")
    step = CHANNELS[f.mode]
    desc = 0x20 | (8 if step == 4 else 0)    # top-left origin, alpha bits
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, f.width, f.height, step * 8, desc)
    return header + _bgr_to_rgb(f.data, step) + b"\0" * 8 + b"TRUEVISION-XFILE.\0"


# ---------------------------------------------------------------------- Netpbm

def _pnm_tokens(data: bytes, count: int):
    tokens, pos = [], 2
    while len(tokens) < count:
        while data[pos:pos + 1].isspace():
            pos += 1
        if data[pos:pos + 1] == b"#":
            while data[pos:pos + 1] not in (b"\n", b"\r", b""):
                pos += 1
            continue
        start = pos
        while not data[pos:pos + 1].isspace() and pos < len(data):
            pos += 1
        tokens.append(int(data[start:pos]))
    return tokens, pos + 1


def decode_pnm(data: bytes) -> Raster:
    magic = data[:2]
    if magic not in (b"P1", b"P2", b"P3", b"P4", b"P5", b"P6"):
        raise ConversionError("Not a PBM/PGM/PPM file")
    kind = int(magic[1:2])
    bitmap = kind in (1, 4)
    (tokens, pos) = _pnm_tokens(data, 2 if bitmap else 3)
    width, height = tokens[0], tokens[1]
    if too_big_for_native(width, height):
        raise Unsupported("large PNM")
    maxval = 1 if bitmap else tokens[2]
    chans = 3 if kind in (3, 6) else 1
    n = width * height * chans
    if kind in (1, 2, 3):   # ASCII
        vals = [int(v) for v in data[pos - 1:].split()[:n]] if kind != 1 else \
            [int(c) for c in data[pos - 1:].decode("ascii") if c in "01"][:n]
    elif kind == 4:
        stride = (width + 7) // 8
        vals = []
        for y in range(height):
            row = data[pos + y * stride:pos + (y + 1) * stride]
            vals += [(row[x // 8] >> (7 - x % 8)) & 1 for x in range(width)]
    elif maxval < 256:
        vals = data[pos:pos + n]
    else:
        vals = data[pos:pos + 2 * n][0::2]   # 16-bit: keep the high byte
        maxval = 255
    if bitmap:
        pixels = bytes(0 if v else 255 for v in vals)   # 1 = black
    elif maxval != 255:
        pixels = bytes(v * 255 // maxval for v in vals)
    else:
        pixels = bytes(vals)
    return Raster([Frame(width, height, "RGB" if chans == 3 else "L", pixels)])


def encode_pnm(raster: Raster) -> bytes:
    f = raster.first
    if f.mode in ("L", "LA"):
        f = f.to("L")
        return b"P5\n%d %d\n255\n" % (f.width, f.height) + f.data
    f = f.to("RGB")
    return b"P6\n%d %d\n255\n" % (f.width, f.height) + f.data


# ------------------------------------------------------------------------- QOI

QOI_END = b"\0" * 7 + b"\1"


def _qoi_hash(r, g, b, a):
    return (r * 3 + g * 5 + b * 7 + a * 11) % 64


def decode_qoi(data: bytes) -> Raster:
    if data[:4] != b"qoif":
        raise ConversionError("Not a QOI file")
    width, height, chans, _cs = struct.unpack(">IIBB", data[4:14])
    if too_big_for_native(width, height):
        raise Unsupported("large QOI")
    n = width * height
    out = bytearray(n * 4)
    index = [(0, 0, 0, 0)] * 64
    r, g, b, a = 0, 0, 0, 255
    pos, px, end = 14, 0, len(data) - 8
    run = 0
    while px < n:
        if run:
            run -= 1
        elif pos < end:
            b1 = data[pos]
            pos += 1
            if b1 == 0xFE:
                r, g, b = data[pos], data[pos + 1], data[pos + 2]
                pos += 3
            elif b1 == 0xFF:
                r, g, b, a = data[pos], data[pos + 1], data[pos + 2], data[pos + 3]
                pos += 4
            elif b1 >> 6 == 0:
                r, g, b, a = index[b1]
            elif b1 >> 6 == 1:
                r = (r + ((b1 >> 4) & 3) - 2) & 0xFF
                g = (g + ((b1 >> 2) & 3) - 2) & 0xFF
                b = (b + (b1 & 3) - 2) & 0xFF
            elif b1 >> 6 == 2:
                b2 = data[pos]
                pos += 1
                dg = (b1 & 0x3F) - 32
                r = (r + dg - 8 + ((b2 >> 4) & 0x0F)) & 0xFF
                g = (g + dg) & 0xFF
                b = (b + dg - 8 + (b2 & 0x0F)) & 0xFF
            else:
                run = b1 & 0x3F
            index[_qoi_hash(r, g, b, a)] = (r, g, b, a)
        out[px * 4:px * 4 + 4] = bytes((r, g, b, a))
        px += 1
    mode = "RGBA" if chans == 4 else "RGB"
    frame = Frame(width, height, "RGBA", bytes(out))
    return Raster([frame if mode == "RGBA" else frame.to("RGB")])


def encode_qoi(raster: Raster) -> bytes:
    f = raster.first
    alpha = f.has_alpha
    f = f.to("RGBA")
    out = bytearray(b"qoif" + struct.pack(">IIBB", f.width, f.height, 4 if alpha else 3, 0))
    index = [None] * 64
    prev = (0, 0, 0, 255)
    run = 0
    data = f.data
    n = f.width * f.height
    for i in range(n):
        px = tuple(data[i * 4:i * 4 + 4])
        if px == prev:
            run += 1
            if run == 62 or i == n - 1:
                out.append(0xC0 | (run - 1))
                run = 0
            continue
        if run:
            out.append(0xC0 | (run - 1))
            run = 0
        h = _qoi_hash(*px)
        if index[h] == px:
            out.append(h)
        else:
            index[h] = px
            r, g, b, a = px
            if a == prev[3]:
                dr, dg, db = (r - prev[0] + 128) % 256 - 128, (g - prev[1] + 128) % 256 - 128, \
                    (b - prev[2] + 128) % 256 - 128
                dr_dg, db_dg = dr - dg, db - dg
                if -2 <= dr < 2 and -2 <= dg < 2 and -2 <= db < 2:
                    out.append(0x40 | ((dr + 2) << 4) | ((dg + 2) << 2) | (db + 2))
                elif -32 <= dg < 32 and -8 <= dr_dg < 8 and -8 <= db_dg < 8:
                    out += bytes((0x80 | (dg + 32), ((dr_dg + 8) << 4) | (db_dg + 8)))
                else:
                    out += bytes((0xFE, r, g, b))
            else:
                out += bytes((0xFF, r, g, b, a))
        prev = px
    return bytes(out) + QOI_END
