"""Generates the app icon (a little format wheel): an .ico for Windows, an .icns for the Mac app."""

from __future__ import annotations

from pathlib import Path

from .config import DATA_DIR
from .formats import CATEGORY_COLORS

ICON_PATH = DATA_DIR / "icon.ico"


def draw_icon(size: int = 256):
    """The icon as an RGBA picture of ``size`` pixels."""
    from PIL import Image, ImageDraw

    k = size / 256
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    colors = list(CATEGORY_COLORS.values())
    pad = 8 * k
    box = (pad, pad, size - pad, size - pad)
    span = 360 / len(colors)
    for i, col in enumerate(colors):
        start = -90 + i * span + 3
        d.pieslice(box, start, start + span - 6, fill=col)
    hub = 70 * k
    c = size / 2
    d.ellipse((c - hub, c - hub, c + hub, c + hub), fill=(24, 25, 30, 255))
    # Arrow in the hub.
    d.line((c - 34 * k, c, c + 26 * k, c), fill="white", width=max(1, round(16 * k)))
    d.polygon([(c + 40 * k, c), (c + 8 * k, c - 28 * k), (c + 8 * k, c + 28 * k)], fill="white")
    return im


def ensure_icon(path: Path = ICON_PATH) -> Path:
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    draw_icon(256).save(path, "ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128),
                                            (256, 256)])
    return path


def make_icns(path: Path) -> Path:
    """The macOS app icon (used when building ShapeShifter.app). Drawn large, so it stays sharp on Retina screens."""
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    big = draw_icon(1024)
    # macOS icons sit inside a margin of about 10 % on the 1024 px canvas.
    canvas = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    canvas.paste(big.resize((824, 824), Image.LANCZOS), (100, 100))
    canvas.save(path, "ICNS", sizes=[(16, 16), (32, 32), (64, 64), (128, 128), (256, 256), (512, 512), (1024, 1024)])
    return path
