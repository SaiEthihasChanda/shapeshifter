"""The shared image model for native codecs, plus pixel-format helpers.

Pixels are stored as packed bytes ("L", "LA", "RGB" or "RGBA", 8 bits per channel).
Conversions use slice assignment so they run at C speed even in pure Python.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core import ConversionError

CHANNELS = {"L": 1, "LA": 2, "RGB": 3, "RGBA": 4}

# Native decoders do per-pixel work in Python; above this size we prefer a library
# codec when one is installed (see too_big_for_native()).
NATIVE_PIXEL_LIMIT = 6_000_000


class Unsupported(ConversionError):
    """This native codec can't handle the file variant; the next engine is tried."""


@dataclass
class Frame:
    width: int
    height: int
    mode: str
    data: bytes | bytearray
    duration: int = 0          # ms, for animations

    def __post_init__(self):
        expected = self.width * self.height * CHANNELS[self.mode]
        if len(self.data) != expected:
            raise ConversionError(f"Corrupt image data ({len(self.data)} bytes, expected {expected})")

    @property
    def has_alpha(self) -> bool:
        return self.mode in ("LA", "RGBA")

    def to(self, mode: str, background=(255, 255, 255)) -> "Frame":
        return Frame(self.width, self.height, mode, convert(self.data, self.mode, mode, background),
                     self.duration)

    def rows(self):
        stride = self.width * CHANNELS[self.mode]
        for y in range(self.height):
            yield self.data[y * stride:(y + 1) * stride]


@dataclass
class Raster:
    frames: list[Frame]
    kind: str = "single"       # single | animation | pages
    dpi: float | None = None
    info: dict = field(default_factory=dict)

    @property
    def first(self) -> Frame:
        return self.frames[0]


def too_big_for_native(width: int, height: int, limit: int = NATIVE_PIXEL_LIMIT) -> bool:
    """True if a library codec should handle this (it's much faster)."""
    if width * height <= limit:
        return False
    from importlib.util import find_spec
    return find_spec("PIL") is not None


def convert(data: bytes, src: str, dst: str, background=(255, 255, 255)) -> bytes:
    """Convert packed pixels between L / LA / RGB / RGBA."""
    if src == dst:
        return bytes(data)
    n = len(data) // CHANNELS[src]
    if src in ("LA", "RGBA") and dst in ("L", "RGB"):
        # Flatten alpha onto the background colour.
        data = flatten(data, src, background)
        src = "L" if src == "LA" else "RGB"
        if src == dst:
            return data
    if src == "L" and dst in ("RGB", "RGBA"):
        out = bytearray(n * CHANNELS[dst])
        step = CHANNELS[dst]
        out[0::step] = data
        out[1::step] = data
        out[2::step] = data
        if dst == "RGBA":
            out[3::4] = b"\xff" * n
        return bytes(out)
    if src == "L" and dst == "LA":
        out = bytearray(n * 2)
        out[0::2] = data
        out[1::2] = b"\xff" * n
        return bytes(out)
    if src == "LA" and dst == "RGBA":
        out = bytearray(n * 4)
        g = data[0::2]
        out[0::4], out[1::4], out[2::4], out[3::4] = g, g, g, data[1::2]
        return bytes(out)
    if src == "RGB" and dst == "RGBA":
        out = bytearray(n * 4)
        out[0::4], out[1::4], out[2::4] = data[0::3], data[1::3], data[2::3]
        out[3::4] = b"\xff" * n
        return bytes(out)
    if src in ("RGB", "RGBA") and dst in ("L", "LA"):
        step = CHANNELS[src]
        r, g, b = data[0::step], data[1::step], data[2::step]
        lum = bytes((299 * r[i] + 587 * g[i] + 114 * b[i]) // 1000 for i in range(n))
        if dst == "L":
            return lum
        out = bytearray(n * 2)
        out[0::2] = lum
        out[1::2] = data[3::4] if src == "RGBA" else b"\xff" * n
        return bytes(out)
    if src == "RGBA" and dst == "RGB":
        return flatten(data, "RGBA", background)
    if src == "LA" and dst == "RGB":
        return convert(flatten(data, "LA", background), "L", "RGB")
    raise ConversionError(f"Can't convert pixels from {src} to {dst}")


def flatten(data: bytes, mode: str, background=(255, 255, 255)) -> bytes:
    """Composite an alpha image onto a solid background; returns L or RGB pixels."""
    step = CHANNELS[mode]
    alpha = data[step - 1::step]
    colour_channels = step - 1
    if alpha.count(255) == len(alpha):           # fully opaque: just drop alpha
        out = bytearray(len(alpha) * colour_channels)
        for c in range(colour_channels):
            out[c::colour_channels] = data[c::step]
        return bytes(out)
    # Lookup table: blended[a][v] for one background value.
    out = bytearray(len(alpha) * colour_channels)
    for c in range(colour_channels):
        bg = background[c] if colour_channels == 3 else background[0]
        chan = data[c::step]
        table = [bytes((v * a + bg * (255 - a) + 127) // 255 for v in range(256)) for a in range(256)]
        out[c::colour_channels] = bytes(table[a][v] for v, a in zip(chan, alpha))
    return bytes(out)


def pad_square(frame: Frame) -> Frame:
    """Centre an image on a transparent square canvas (icons must be square)."""
    f = frame.to("RGBA")
    side = max(f.width, f.height)
    if f.width == f.height:
        return f
    out = bytearray(side * side * 4)
    x0, y0 = (side - f.width) // 2, (side - f.height) // 2
    stride = f.width * 4
    for y in range(f.height):
        dst = ((y0 + y) * side + x0) * 4
        out[dst:dst + stride] = f.data[y * stride:(y + 1) * stride]
    return Frame(side, side, "RGBA", bytes(out))


def resize_nearest(frame: Frame, width: int, height: int) -> Frame:
    ch = CHANNELS[frame.mode]
    src, sw = frame.data, frame.width
    xs = [min(frame.width - 1, x * frame.width // width) * ch for x in range(width)]
    out = bytearray(width * height * ch)
    pos = 0
    for y in range(height):
        row = min(frame.height - 1, y * frame.height // height) * sw * ch
        for x in xs:
            out[pos:pos + ch] = src[row + x:row + x + ch]
            pos += ch
    return Frame(width, height, frame.mode, bytes(out))


def resize_box(frame: Frame, width: int, height: int) -> Frame:
    """Downscale by averaging; used for icons (output is small, so speed is fine)."""
    ch = CHANNELS[frame.mode]
    sw, sh, src = frame.width, frame.height, frame.data
    out = bytearray(width * height * ch)
    pos = 0
    for y in range(height):
        y0, y1 = y * sh // height, max(y * sh // height + 1, (y + 1) * sh // height)
        for x in range(width):
            x0, x1 = x * sw // width, max(x * sw // width + 1, (x + 1) * sw // width)
            # Sample at most 4x4 points per output pixel to bound the cost.
            ys = range(y0, y1, max(1, (y1 - y0) // 4))
            xs = range(x0, x1, max(1, (x1 - x0) // 4))
            count = len(ys) * len(xs)
            for c in range(ch):
                total = 0
                for yy in ys:
                    base = yy * sw * ch + c
                    for xx in xs:
                        total += src[base + xx * ch]
                out[pos + c] = total // count
            pos += ch
    return Frame(width, height, frame.mode, bytes(out))
