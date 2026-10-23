"""Fill in a form that only exists as an image.

Detection (OCR + a little pixel analysis) finds the places a person writes: a label followed by a
blank line or space, small square check boxes, and signature lines. Rendering then writes typed
text, ticks and a signature image onto a copy of the picture, leaving everything else untouched.

Only Pillow is used for the pixels (no numpy), so it works in the packaged app.
"""

from __future__ import annotations

import functools
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import ocr

log = logging.getLogger(__name__)

DPI = 200                       # PDF pages are read (and written on) at this resolution
MAX_PAGE_SIDE = 3200            # ...but never larger than this many pixels
MAX_PAGES = 30
ANALYSIS_SIDE = 1600            # pixel analysis runs on a copy at most this big
DARK = 150                      # grey level below which a pixel counts as ink
BLANK_SIDE = 2400               # blank lines and table cells are looked for on a copy at most this big...
BLANK_DARK = 185                # ...where lighter grey counts as ink, so faint dotted leaders survive
MAX_LABEL_CHARS = 48
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
    page: int = 0                            # PDF page the field is on (0 for a picture)
    widget: int = 0                          # xref of a real PDF form field, filled natively
    font: str = ""                           # text fields: a font for this answer only ("" = the form's)
    scale: float = 1.0                       # text fields: this answer's size, times the form's


# ------------------------------------------------------------------ loading

def is_pdf(path) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(1024).lstrip(b"\x00\xef\xbb\xbf \r\n\t").startswith(b"%PDF-")
    except OSError:
        return False


def pdf_dpi(page) -> int:
    """Resolution to read a PDF page at: DPI, less for huge pages."""
    side = max(page.rect.width, page.rect.height) or 1.0
    return max(36, int(min(DPI, MAX_PAGE_SIDE * 72.0 / side)))


