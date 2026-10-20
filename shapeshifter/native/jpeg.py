"""Baseline JPEG decoder (sequential Huffman, 8-bit, any sampling, 1 or 3 components).

Progressive and arithmetic-coded JPEGs raise Unsupported, so a library codec takes over.
Also exposes jpeg_info() for embedding JPEGs in PDFs without decoding them.
"""

from __future__ import annotations

import math
import struct

from ..core import ConversionError
from .raster import Frame, Raster, Unsupported, too_big_for_native

ZIGZAG = [0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5, 12, 19, 26, 33, 40, 48, 41, 34,
          27, 20, 13, 6, 7, 14, 21, 28, 35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37,
          44, 51, 58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63]

# Separable IDCT basis: COS[x][u] = C(u)/2 * cos((2x+1)uπ/16)
COS = [[(math.sqrt(0.5) if u == 0 else 1.0) / 2 * math.cos((2 * x + 1) * u * math.pi / 16)
        for u in range(8)] for x in range(8)]


def jpeg_info(data: bytes) -> tuple[int, int, int]:
    """(width, height, components) from the SOF marker."""
    pos = 2
    while pos < len(data):
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        length = struct.unpack(">H", data[pos + 2:pos + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            _p, h, w, nc = struct.unpack(">BHHB", data[pos + 4:pos + 10])
            return w, h, nc
        pos += 2 + length
    raise ConversionError("Corrupt JPEG (no frame header)")


class _Bits:
    def __init__(self, data: bytes, pos: int):
        self.data, self.pos, self.buf, self.n = data, pos, 0, 0

    def bit(self) -> int:
        if self.n == 0:
            b = self.data[self.pos] if self.pos < len(self.data) else 0
            self.pos += 1
            if b == 0xFF:
                nxt = self.data[self.pos] if self.pos < len(self.data) else 0
                if nxt == 0:
                    self.pos += 1          # stuffed byte
                elif 0xD0 <= nxt <= 0xD7:
                    b = 0                  # marker: feed zeros until reset
                    self.pos -= 1
            self.buf, self.n = b, 8
        self.n -= 1
        return (self.buf >> self.n) & 1

    def bits(self, k: int) -> int:
        v = 0
        for _ in range(k):
            v = (v << 1) | self.bit()
        return v

    def reset(self):
        """Align to the next byte and skip an RSTn marker."""
        self.n = 0
        while self.pos + 1 < len(self.data):
            if self.data[self.pos] == 0xFF and 0xD0 <= self.data[self.pos + 1] <= 0xD7:
                self.pos += 2
                return
            self.pos += 1


def _build_huffman(counts: bytes, symbols: bytes) -> dict[tuple[int, int], int]:
    table, code, k = {}, 0, 0
    for length in range(1, 17):
        for _ in range(counts[length - 1]):
            table[(length, code)] = symbols[k]
            code += 1
            k += 1
        code <<= 1
    return table


def _decode_sym(bits: _Bits, table: dict) -> int:
    code = 0
    for length in range(1, 17):
        code = (code << 1) | bits.bit()
        sym = table.get((length, code))
        if sym is not None:
            return sym
    raise ConversionError("Corrupt JPEG (bad Huffman code)")


def _extend(v: int, t: int) -> int:
    return v - (1 << t) + 1 if t and v < (1 << (t - 1)) else v


def _idct(block: list[float]) -> list[int]:
    nz = [i for i in range(64) if block[i]]
    if not nz or nz == [0]:
        # Flat block (very common): every pixel is DC/8 + 128.
        v = int(block[0] / 8 + 128.5) if nz else 128
        return [0 if v < 0 else 255 if v > 255 else v] * 64
    tmp = [0.0] * 64
    for y in {i >> 3 for i in nz}:        # only rows with coefficients
        row = block[y * 8:y * 8 + 8]
        us = [u for u in range(8) if row[u]]
        for x in range(8):
            c = COS[x]
            tmp[y * 8 + x] = sum(c[u] * row[u] for u in us)
    vs = sorted({i >> 3 for i in nz})
    out = [0] * 64
    for x in range(8):                    # columns
        col = [tmp[v * 8 + x] for v in vs]
        for y in range(8):
            c = COS[y]
            val = sum(c[v] * cv for v, cv in zip(vs, col)) + 128
            out[y * 8 + x] = 0 if val < 0 else 255 if val > 255 else int(val + 0.5)
    return out


def decode(data: bytes) -> Raster:
    if data[:2] != b"\xff\xd8":
        raise ConversionError("Not a JPEG file")
    qt: dict[int, list[int]] = {}
    hts: dict[tuple[int, int], dict] = {}
    comps = []
    restart = 0
    width = height = 0
    adobe_transform = None
    pos = 2
    while pos < len(data):
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        pos += 2
        if marker == 0xD8 or marker == 0x01 or 0xD0 <= marker <= 0xD7:
            continue
        if marker == 0xD9:
            break
        length = struct.unpack(">H", data[pos:pos + 2])[0]
        seg = data[pos + 2:pos + length]
        if marker == 0xDB:                       # quantisation tables
            i = 0
            while i < len(seg):
                pq, tq = seg[i] >> 4, seg[i] & 15
                if pq:
                    vals = struct.unpack(">64H", seg[i + 1:i + 129])
                    i += 129
                else:
                    vals = seg[i + 1:i + 65]
                    i += 65
                table = [0] * 64
                for k in range(64):
                    table[ZIGZAG[k]] = vals[k]
                qt[tq] = table
        elif marker == 0xC4:                     # Huffman tables
            i = 0
            while i < len(seg):
                tc, th = seg[i] >> 4, seg[i] & 15
                counts = seg[i + 1:i + 17]
                total = sum(counts)
                hts[(tc, th)] = _build_huffman(counts, seg[i + 17:i + 17 + total])
                i += 17 + total
        elif marker in (0xC0, 0xC1):             # baseline / extended sequential
            precision, height, width, nc = struct.unpack(">BHHB", seg[:6])
            if precision != 8:
                raise Unsupported("12-bit JPEG")
            for k in range(nc):
                cid, samp, tq = seg[6 + k * 3:9 + k * 3]
                comps.append({"id": cid, "h": samp >> 4, "v": samp & 15, "tq": tq})
        elif marker in (0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            raise Unsupported("progressive or arithmetic-coded JPEG")
        elif marker == 0xDD:
            restart = struct.unpack(">H", seg[:2])[0]
        elif marker == 0xEE and seg[:5] == b"Adobe":
            adobe_transform = seg[11] if len(seg) > 11 else None
        elif marker == 0xDA:                     # start of scan
            if too_big_for_native(width, height, limit=2_000_000):
                raise Unsupported("large JPEG")
            ns = seg[0]
            if ns != len(comps):
                raise Unsupported("multi-scan JPEG")
            for k in range(ns):
                cid, tables = seg[1 + k * 2], seg[2 + k * 2]
                c = next(c for c in comps if c["id"] == cid)
                c["dc"], c["ac"] = hts[(0, tables >> 4)], hts[(1, tables & 15)]
            return _decode_scan(data, pos + length, width, height, comps, qt, restart,
                                adobe_transform)
        pos += length
    raise ConversionError("Corrupt JPEG (no image data)")


def _decode_scan(data, pos, width, height, comps, qt, restart, adobe_transform) -> Raster:
    hmax = max(c["h"] for c in comps)
    vmax = max(c["v"] for c in comps)
    mcux = (width + 8 * hmax - 1) // (8 * hmax)
    mcuy = (height + 8 * vmax - 1) // (8 * vmax)
    for c in comps:
        c["bw"] = mcux * c["h"] * 8
        c["bh"] = mcuy * c["v"] * 8
        c["plane"] = bytearray(c["bw"] * c["bh"])
        c["pred"] = 0
    bits = _Bits(data, pos)
    mcu_count = 0
    for my in range(mcuy):
        for mx in range(mcux):
            if restart and mcu_count and mcu_count % restart == 0:
                bits.reset()
                for c in comps:
                    c["pred"] = 0
            mcu_count += 1
            for c in comps:
                q = qt[c["tq"]]
                for by in range(c["v"]):
                    for bx in range(c["h"]):
                        coef = [0.0] * 64
                        t = _decode_sym(bits, c["dc"])
                        diff = _extend(bits.bits(t), t) if t else 0
                        c["pred"] += diff
                        coef[0] = c["pred"] * q[0]
                        k = 1
                        while k < 64:
                            rs = _decode_sym(bits, c["ac"])
                            r, s = rs >> 4, rs & 15
                            if s == 0:
                                if r == 15:
                                    k += 16
                                    continue
                                break
                            k += r
                            if k > 63:
                                break
                            z = ZIGZAG[k]
                            coef[z] = _extend(bits.bits(s), s) * q[z]
                            k += 1
                        px = _idct(coef)
                        x0 = (mx * c["h"] + bx) * 8
                        y0 = (my * c["v"] + by) * 8
                        plane, bw = c["plane"], c["bw"]
                        for yy in range(8):
                            o = (y0 + yy) * bw + x0
                            plane[o:o + 8] = bytes(px[yy * 8:yy * 8 + 8])
    # Upsample each component to full resolution and crop.
    planes = []
    for c in comps:
        sx, sy = hmax // c["h"], vmax // c["v"]
        bw, plane = c["bw"], c["plane"]
        cw = (width + sx - 1) // sx
        ch = (height + sy - 1) // sy
        rows = [bytes(plane[y * bw:y * bw + cw]) for y in range(ch)]
        if sx == 2:
            rows = [_fancy_h2(r)[:width] for r in rows]
        elif sx != 1:
            rows = [bytes(r[x // sx] for x in range(width)) for r in rows]
        else:
            rows = [r[:width] for r in rows]
        if sy == 2:
            rows = _fancy_v2(rows)[:height]
        elif sy != 1:
            rows = [rows[y // sy] for y in range(height)]
        planes.append(b"".join(rows[:height]))
    if len(comps) == 1:
        return Raster([Frame(width, height, "L", planes[0])])
    if len(comps) != 3:
        raise Unsupported("CMYK JPEG")
    if adobe_transform == 0:   # already RGB
        rgb = bytearray(width * height * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = planes
        return Raster([Frame(width, height, "RGB", bytes(rgb))])
    return Raster([Frame(width, height, "RGB", _ycc_to_rgb(*planes))])


def _fancy_h2(row: bytes) -> bytes:
    """libjpeg-style 'fancy' horizontal 2x upsampling (triangle filter)."""
    n = len(row)
    if n == 1:
        return bytes((row[0], row[0]))
    out = bytearray(n * 2)
    out[0] = row[0]
    out[1] = (row[0] * 3 + row[1] + 2) >> 2
    for i in range(1, n - 1):
        c3 = row[i] * 3
        out[2 * i] = (c3 + row[i - 1] + 1) >> 2
        out[2 * i + 1] = (c3 + row[i + 1] + 2) >> 2
    out[2 * n - 2] = (row[n - 1] * 3 + row[n - 2] + 1) >> 2
    out[2 * n - 1] = row[n - 1]
    return bytes(out)


def _fancy_v2(rows: list[bytes]) -> list[bytes]:
    """Vertical 2x upsampling: each output row is 3/4 nearest + 1/4 neighbour row."""
    out = []
    n = len(rows)
    for i, r in enumerate(rows):
        above = rows[i - 1] if i > 0 else r
        below = rows[i + 1] if i < n - 1 else r
        out.append(bytes((a * 3 + b + 1) >> 2 for a, b in zip(r, above)))
        out.append(bytes((a * 3 + b + 2) >> 2 for a, b in zip(r, below)))
    return out


def _ycc_to_rgb(Y: bytes, Cb: bytes, Cr: bytes) -> bytes:
    clamp = bytes(min(255, max(0, v)) for v in range(-512, 768))
    cr_r = [int(1.402 * (v - 128) + 0.5) for v in range(256)]
    cb_g = [int(-0.344136 * (v - 128)) for v in range(256)]
    cr_g = [int(-0.714136 * (v - 128)) for v in range(256)]
    cb_b = [int(1.772 * (v - 128) + 0.5) for v in range(256)]
    out = bytearray(len(Y) * 3)
    out[0::3] = bytes(clamp[y + cr_r[cr] + 512] for y, cr in zip(Y, Cr))
    out[1::3] = bytes(clamp[y + cb_g[cb] + cr_g[cr] + 512] for y, cb, cr in zip(Y, Cb, Cr))
    out[2::3] = bytes(clamp[y + cb_b[cb] + 512] for y, cb in zip(Y, Cb))
    return bytes(out)
