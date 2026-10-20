"""Minimal PDF writer: images (lossless, JPEG passthrough) and simple typeset documents."""

from __future__ import annotations

import zlib

from .raster import Raster

A4 = (595.28, 841.89)


class PdfWriter:
    def __init__(self):
        self.objects: list[bytes | None] = []
        self.pages: list[int] = []
        self.fonts: dict[str, int] = {}

    def add(self, body: bytes) -> int:
        self.objects.append(body)
        return len(self.objects)

    def reserve(self) -> int:
        self.objects.append(None)
        return len(self.objects)

    def stream(self, data: bytes, extra: str = "", compress: bool = True) -> int:
        if compress:
            data = zlib.compress(data, 6)
            extra += " /Filter /FlateDecode"
        return self.add(f"<< /Length {len(data)}{extra} >>\nstream\n".encode() + data
                        + b"\nendstream")

    def font(self, base: str) -> int:
        if base not in self.fonts:
            self.fonts[base] = self.add(
                f"<< /Type /Font /Subtype /Type1 /BaseFont /{base} "
                f"/Encoding /WinAnsiEncoding >>".encode())
        return self.fonts[base]

    def page(self, size, content: bytes, resources: str):
        contents = self.stream(content)
        self.pages.append(self.add(
            f"<< /Type /Page /Parent {{PAGES}} 0 R /MediaBox [0 0 {size[0]:.2f} {size[1]:.2f}] "
            f"/Resources << {resources} >> /Contents {contents} 0 R >>".encode()))

    def tobytes(self) -> bytes:
        pages_id = self.add(b"")
        kids = " ".join(f"{p} 0 R" for p in self.pages)
        self.objects[pages_id - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(self.pages)} >>".encode()
        for p in self.pages:
            self.objects[p - 1] = self.objects[p - 1].replace(b"{PAGES}", str(pages_id).encode())
        catalog = self.add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(self.objects, 1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(self.objects) + 1}\n0000000000 65535 f \n".encode()
        out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
        out += (f"trailer\n<< /Size {len(self.objects) + 1} /Root {catalog} 0 R "
                f"/Producer (ShapeShifter) >>\nstartxref\n{xref}\n%%EOF\n").encode()
        return bytes(out)


def _page_size(width: int, height: int, dpi: float | None) -> tuple[float, float]:
    scale = 72.0 / (dpi or 96.0)
    return width * scale, height * scale


def images_to_pdf(raster: Raster | None = None, jpeg: bytes | None = None,
                  jpeg_size: tuple[int, int, int] | None = None) -> bytes:
    """One page per frame. JPEG data is embedded as-is (no re-encoding)."""
    pdf = PdfWriter()
    if jpeg is not None:
        w, h, comps = jpeg_size
        cs = {1: "/DeviceGray", 3: "/DeviceRGB", 4: "/DeviceCMYK"}[comps]
        img = pdf.stream(jpeg, f" /Type /XObject /Subtype /Image /Width {w} /Height {h} "
                               f"/ColorSpace {cs} /BitsPerComponent 8 /Filter /DCTDecode"
                               + (" /Decode [1 0 1 0 1 0 1 0]" if comps == 4 else ""),
                         compress=False)
        size = _page_size(w, h, None)
        pdf.page(size, f"q {size[0]:.2f} 0 0 {size[1]:.2f} 0 0 cm /Im0 Do Q".encode(),
                 f"/XObject << /Im0 {img} 0 R >>")
        return pdf.tobytes()
    for frame in raster.frames:
        f = frame.to("L" if frame.mode in ("L", "LA") else "RGB")
        cs = "/DeviceGray" if f.mode == "L" else "/DeviceRGB"
        img = pdf.stream(f.data, f" /Type /XObject /Subtype /Image /Width {f.width} "
                                 f"/Height {f.height} /ColorSpace {cs} /BitsPerComponent 8")
        size = _page_size(f.width, f.height, raster.dpi)
        pdf.page(size, f"q {size[0]:.2f} 0 0 {size[1]:.2f} 0 0 cm /Im0 Do Q".encode(),
                 f"/XObject << /Im0 {img} 0 R >>")
    return pdf.tobytes()


# ---------------------------------------------------------------- text layout

