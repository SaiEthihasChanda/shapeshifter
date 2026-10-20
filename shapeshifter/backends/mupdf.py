"""PDF, SVG, e-book and HTML conversions with PyMuPDF.

HTML, Markdown and plain text become PDFs through MuPDF's Story layout engine, which keeps
images (files and data: URIs), tables, links, styling and non-Latin text. PDFs become
HTML (with embedded images), SVG (vectors kept) or raster images; scanned pages are
recognised with OCR when the text layer is empty.
"""

from __future__ import annotations

import html as html_lib
import logging
from pathlib import Path

from .base import Backend, Context, ConversionError, target_ext

log = logging.getLogger(__name__)

EBOOKS = {"epub", "mobi", "fb2", "xps", "oxps", "cbz"}
LAYOUT_SOURCES = {"html", "md", "txt"}           # laid out by Story
SOURCES = {"pdf", "svg"} | EBOOKS | LAYOUT_SOURCES

TARGETS = {
    "pdf": {"png", "jpg", "webp", "tiff", "txt", "html", "svg"},
    "svg": {"png", "jpg", "webp", "pdf", "ico"},
}
for _e in EBOOKS:
    TARGETS[_e] = {"pdf"} if _e == "cbz" else {"pdf", "txt"}   # comics have no text layer
for _s in LAYOUT_SOURCES:
    TARGETS[_s] = {"pdf"}

PAGE_CSS = """
body { font-family: sans-serif; font-size: 10.5pt; line-height: 1.4; }
h1 { font-size: 20pt; } h2 { font-size: 16pt; } h3 { font-size: 13pt; }
h4, h5, h6 { font-size: 11pt; }
code, pre { font-family: monospace; }
pre { background-color: #f3f3f3; padding: 6pt; }
code { background-color: #f3f3f3; }
blockquote { margin-left: 14pt; padding-left: 8pt; color: #555555; border-left: 2px solid #bbbbcc; }
table { border-collapse: collapse; margin: 6pt 0; }
th, td { border: 1px solid #888888; padding: 3pt 6pt; }
th { background-color: #eeeeee; }
a { color: #0645ad; }
img { max-width: 100%; }
"""


_LIGATURES = {"FB00": "00660066", "FB01": "00660069", "FB02": "0066006C",
              "FB03": "006600660069", "FB04": "00660066006C"}


def _rewrite_cmap(cmap: str) -> str | None:
    """A ToUnicode CMap with ligature targets expanded to letter sequences, or None if the map
    has none (or uses a form we don't handle). Ranges are expanded to single entries first:
    `<6d> <6e> <fb01>` means glyph 6d -> U+FB01 *and* 6e -> U+FB02, so changing only the
    range's start would corrupt the next glyph."""
    import re
    if not re.search(r"<fb0[0-4]>", cmap, re.I):
        return None
    hexpair = r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>"
    mapping: dict[int, str] = {}
    for block in re.findall(r"beginbfchar(.*?)endbfchar", cmap, re.S):
        for a, b in re.findall(hexpair, block):
            mapping[int(a, 16)] = b.upper()
    for block in re.findall(r"beginbfrange(.*?)endbfrange", cmap, re.S):
        if "[" in block:
            return None
        for a, b, c in re.findall(hexpair + r"\s*<([0-9A-Fa-f]+)>", block):
            lo, hi, dst = int(a, 16), int(b, 16), int(c, 16)
            if len(c) > 4:
                return None
            for k in range(hi - lo + 1):
                mapping[lo + k] = f"{dst + k:04X}"
    for code, dst in mapping.items():
        mapping[code] = _LIGATURES.get(dst, dst)
    space = re.search(r"begincodespacerange\s*<([0-9A-Fa-f]+)>", cmap)
    width = len(space.group(1)) if space else 4
    start = re.search(r"\d+\s+beginbf(?:range|char)", cmap)
    end = cmap.find("endcmap")
    if not start or end < 0:
        return None
    entries = [f"<{code:0{width}X}> <{dst}>" for code, dst in sorted(mapping.items())]
    blocks = [f"{len(chunk)} beginbfchar\n" + "\n".join(chunk) + "\nendbfchar\n"
              for chunk in (entries[i:i + 100] for i in range(0, len(entries), 100))]
    return cmap[:start.start()] + "".join(blocks) + cmap[end:]