def render_pdf_page(page):
    from PIL import Image
    pix = page.get_pixmap(dpi=pdf_dpi(page), alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def load_image(path, page: int = 0):
    """A picture file, or one page of a PDF, as an RGB image."""
    from PIL import Image, ImageOps
    if is_pdf(path):
        import pymupdf
        with pymupdf.open(path) as doc:
            return render_pdf_page(doc[page])
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

    def __init__(self, image, scale: float, dark: int = DARK):
        from PIL import Image
        g = image.convert("L")
        if scale != 1.0:
            g = g.resize((max(1, round(g.width * scale)), max(1, round(g.height * scale))), Image.BILINEAR)
        self.w, self.h = g.size
        self.data = g.point(lambda v: 1 if v < dark else 0).tobytes()

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
    """A horizontal line inside the region: (y, x_start, x_end) or None. Prefers the line that starts
    nearest the left of the region (the one beside the label), then the lowest row of it."""
    found = None
    for y in range(max(0, y0), min(ink.h, y1)):
        for m in re.finditer(rb"\x01(?:\x01|\x00{1,2}(?=\x01))*", ink.row(y, x0, x1)):
            if m.end() - m.start() >= min_len:
                cand = (y, x0 + m.start(), x0 + m.end())
                if found is None or cand[1] <= found[1] + 3:
                    found = cand
                break
    return found


def _components(ink: _Ink, with_runs: bool = False):
    """Connected blobs of ink as (x0, y0, x1, y1, pixels), by joining overlapping runs of adjacent rows.
    ``with_runs`` adds a sixth item: the blob's own runs as (x0, x1, y)."""
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
    boxes: dict[int, list] = {}
    for idx, (a, b, y) in enumerate(runs):
        r = find(idx)
        bb = boxes.get(r)
        if bb is None:
            bb = boxes[r] = [a, y, b, y + 1, b - a] + ([[]] if with_runs else [])
        else:
            bb[0], bb[1] = min(bb[0], a), min(bb[1], y)
            bb[2], bb[3] = max(bb[2], b), max(bb[3], y + 1)
            bb[4] += b - a
        if with_runs:
            bb[5].append((a, b, y))
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
    text = re.sub(r"[\s_.\-–—:|,;]+$", "", re.sub(r"^[\s_.\-–—|]+", "", text)).strip()
    return "I" if text in ("1", "l", "|") else text      # "I, ________, son of": the OCR reads "I" as 1


def _same_row(a, b) -> bool:
    ay, ah = a[1], a[3]
    by, bh = b[1], b[3]
    overlap = min(ay + ah, by + bh) - max(ay, by)
    return overlap > 0.5 * min(ah, bh)


def _wordy(text: str) -> bool:
    return re.search(r"\w", text.replace("_", "")) is not None


def _solid_width(text: str, w: float) -> float:
    """Width of a word without trailing leader dots/underscores ("20......" -> the "20")."""
    core = re.sub(r"[_.…·\-]{2,}.*$", "", text)
    return w * len(core) / max(1, len(text))


def _blank_lines(ink: _Ink, words, k: float, comps=None) -> list[tuple[float, float, float, str]]:
    """Long horizontal places to write: rules, runs of underscores and dotted leaders (".......").
    Returns (baseline y, x0, x1, "dot" | "rule") in original pixels; underlines beneath words are left out."""
    W = ink.w
    thin = max(3, round(0.003 * ink.h))
    dot_max = max(3, round(0.0045 * W))
    marks = []                                       # (x0, x1, bottom y, kind)
    for x0, y0, x1, y1, px, *_ in comps if comps is not None else _components(ink):
        w, h = x1 - x0, y1 - y0
        if w <= dot_max and h <= dot_max and px >= 0.4 * w * h:
            marks.append((x0, x1, y1, "dot"))
        elif h <= thin and w >= max(6, 3 * h) and px >= 0.6 * w * h:
            marks.append((x0, x1, y1, "rule"))
    marks.sort()
    tol = max(2, round(0.001 * ink.h))
    chains: list[list] = []                          # [x0, x1, y, kind, count]
    for x0, x1, y, kind in marks:
        gap = max(5, 3 * (x1 - x0)) if kind == "dot" else max(8, round(0.008 * W))
        for c in reversed(chains):
            if c[3] == kind and abs(c[2] - y) <= tol and 0 <= x0 - c[1] <= gap:
                c[1], c[4] = max(c[1], x1), c[4] + 1
                break
        else:
            chains.append([x0, x1, y, kind, 1])
    min_len = max(40, 0.04 * W)
    out = []
    for x0, x1, y, kind, n in chains:
        if x1 - x0 < min_len or (kind == "dot" and n < 8):
            continue
        X0, X1, Y = x0 / k, x1 / k, y / k
        covered = 0.0
        for text, wx, wy, ww, wh in words:
            if not _wordy(text) or not (Y - 1.1 * wh <= wy + wh <= Y + 0.5 * wh):
                continue
            ww = _solid_width(text, ww)
            covered += max(0.0, min(X1, wx + ww) - max(X0, wx))
        if covered > 0.25 * (X1 - X0):
            continue                                 # an underlined heading or an already-written answer
        out.append((Y, X0, X1, kind))
    return out


def _grids(ink: _Ink, lh: float, comps=None) -> list[tuple]:
    """Blobs of thin lines the size of a table: (x0, y0, x1, y1, pixels, runs)."""
    return [c for c in (comps if comps is not None else _components(ink, with_runs=True))
            if c[2] - c[0] >= 0.15 * ink.w and c[3] - c[1] >= 2.2 * lh and c[4] <= 0.12 * (c[2] - c[0]) * (c[3] - c[1])]


def _without_grids(image, grids, k: float, lh: float):
    """A copy of the page with the table lines painted out, so the OCR reads the headings whole
    (letters like g, y and p that touch a rule otherwise get dropped). Only long straight stretches
    are removed, so the parts of letters that cross a rule stay."""
    from PIL import ImageDraw
    clean = image.convert("RGB")
    draw = ImageDraw.Draw(clean)
    long = max(6, 1.6 * lh)
    for x0, y0, x1, y1, px, runs in grids:
        w, h = x1 - x0, y1 - y0
        mask = bytearray(w * h)
        for a, b, y in runs:
            if b - a >= long:                        # a horizontal rule
                draw.rectangle((a / k - 1, y / k - 1, b / k + 1, (y + 1) / k + 1), fill="white")
            mask[(y - y0) * w + a - x0:(y - y0) * w + b - x0] = b"" * (b - a)
        mask = bytes(mask)
        for x in range(w):                           # vertical rules: tall stretches of one column
            col = mask[x:w * h:w]
            for m in re.finditer(rb"+", col):
                if m.end() - m.start() >= long:
                    draw.rectangle(((x0 + x) / k - 1, (y0 + m.start()) / k - 1,
                                    (x0 + x + 1) / k + 1, (y0 + m.end()) / k + 1), fill="white")
    return clean


def _table_cells(ink: _Ink, grids, words, k: float, lh: float) -> list[tuple[tuple, str]]:
    """Empty cells of ruled tables, as ((x, y, w, h) in original pixels, label from the row and column
    headings)."""
    lhs = lh * k
    out = []
    for x0, y0, x1, y1, px, runs in grids:
        w, h = x1 - x0, y1 - y0
        mask = bytearray(w * h)
        for a, b, y in runs:
            mask[(y - y0) * w + a - x0:(y - y0) * w + b - x0] = b"\x01" * (b - a)
        mask = bytes(mask)
        rows = [y for y in range(h) if _longest_run(mask[y * w:(y + 1) * w])[1] >= 0.25 * w]
        lines: list[list[int]] = []
        for y in rows:
            if lines and y - lines[-1][1] <= 2:
                lines[-1][1] = y
            else:
                lines.append([y, y])
        if len(lines) < 2:
            continue
        bands = [(a[1] + 1, b[0]) for a, b in zip(lines, lines[1:]) if b[0] - a[1] - 1 >= 0.9 * lhs]
        grid = []                                    # per band: (ya, yb, [(xa, xb), ...])
        for ya, yb in bands:
            need = 0.85 * (yb - ya)
            cols = [x for x in range(w) if sum(mask[ya * w + x:yb * w + x:w]) >= need]
            seps: list[list[int]] = []
            for x in cols:
                if seps and x - seps[-1][1] <= 2:
                    seps[-1][1] = x
                else:
                    seps.append([x, x])
            edges = ([] if seps and seps[0][0] <= 3 else [[-1, -1]]) + seps + \
                    ([] if seps and seps[-1][1] >= w - 4 else [[w, w]])
            grid.append((ya, yb, [(a[1] + 1, b[0]) for a, b in zip(edges, edges[1:]) if b[0] - a[1] > 2.5 * lhs]))

        def words_in(xa, ya, xb, yb):
            X0, Y0, X1, Y1 = (x0 + xa) / k, (y0 + ya) / k, (x0 + xb) / k, (y0 + yb) / k
            got = [(wx, t) for t, wx, wy, ww, wh in words
                   if _wordy(t) and X0 <= wx + ww / 2 <= X1 and Y0 <= wy + wh / 2 <= Y1]
            return _clean_label(" ".join(t for _, t in sorted(got)))

        for bi, (ya, yb, cells) in enumerate(grid):
            for ci, (xa, xb) in enumerate(cells):
                pad = 2
                inner = sum(sum(ink.row(y0 + y, x0 + xa + pad, x0 + xb - pad)) for y in range(ya + pad, yb - pad))
                if inner > 0.004 * (xb - xa) * (yb - ya) + 2 or words_in(xa, ya, xb, yb):
                    continue
                row = words_in(cells[0][0], ya, cells[0][1], yb) if ci > 0 else ""
                col = ""
                if bi > 0:
                    ta, tb, tcells = grid[0]
                    for txa, txb in tcells:
                        if min(xb, txb) - max(xa, txa) > 0.5 * (xb - xa):
                            col = words_in(txa, ta, txb, tb)
                label = " — ".join(p for p in (row, col) if p) or "Cell"
                box = ((x0 + xa + pad) / k, (y0 + ya + pad) / k, (xb - xa - 2 * pad) / k, (yb - ya - 2 * pad) / k)
                out.append((box, label))
    return out


def _box_label(words, box, others=()) -> tuple[str, list]:
    """The words beside a check box: whichever side's nearest word is closer ("Yes [ ]  No [ ]" or
    "[ ] Yes  [ ] No"). Returns (label, words used)."""
    bx, by, bw, bh = box
    row = sorted((w for w in words if _same_row(box, w[1:]) and _wordy(w[0])
                  and not any(o[0] - 2 <= w[1] + w[3] / 2 <= o[0] + o[2] + 2 for o in others)),
                 key=lambda w: w[1])
    right, edge = [], bx + bw
    for w in row:
        if w[1] < bx + bw - 2:
            continue
        if w[1] - edge > (2.6 * bh if not right else 1.2 * bh) or \
                any(edge <= o[0] <= w[1] for o in others):
            break
        right.append(w)
        edge = w[1] + w[3]
    left, edge = [], bx
    for w in reversed(row):
        if w[1] + w[3] / 2 >= bx - 2:
            continue
        if edge - (w[1] + w[3]) > (2.6 * bh if not left else 1.2 * bh) or \
                any(w[1] + w[3] <= o[0] + o[2] <= edge for o in others):
            break
        left.insert(0, w)
        edge = w[1]
    gap_r = right[0][1] - (bx + bw) if right else 1e9
    gap_l = bx - (left[-1][1] + left[-1][3]) if left else 1e9
    used = right if gap_r <= gap_l else left
    return _clean_label(" ".join(w[0] for w in used)), used


def _second_look(image, regions, cfg, cancel) -> list[list]:
    """OCR small parts of the page again, enlarged: the full-page OCR sometimes skips a short word
    on its own ("No", "is"). ``regions`` are (x0, y0, x1, y1) in page pixels, with optional boxes to
    blank out; returns each region's words in page pixels."""
    if not regions:
        return []
    import tempfile
    from PIL import Image, ImageDraw
    paths = []
    with tempfile.TemporaryDirectory(prefix="ss-form-") as tmp:
        geo = []
        for i, (x0, y0, x1, y1, *hide) in enumerate(regions):
            x0, y0, x1, y1 = max(0, int(x0)), max(0, int(y0)), min(image.width, int(x1)), min(image.height, int(y1))
            crop = image.crop((x0, y0, x1, y1)).convert("RGB")
            d = ImageDraw.Draw(crop)
            for hx, hy, hw, hh in hide:
                d.rectangle((hx - x0 - 2, hy - y0 - 2, hx - x0 + hw + 2, hy - y0 + hh + 2), fill="white")
            z = max(1.0, min(3.0, 48 / max(1, y1 - y0) * 1.6))
            big = crop.resize((max(1, round(crop.width * z)), max(1, round(crop.height * z))), Image.LANCZOS)
            pad = 60
            canvas = Image.new("RGB", (big.width + 2 * pad, big.height + 2 * pad), "white")
            canvas.paste(big, (pad, pad))
            paths.append(Path(tmp) / f"look{i}.png")
            canvas.save(paths[-1])
            geo.append((x0, y0, z, pad))
        try:
            pages = ocr.recognize(paths, cfg, cancel)
        except ocr.OcrUnavailable:
            return [[] for _ in regions]
    out = []
    for (x0, y0, z, pad), pg in zip(geo, pages):
        out.append([(wd.text, x0 + (wd.x - pad) / z, y0 + (wd.y - pad) / z, wd.w / z, wd.h / z)
                    for ln in pg.lines for wd in ln.words])
    return out


def _overlaps(box, fields: list[Field], share: float = 0.3) -> bool:
    x, y, w, h = box
    for f in fields:
        fx, fy, fw, fh = f.box
        ix = min(x + w, fx + fw) - max(x, fx)
        iy = min(y + h, fy + fh) - max(y, fy)
        if ix > 0 and iy > 0 and ix * iy > share * min(w * h, fw * fh):
            return True
    return False


def _left_label(words, y: float, x0: float, lh: float) -> str:
    """The words written just before a blank on its own line ("premises situated at ......")."""
    row = sorted((wx, t, ww) for t, wx, wy, ww, wh in words
                 if abs((wy + wh) - y) <= 0.7 * wh and wx + _solid_width(t, ww) <= x0 + 0.5 * lh)
    parts, edge = [], x0
    for wx, t, ww in reversed(row):
        right = wx + _solid_width(t, ww)
        if edge - right > 5 * lh:
            break
        if _wordy(t):
            parts.insert(0, re.sub(r"[_.…·]{2,}.*$", "", t))
        edge = wx
    label = _clean_label(re.sub(r"^\(?[A-Za-z0-9]{1,2}[.)]\s+", "", " ".join(parts)))   # "C. FROM THE"
    return " ".join(label.split()[-4:]) if len(label) > 28 else label


def _paper(image) -> int:
    """Grey level of the paper (the most common brightness), so grey scans get the right ink threshold."""
    g = image.convert("L")
    g.thumbnail((600, 600))
    hist = g.histogram()
    return max(range(96, 256), key=lambda v: sum(hist[max(0, v - 4):v + 5]))


def _skew(image) -> float:
    """Degrees to rotate the page (counter-clockwise) to make its lines level; 0 for a straight page."""
    from PIL import Image, ImageOps
    cut = 255 - 0.75 * _paper(image)                 # ink, on the inverted page
    g = ImageOps.invert(image.convert("L"))
    g.thumbnail((800, 800))
    g = g.point(lambda v: 255 if v > cut else 0)

    def score(a):
        r = g.rotate(a, resample=Image.BILINEAR, fillcolor=0)
        prof = r.resize((1, r.height), Image.BOX).tobytes()
        return sum(v * v for v in prof)

    best = max((a / 10 for a in range(-40, 41, 2)), key=score)
    return max((best + a / 100 for a in range(-20, 21, 4)), key=score)


def detect(path, cfg: dict | None = None, cancel=None) -> tuple[list[Field], tuple[int, int]]:
    """Find the fields of a form picture. Returns (fields, (width, height)).

    A photo or scan that is a little crooked is straightened first, so its lines read as lines; the
    fields found are then turned back onto the picture as it is."""
    import math
    image = load_image(path)
    W, H = image.size
    angle = _skew(image)
    if abs(angle) < 0.15:
        return _detect(image, path, cfg, cancel), (W, H)
    import tempfile
    from PIL import Image
    paper = _paper(image)
    straight = image.convert("RGB").rotate(angle, resample=Image.BICUBIC, fillcolor=(paper,) * 3)
    with tempfile.TemporaryDirectory(prefix="ss-form-") as tmp:
        shot = Path(tmp) / "straight.png"
        straight.save(shot)
        fields = _detect(straight, shot, cfg, cancel)
    # PIL turned the page counter-clockwise by `angle` about its centre; undo that for each field.
    a = math.radians(angle)
    cx, cy = W / 2, H / 2
    for f in fields:
        x, y, w, h = f.box
        # text is written from the left end of its line, so that end is the point that must stay on it
        ax = x + 0.5 * h if f.kind == "text" else x + w / 2
        dx, dy = ax - cx, y + h / 2 - cy
        ox = cx + dx * math.cos(a) - dy * math.sin(a)
        oy = cy + dx * math.sin(a) + dy * math.cos(a)
        f.box = (ox - (ax - x), oy - h / 2, w, h)
    return fields, (W, H)


def _detect(image, path, cfg, cancel) -> list[Field]:
    W, H = image.size
    paper = _paper(image)
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
    ink = _Ink(image, scale, round(DARK * paper / 255))
    fields: list[Field] = []
    later: list[Field] = []                          # "Label:" + free space: kept unless a blank line is there
    used_lines: set[int] = set()

    # ---- check boxes first, so their labels aren't mistaken for text fields
    all_words = [w for ln in lines for w in ln[5]]
    boxes = [(x0 / scale, y0 / scale, (x1 - x0) / scale, (y1 - y0) / scale)
             for (x0, y0, x1, y1, px) in _components(ink) if _is_square_box(ink, x0, y0, x1, y1, px)]
    unnamed: list[Field] = []
    for box in boxes:
        others = [o for o in boxes if o is not box]
        label, used = _box_label(all_words, box, others)
        for i, ln in enumerate(lines):
            if any(w in ln[5] for w in used):
                used_lines.add(i)
        f = Field("checkbox", label or "Check box", box)
        fields.append(f)
        if not label:
            unnamed.append(f)

    # ---- text and signature fields: a label, then a rule or free space to its right
    for i, (text, lx, ly, lw, lh, words) in enumerate(lines):
        if i in used_lines or len(text.split()) > MAX_LABEL_WORDS:
            continue
        label = _clean_label(re.sub(r"[_.]{3,}.*$", "", text))
        if not label or len(label) < 2 or len(label) > MAX_LABEL_CHARS:
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
        if sign and not has_colon:
            # "Signature of applicant" printed under a line or an empty space: the signature goes above it
            above = _find_rule(ink, int((lx - lh) * scale), int((lx + lw + lh) * scale),
                               int((ly - 1.8 * lh) * scale), int(ly * scale), min_len=int(max(40, 0.6 * lw) * scale))
            if above is not None:
                ry, rx0, rx1 = above
                hbox = max(3.2 * lh, 0.05 * H)
                fields.append(Field("signature", label, (rx0 / scale, ry / scale - hbox, (rx1 - rx0) / scale, hbox), lh))
                continue
            bx0, bx1 = lx, lx + max(lw, 0.22 * W)
            ay1 = int((ly - 0.3 * lh) * scale)
            ay0 = ay1
            while ay0 > max(0, ay1 - max(3.2 * lh, 0.05 * H) * scale) and \
                    not sum(ink.row(ay0 - 1, int(bx0 * scale), int(bx1 * scale))):
                ay0 -= 1                             # climb through the clear space above the caption
            clear = (ay1 - ay0) / scale - 0.3 * lh   # keep off whatever is above
            if clear >= 1.8 * lh:
                fields.append(Field("signature", label, (bx0, ly - 0.3 * lh - clear, bx1 - bx0, clear), lh))
                continue
            if not (sx1 - sx0 >= 4 * lh * scale and _find_rule(
                    ink, sx0, min(sx1, sx0 + int(3 * lh * scale)), int((ly + 0.55 * lh) * scale),
                    int((ly + 1.9 * lh) * scale), min_len=int(lh * scale))):
                continue                                 # no room above and no line beside it
        if sx1 - sx0 < max(40 * scale, 4 * lh * scale):
            continue
        rule = _find_rule(ink, sx0, sx1, int((ly + 0.55 * lh) * scale), int((ly + 1.9 * lh) * scale),
                          min_len=int(max(60, 3.5 * lh) * scale))
        if rule is not None and rule[1] - sx0 > 0.3 * ink.w:
            limit = min(limit, rule[1] / scale - 0.5 * lh)       # a line further along belongs to something else
            sx1 = int(limit * scale)
            rule = None
            if sx1 - sx0 < 4 * lh * scale:
                continue
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
            (fields if rule is not None else later).append(
                Field("signature", label, (x0f, rbase - hbox, x1f - x0f, hbox), lh))
        else:
            (fields if rule is not None else later).append(
                Field("text", label, (x0f, rbase - 1.3 * lh, x1f - x0f, 1.3 * lh), lh))

    # ---- blanks with no "Label:" in front: dotted leaders, continuation lines and empty table cells
    words = [w for (_, _, _, _, _, ws) in lines for w in ws]
    heights = sorted(ln[4] for ln in lines) or [0.02 * H]
    lh_typ = heights[len(heights) // 2]
    k = min(1.0, BLANK_SIDE / max(W, H))
    fine = _Ink(image, k, round(BLANK_DARK * paper / 255))
    comps = _components(fine, with_runs=True)
    grids = _grids(fine, lh_typ * k, comps)
    cell_words = words
    if grids:
        import tempfile
        with tempfile.TemporaryDirectory(prefix="ss-form-") as tmp:
            shot = Path(tmp) / "tables.png"
            _without_grids(image, grids, k, lh_typ * k).save(shot)
            try:
                page2, s2 = ocr.recognize_file(shot, cfg, cancel)
                cell_words = [(wd.text, wd.x / s2, wd.y / s2, wd.w / s2, wd.h / s2)
                              for ln in page2.lines for wd in ln.words] or words
            except ocr.OcrUnavailable:
                pass
    for box, label in _table_cells(fine, grids, cell_words, k, lh_typ):
        if _overlaps(box, fields):
            continue
        x, y, w, h = box
        if label == "Cell":                          # a lone box: named by the words just above it
            label = next((_clean_label(ln[0]) for ln in lines if 0 <= y - (ln[2] + ln[4]) < 1.5 * ln[4]
                          and ln[1] < x + w and ln[1] + ln[3] > x and len(ln[0]) <= MAX_LABEL_CHARS), "") or "Box"
        size = min(lh_typ, 0.75 * h)
        if h > 2.4 * size:                           # a big box: the answer starts on its first line
            box = (x + 0.3 * size, y + 0.25 * size, w - 0.6 * size, 1.5 * size)
        elif h > 1.45 * size:                        # a table cell: the answer in the middle of it
            box = (x, y + (h - 1.35 * size) / 2, w, 1.35 * size)
        fields.append(Field("text", label, box, size))
    blanks = [b for b in sorted(_blank_lines(fine, words, k, comps))
              if not _overlaps((b[1], b[0] - 1.45 * lh_typ, b[2] - b[1], 1.3 * lh_typ), fields)]
    lefts = [_left_label(words, y, x0, lh_typ) for y, x0, x1, kind in blanks]
    looks, wants = [], []
    for i, (y, x0, x1, kind) in enumerate(blanks):
        # printed ink just before the blank that the OCR returned no word for
        gx0, gx1 = int((x0 - 2.5 * lh_typ) * k), int((x0 - 0.3 * lh_typ) * k)
        if not lefts[i] and gx1 > 0 and sum(sum(fine.row(yy, gx0, gx1)) for yy in
                                             range(int((y - 1.1 * lh_typ) * k), int((y - 0.2 * lh_typ) * k))) > 6:
            looks.append((x0 - 9 * lh_typ, y - 1.8 * lh_typ, x0 + 0.5 * lh_typ, y + 0.5 * lh_typ))
            wants.append(("blank", i))
    for f in unnamed:
        x, y, w, h = f.box
        looks.append((x - 7 * h, y - 0.8 * h, x + w + 7 * h, y + 1.8 * h, *[o.box for o in fields if o.kind == "checkbox"]))
        wants.append(("box", f))
    for (what, ref), got in zip(wants, _second_look(image, looks, cfg, cancel)):
        if what == "blank":
            y, x0 = blanks[ref][0], blanks[ref][1]
            lefts[ref] = _left_label(got, y, x0, lh_typ)
        else:
            others = [o.box for o in fields if o.kind == "checkbox" and o is not ref]
            ref.label = _box_label(got, ref.box, others)[0] or ref.label
    prev: Field | None = None
    prev_base = ""
    parts_on_row = 1
    for (y, x0, x1, kind), label in zip(blanks, lefts):
        box = (x0, y - 1.45 * lh_typ, x1 - x0, 1.3 * lh_typ)     # written just clear of the dots
        if _overlaps(box, fields):
            continue
        if not label and kind == "rule" and x1 - x0 > 0.75 * W and not any(_wordy(t) and abs(wy - y) < 3 * lh_typ
                                                       for t, wx, wy, ww, wh in words):
            continue                                 # a long ruled line on its own: a separator
        hint = next((_clean_label(ln[0].strip("() ")) for ln in lines           # "(full address)" under it
                     if re.fullmatch(r"\([^()]{2,40}\)", ln[0]) and 0 < ln[2] - y < 2.0 * ln[4] and x0 <= ln[1] + ln[3] / 2 <= x1), "")
        if label and len(label) <= 3 and not hint and label.lower() not in ("i", "20", "19"):
            # "...and my mobile number / is ______": finish the sentence from the line above
            tail = [ln for ln in lines if 0.6 * ln[4] < y - (ln[2] + ln[4]) < 2.6 * ln[4]]
            if tail:                                 # the nearest line above, where it ends (OCR may split it)
                near = max(ln[2] for ln in tail)
                end = max((ln for ln in tail if near - ln[2] < 0.5 * ln[4]), key=lambda ln: ln[1] + ln[3])
                prior = [t for t in end[0].split() if _wordy(t)][-3:]
                label = _clean_label(" ".join(prior + [label]))
        base = label
        prev_line = prev.box[1] + prev.box[3] + 0.15 * lh_typ if prev is not None else 0.0
        if label:
            if hint:
                label = hint if len(label) <= 3 else f"{label} ({hint})"
            parts_on_row = 1
        elif prev is not None and abs(y - prev_line) < 0.8 * lh_typ:
            parts_on_row += 1                        # "____ / ____ / ________": parts of one answer
            base, label = prev_base, f"{prev_base} ({parts_on_row})"
        elif prev is not None and 0 < y - prev_line < 3.5 * lh_typ:
            base = prev_base                         # the second line of the blank above
            label = f"{base} ({hint})" if hint else f"{base} (cont.)"
            parts_on_row = 1
        else:
            label = base = hint or "Text"
            parts_on_row = 1
        if SIGN_WORDS.search(label):
            hbox = max(3.2 * lh_typ, 0.05 * H)
            f = Field("signature", label, (x0, y - hbox, x1 - x0, hbox), lh_typ)
        else:
            f = Field("text", label, box, lh_typ)
        fields.append(f)
        prev, prev_base = f, base
    fields += [f for f in later if not _overlaps(f.box, fields)]
    row = max(8.0, 0.7 * lh_typ)                     # reading order: by the line each answer sits on
    fields.sort(key=lambda f: (int((f.box[1] + f.box[3]) // row), f.box[0]))
    sizes = sorted(f.size for f in fields if f.kind == "text" and f.size)
    if sizes:                                       # one size for every answer on the page
        typical = sizes[len(sizes) // 2]
        for f in fields:
            if f.kind == "text":
                f.size = typical
    return fields


def _widget_fields(page, k: float, index: int) -> list[Field]:
    """The real (fillable) form fields of a PDF page, as Field objects in page-image pixels."""
    import pymupdf as mu
    out: list[Field] = []
    for w in list(page.widgets() or []):
        r = w.rect * page.rotation_matrix
        box = (r.x0 * k, r.y0 * k, r.width * k, r.height * k)
        label = (w.field_label or w.field_name or "").strip()
        t = w.field_type
        if t in (mu.PDF_WIDGET_TYPE_TEXT, mu.PDF_WIDGET_TYPE_COMBOBOX, mu.PDF_WIDGET_TYPE_LISTBOX):
            out.append(Field("text", label, box, box[3] * 0.7, value=str(w.field_value or ""),
                             page=index, widget=w.xref))
        elif t in (mu.PDF_WIDGET_TYPE_CHECKBOX, mu.PDF_WIDGET_TYPE_RADIOBUTTON):
            on = w.field_value not in (None, "", "Off", False)
            name = label
            if t == mu.PDF_WIDGET_TYPE_RADIOBUTTON:
                state = w.on_state()
                name = f"{label}: {state}" if isinstance(state, str) and state not in ("Off", "") else label
            out.append(Field("checkbox", name, box, checked=bool(on), page=index, widget=w.xref))
        elif t == mu.PDF_WIDGET_TYPE_SIGNATURE:
            out.append(Field("signature", label or "Signature", box, box[3] * 0.4, page=index))
    return out


def detect_document(path, cfg: dict | None = None, cancel=None, progress=None):
    """Fields of a picture or every page of a PDF: (fields, [(width, height) per page]).

    A PDF page with real form fields uses those; any other page is read like a picture."""
    if not is_pdf(path):
        fields, size = detect(path, cfg, cancel)
        return fields, [size]
    import tempfile

    import pymupdf
    from .core import ConversionError
    try:
        doc = pymupdf.open(path)
    except Exception as e:                           # noqa: BLE001
        raise ConversionError(f"Couldn't open this PDF: {e}") from e
    with doc:
        if doc.needs_pass:
            raise ConversionError("This PDF is password-protected, so it can't be filled in.")
        n = min(len(doc), MAX_PAGES)
        fields: list[Field] = []
        sizes: list[tuple[int, int]] = []
        with tempfile.TemporaryDirectory(prefix="ss-form-") as tmp:
            for i in range(n):
                if cancel is not None and cancel.is_set():
                    from .core import Cancelled
                    raise Cancelled()
                page = doc[i]
                k = pdf_dpi(page) / 72.0
                img = render_pdf_page(page)
                sizes.append(img.size)
                native = _widget_fields(page, k, i)
                if native:
                    fields += native
                else:
                    shot = Path(tmp) / f"page{i}.png"
                    img.save(shot)
                    found, _ = detect(shot, cfg, cancel)
                    for f in found:
                        f.page = i
                    fields += found
                if progress:
                    progress(i + 1, n)
    return fields, sizes


# ---------------------------------------------------------------- rendering

FONT_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
_STYLE_WORDS = re.compile(r"\b(bold|italic|oblique|light|semibold|semilight|semi|black|thin|medium|"
                          r"condensed|narrow|demi|demibold|heavy|extra|ultra|extralight|extrabold)\b", re.I)
_installed: dict[str, str] | None = None


def installed_fonts() -> dict[str, str]:
    """Every font installed in Windows (its regular style): display name -> font file."""
    global _installed
    if _installed is not None:
        return _installed
    found: dict[str, str] = {}
    try:
        import winreg
    except ImportError:                              # not Windows: only the built-in styles
        winreg = None
    if winreg is not None:
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                key = winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts")
            except OSError:
                continue
            with key:
                i = 0
                while True:
                    try:
                        name, file, _ = winreg.EnumValue(key, i)
                    except OSError:
                        break
                    i += 1
                    name = re.sub(r"\s*\((TrueType|OpenType)\)\s*$", "", str(name)).split(" & ")[0].strip()
                    file = Path(str(file))
                    if file.suffix.lower() not in (".ttf", ".otf", ".ttc") or not name or _STYLE_WORDS.search(name):
                        continue
                    path = file if file.is_absolute() else FONT_DIR / file
                    if name not in found and path.exists():
                        found[name] = str(path)
    _installed = dict(sorted(found.items(), key=lambda kv: kv[0].lower()))
    return _installed


def font_names() -> list[str]:
    """What the font lists offer: the three writing styles, then every installed font."""
    return list(FONTS) + [n for n in installed_fonts() if n not in FONTS]


@functools.lru_cache(maxsize=512)
def _truetype(path: str, px: int):
    from PIL import ImageFont
    return ImageFont.truetype(path, px)


def find_font(style: str, size: float):
    """A writing style ("Typed", "Handwriting", "Typewriter") or any installed font, at ``size`` pixels."""
    from PIL import ImageFont
    px = max(6, int(round(size)))
    paths = [FONT_DIR / n for n in FONTS[style]] if style in FONTS else \
        [Path(p) for p in [installed_fonts().get(style)] if p] + [FONT_DIR / n for n in FONTS["Typed"]]
    for p in paths:
        if p.exists():
            try:
                return _truetype(str(p), px)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=px)
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


def _paint(img, fields: list[Field], style: str, ink: str, size_pct: float, scale: float) -> None:
    """Draw every field's content onto an RGBA image (in place)."""
    from PIL import Image, ImageDraw
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
            face = f.font or style
            size = max(8.0, (f.size or h / 1.3) * scale * size_pct / 100.0 * (f.scale or 1.0))
            font = find_font(face, size)
            avail = max(10.0, w - 4 * scale)
            while size > 8 and font.getlength(f.value) > avail:     # shrink to fit the line
                size -= 1
                font = find_font(face, size)
            draw.text((x + 3 * scale, y + h - 0.22 * size), f.value, font=font, fill=color, anchor="ls")


def render(path, fields: list[Field], style: str = "Typed", ink: str = "Blue",
           size_pct: float = 100.0, scale: float = 1.0, base=None, prescaled: bool = False,
           page: int = 0):
    """The picture (or one PDF page) with the given fields drawn on. ``scale`` < 1 gives a quick preview."""
    from PIL import Image
    img = (base if base is not None else load_image(path, page)).copy()
    if scale != 1.0 and not prescaled:
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
    img = img.convert("RGBA")
    _paint(img, fields, style, ink, size_pct, scale)
    return img.convert("RGB")


def output_path(src: Path) -> Path:
    ext = src.suffix.lower()
    if is_pdf(src):
        ext = ".pdf"
    elif ext not in (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"):
        ext = ".png"
    n, out = 0, src.with_name(f"{src.stem} (filled){ext}")
    while out.exists():
        n += 1
        out = src.with_name(f"{src.stem} (filled {n}){ext}")
    return out


def save_filled_pdf(path, fields: list[Field], style="Typed", ink="Blue", size_pct=100.0) -> Path:
    """Write the answers into a copy of the PDF: real form fields are filled natively, anything
    else (blanks found by reading the page, check boxes, signatures) is laid over the page."""
    import io

    import pymupdf
    from PIL import Image
    src = Path(path)
    out = output_path(src)
    by_page: dict[int, list[Field]] = {}
    for f in fields:
        by_page.setdefault(f.page, []).append(f)
    with pymupdf.open(src) as doc:
        for index, fs in sorted(by_page.items()):
            page = doc[index]
            native = {f.widget: f for f in fs if f.widget}
            if native:
                for w in list(page.widgets() or []):
                    f = native.get(w.xref)
                    if f is None:
                        continue
                    if f.kind == "text":
                        w.field_value = f.value
                    else:
                        state = w.on_state()
                        w.field_value = (state if isinstance(state, str) else True) if f.checked else (
                            "Off" if isinstance(state, str) else False)
                    w.update()
            drawn = [f for f in fs if not f.widget]
            if not any(f.value or f.checked or f.sign_path for f in drawn):
                continue
            k = pdf_dpi(page) / 72.0
            canvas = Image.new("RGBA", (max(1, round(page.rect.width * k)), max(1, round(page.rect.height * k))),
                               (0, 0, 0, 0))
            _paint(canvas, drawn, style, ink, size_pct, 1.0)
            rect = page.rect
            if page.rotation:           # the fields were placed on the page as displayed
                canvas = canvas.rotate(page.rotation, expand=True)
                rect = pymupdf.Rect(page.rect * page.derotation_matrix)
                rect.normalize()
            buf = io.BytesIO()
            canvas.save(buf, "PNG")
            page.insert_image(rect, stream=buf.getvalue(), overlay=True)
        doc.save(out, garbage=3, deflate=True)
    return out


def save_filled(path, fields: list[Field], style="Typed", ink="Blue", size_pct=100.0) -> Path:
    src = Path(path)
    if is_pdf(src):
        return save_filled_pdf(src, fields, style, ink, size_pct)
    out = output_path(src)
    img = render(src, fields, style, ink, size_pct)
    kw = {"quality": 95} if out.suffix.lower() in (".jpg", ".jpeg", ".webp") else {}
    img.save(out, **kw)
    return out
