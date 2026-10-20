"""Generates the app icon (a little format wheel) as an .ico file."""

from __future__ import annotations

from pathlib import Path

from .config import DATA_DIR
from .formats import CATEGORY_COLORS

ICON_PATH = DATA_DIR / "icon.ico"


def ensure_icon(path: Path = ICON_PATH) -> Path:
    if path.exists():
        return path
    from PIL import Image, ImageDraw

    size = 256
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    colors = list(CATEGORY_COLORS.values())
    pad = 8
    box = (pad, pad, size - pad, size - pad)
    span = 360 / len(colors)
    for i, col in enumerate(colors):
        start = -90 + i * span + 3
        d.pieslice(box, start, start + span - 6, fill=col)
    hub = 70
    c = size / 2
    d.ellipse((c - hub, c - hub, c + hub, c + hub), fill=(24, 25, 30, 255))
    # Arrow in the hub.
    d.line((c - 34, c, c + 26, c), fill="white", width=16)
    d.polygon([(c + 40, c), (c + 8, c - 28), (c + 8, c + 28)], fill="white")
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, "ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128),
                                (256, 256)])
    return path