def _expand_ligatures(doc) -> int:
    """MuPDF's layout maps ligature glyphs (fi, fl, ff…) to single characters (U+FB01…) in each
    font's ToUnicode table, so copying "first" pastes "ﬁrst" and searching for it can miss.
    Rewrite those maps to the plain letter sequences. Returns how many fonts were changed."""
    changed = 0
    seen = set()
    for page in doc:
        for font in page.get_fonts():
            kind, ref = doc.xref_get_key(font[0], "ToUnicode")
            if kind != "xref":
                continue
            xref = int(ref.split()[0])
            if xref in seen:
                continue
            seen.add(xref)
            new = _rewrite_cmap(doc.xref_stream(xref).decode("latin-1"))
            if new is not None:
                doc.update_stream(xref, new.encode("latin-1"))
                changed += 1
    return changed


MAX_LAYOUT_PAGES = 3000


def html_to_pdf(html: str, dst: Path, resource_dirs, title: str = "") -> int:
    """Lay HTML out on A4 pages with MuPDF's Story engine. Returns the page count."""
    import pymupdf

    archive = pymupdf.Archive()
    for d in resource_dirs or []:
        try:
            archive.add(str(d))
        except Exception:
            log.warning("Can't use %s for document resources", d)
    story = pymupdf.Story(html=html, user_css=PAGE_CSS, archive=archive)
    media = pymupdf.paper_rect("a4")
    where = media + (56, 56, -56, -56)
    stalled = [0]

    def page(num, filled):
        # If a page takes nothing (a picture or table row too big to place), the layout would repeat
        # forever and eat memory: stop with a clear message instead.
        height = (filled[3] - filled[1]) if filled else 0.0
        if num > 0 and height < 1:
            stalled[0] += 1
            if stalled[0] >= 3:
                raise ConversionError("Part of this document can't be laid out on a page "
                                      "(a table row or picture is too large).")
        else:
            stalled[0] = 0
        if num > MAX_LAYOUT_PAGES:
            raise ConversionError(f"The document is too long to lay out (over {MAX_LAYOUT_PAGES} pages).")
        return media, where, pymupdf.Identity
    doc = story.write_with_links(page)
    try:
        _expand_ligatures(doc)
        doc.set_metadata({"title": title, "producer": "ShapeShifter"})
        doc.save(str(dst), garbage=3, deflate=True)
        return doc.page_count
    finally:
        doc.close()


