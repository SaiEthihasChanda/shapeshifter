"""Fonts: WOFF <-> TrueType / OpenType (lossless table re-packing with zlib)."""

from __future__ import annotations

import struct
import zlib

from ..core import ConversionError
from .raster import Unsupported
from .registry import register


def _checksum(data: bytes) -> int:
    data += b"\0" * (-len(data) % 4)
    return sum(struct.unpack(f">{len(data) // 4}I", data)) & 0xFFFFFFFF


def woff_to_sfnt(data: bytes) -> tuple[bytes, bytes]:
    """Returns (flavor, font bytes)."""
    if data[:4] == b"wOF2":
        raise Unsupported("WOFF2 needs Brotli")
    if data[:4] != b"wOFF":
        raise ConversionError("Not a WOFF font")
    flavor, _len, num = struct.unpack_from(">4sIH", data, 4)
    tables = []
    for i in range(num):
        tag, off, clen, olen, csum = struct.unpack_from(">4sIIII", data, 44 + i * 20)
        raw = data[off:off + clen]
        tables.append((tag, zlib.decompress(raw) if clen < olen else raw, csum))
    return flavor, _pack_sfnt(flavor, tables)


def _pack_sfnt(flavor: bytes, tables) -> bytes:
    num = len(tables)
    es = max(i for i in range(17) if (1 << i) <= num) if num else 0
    search = (1 << es) * 16
    header = struct.pack(">4sHHHH", flavor, num, search, es, num * 16 - search)
    offset = 12 + 16 * num
    directory, body = b"", b""
    for tag, payload, csum in sorted(tables, key=lambda t: t[0]):
        directory += struct.pack(">4sIII", tag, csum or _checksum(payload), offset + len(body),
                                 len(payload))
        body += payload + b"\0" * (-len(payload) % 4)
    return header + directory + body


TTF_FLAVOR = b"\x00\x01\x00\x00"


def _read_tables(data: bytes, off: int = 0) -> tuple[bytes, list[tuple[bytes, bytes, int]]]:
    flavor, num = struct.unpack_from(">4sH", data, off)
    if num > 512:
        raise ConversionError("This doesn't look like a font")
    tables = []
    for i in range(num):
        tag, csum, toff, tlen = struct.unpack_from(">4sIII", data, off + 12 + 16 * i)
        if toff + tlen > len(data):
            raise ConversionError("The font is truncated")
        tables.append((tag, data[toff:toff + tlen], csum))
    return flavor, tables


def _font_name(tables) -> str:
    """The font's full name from its 'name' table (empty if it has none)."""
    for tag, payload, _ in tables:
        if tag != b"name" or len(payload) < 6:
            continue
        count, string_off = struct.unpack_from(">HH", payload, 2)
        best = ""
        for i in range(min(count, 200)):
            plat, enc, lang, nid, ln, off = struct.unpack_from(">HHHHHH", payload, 6 + 12 * i)
            if nid not in (4, 1):
                continue
            raw = payload[string_off + off:string_off + off + ln]
            text = raw.decode("utf-16-be", "replace") if plat in (0, 3) else raw.decode("latin-1", "replace")
            if nid == 4 or not best:
                best = text.strip()
            if nid == 4 and plat == 3:
                return best
        return best
    return ""


def split_ttc(data: bytes) -> list[tuple[str, bytes]]:
    """The fonts of a TrueType collection as separate .ttf files: [(font name, file bytes)]."""
    if data[:4] != b"ttcf" or len(data) < 16:
        raise ConversionError("Not a TrueType collection")
    num = struct.unpack_from(">I", data, 8)[0]
    if not 0 < num <= 256:
        raise ConversionError("Not a valid TrueType collection")
    fonts = []
    for off in struct.unpack_from(f">{num}I", data, 12):
        flavor, tables = _read_tables(data, off)
        if flavor != TTF_FLAVOR and flavor != b"true":
            continue                                          # a CFF/OpenType member can't be a .ttf
        fonts.append((_font_name(tables), _pack_sfnt(flavor, tables)))
    if not fonts:
        raise Unsupported("this collection holds no TrueType fonts")
    return fonts


def build_ttc(fonts: list[bytes]) -> bytes:
    """A TrueType collection holding the given .ttf fonts (each keeps its own tables)."""
    parsed = []
    for blob in fonts:
        flavor, tables = _read_tables(blob)
        if flavor not in (TTF_FLAVOR, b"true"):
            raise ConversionError("Only TrueType (.ttf) fonts can go into a collection")
        parsed.append((flavor, sorted(tables, key=lambda t: t[0])))
    n = len(parsed)
    dir_sizes = [12 + 16 * len(t) for _, t in parsed]
    data_start = 12 + 4 * n + sum(dir_sizes)
    offsets, directories, body = [], [], bytearray()
    pos = 12 + 4 * n
    for (flavor, tables), dsize in zip(parsed, dir_sizes):
        offsets.append(pos)
        pos += dsize
        num = len(tables)
        es = max(i for i in range(17) if (1 << i) <= num) if num else 0
        search = (1 << es) * 16
        d = struct.pack(">4sHHHH", flavor, num, search, es, num * 16 - search)
        for tag, payload, csum in tables:
            d += struct.pack(">4sIII", tag, csum or _checksum(payload), data_start + len(body), len(payload))
            body += payload + b"\0" * (-len(payload) % 4)
        directories.append(d)
    return b"ttcf" + struct.pack(">II", 0x00010000, n) + struct.pack(f">{n}I", *offsets) + b"".join(directories) + bytes(body)


@register("ttc", "ttf", multi=True)
def ttc_to_ttf(data: bytes, src, dst, ctx) -> list:
    import re
    out = []
    for i, (name, font) in enumerate(split_ttc(data), 1):
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip()
        out.append((f" - {safe}" if safe else f"-{i}", font))
    return out


def sfnt_to_woff(data: bytes) -> bytes:
    flavor, num = struct.unpack_from(">4sH", data, 0)
    if flavor not in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
        raise ConversionError("Not a TrueType/OpenType font")
    entries = []
    for i in range(num):
        tag, csum, off, length = struct.unpack_from(">4sIII", data, 12 + i * 16)
        raw = data[off:off + length]
        comp = zlib.compress(raw, 9)
        entries.append((tag, comp if len(comp) < len(raw) else raw, len(raw), csum))
    offset = 44 + 20 * num
    directory, body = b"", b""
    for tag, payload, olen, csum in entries:
        directory += struct.pack(">4sIIII", tag, offset + len(body), len(payload), olen, csum)
        body += payload + b"\0" * (-len(payload) % 4)
    total = offset + len(body)
    header = struct.pack(">4s4sIHHIHHIIIII", b"wOFF", flavor, total, num, 0, len(data), 1, 0,
                         0, 0, 0, 0, 0)
    return header + directory + body


@register("woff", ["ttf", "otf"])
def woff_to(data: bytes, src, dst, ctx) -> bytes:
    flavor, font = woff_to_sfnt(data)
    if (flavor == b"OTTO") != (dst == "otf"):
        raise Unsupported(f"this WOFF holds a {'CFF/OpenType' if flavor == b'OTTO' else 'TrueType'} font")
    return font


@register(["ttf", "otf"], "woff")
def to_woff(data: bytes, src, dst, ctx) -> bytes:
    return sfnt_to_woff(data)
