"""WAV <-> AIFF (uncompressed PCM, 8/16/24/32-bit integer and 32-bit float WAV)."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

from ..core import ConversionError
from .raster import Unsupported


@dataclass
class Pcm:
    rate: int
    channels: int
    bits: int
    data: bytes            # little-endian signed (8-bit: signed too)


def _swap(data: bytes, width: int) -> bytes:
    if width == 1:
        return bytes(data)
    out = bytearray(len(data))
    for i in range(width):
        out[i::width] = data[width - 1 - i::width]
    return bytes(out)


def _float_to_int16(data: bytes) -> bytes:
    n = len(data) // 4
    vals = struct.unpack(f"<{n}f", data[:n * 4])
    return struct.pack(f"<{n}h", *(max(-32768, min(32767, int(v * 32767))) for v in vals))


def read_wav(data: bytes) -> Pcm:
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ConversionError("Not a WAV file")
    pos, fmt, body = 12, None, None
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        chunk = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt = chunk
        elif cid == b"data":
            body = chunk
        pos += 8 + size + (size & 1)
    if fmt is None or body is None:
        raise ConversionError("Corrupt WAV (missing fmt or data)")
    tag, channels, rate, _br, _align, bits = struct.unpack("<HHIIHH", fmt[:16])
    if tag == 0xFFFE and len(fmt) >= 26:          # WAVE_FORMAT_EXTENSIBLE
        tag = struct.unpack("<H", fmt[24:26])[0]
    if tag == 3 and bits == 32:                   # IEEE float
        return Pcm(rate, channels, 16, _float_to_int16(body))
    if tag != 1:
        raise Unsupported("compressed WAV")
    if bits == 8:                                  # unsigned -> signed
        body = bytes((b - 128) & 0xFF for b in body)
    return Pcm(rate, channels, bits, bytes(body))


def write_wav(pcm: Pcm) -> bytes:
    body = pcm.data
    if pcm.bits == 8:
        body = bytes((b + 128) & 0xFF for b in body)
    width = pcm.bits // 8
    fmt = struct.pack("<HHIIHH", 1, pcm.channels, pcm.rate, pcm.rate * pcm.channels * width,
                      pcm.channels * width, pcm.bits)
    pad = b"\0" if len(body) & 1 else b""
    return (b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(body) + len(pad)) + b"WAVE"
            + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"data" + struct.pack("<I", len(body)) + body + pad)


def _ext80_to_float(b: bytes) -> float:
    exp, mant = struct.unpack(">HQ", b)
    sign = -1 if exp & 0x8000 else 1
    exp &= 0x7FFF
    return 0.0 if exp == 0 and mant == 0 else sign * mant * 2.0 ** (exp - 16383 - 63)


def _float_to_ext80(v: float) -> bytes:
    if v == 0:
        return b"\0" * 10
    m, e = math.frexp(v)            # v = m * 2**e, 0.5 <= m < 1
    return struct.pack(">HQ", e - 1 + 16383, int(m * (1 << 64)))


def read_aiff(data: bytes) -> Pcm:
    if data[:4] != b"FORM" or data[8:12] not in (b"AIFF", b"AIFC"):
        raise ConversionError("Not an AIFF file")
    aifc = data[8:12] == b"AIFC"
    pos, comm, ssnd = 12, None, None
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack(">I", data[pos + 4:pos + 8])[0]
        chunk = data[pos + 8:pos + 8 + size]
        if cid == b"COMM":
            comm = chunk
        elif cid == b"SSND":
            ssnd = chunk
        pos += 8 + size + (size & 1)
    if comm is None or ssnd is None:
        raise ConversionError("Corrupt AIFF")
    channels, _frames, bits = struct.unpack(">hIh", comm[:8])
    rate = int(round(_ext80_to_float(comm[8:18])))
    little = False
    if aifc:
        kind = comm[18:22]
        if kind == b"sowt":
            little = True
        elif kind not in (b"NONE", b"twos"):
            raise Unsupported(f"compressed AIFF-C ({kind.decode(errors='replace')})")
    offset = struct.unpack(">I", ssnd[:4])[0]
    body = ssnd[8 + offset:]
    width = (bits + 7) // 8
    body = body[:len(body) - len(body) % (width * channels)]
    return Pcm(rate, channels, width * 8, body if little else _swap(body, width))


def write_aiff(pcm: Pcm) -> bytes:
    width = pcm.bits // 8
    body = _swap(pcm.data, width)
    frames = len(body) // (width * pcm.channels)
    comm = struct.pack(">hIh", pcm.channels, frames, pcm.bits) + _float_to_ext80(float(pcm.rate))
    ssnd = struct.pack(">II", 0, 0) + body
    pad = b"\0" if len(ssnd) & 1 else b""
    chunks = (b"COMM" + struct.pack(">I", len(comm)) + comm
              + b"SSND" + struct.pack(">I", len(ssnd)) + ssnd + pad)
    return b"FORM" + struct.pack(">I", 4 + len(chunks)) + b"AIFF" + chunks