def _read_html(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeError:
            continue
    return raw.decode("utf-8", "replace")


class MuPDFBackend(Backend):
    name = "mupdf"
    base_cost = 2.0

    def multi_output(self, src, dst):
        return (src != "svg" and dst in ("png", "jpg", "webp")) or (src == "pdf" and dst == "svg")

    def terminal(self, src, dst):
        return dst in ("png", "jpg", "webp", "tiff", "ico", "svg") or (src == "svg" and dst == "pdf")

    def first_step_only(self, src, dst):
        return src == "pdf" and dst == "txt"   # a PDF made from images has no text

    def cost(self, src, dst):
        # Laying out HTML/Markdown/text with Story beats the basic native writer.
        return 1.0 if src in LAYOUT_SOURCES else self.base_cost

    def available(self):
        from importlib.util import find_spec
        return find_spec("pymupdf") is not None

    def sources(self):
        return SOURCES

    def targets(self, src):
        return TARGETS.get(src, set())

    def convert(self, src: Path, dst: Path, ctx: Context) -> list[Path]:
        import pymupdf as fitz

        tgt = ctx.dst_fmt or target_ext(dst)
        s = ctx.src_fmt or target_ext(src)
        if s in LAYOUT_SOURCES:
            return self._layout(src, dst, s, ctx)
        try:
            doc = fitz.open(str(src))
        except Exception as e:
            raise ConversionError(f"Can't open document: {e}") from e
        with doc:
            if doc.needs_pass:
                raise ConversionError("The document is password protected.")
            n = doc.page_count
            if tgt == "pdf":
                pdf_bytes = doc.convert_to_pdf()
                dst.write_bytes(pdf_bytes)
                ctx.progress(1.0)
                return [dst]
            if tgt == "txt":
                return [self._to_text(doc, dst, ctx)]
            if tgt == "html":
                return [self._to_html(doc, src, dst, ctx)]
            if tgt == "svg":
                return self._to_svg(doc, dst, ctx)
            return self._render(doc, src, dst, tgt, ctx)

    # ------------------------------------------------------- HTML/MD/TXT -> PDF

    def _layout(self, src: Path, dst: Path, s: str, ctx: Context) -> list[Path]:
        from ..native import docs

        raw = _read_html(src)
        title = src.stem
        if s == "html":
            html = raw
            title = docs.html_title(raw) or title
        else:
            blocks = docs.parse_markdown(raw) if s == "md" else docs.parse_text(raw)
            html = docs.to_html(blocks, title, css=False)
        ctx.progress(0.3)
        html_to_pdf(html, dst, ctx.resource_dirs or [src.parent], title)
        ctx.progress(1.0)
        return [dst]

    # ---------------------------------------------------------------- PDF -> text

    def _ocr_page(self, page, ctx: Context):
        """Recognised text for one scanned page, or None if OCR can't run."""
        from .. import ocr
        if not ocr.available():
            return None
        try:
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "page.png"
                page.get_pixmap(matrix=__import__("pymupdf").Matrix(200 / 72, 200 / 72)).save(str(path))
                return ocr.recognize([path], ctx.cfg, ctx.cancel_event)[0]
        except ocr.OcrUnavailable as e:
            log.info("OCR unavailable: %s", e)
            self._ocr_error = str(e)
            return None

    @staticmethod
    def _is_scanned(page, text: str) -> bool:
        return len(text.strip()) < 10 and (bool(page.get_images()) or bool(page.get_drawings()))

    def _to_text(self, doc, dst: Path, ctx: Context) -> Path:
        n = doc.page_count
        parts, scanned, recognised = [], 0, 0
        self._ocr_error = ""
        for i, page in enumerate(doc):
            ctx.check_cancel()
            text = page.get_text()
            if self._is_scanned(page, text):
                scanned += 1
                result = self._ocr_page(page, ctx)
                if result is not None:
                    text = result.text()
                    recognised += 1
            parts.append(text)
            ctx.progress((i + 1) / n)
        if scanned and not recognised and not "".join(parts).strip():
            raise ConversionError(
                "This PDF is a scan (it has no text layer) and OCR isn't available"
                + (f": {self._ocr_error}" if self._ocr_error else "."))
        dst.write_text("\f".join(parts), encoding="utf-8")
        return dst

    # ---------------------------------------------------------------- PDF -> HTML

    def _to_html(self, doc, src: Path, dst: Path, ctx: Context) -> Path:
        n = doc.page_count
        body = []
        self._ocr_error = ""
        for i, page in enumerate(doc):
            ctx.check_cancel()
            text = page.get_text()
            if self._is_scanned(page, text):
                result = self._ocr_page(page, ctx)
                if result is not None:                 # recognised text instead of a picture of it
                    body.append(f'<div class="page" id="page{i}">{result.html()}</div>')
                    ctx.progress((i + 1) / n)
                    continue
            body.append(page.get_text("xhtml"))        # text + images (embedded as data: URIs)
            ctx.progress((i + 1) / n)
        title = (doc.metadata or {}).get("title") or src.stem
        from ..native import docs
        dst.write_text(
            f'<!DOCTYPE html>\n<html><head><meta charset="utf-8"><title>{html_lib.escape(title)}</title>'
            f"<style>{docs.CSS}img{{max-width:100%}}</style></head>\n<body>\n"
            + "\n<hr>\n".join(body) + "\n</body></html>\n", encoding="utf-8")
        return dst

    # ----------------------------------------------------------------- PDF -> SVG

    def _to_svg(self, doc, dst: Path, ctx: Context) -> list[Path]:
        import pymupdf as fitz
        n = doc.page_count
        outputs = []
        for i, page in enumerate(doc):
            ctx.check_cancel()
            out = dst if n == 1 else dst.with_name(f"{dst.stem}-{i + 1:0{len(str(n))}d}{dst.suffix}")
            out.write_text(page.get_svg_image(matrix=fitz.Identity, text_as_path=True), encoding="utf-8")
            outputs.append(out)
            ctx.progress((i + 1) / n)
        return outputs

    # ------------------------------------------------------------- raster output

    def _tall(self, doc, dst: Path, tgt: str, zoom: float, ctx: Context) -> list[Path]:
        """Every page stacked top to bottom in one image."""
        import pymupdf as fitz
        from PIL import Image

        from .images import _prepare
        pages = []
        for i, page in enumerate(doc):
            ctx.check_cancel()
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            pages.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
            ctx.progress((i + 1) / doc.page_count * 0.8)
        width = max(p.width for p in pages)
        gap = max(4, width // 120)
        height = sum(p.height for p in pages) + gap * (len(pages) - 1)
        scale = min(1.0, 30000 / height)                      # keep the image a sane size
        canvas = Image.new("RGB", (round(width * scale), round(height * scale)), (200, 200, 200))
        y = 0.0
        for p in pages:
            if scale != 1.0:
                p = p.resize((max(1, round(p.width * scale)), max(1, round(p.height * scale))), Image.LANCZOS)
            canvas.paste(p, (0, round(y)))
            y += p.height + gap * scale
        fmt = {"png": "PNG", "jpg": "JPEG", "webp": "WEBP"}[tgt]
        kw = {"quality": ctx.cfg["jpeg_quality"]} if tgt == "jpg" else {"quality": ctx.cfg["webp_quality"]} if tgt == "webp" else {}
        _prepare(canvas, tgt).save(dst, fmt, **kw)
        ctx.progress(1.0)
        return [dst]

    def _render(self, doc, src: Path, dst: Path, tgt: str, ctx: Context) -> list[Path]:
        import pymupdf as fitz
        from PIL import Image

        from .images import _prepare, _square

        is_svg = src.suffix.lower() == ".svg"
        # SVGs are usually small icons/drawings: render them at 2x for crispness.
        zoom = 2.0 if is_svg else ctx.cfg["render_dpi"] / 72.0
        alpha = is_svg and tgt in ("png", "webp", "ico")
        n = doc.page_count
        images = []
        outputs = []
        if ctx.variant == "tall" and tgt in ("png", "jpg", "webp") and n > 1:
            return self._tall(doc, dst, tgt, zoom, ctx)
        if ctx.variant == "first" and n > 1:
            n_first = doc.page_count
            doc.select([0])                                   # render only the first page
            n = 1
        for i, page in enumerate(doc):
            ctx.check_cancel()
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=alpha)
            mode = "RGBA" if pix.alpha else "RGB"
            im = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
            if tgt == "tiff":
                images.append(im)
            else:
                out = dst if n == 1 else dst.with_name(f"{dst.stem}-{i + 1:0{len(str(n))}d}{dst.suffix}")
                im = _prepare(im, tgt)
                if tgt == "ico":
                    im = _square(im)
                    im.save(out, "ICO", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)
                                               if s <= im.size[0]])
                else:
                    fmt = {"png": "PNG", "jpg": "JPEG", "webp": "WEBP"}[tgt]
                    kw = {"quality": ctx.cfg["jpeg_quality"]} if tgt == "jpg" else \
                         {"quality": ctx.cfg["webp_quality"]} if tgt == "webp" else {}
                    im.save(out, fmt, dpi=(ctx.cfg["render_dpi"],) * 2, **kw)
                outputs.append(out)
            ctx.progress((i + 1) / n * (0.9 if tgt == "tiff" else 1.0))
        if tgt == "tiff":
            images[0].save(dst, "TIFF", save_all=True, append_images=images[1:],
                           compression="tiff_lzw", dpi=(ctx.cfg["render_dpi"],) * 2)
            outputs.append(dst)
        ctx.progress(1.0)
        return outputs
