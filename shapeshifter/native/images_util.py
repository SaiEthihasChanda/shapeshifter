"""Resolve image references (relative paths, file: and data: URIs) found in documents."""

from __future__ import annotations

import base64
import io
import re
import struct
from pathlib import Path
from urllib.parse import unquote, urlparse


def load_image_ref(src: str, base_dirs: list[Path]) -> bytes | None:
    """Bytes of the referenced image, or None (remote URLs are never fetched)."""
    if not src:
        return None
    if src.startswith("data:"):
        m = re.match(r"data:([^;,]*)(;base64)?,(.*)", src, re.S)
        if not m:
            return None
        try:
            return base64.b64decode(m.group(3)) if m.group(2) else unquote(m.group(3)).encode("latin-1")
        except Exception:
            return None
    parsed = urlparse(src)
    if parsed.scheme in ("http", "https", "ftp"):
        return None
    path = unquote(parsed.path if parsed.scheme == "file" else src)
    if re.match(r"^/[A-Za-z]:", path):             # file:///C:/x
        path = path[1:]
    candidates = [Path(path)] if Path(path).is_absolute() else [d / path for d in base_dirs]
    for p in candidates:
        try:
            if p.is_file() and p.stat().st_size < 64 * 1024 * 1024:
                return p.read_bytes()
        except OSError:
            continue
    return None


def sniff(data: bytes) -> str:
    """'png' | 'jpg' | 'gif' | 'bmp' | 'webp' | 'svg' | '' from the first bytes."""
    if data.startswith(b"\x89PNG"):
        return "png"
    if data.startswith(b"\xff\xd8"):
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:2] == b"BM":
        return "bmp"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if b"<svg" in data[:1024]:
        return "svg"
    return ""


def size_of(data: bytes) -> tuple[int, int] | None:
    """(width, height) in pixels for the common raster formats, without decoding."""
    kind = sniff(data)
    try:
        if kind == "png":
            return struct.unpack(">II", data[16:24])
        if kind == "gif":
            return struct.unpack("<HH", data[6:10])
        if kind == "bmp":
            w, h = struct.unpack("<ii", data[18:26])
            return w, abs(h)
        if kind == "jpg":
            from .jpeg import jpeg_info
            w, h, _ = jpeg_info(data)
            return w, h
    except Exception:
        pass
    try:                                           # anything else Pillow knows
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            return im.size
    except Exception:
        return None


def to_png_or_jpeg(data: bytes) -> tuple[str, bytes] | None:
    """('png'|'jpg', bytes) that DOCX/PDF writers can embed, converting other formats."""
    kind = sniff(data)
    if kind in ("png", "jpg"):
        return kind, data
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            im.seek(0)
            buf = io.BytesIO()
            im.convert("RGBA" if "A" in im.getbands() or im.mode == "P" else "RGB").save(buf, "PNG")
            return "png", buf.getvalue()
    except Exception:
        return None
