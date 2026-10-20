"""Fill in a form that only exists as an image.

Detection (OCR + a little pixel analysis) finds the places a person writes: a label followed by a
blank line or space, small square check boxes, and signature lines. Rendering then writes typed
text, ticks and a signature image onto a copy of the picture, leaving everything else untouched.

Only Pillow is used for the pixels (no numpy), so it works in the packaged app.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import ocr

log = logging.getLogger(__name__)

ANALYSIS_SIDE = 1600            # pixel analysis runs on a copy at most this big
DARK = 150                      # grey level below which a pixel counts as ink
SIGN_WORDS = re.compile(r"\b(signature|signed|sign here|sign)\b", re.I)
MAX_LABEL_WORDS = 7
FONTS = {                       # label -> candidate files, first one that exists wins
    "Typed": ["arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"],
    "Handwriting": ["segoesc.ttf", "Inkfree.ttf", "BRUSHSCI.TTF", "arial.ttf"],
    "Typewriter": ["cour.ttf", "consola.ttf", "arial.ttf"],
}
INKS = {"Blue": (28, 52, 148), "Black": (20, 20, 20), "Red": (170, 30, 30)}


@dataclass
class Field:
    kind: str                                # text | checkbox | signature
    label: str
    box: tuple[float, float, float, float]   # x, y, w, h in the original image: where to write
    size: float = 0.0                        # text height in pixels (text fields)
    value: str = ""
    checked: bool = False
    sign_path: str = ""                      # signature fields: the image to place


# ------------------------------------------------------------------ loading

def load_image(path):
    from PIL import Image, ImageOps
    with Image.open(path) as im:
        im.seek(0)
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.getchannel("A"))
            return bg
        return im.convert("RGB")


# ---------------------------------------------------------------- detection

class _Ink:
    """The picture as a 1-byte-per-pixel ink map, for finding rules and squares."""

    def __init__(self, image, scale: float):
        from PIL import Image
        g = image.convert("L")
        if scale != 1.0:
            g = g.resize((max(1, round(g.width * scale)), max(1, round(g.height * scale))), Image.BILINEAR)
        self.w, self.h = g.size
        self.data = g.point(lambda v: 1 if v < DARK else 0).tobytes()

    def row(self, y: int, x0: int, x1: int) -> bytes:
        x0, x1 = max(0, x0), min(self.w, x1)
        if not 0 <= y < self.h or x1 <= x0:
            return b""
        return self.data[y * self.w + x0:y * self.w + x1]

    def dark(self, x: int, y: int) -> bool:
        return 0 <= x < self.w and 0 <= y < self.h and self.data[y * self.w + x] == 1

    def any_dark(self, x: int, y: int, r: int = 1) -> bool:
        return any(self.dark(x + dx, y + dy) for dx in range(-r, r + 1) for dy in range(-r, r + 1))


def _longest_run(row: bytes) -> tuple[int, int]:
    """(start, length) of the longest stretch of ink in a row, tolerating 2-pixel gaps."""
    best = (0, 0)
    for m in re.finditer(rb"\x01(?:\x01|\x00{1,2}(?=\x01))*", row):
        if m.end() - m.start() > best[1]:
            best = (m.start(), m.end() - m.start())
    return best


def _find_rule(ink: _Ink, x0: int, x1: int, y0: int, y1: int, min_len: int):
    """A horizontal line inside the region: (y, x_start, x_end) or None. Prefers the lowest."""
    found = None
    for y in range(max(0, y0), min(ink.h, y1)):
        start, length = _longest_run(ink.row(y, x0, x1))
        if length >= min_len:
            found = (y, x0 + start, x0 + start + length)
    return found


def _components(ink: _Ink):
    """Connected blobs of ink as (x0, y0, x1, y1, pixels), by joining overlapping runs of adjacent rows."""
    parent: list[int] = []
    runs: list[tuple[int, int, int]] = []          # (x0, x1, y) per run

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    prev: list[tuple[int, int, int]] = []
    for y in range(ink.h):
        row = ink.data[y * ink.w:(y + 1) * ink.w]
        cur = []
        for m in re.finditer(rb"+", row):
            a, b = m.start(), m.end()
            idx = len(parent)
            parent.append(idx)
            runs.append((a, b, y))
            cur.append((a, b, idx))
            for pa, pb, pi in prev:
                if pa < b and a < pb:
                    r1, r2 = find(idx), find(pi)
                    if r1 != r2:
                        parent[r2] = r1
        prev = cur
    boxes: dict[int, list[int]] = {}
    for idx, (a, b, y) in enumerate(runs):
        r = find(idx)
        bb = boxes.get(r)
        if bb is None:
            boxes[r] = [a, y, b, y + 1, b - a]
        else:
            bb[0], bb[1] = min(bb[0], a), min(bb[1], y)
            bb[2], bb[3] = max(bb[2], b), max(bb[3], y + 1)
            bb[4] += b - a
    return [tuple(v) for v in boxes.values()]


def _is_square_box(ink: _Ink, x0, y0, x1, y1, pixels) -> bool:
    w, h = x1 - x0, y1 - y0
    if not (15 <= w <= 72 and 15 <= h <= 72) or abs(w - h) > 0.2 * max(w, h):
        return False
    if pixels > 0.5 * w * h:
        return False                                # a solid blob, not an outline
    # Sharp corners and four solid sides: letters like o, e, E, H fail one of these.
    if not all(ink.dark(cx, cy) for cx, cy in ((x0 + 1, y0 + 1), (x1 - 2, y0 + 1),
                                                (x0 + 1, y1 - 2), (x1 - 2, y1 - 2))):
        return False
    mx, my = (x0 + x1) // 2, (y0 + y1) // 2
    if not all(ink.any_dark(ex, ey, 1) for ex, ey in ((mx, y0 + 1), (mx, y1 - 2), (x0 + 1, my), (x1 - 2, my))):
        return False
    # An empty middle (a ticked box has marks in it, but never fills it solid).
    ix0, ix1, iy0, iy1 = x0 + w // 4, x1 - w // 4, y0 + h // 4, y1 - h // 4
    inner = sum(sum(ink.row(y, ix0, ix1)) for y in range(iy0, iy1))
    return inner < 0.45 * (ix1 - ix0) * (iy1 - iy0)


def _clean_label(text: str) -> str:
    return re.sub(r"[\s_.\-–—:|]+$", "", re.sub(r"^[\s_.\-–—|]+", "", text)).strip()


def _same_row(a, b) -> bool:
    ay, ah = a[1], a[3]
    by, bh = b[1], b[3]
    overlap = min(ay + ah, by + bh) - max(ay, by)
    return overlap > 0.5 * min(ah, bh)


def detect(path, cfg: dict | None = None, cancel=None) -> tuple[list[Field], tuple[int, int]]:
    """Find the fields of a form picture. Returns (fields, (width, height))."""
    image = load_image(path)
    W, H = image.size
    page, ocr_scale = ocr.recognize_file(path, cfg, cancel)
    # OCR works on a possibly resized copy; its boxes -> original pixels.
    lines = []
    for ln in page.lines:
        x, y, w, h = ln.box
        if w <= 0 or h <= 0 or not ln.text.strip():
            continue
        words = [(wd.text, wd.x / ocr_scale, wd.y / ocr_scale, wd.w / ocr_scale, wd.h / ocr_scale)
                 for wd in ln.words]
        lines.append((ln.text.strip(), x / ocr_scale, y / ocr_scale, w / ocr_scale, h / ocr_scale, words))
    scale = min(1.0, ANALYSIS_SIDE / max(W, H))
    ink = _Ink(image, scale)
    fields: list[Field] = []
    used_lines: set[int] = set()

    # ---- check boxes first, so their labels aren't mistaken for text fields
    for (x0, y0, x1, y1, px) in _components(ink):
        if not _is_square_box(ink, x0, y0, x1, y1, px):
            continue
        bx, by, bw, bh = x0 / scale, y0 / scale, (x1 - x0) / scale, (y1 - y0) / scale
        label, li = "", None
        best = None
        for i, (text, lx, ly, lw, lh, words) in enumerate(lines):
            if not _same_row((bx, by, bw, bh), (lx, ly, lw, lh)):
                continue
            for wt, wx, wy, ww, wh in words:
                if wx >= bx + bw - 2 and (best is None or wx < best[0]):
                    best = (wx, i)
        if best is None or best[0] - (bx + bw) > 2.6 * bh:
            # Label written to the left of the box ("I agree ... [ ]").
            for i, (text, lx, ly, lw, lh, words) in enumerate(lines):
                if _same_row((bx, by, bw, bh), (lx, ly, lw, lh)) and -1.6 * bw <= bx - (lx + lw) < 4.5 * bh:
                    # the OCR often reads the box itself as a stray letter at the end of the line
                    label = " ".join(wt for wt, wx, wy, ww, wh in words if wx + ww / 2 < bx - 2) or text
                    used_lines.add(i)
                    break
            best = None
        if best is not None:
            li = best[1]
            text, lx, ly, lw, lh, words = lines[li]
            gap = 2.6 * bh
            parts, last = [], bx + bw
            for wt, wx, wy, ww, wh in sorted(words, key=lambda t: t[1]):
                if wx < bx + bw - 2:
                    continue
                if parts and wx - last > gap:
                    break
                parts.append(wt)
                last = wx + ww
            label = " ".join(parts)
            # The OCR often reads the box itself ("O", "D", "[]") as the first word.
            used_lines.add(li)
        fields.append(Field("checkbox", _clean_label(label) or "Check box", (bx, by, bw, bh)))

    # ---- text and signature fields: a label, then a rule or free space to its right
    for i, (text, lx, ly, lw, lh, words) in enumerate(lines):
        if i in used_lines or len(text.split()) > MAX_LABEL_WORDS:
            continue
        label = _clean_label(re.sub(r"[_.]{3,}.*$", "", text))
        if not label or len(label) < 2:
            continue
        has_colon = ":" in text or re.search(r"[_.]{3,}\s*$", text) is not None
        sign = SIGN_WORDS.search(label) is not None
        # label's right edge: after the word carrying the colon, else the whole line
        right = lx + lw
        for wt, wx, wy, ww, wh in words:
            if ":" in wt:
                right = wx + ww
                break
        else:
            if re.search(r"[_.]{3,}", text):
                for wt, wx, wy, ww, wh in words:
                    if re.fullmatch(r"[_.\-]+", wt) or re.search(r"[_.]{3,}", wt):
                        right = min(right, wx)
                        break
        # room to the right: up to the next thing on the same row
        limit = W - 0.04 * W
        for j, (t2, x2, y2, w2, h2, _) in enumerate(lines):
            if j != i and x2 > right + 0.3 * lh and _same_row((lx, ly, lw, lh), (x2, y2, w2, h2)):
                limit = min(limit, x2 - 0.5 * lh)
        sx0 = int((right + 0.35 * lh) * scale)
        sx1 = int(limit * scale)
        if sx1 - sx0 < 40 * scale:
            continue
        rule = _find_rule(ink, sx0, sx1, int((ly + 0.55 * lh) * scale), int((ly + 1.9 * lh) * scale),
                          min_len=int(max(60, 3.5 * lh) * scale))
        if rule is not None and ink.dark(sx0 - 4, rule[0]):
            rule = None                              # an underline running beneath the label itself
            if not sign:
                continue
        if rule is None and not has_colon and not sign:
            continue
        if rule is None and not sign and any(
                f.kind == "checkbox" and 0 < f.box[1] - ly < 3.2 * lh and f.box[0] < lx + 4 * lh for f in fields):
            continue                                 # a heading above a row of check boxes
        if rule is not None:
            ry, rx0, rx1 = rule
            x0f, x1f, rbase = rx0 / scale, rx1 / scale, ry / scale
        else:
            x0f, x1f, rbase = sx0 / scale, sx1 / scale, ly + lh
        if sign:
            hbox = max(3.2 * lh, 0.05 * H)
            fields.append(Field("signature", label, (x0f, rbase - hbox, x1f - x0f, hbox), lh))
        else:
            fields.append(Field("text", label, (x0f, rbase - 1.3 * lh, x1f - x0f, 1.3 * lh), lh))
    fields.sort(key=lambda f: (int((f.box[1] + f.box[3] / 2) // 25), f.box[0]))
    return fields, (W, H)


# ---------------------------------------------------------------- rendering

def find_font(style: str, size: float):
    from PIL import ImageFont
    import os
    dirs = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"]
    for name in FONTS.get(style, FONTS["Typed"]):
        for d in dirs:
            p = d / name
            if p.exists():
                try:
                    return ImageFont.truetype(str(p), max(6, int(round(size))))
                except OSError:
                    continue
    try:
        return ImageFont.load_default(size=max(6, int(round(size))))
    except TypeError:
        return ImageFont.load_default()


def signature_image(src):
    """Turn a signature picture (scan, photo or drawing) into ink on a transparent background."""
    from PIL import Image, ImageOps
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            alpha = rgba.getchannel("A")
            lum = rgba.convert("L")
            # Already transparent: keep its alpha, but pale ink on white still drops out.
            a = Image.eval(lum, lambda v: 255 if v < 128 else max(0, int((230 - v) * 255 / 102)))
            a = Image.composite(a, Image.new("L", rgba.size, 0), alpha) if alpha.getextrema()[0] < 255 else a
        else:
            lum = im.convert("L")
            a = Image.eval(lum, lambda v: 255 if v < 110 else max(0, int((225 - v) * 255 / 115)))
        out = Image.new("RGBA", a.size, (20, 30, 110, 0))
        out.putalpha(a)
        box = a.point(lambda v: 255 if v > 24 else 0).getbbox()
        return out.crop(box) if box else out


def render(path, fields: list[Field], style: str = "Typed", ink: str = "Blue",
           size_pct: float = 100.0, scale: float = 1.0, base=None, prescaled: bool = False):
    """The picture with every field's content drawn on. ``scale`` < 1 gives a quick preview."""
    from PIL import Image, ImageDraw
    img = (base if base is not None else load_image(path)).copy()
    if scale != 1.0 and not prescaled:
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
    img = img.convert("RGBA")
    draw = ImageDraw.Draw(img)
    color = INKS.get(ink, INKS["Blue"]) + (255,)
    for f in fields:
        x, y, w, h = (v * scale for v in f.box)
        if f.kind == "checkbox":
            if f.checked:
                t = max(2.0, 0.13 * min(w, h))
                pts = [(x + 0.18 * w, y + 0.55 * h), (x + 0.42 * w, y + 0.80 * h), (x + 0.88 * w, y + 0.18 * h)]
                draw.line(pts, fill=color, width=int(round(t)), joint="curve")
        elif f.kind == "signature":
            if f.sign_path and Path(f.sign_path).exists():
                sig = signature_image(f.sign_path)
                if sig.width and sig.height:
                    k = min((w * 0.96) / sig.width, (h * 0.96) / sig.height)
                    sig = sig.resize((max(1, round(sig.width * k)), max(1, round(sig.height * k))), Image.LANCZOS)
                    img.alpha_composite(sig, (int(x + 0.02 * w), int(y + h - sig.height)))
        elif f.value:
            size = max(8.0, (f.size or h / 1.3) * scale * size_pct / 100.0)
            font = find_font(style, size)
            avail = max(10.0, w - 4 * scale)
            while size > 8 and font.getlength(f.value) > avail:     # shrink to fit the line
                size -= 1
                font = find_font(style, size)
            draw.text((x + 3 * scale, y + h - 0.22 * size), f.value, font=font, fill=color, anchor="ls")
    return img.convert("RGB")


def output_path(src: Path) -> Path:
    ext = src.suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"):
        ext = ".png"
    n, out = 0, src.with_name(f"{src.stem} (filled){ext}")
    while out.exists():
        n += 1
        out = src.with_name(f"{src.stem} (filled {n}){ext}")
    return out


def save_filled(path, fields: list[Field], style="Typed", ink="Blue", size_pct=100.0) -> Path:
    src = Path(path)
    out = output_path(src)
    img = render(src, fields, style, ink, size_pct)
    kw = {"quality": 95} if out.suffix.lower() in (".jpg", ".jpeg", ".webp") else {}
    img.save(out, **kw)
    return out
