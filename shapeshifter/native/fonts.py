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
