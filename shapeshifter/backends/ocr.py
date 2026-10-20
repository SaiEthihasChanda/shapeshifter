"""OCR conversions: images -> text / Markdown / HTML / Word / searchable PDF, and scanned
PDFs -> searchable PDF. (Scanned PDF -> text/HTML falls back to OCR inside the PyMuPDF engine.)

The recogniser is Windows' built-in one (see shapeshifter/ocr.py).
"""

from __future__ import annotations

import io
import logging
import tempfile
from pathlib import Path

from .. import ocr
from ..core import Backend, Context, ConversionError, target_ext

log = logging.getLogger(__name__)


def _has_pymupdf() -> bool:
    from importlib.util import find_spec
    return find_spec("pymupdf") is not None


class OcrBackend(Backend):
    name = "ocr"
    base_cost = 4.0

    def available(self):
        from importlib.util import find_spec
        return ocr.available() and find_spec("PIL") is not None

    def sources(self):
        from .images import ImageBackend
        return set(ImageBackend().sources()) | ({"pdf"} if _has_pymupdf() else set())

    def targets(self, src):
        if src == "pdf":
            return {"pdfocr"}
        return {"txt", "md", "html", "docx"} | ({"pdfocr"} if _has_pymupdf() else set())

    def terminal(self, src, dst):
        return True               # recognised text is an end product, not a stepping stone

    def first_step_only(self, src, dst):
        # Only for a PDF the user actually has (a scan), never for one we just generated.
        return src == "pdf"

    def convert(self, src: Path, dst: Path, ctx: Context) -> list[Path]:
        s = ctx.src_fmt or target_ext(src)
        t = ctx.dst_fmt or target_ext(dst)
        if s == "pdf":
            return [self._scanned_pdf_to_searchable(src, dst, ctx)]
        ctx.progress(0.05)
        page, scale = ocr.recognize_file(src, ctx.cfg, ctx.cancel_event)
        ctx.progress(0.7)
        if not page.lines:
            raise ConversionError("No text was found in the image.")
        if t == "pdfocr":
            self._image_to_searchable_pdf(src, page, dst)
        else:
            self._write_document(page, dst, t, src.stem, ctx)
        ctx.progress(1.0)
        return [dst]

    # ------------------------------------------------------------- documents

    @staticmethod
    def _write_document(page: "ocr.Page", dst: Path, t: str, title: str, ctx: Context) -> None:
        from ..native import docs
        from ..native.docx import blocks_to_docx

        if t == "txt":
            dst.write_text("\n\n".join(page.paragraph_texts()) + "\n", encoding="utf-8")
            return
        # Escaped so recognised characters like * or _ never turn into formatting.
        blocks = [docs.Block(kind, docs.escape_markdown(text)) for kind, text in page.blocks()]
        if t == "md":
            dst.write_text(docs.to_markdown(blocks), encoding="utf-8")
        elif t == "html":
            dst.write_text(docs.to_html(blocks, title), encoding="utf-8")
        elif t == "docx":
            dst.write_bytes(blocks_to_docx(blocks, [], title))
        else:
            raise ConversionError(f"Can't write {t} from OCR")

    # ------------------------------------------------------- searchable PDFs

    @staticmethod
    def _font_for(text: str, fonts: dict):
        """Latin/Greek/Cyrillic text uses Helvetica; anything else the CJK fallback."""
        import pymupdf
        base = fonts.setdefault("helv", pymupdf.Font("helv"))
        if all(base.has_glyph(ord(c)) for c in text if not c.isspace()):
            return base
        return fonts.setdefault("cjk", pymupdf.Font("cjk"))

    @classmethod
    def overlay_text(cls, pdf_page, ocr_page: "ocr.Page") -> int:
        """Add an invisible text layer matching the recognised lines. Returns lines added."""
        import pymupdf
        sx = pdf_page.rect.width / ocr_page.width
        sy = pdf_page.rect.height / ocr_page.height
        tw = pymupdf.TextWriter(pdf_page.rect)
        fonts: dict = {}
        added = 0
        for ln in ocr_page.lines:
            text = ln.text.strip()
            x, y, w, h = ln.box
            if not text or w <= 0 or h <= 0:
                continue
            font = cls._font_for(text, fonts)
            width_pt, height_pt = w * sx, h * sy
            natural = font.text_length(text, fontsize=1.0)
            if natural <= 0:
                continue
            # Size the text so it spans the same width as the printed line.
            size = max(0.5 * height_pt, min(1.6 * height_pt, width_pt / natural))
            tw.append((x * sx, (y + h) * sy - 0.2 * height_pt), text, font=font, fontsize=size)
            added += 1
        if added:
            tw.write_text(pdf_page, render_mode=3)          # 3 = invisible
        return added

    @staticmethod
    def _page_size(width_px: int, height_px: int, dpi) -> tuple[float, float]:
        if dpi and dpi[0] and dpi[0] >= 72:
            return width_px * 72 / dpi[0], height_px * 72 / (dpi[1] or dpi[0])
        # No resolution recorded (phone photos, screenshots): fit an A4 sheet.
        long_pt, short_pt = 842.0, 595.0
        if width_px >= height_px:
            return long_pt, long_pt * height_px / width_px
        return short_pt, short_pt * height_px / width_px

    def _image_to_searchable_pdf(self, src: Path, ocr_page: "ocr.Page", dst: Path) -> None:
        import pymupdf
        from PIL import Image, ImageOps

        raw = src.read_bytes()
        with Image.open(io.BytesIO(raw)) as im:
            im.seek(0)
            dpi = im.info.get("dpi")
            rotated = ImageOps.exif_transpose(im)
            width, height = rotated.size
            keep_original = (im.format == "JPEG" and rotated.size == im.size and im.mode in ("RGB", "L"))
            if keep_original:
                image_bytes = raw                              # JPEGs go in untouched
            else:
                buf = io.BytesIO()
                if "A" in rotated.getbands() or rotated.mode == "P":
                    rotated.convert("RGBA").save(buf, "PNG")
                else:
                    rotated.convert("RGB").save(buf, "JPEG", quality=92)
                image_bytes = buf.getvalue()
        pw, ph = self._page_size(width, height, dpi)
        doc = pymupdf.open()
        page = doc.new_page(width=pw, height=ph)
        page.insert_image(page.rect, stream=image_bytes)
        self.overlay_text(page, ocr_page)
        doc.set_metadata({"title": src.stem, "producer": "ShapeShifter OCR"})
        doc.save(str(dst), garbage=3, deflate=True)
        doc.close()

    def _scanned_pdf_to_searchable(self, src: Path, dst: Path, ctx: Context) -> Path:
        import pymupdf
        try:
            doc = pymupdf.open(str(src))
        except Exception as e:
            raise ConversionError(f"Can't open document: {e}") from e
        with doc:
            if doc.needs_pass:
                raise ConversionError("The document is password protected.")
            todo = [i for i, p in enumerate(doc) if len(p.get_text().strip()) < 10
                    and (p.get_images() or p.get_drawings())]
            if not todo:
                raise ConversionError("Nothing to do: every page of this PDF already has searchable text.")
            with tempfile.TemporaryDirectory(prefix="ss-ocr-pdf-") as tmp:
                paths = []
                for k, i in enumerate(todo):
                    ctx.check_cancel()
                    p = Path(tmp) / f"p{i}.png"
                    doc[i].get_pixmap(matrix=pymupdf.Matrix(200 / 72, 200 / 72)).save(str(p))
                    paths.append(p)
                    ctx.progress(0.3 * (k + 1) / len(todo))
                pages = ocr.recognize(paths, ctx.cfg, ctx.cancel_event,
                                      progress=lambda f: ctx.progress(0.3 + 0.6 * f))
            added = 0
            for i, ocr_page in zip(todo, pages):
                added += self.overlay_text(doc[i], ocr_page)
            if not added:
                raise ConversionError("No text was found on the scanned pages.")
            doc.set_metadata({**(doc.metadata or {}), "producer": "ShapeShifter OCR"})
            doc.save(str(dst), garbage=3, deflate=True)
        ctx.progress(1.0)
        return dst