# Helvetica widths (1/1000 em) for WinAnsi 32..126; other characters use 556.
_HELV = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556,
         556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722,
         722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722,
         667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556, 556, 278, 556,
         556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500,
         500, 334, 260, 334, 584]


def _width(text: str, size: float, mono: bool, bold: bool) -> float:
    if mono:
        return len(text) * 0.6 * size
    w = sum(_HELV[ord(c) - 32] if 32 <= ord(c) <= 126 else 556 for c in text)
    return w / 1000 * size * (1.05 if bold else 1.0)


def _pdf_string(text: str) -> str:
    data = text.encode("cp1252", "replace").decode("latin-1")
    return "(" + data.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"


def _wrap(text: str, width: float, size: float, mono: bool, bold: bool) -> list[str]:
    lines = []
    for para in text.split("\n"):
        words, line = para.split(" "), ""
        for word in words:
            trial = f"{line} {word}" if line else word
            if _width(trial, size, mono, bold) <= width or not line:
                line = trial
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


STYLES = {   # kind -> (font size, bold, mono, space before, indent)
    "h1": (22, True, False, 14, 0), "h2": (17, True, False, 12, 0), "h3": (14, True, False, 10, 0),
    "h4": (12, True, False, 8, 0), "h5": (11, True, False, 6, 0), "h6": (10, True, False, 6, 0),
    "p": (10.5, False, False, 6, 0), "li": (10.5, False, False, 2, 16),
    "oli": (10.5, False, False, 2, 16),
    "quote": (10.5, False, False, 6, 18), "code": (9, False, True, 6, 8),
    "table": (9.5, False, False, 6, 0), "hr": (1, False, False, 8, 0),
}


def _embed_image(pdf: PdfWriter, src: str, base_dirs) -> tuple[int, float, float] | None:
    """Add the referenced image as an XObject: (object id, width px, height px)."""
    from .images_util import load_image_ref, sniff, to_png_or_jpeg
    raw = load_image_ref(src, list(base_dirs or []))
    packed = to_png_or_jpeg(raw) if raw else None
    if not packed:
        return None
    kind, data = packed
    try:
        if kind == "jpg":
            from .jpeg import jpeg_info
            w, h, comps = jpeg_info(data)
            cs = {1: "/DeviceGray", 3: "/DeviceRGB", 4: "/DeviceCMYK"}[comps]
            obj = pdf.stream(data, f" /Type /XObject /Subtype /Image /Width {w} /Height {h} "
                                   f"/ColorSpace {cs} /BitsPerComponent 8 /Filter /DCTDecode"
                                   + (" /Decode [1 0 1 0 1 0 1 0]" if comps == 4 else ""),
                             compress=False)
            return obj, w, h
        from . import png
        frame = png.decode(data).first
        smask = ""
        if frame.has_alpha:
            alpha = frame.to("RGBA").data[3::4]
            sm = pdf.stream(alpha, f" /Type /XObject /Subtype /Image /Width {frame.width} "
                                   f"/Height {frame.height} /ColorSpace /DeviceGray /BitsPerComponent 8")
            smask = f" /SMask {sm} 0 R"
        rgb = frame.to("L" if frame.mode in ("L", "LA") else "RGB")
        cs = "/DeviceGray" if rgb.mode == "L" else "/DeviceRGB"
        obj = pdf.stream(rgb.data, f" /Type /XObject /Subtype /Image /Width {rgb.width} "
                                   f"/Height {rgb.height} /ColorSpace {cs} /BitsPerComponent 8{smask}")
        return obj, rgb.width, rgb.height
    except Exception:
        return None


