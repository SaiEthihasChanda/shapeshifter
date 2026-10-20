"""Presentations (PPTX, ODP) and native Word/OpenDocument text readers.

Slides are read into a shape model and redrawn / re-written without PowerPoint or LibreOffice:
  PPTX <-> ODP, PPTX/ODP -> PDF, HTML, Markdown, text, DOCX; PDF, pictures and documents -> PPTX/ODP.
Longer routes (slides -> pictures, CSV -> slides, slides -> EPUB…) chain these steps with the others.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..core import Backend, Context, ConversionError, target_ext

log = logging.getLogger(__name__)

DECK_SOURCES = {"pptx", "odp", "ppsx", "potx", "ppt"}           # ppt: old binary format, text only
DECK_OUTLINES = {"pdf", "html", "md", "txt", "docx"}
DECK_FORMATS = {"pptx", "odp"}
TEXT_SOURCES = {"md", "html", "txt", "docx", "odt"}
READ_TARGETS = {"md", "html", "txt", "pdf"}            # what the native Word/ODT reader can write


def _picture_sources() -> set[str]:
    from .images import ImageBackend
    return set(ImageBackend().sources()) - {"svg", "eps", "psd", "dds", "icns", "gif", "ico", "cur"}


class SlidesBackend(Backend):
    name = "slides"
    base_cost = 3.0

    def available(self):
        from importlib.util import find_spec
        return find_spec("pymupdf") is not None

    def sources(self):
        return DECK_SOURCES | {"pdf"} | TEXT_SOURCES | _picture_sources()

    def targets(self, src):
        if src in DECK_SOURCES:
            return (DECK_OUTLINES | DECK_FORMATS) - {src}
        if src == "pdf" or src in TEXT_SOURCES or src in _picture_sources():
            return set(DECK_FORMATS)
        return set()

    def cost(self, src, dst):
        if src == "ppt":
            return 6.8                          # LibreOffice does this better and wins when it is installed
        if src == "pdf":
            return 4.5                          # every page becomes a picture
        if src in _picture_sources():
            return 3.5
        if src in TEXT_SOURCES:
            return 3.5
        if dst == "pdf":
            return 3.0
        return 2.8 if dst in DECK_OUTLINES else 3.2

    def first_step_only(self, src, dst):
        return src == "pdf"                     # PDF -> slides only for a PDF the user actually has

    # ------------------------------------------------------------------ work

    def convert(self, src: Path, dst: Path, ctx: Context) -> list[Path]:
        s = ctx.src_fmt or target_ext(src)
        t = ctx.dst_fmt or target_ext(dst)
        deck = self._read(src, s, ctx)
        if not deck.slides:
            raise ConversionError("The presentation has no slides.")
        ctx.progress(0.7)
        self._write(deck, dst, t, ctx, src)
        ctx.progress(1.0)
        return [dst]

    def _read(self, src: Path, s: str, ctx: Context):
        from ..native import docread, slides, slides_write
        try:
            if s in ("pptx", "ppsx", "potx"):
                return slides.read_pptx(src)
            if s == "ppt":
                from ..native import legacy
                return slides_write.deck_from_blocks(legacy.ppt_blocks(src.read_bytes()), src.stem)
            if s == "odp":
                return slides_write.read_odp(src)
            if s == "pdf":
                return slides_write.deck_from_pdf(src, ctx.cancel_event, lambda f: ctx.progress(f * 0.65))
            if s in _picture_sources():
                return slides_write.deck_from_images([_picture_bytes(src)], src.stem)
            if s == "docx":
                blocks, title = docread.docx_blocks(src)
            elif s == "odt":
                blocks, title = docread.odt_blocks(src)
            else:
                from ..native import docs
                text = src.read_bytes().decode("utf-8-sig", errors="replace")
                blocks = (docs.parse_markdown(text) if s == "md" else docs.parse_html(text) if s == "html"
                          else docs.parse_text(text))
                title = docs.html_title(text) if s == "html" else ""
            return slides_write.deck_from_blocks(blocks, title or src.stem, [src.parent])
        except ConversionError:
            raise
        except Exception as e:                               # noqa: BLE001
            raise ConversionError(f"Couldn't read the {s.upper()} file: {e}") from e

    def _write(self, deck, dst: Path, t: str, ctx: Context, src: Path) -> None:
        from ..native import docs, slides_out, slides_write
        title = deck.title or src.stem
        if t == "pdf":
            slides_out.deck_to_pdf(deck, dst, lambda f: ctx.progress(0.7 + f * 0.3), ctx.cancel_event)
        elif t == "pptx":
            slides_write.write_pptx(deck, dst)
        elif t == "odp":
            slides_write.write_odp(deck, dst)
        elif t == "html":
            dst.write_text(docs.to_html(slides_out.deck_to_blocks(deck), title), encoding="utf-8")
        elif t == "md":
            dst.write_text(docs.to_markdown(slides_out.deck_to_blocks(deck, images=False)), encoding="utf-8")
        elif t == "txt":
            dst.write_text(docs.to_text(slides_out.deck_to_blocks(deck, images=False)), encoding="utf-8")
        elif t == "docx":
            from ..native.docx import blocks_to_docx
            dst.write_bytes(blocks_to_docx(slides_out.deck_to_blocks(deck), [], title))
        else:
            raise ConversionError(f"Can't write slides as {t}")


def _picture_bytes(path: Path) -> bytes:
    """The picture as JPEG or PNG bytes (anything else is re-encoded)."""
    import io

    from PIL import Image, ImageOps
    raw = path.read_bytes()
    if raw[:8] == b"\x89PNG\r\n\x1a\n" or raw[:3] == b"\xff\xd8\xff":
        return raw
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception:                                        # noqa: BLE001
        pass
    with Image.open(path) as im:
        im.seek(0)
        im = ImageOps.exif_transpose(im)
        buf = io.BytesIO()
        im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB").save(buf, "PNG")
        return buf.getvalue()


class DocReadBackend(Backend):
    """Word, OpenDocument text and RTF <-> Markdown / HTML / text / PDF, and between each other,
    without pandoc or LibreOffice (pandoc, when installed, handles more of Word and wins on cost)."""

    name = "docread"
    base_cost = 3.4

    def available(self):
        return True

    def sources(self):
        return {"doc", "docx", "odt", "rtf", "md", "html", "txt"}

    def targets(self, src):
        if src == "doc":
            return {"md", "html", "txt", "pdf", "docx", "odt", "rtf"}
        if src == "docx":
            return {"md", "html", "txt", "pdf", "odt", "rtf"}
        if src == "odt":
            return {"md", "html", "txt", "pdf", "docx", "rtf"}
        if src == "rtf":
            return {"md", "html", "txt", "pdf", "docx", "odt"}
        return {"odt", "rtf"}                          # Markdown / HTML / text have other ways to Word already

    def cost(self, src, dst):
        # Only a fallback: pandoc understands more of Word (and wins whenever it is installed).
        if src == "doc":
            return 6.8                                  # old binary Word: LibreOffice does it better when installed
        return 6.5 if dst == "pdf" else 3.4

    def convert(self, src: Path, dst: Path, ctx: Context) -> list[Path]:
        from ..native import docread, docs
        s = ctx.src_fmt or target_ext(src)
        t = ctx.dst_fmt or target_ext(dst)
        try:
            if s == "doc":
                from ..native import legacy
                blocks, title = legacy.doc_blocks(src.read_bytes()), ""
            elif s == "docx":
                blocks, title = docread.docx_blocks(src)
            elif s == "odt":
                blocks, title = docread.odt_blocks(src)
            elif s == "rtf":
                from ..native import rtf
                blocks, title = rtf.rtf_blocks(src.read_bytes()), ""
            else:
                text = src.read_bytes().decode("utf-8-sig", errors="replace")
                blocks = docs.parse_markdown(text) if s == "md" else docs.parse_html(text) if s == "html" else docs.parse_text(text)
                title = docs.html_title(text) if s == "html" else ""
        except Exception as e:                               # noqa: BLE001
            raise ConversionError(f"Couldn't read the {s.upper()} file: {e}") from e
        title = title or next((docs.plain(b.text) for b in blocks if b.kind == "h1"), "") or src.stem
        ctx.progress(0.5)
        base = [src.parent]
        if t == "md":
            dst.write_text(docs.to_markdown([b for b in blocks if b.kind != "img"]), encoding="utf-8")
        elif t == "txt":
            dst.write_text(docs.to_text([b for b in blocks if b.kind != "img"]), encoding="utf-8")
        elif t == "html":
            dst.write_text(docs.to_html(blocks, title), encoding="utf-8")
        elif t == "pdf":
            from .mupdf import html_to_pdf
            html_to_pdf(docs.to_html(blocks, title, css=False), dst, base, title)
        elif t == "docx":
            from ..native.docx import blocks_to_docx
            dst.write_bytes(blocks_to_docx(blocks, base, title))
        elif t == "odt":
            from ..native.odtwrite import blocks_to_odt
            dst.write_bytes(blocks_to_odt(blocks, base, title))
        elif t == "rtf":
            from ..native.rtf import write_rtf
            dst.write_bytes(write_rtf(blocks, base, title))
        ctx.progress(1.0)
        return [dst]
