"""Draw a ``Deck`` to PDF, or turn it into an outline (document blocks)."""

from __future__ import annotations

import base64
import html as _html
import io
from pathlib import Path

from . import docs
from .slides import EMU_PER_PT, Deck, Para, Shape, Slide

SERIF = ("times", "georgia", "garamond", "cambria", "palatino", "book antiqua", "serif")
MONO = ("courier", "consolas", "lucida console", "monaco", "menlo", "mono")


def _rgb(hexcolor: str | None):
    if not hexcolor:
        return None
    h = hexcolor.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _family(name: str | None) -> str:
    n = (name or "").lower()
    if any(k in n for k in MONO):
        return "monospace"
    if any(k in n for k in SERIF):
        return "serif"
    return "sans-serif"


def _image_bytes(data: bytes) -> bytes:
    """Bytes PyMuPDF can place: re-encode anything unusual (WebP, TIFF, …) as PNG."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff":
        return data
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        buf = io.BytesIO()
        im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB").save(buf, "PNG")
        return buf.getvalue()


def para_html(p: Para, number: int | None, color_default: str = "000000") -> str:
    size = p.size or 18.0
    left = 6.0 + p.level * 22.0 + (16.0 if p.bullet else 0.0)
    indent = -16.0 if p.bullet else 0.0
    align = {"ctr": "center", "r": "right", "just": "justify"}.get(p.align, "left")
    parts = []
    if p.bullet:
        mark = f"{number}." if p.bullet == "#" and number else ("•" if p.bullet == "#" else p.bullet)
        parts.append(f'<span style="font-size:{size:.1f}pt;color:#{color_default}">{_html.escape(mark)}&nbsp;</span>')
    for r in p.runs:
        if r.text == "\n":
            parts.append("<br>")
            continue
        css = [f"font-size:{r.size or size:.1f}pt", f"color:#{r.color or color_default}",
               f"font-family:{_family(r.font)}"]
        if r.bold:
            css.append("font-weight:bold")
        if r.italic:
            css.append("font-style:italic")
        if r.underline or r.link:
            css.append("text-decoration:underline")
        parts.append(f'<span style="{";".join(css)}">{_html.escape(r.text)}</span>')
    if not parts:
        parts = ["&nbsp;"]
    return (f'<p style="margin:0 0 {size * 0.25:.1f}pt {left:.1f}pt;text-indent:{indent:.1f}pt;'
            f'text-align:{align};line-height:1.15">{"".join(parts)}</p>')


def shape_html(shape: Shape) -> str:
    out, counters = [], {}
    for p in shape.paras:
        number = None
        if p.bullet == "#":
            counters[p.level] = counters.get(p.level, 0) + 1
            number = counters[p.level]
        else:
            counters.pop(p.level, None)
        out.append(para_html(p, number))
    return "".join(out)


def _draw_text(page, shape: Shape, scratch) -> None:
    import pymupdf
    k = 1 / EMU_PER_PT
    l, t, r, b = (v * k for v in shape.inset)
    rect = pymupdf.Rect(shape.x * k + l, shape.y * k + t, (shape.x + shape.w) * k - r, (shape.y + shape.h) * k - b)
    if rect.width < 4 or rect.height < 4:
        rect = pymupdf.Rect(rect.x0, rect.y0, rect.x0 + max(rect.width, 40), rect.y0 + max(rect.height, 16))
    html = shape_html(shape)
    # Text that doesn't fit its box is shrunk to fit, as PowerPoint's autofit does.
    spare, _ = scratch.insert_htmlbox(rect, html, scale_low=0.2)
    if spare >= 0:
        if shape.anchor in ("ctr", "b"):
            off = spare / 2 if shape.anchor == "ctr" else spare
            rect = pymupdf.Rect(rect.x0, rect.y0 + off, rect.x1, rect.y1)
        page.insert_htmlbox(rect, html, scale_low=0.2)
    else:                                              # still too long: use the rest of the page, shrinking as needed
        tall = pymupdf.Rect(rect.x0, rect.y0, rect.x1, max(rect.y1, page.rect.y1 - 4))
        page.insert_htmlbox(tall, html, scale_low=0)


def _draw_shape(page, shape: Shape, scratch) -> None:
    import pymupdf
    k = 1 / EMU_PER_PT
    rect = pymupdf.Rect(shape.x * k, shape.y * k, (shape.x + shape.w) * k, (shape.y + shape.h) * k)
    if shape.kind == "picture" and shape.image:
        try:
            page.insert_image(rect, stream=_image_bytes(shape.image), keep_proportion=False)
        except Exception:                              # noqa: BLE001 - unreadable picture: leave a frame
            page.draw_rect(rect, color=(0.6, 0.6, 0.6), width=0.5)
        return
    if shape.kind == "table":
        _draw_table(page, shape, rect)
        return
    fill, line = _rgb(shape.fill), _rgb(shape.line)
    width = max(0.25, shape.line_w * k) if line else 0
    if shape.geom == "line" or (shape.kind == "line"):
        page.draw_line(rect.tl, rect.br, color=line or (0, 0, 0), width=width or 0.75)
    elif shape.fill or shape.line:
        if shape.geom == "ellipse":
            page.draw_oval(rect, color=line, fill=fill, width=width)
        elif shape.geom == "roundrect":
            page.draw_rect(rect, color=line, fill=fill, width=width, radius=0.16)
        else:
            page.draw_rect(rect, color=line, fill=fill, width=width)
    if shape.paras:
        _draw_text(page, shape, scratch)


def _draw_table(page, shape: Shape, rect) -> None:
    import pymupdf
    rows = shape.rows
    if not rows:
        return
    cols = max(len(r) for r in rows)
    k = 1 / EMU_PER_PT
    widths = [w * k for w in shape.col_w] if len(shape.col_w) == cols and sum(shape.col_w) > 0 else [rect.width / cols] * cols
    total = sum(widths)
    widths = [w * rect.width / total for w in widths] if total else widths
    natural = [h * k for h in shape.row_h] if len(shape.row_h) == len(rows) else [rect.height / len(rows)] * len(rows)
    heights = [max(h, 20.0) for h in natural]               # a row always has room for a line of text
    size = max(7.0, min(22.0, min(heights) * 0.5, min(widths) / 6))
    ys = [rect.y0]
    for h in heights:
        ys.append(ys[-1] + h)
    xs = [rect.x0]
    for w in widths:
        xs.append(xs[-1] + w)
    for ri, row in enumerate(rows):
        for ci in range(cols):
            cell = pymupdf.Rect(xs[ci], ys[ri], xs[ci + 1], ys[ri + 1])
            page.draw_rect(cell, color=(0.55, 0.55, 0.55), fill=(0.92, 0.94, 0.98) if ri == 0 else None, width=0.6)
            text = row[ci] if ci < len(row) else ""
            if text:
                inner = pymupdf.Rect(cell.x0 + 3, cell.y0 + 2, cell.x1 - 3, cell.y1 - 2)
                weight = "font-weight:bold;" if ri == 0 else ""
                page.insert_htmlbox(inner, f'<p style="margin:0;font-family:sans-serif;font-size:{size:.1f}pt;{weight}">{_html.escape(text)}</p>',
                                    scale_low=0.4)


def deck_to_pdf(deck: Deck, dst: Path, progress=None, cancel=None) -> int:
    import pymupdf
    doc = pymupdf.open()
    scratch_doc = pymupdf.open()
    for i, slide in enumerate(deck.slides):
        if cancel is not None and cancel.is_set():
            from ..core import Cancelled
            raise Cancelled()
        page = doc.new_page(width=deck.width / EMU_PER_PT, height=deck.height / EMU_PER_PT)
        # Text is measured on a throw-away page (a fresh one per slide: a shared one grows and slows everything down).
        scratch = scratch_doc.new_page(width=deck.width / EMU_PER_PT, height=deck.height / EMU_PER_PT)
        if slide.bg_image:
            try:
                page.insert_image(page.rect, stream=_image_bytes(slide.bg_image), keep_proportion=False)
            except Exception:                          # noqa: BLE001
                pass
        elif slide.bg:
            page.draw_rect(page.rect, fill=_rgb(slide.bg), color=None, width=0)
        for shape in slide.shapes:
            try:
                _draw_shape(page, shape, scratch)
            except Exception:                          # noqa: BLE001 - one odd shape shouldn't sink the slide
                continue
        scratch_doc.delete_page(-1)
        if progress:
            progress((i + 1) / max(1, len(deck.slides)))
    if not len(doc):
        doc.new_page(width=deck.width / EMU_PER_PT, height=deck.height / EMU_PER_PT)
    doc.save(str(dst), garbage=3, deflate=True)
    doc.close()
    return len(deck.slides)


# --------------------------------------------------------------------- outline

def _inline(p: Para) -> str:
    """Paragraph text with Markdown emphasis and links, so the outline keeps its formatting."""
    out = []
    for r in p.runs:
        if r.text == "\n":
            out.append("  \n")
            continue
        t = docs.escape_markdown(r.text)
        if not t.strip():
            out.append(t)
            continue
        lead, core, trail = t[:len(t) - len(t.lstrip())], t.strip(), t[len(t.rstrip()):]
        if r.bold:
            core = f"**{core}**"
        if r.italic:
            core = f"*{core}*"
        if r.link and r.link.startswith(("http", "mailto")):
            core = f"[{core}]({r.link})"
        out.append(lead + core + trail)
    return "".join(out).strip()


def slide_title(slide: Slide, number: int) -> str:
    return slide.title or f"Slide {number}"


def deck_to_blocks(deck: Deck, images: bool = True, notes: bool = True) -> list[docs.Block]:
    blocks: list[docs.Block] = []
    for n, slide in enumerate(deck.slides, 1):
        title = slide_title(slide, n)
        blocks.append(docs.Block("h2", docs.escape_markdown(title) if slide.title else title))
        ordered = sorted((s for s in slide.shapes if s.role not in ("title",)), key=lambda s: (round(s.y / 400000), s.x))
        for shape in ordered:
            if shape.kind == "picture" and shape.image and images:
                try:
                    data = _image_bytes(shape.image)
                    mime = "image/jpeg" if data[:3] == b"\xff\xd8\xff" else "image/png"
                    blocks.append(docs.Block("img", f"Picture on slide {n}",
                                             src=f"data:{mime};base64,{base64.b64encode(data).decode()}"))
                except Exception:                      # noqa: BLE001
                    pass
            elif shape.kind == "table" and shape.rows:
                blocks.append(docs.Block("table", rows=[[docs.escape_markdown(c) for c in r] for r in shape.rows]))
            else:
                counters: dict[int, int] = {}
                for p in shape.paras:
                    text = _inline(p)
                    if not text:
                        continue
                    if p.bullet == "#":
                        counters[p.level] = counters.get(p.level, 0) + 1
                        blocks.append(docs.Block("oli", text, level=p.level))
                    elif p.bullet:
                        blocks.append(docs.Block("li", text, level=p.level))
                    else:
                        counters.clear()
                        blocks.append(docs.Block("p", text))
        if notes and slide.notes.strip():
            blocks.append(docs.Block("quote", "Notes: " + docs.escape_markdown(slide.notes.strip().replace("\n", " "))))
    return blocks