def document_to_pdf(blocks: list[tuple], title: str = "", base_dirs=None) -> bytes:
    """Lay out (kind, text[, extra]) blocks on A4 pages: headings, paragraphs, lists, quotes,
    code, rules, tables and images. (Inline bold/italic and links need the PyMuPDF engine.)"""
    pdf = PdfWriter()
    fonts = {"R": pdf.font("Helvetica"), "B": pdf.font("Helvetica-Bold"), "M": pdf.font("Courier")}
    margin = 56
    W, H = A4
    usable = W - 2 * margin
    ops: list[str] = []
    page_images: dict[str, int] = {}
    y = H - margin

    def flush():
        nonlocal ops, y, page_images
        xo = (" /XObject << " + " ".join(f"/{k} {v} 0 R" for k, v in page_images.items()) + " >>"
              if page_images else "")
        res = "/Font << " + " ".join(f"/{k} {v} 0 R" for k, v in fonts.items()) + " >>" + xo
        pdf.page(A4, "\n".join(ops).encode("latin-1"), res)
        ops, y, page_images = [], H - margin, {}

    n_img = 0
    for item in blocks:
        kind, text = item[0], item[1]
        extra = item[2] if len(item) > 2 else None
        size, bold, mono, before, indent = STYLES.get(kind, STYLES["p"])
        if kind == "img":
            emb = _embed_image(pdf, extra or "", base_dirs)
            if emb is None:                      # not found / remote: show the alt text
                kind, text, extra = "p", f"[image: {text}]" if text else "[image]", None
            else:
                obj, wpx, hpx = emb
                w, h = wpx * 0.75, hpx * 0.75
                scale = min(1.0, usable / w, (H - 2 * margin) / h)
                w, h = w * scale, h * scale
                if y - 8 - h < margin and ops:
                    flush()
                n_img += 1
                name = f"Im{n_img}"
                page_images[name] = obj
                y -= 8 + h
                ops.append(f"q {w:.2f} 0 0 {h:.2f} {margin} {y:.2f} cm /{name} Do Q")
                continue
        if kind == "table":
            rows = extra or []
            if not rows:
                continue
            cols = max(len(r) for r in rows)
            weights = [max(6, max((len(r[c]) if c < len(r) else 0) for r in rows)) for c in range(cols)]
            weights = [min(w, 40) for w in weights]
            widths = [usable * w / sum(weights) for w in weights]
            pad, fsize = 4, 9.5
            y -= 8
            for ri, row in enumerate(rows):
                cells = [_wrap(row[c] if c < len(row) else "", widths[c] - 2 * pad, fsize, False, ri == 0)
                         for c in range(cols)]
                height = max(len(c) for c in cells) * fsize * 1.3 + 2 * pad
                if y - height < margin and ops:
                    flush()
                top = y
                x = margin
                for c in range(cols):
                    if ri == 0:
                        ops.append(f"0.93 g {x:.1f} {top - height:.1f} {widths[c]:.1f} {height:.1f} re f 0 g")
                    ops.append(f"0.55 G 0.5 w {x:.1f} {top - height:.1f} {widths[c]:.1f} {height:.1f} re S")
                    for li, line in enumerate(cells[c]):
                        ty = top - pad - fsize * (1.1 + 1.3 * li)
                        ops.append(f"BT /{'B' if ri == 0 else 'R'} {fsize} Tf {x + pad:.1f} {ty:.1f} Td "
                                   f"{_pdf_string(line)} Tj ET")
                    x += widths[c]
                y -= height
            y -= 6
            continue
        if kind == "hr":
            if y - 12 < margin:
                flush()
            y -= before
            ops.append(f"0.6 G 0.5 w {margin} {y:.1f} m {W - margin} {y:.1f} l S")
            y -= before
            continue
        leading = size * 1.35
        y -= before
        font = "M" if mono else ("B" if bold else "R")
        bullet = kind in ("li", "oli")
        mark = _pdf_string(extra if kind == "oli" and extra else chr(149))
        hang = 16 if kind == "oli" else 10
        for i, line in enumerate(_wrap(text, usable - indent - (hang if bullet else 0), size, mono, bold)):
            if y - leading < margin:
                flush()
            y -= leading
            x = margin + indent + (hang if bullet else 0)
            if bullet and i == 0:
                ops.append(f"BT /R {size} Tf {margin + indent:.1f} {y:.1f} Td {mark} Tj ET")
            if kind == "quote":
                ops.append(f"0.7 G 2 w {margin + 6} {y - 2:.1f} m {margin + 6} {y + size:.1f} l S")
            ops.append(f"BT /{font} {size} Tf {x:.1f} {y:.1f} Td {_pdf_string(line)} Tj ET")
    if ops or not pdf.pages:
        flush()
    return pdf.tobytes()
