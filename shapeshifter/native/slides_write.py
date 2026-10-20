"""Build decks (from PDF pages, pictures, document blocks) and write them as PPTX or ODP; read ODP."""

from __future__ import annotations

import io
import math
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from . import docs
from .slides import EMU_PER_CM, EMU_PER_IN, EMU_PER_PT, Deck, Para, Run, Shape, Slide

W16x9, H16x9 = 12192000, 6858000


# ===================================================================== builders

def _image_info(data: bytes) -> tuple[str, int, int]:
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        ext = {"JPEG": "jpg", "PNG": "png", "GIF": "gif"}.get(im.format or "", "png")
        return ext, im.width, im.height


def picture_slide(deck_w: float, deck_h: float, data: bytes, ext: str, iw: int, ih: int, alt: str = "") -> Slide:
    """A slide with one picture fitted inside the slide, centred."""
    k = min(deck_w / iw, deck_h / ih)
    w, h = iw * k, ih * k
    return Slide(shapes=[Shape(kind="picture", x=(deck_w - w) / 2, y=(deck_h - h) / 2, w=w, h=h,
                               image=data, image_ext=ext, name=alt)])


def deck_from_pdf(path, cancel=None, progress=None) -> Deck:
    """One full-page picture per PDF page (the slide has the page's size and shape). The page's
    text goes into each picture's alternative text so it stays searchable and readable."""
    import pymupdf
    from ..core import ConversionError
    try:
        doc = pymupdf.open(str(path))
    except Exception as e:                                   # noqa: BLE001
        raise ConversionError(f"Can't open the PDF: {e}") from e
    with doc:
        if doc.needs_pass:
            raise ConversionError("The PDF is password protected.")
        first = doc[0].rect
        deck = Deck(first.width * EMU_PER_PT, first.height * EMU_PER_PT, title=Path(path).stem)
        for i, page in enumerate(doc):
            if cancel is not None and cancel.is_set():
                from ..core import Cancelled
                raise Cancelled()
            pix = page.get_pixmap(dpi=150, alpha=False)
            photo = bool(page.get_images())
            data = pix.tobytes("jpeg", jpg_quality=88) if photo else pix.tobytes("png")
            slide = Slide(shapes=[Shape(kind="picture", x=0.0, y=0.0, w=deck.width, h=deck.height, image=data,
                                        image_ext="jpg" if photo else "png",
                                        name=re.sub(r"\s+", " ", page.get_text("text")).strip()[:900])])
            deck.slides.append(slide)
            if progress:
                progress((i + 1) / doc.page_count)
    return deck


def deck_from_images(pictures: list[bytes], title: str = "") -> Deck:
    deck = Deck(W16x9, H16x9, title=title)
    for data in pictures:
        ext, iw, ih = _image_info(data)
        deck.slides.append(picture_slide(deck.width, deck.height, data, ext, iw, ih))
    return deck


def _runs(text: str, size: float, bold: bool = False, italic: bool = False, color: str | None = None,
          font: str | None = None) -> list[Run]:
    runs = []
    for sp in docs.parse_inline(text):
        if sp.img:
            continue
        runs.append(Run(sp.text, size=size, bold=bold or sp.bold, italic=italic or sp.italic,
                        font="Courier New" if sp.code else font, color=color, link=sp.href or None))
    return [r for r in runs if r.text] or [Run("", size=size)]


def deck_from_blocks(blocks: list[docs.Block], title: str = "", base_dirs: list[Path] | None = None) -> Deck:
    """An outline as slides: headings become slide titles, the text under them the body."""
    from .images_util import load_image_ref
    deck = Deck(W16x9, H16x9, title=title)
    W, H = deck.width, deck.height
    margin = 0.5 * EMU_PER_IN
    MAX_LINES = 9

    class Cur:
        def __init__(self, title_text: str, cont: bool = False):
            self.title = title_text + (" (cont.)" if cont else "")
            self.paras: list[Para] = []
            self.pictures: list[tuple[bytes, str, int, int]] = []
            self.tables: list[list[list[str]]] = []
            self.lines = 0

    slides: list[Cur] = []
    title_slide: tuple[str, list[str]] | None = None
    cur: Cur | None = None
    base_title = title or "Presentation"

    def start(t: str, cont=False):
        nonlocal cur
        cur = Cur(t, cont)
        slides.append(cur)

    def room(n: int) -> bool:
        return cur is not None and cur.lines + n <= MAX_LINES

    def add_para(text: str, level=0, bullet: str | None = None, italic=False, mono=False, numbered=False):
        nonlocal cur
        if cur is None:
            start(base_title)
        n = max(1, math.ceil(len(re.sub(r"[*_`\[\]]", "", text)) / (62 - level * 8)))
        if not room(n) and cur.paras:
            start(cur.title.replace(" (cont.)", ""), cont=True)
        size = 26.0 - 4.0 * min(level, 2)
        runs = _runs(text, size, italic=italic, font="Courier New" if mono else None)
        cur.paras.append(Para(runs, level=level, bullet=("#" if numbered else bullet), size=size))
        cur.lines += n

    seen_any_heading = any(b.kind.startswith("h") and b.kind[1:].isdigit() for b in blocks)
    for b in blocks:
        k = b.kind
        if k.startswith("h") and k[1:].isdigit():
            plain = docs.plain(b.text).strip()
            if int(k[1]) == 1 and not slides and title_slide is None:
                title_slide = (plain, [])
                continue
            start(plain or base_title)
        elif k == "p":
            if title_slide is not None and not slides:
                title_slide[1].append(docs.plain(b.text))
                continue
            long = len(docs.plain(b.text)) > 140
            if cur is None and not seen_any_heading:
                start(base_title)
            add_para(b.text, bullet=None if long else "•")
        elif k in ("li", "oli"):
            add_para(b.text, level=min(b.level, 3), bullet="•", numbered=(k == "oli"))
        elif k == "quote":
            add_para(b.text, italic=True)
        elif k == "code":
            for ln in b.text.splitlines()[:10]:
                add_para(ln or " ", mono=True)
        elif k == "table" and b.rows:
            if cur is None or cur.paras or cur.tables:
                start(cur.title if cur else base_title, cont=bool(cur and (cur.paras or cur.tables)))
            cur.tables.append(b.rows[:12])
            cur.lines = MAX_LINES
        elif k == "img":
            data = load_image_ref(b.src, list(base_dirs or []))
            if data:
                try:
                    ext, iw, ih = _image_info(data)
                except Exception:                           # noqa: BLE001
                    continue
                if cur is None:
                    start(base_title)
                if cur.pictures:
                    start(cur.title.replace(" (cont.)", ""), cont=True)
                cur.pictures.append((data, ext, iw, ih))
    if title_slide is None and not slides:
        title_slide = (base_title, [])
    # ---- lay the collected content out on slides
    if title_slide is not None:
        t, sub = title_slide
        s = Slide()
        s.shapes.append(Shape(kind="text", x=margin, y=H * 0.28, w=W - 2 * margin, h=H * 0.2, role="title", anchor="b",
                              paras=[Para(_runs(t, 44.0, bold=True), align="ctr", size=44.0)]))
        if sub:
            s.shapes.append(Shape(kind="text", x=margin, y=H * 0.52, w=W - 2 * margin, h=H * 0.25, role="",
                                  paras=[Para(_runs(x, 24.0, color="595959"), align="ctr", size=24.0) for x in sub[:4]]))
        deck.slides.append(s)
    for c in slides:
        s = Slide()
        s.shapes.append(Shape(kind="text", x=margin, y=0.3 * EMU_PER_IN, w=W - 2 * margin, h=1.0 * EMU_PER_IN, role="title",
                              anchor="ctr", paras=[Para(_runs(c.title, 34.0, bold=True), size=34.0)]))
        top, avail_h = 1.5 * EMU_PER_IN, H - 1.9 * EMU_PER_IN
        body_w = W - 2 * margin
        if c.pictures:
            body_w = (W - 2 * margin) * 0.52 if c.paras else 0
            px, pw = margin + (W - 2 * margin) * 0.56, (W - 2 * margin) * 0.44
            data, ext, iw, ih = c.pictures[0]
            if not c.paras:
                px, pw = margin, W - 2 * margin
            k = min(pw / iw, avail_h / ih)
            s.shapes.append(Shape(kind="picture", x=px + (pw - iw * k) / 2, y=top, w=iw * k, h=ih * k, image=data, image_ext=ext))
        if c.paras:
            s.shapes.append(Shape(kind="text", x=margin, y=top, w=body_w or (W - 2 * margin), h=avail_h, role="body",
                                  paras=c.paras))
        for rows in c.tables:
            s.shapes.append(Shape(kind="table", x=margin, y=top, w=W - 2 * margin, h=min(avail_h, 0.55 * EMU_PER_IN * len(rows)),
                                  rows=[[docs.plain(x) for x in r] for r in rows]))
        deck.slides.append(s)
    return deck


# ===================================================================== PPTX writer

NSDECL = ('xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
          'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"')
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

THEME = XML + (
    '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="ShapeShifter"><a:themeElements>'
    '<a:clrScheme name="Office"><a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>'
    '<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="44546A"/></a:dk2>'
    '<a:lt2><a:srgbClr val="E7E6E6"/></a:lt2><a:accent1><a:srgbClr val="4472C4"/></a:accent1>'
    '<a:accent2><a:srgbClr val="ED7D31"/></a:accent2><a:accent3><a:srgbClr val="A5A5A5"/></a:accent3>'
    '<a:accent4><a:srgbClr val="FFC000"/></a:accent4><a:accent5><a:srgbClr val="5B9BD5"/></a:accent5>'
    '<a:accent6><a:srgbClr val="70AD47"/></a:accent6><a:hlink><a:srgbClr val="0563C1"/></a:hlink>'
    '<a:folHlink><a:srgbClr val="954F72"/></a:folHlink></a:clrScheme>'
    '<a:fontScheme name="Office"><a:majorFont><a:latin typeface="Calibri Light"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>'
    '<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont></a:fontScheme>'
    '<a:fmtScheme name="Office"><a:fillStyleLst>' + '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>' * 3 +
    '</a:fillStyleLst><a:lnStyleLst>' +
    ''.join(f'<a:ln w="{w}"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>' for w in (6350, 12700, 19050)) +
    '</a:lnStyleLst><a:effectStyleLst>' + '<a:effectStyle><a:effectLst/></a:effectStyle>' * 3 +
    '</a:effectStyleLst><a:bgFillStyleLst>' + '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>' * 3 +
    '</a:bgFillStyleLst></a:fmtScheme></a:themeElements><a:objectDefaults/><a:extraClrSchemeLst/></a:theme>')

GRP = ('<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm>'
       '<a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>')


def _xfrm(x, y, w, h, tag="a:xfrm") -> str:
    return (f'<{tag}><a:off x="{int(x)}" y="{int(y)}"/><a:ext cx="{max(1, int(w))}" cy="{max(1, int(h))}"/></{tag}>')


def _rpr(r: Run, default_size: float | None, link_id: str | None = None) -> str:
    size = r.size or default_size
    attrs = ['lang="en-US"']
    if size:
        attrs.append(f'sz="{int(round(size * 100))}"')
    if r.bold:
        attrs.append('b="1"')
    if r.italic:
        attrs.append('i="1"')
    if r.underline:
        attrs.append('u="sng"')
    inner = ""
    if r.color:
        inner += f'<a:solidFill><a:srgbClr val="{r.color.upper()}"/></a:solidFill>'
    if r.font:
        inner += f'<a:latin typeface={quoteattr(r.font)}/>'
    if link_id:
        inner += f'<a:hlinkClick r:id="{link_id}"/>'
    return f'<a:rPr {" ".join(attrs)}>{inner}</a:rPr>' if inner else f'<a:rPr {" ".join(attrs)}/>'


def _para_xml(p: Para, links: dict) -> str:
    attrs = []
    if p.level:
        attrs.append(f'lvl="{min(p.level, 8)}"')
    if p.align != "l":
        attrs.append(f'algn="{p.align}"')
    mar = 342900 * (p.level + 1) if p.bullet else 0
    if p.bullet:
        attrs += [f'marL="{mar}"', 'indent="-342900"']
    bu = ('<a:buAutoNum type="arabicPeriod"/>' if p.bullet == "#" else
          f'<a:buChar char={quoteattr(p.bullet)}/>' if p.bullet else "<a:buNone/>")
    out = [f'<a:p><a:pPr {" ".join(attrs)}>{bu}</a:pPr>']
    for r in p.runs:
        if r.text == "\n":
            out.append("<a:br/>")
            continue
        link_id = links.setdefault(r.link, f"rIdL{len(links) + 1}") if r.link else None
        out.append(f'<a:r>{_rpr(r, p.size, link_id)}<a:t>{escape(r.text)}</a:t></a:r>')
    out.append(f'<a:endParaRPr lang="en-US"{f" sz={quoteattr(str(int(p.size * 100)))}" if p.size else ""}/></a:p>')
    return "".join(out)


def _slide_xml(slide: Slide, deck: Deck, media: dict, links: dict) -> tuple[str, list[tuple[str, str, str]]]:
    """(slide XML, extra relationships [(id, type, target)]) for one slide."""
    rels: list[tuple[str, str, str]] = []
    shapes = []
    sid = 2
    bg = ""
    if slide.bg:
        bg = f'<p:bg><p:bgPr><a:solidFill><a:srgbClr val="{slide.bg.upper()}"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>'
    for sh in slide.shapes:
        sid += 1
        name = escape(sh.name or f"Shape {sid}", {'"': "&quot;"})
        if sh.kind == "picture" and sh.image:
            ext = "jpeg" if sh.image_ext in ("jpg", "jpeg") else (sh.image_ext if sh.image_ext in ("png", "gif") else "png")
            data = sh.image
            if ext == "png" and sh.image_ext not in ("png",):
                from .slides_out import _image_bytes
                data = _image_bytes(sh.image)
            key = (len(data), hash(data[:4096]), ext)
            fname = media.setdefault(key, (f"image{len(media) + 1}.{ext}", data))[0]
            rid = f"rIdP{len(rels) + 1}"
            rels.append((rid, "image", f"../media/{fname}"))
            shapes.append(
                f'<p:pic><p:nvPicPr><p:cNvPr id="{sid}" name="Picture {sid}" descr="{name}"/><p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>'
                f'<p:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>'
                f'<p:spPr>{_xfrm(sh.x, sh.y, sh.w, sh.h)}<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>')
        elif sh.kind == "table" and sh.rows:
            cols = max(len(r) for r in sh.rows)
            cw, rh = int(sh.w / cols), int(sh.h / len(sh.rows))
            rows_xml = []
            for ri, row in enumerate(sh.rows):
                cells = []
                for ci in range(cols):
                    text = row[ci] if ci < len(row) else ""
                    border = "".join(f'<a:{e} w="6350"><a:solidFill><a:srgbClr val="8C8C8C"/></a:solidFill></a:{e}>' for e in ("lnL", "lnR", "lnT", "lnB"))
                    fill = '<a:solidFill><a:srgbClr val="EAF0F9"/></a:solidFill>' if ri == 0 else ""
                    cells.append(f'<a:tc><a:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="en-US" sz="1800"{" b=\"1\"" if ri == 0 else ""}/><a:t>{escape(text)}</a:t></a:r></a:p></a:txBody><a:tcPr>{border}{fill}</a:tcPr></a:tc>')
                rows_xml.append(f'<a:tr h="{rh}">{"".join(cells)}</a:tr>')
            shapes.append(
                f'<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="{sid}" name="Table {sid}"/><p:cNvGraphicFramePr><a:graphicFrameLocks noGrp="1"/></p:cNvGraphicFramePr><p:nvPr/></p:nvGraphicFramePr>'
                f'{_xfrm(sh.x, sh.y, sh.w, sh.h, "p:xfrm")}<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table"><a:tbl>'
                f'<a:tblPr firstRow="1"/><a:tblGrid>{"".join(f"<a:gridCol w={quoteattr(str(cw))}/>" for _ in range(cols))}</a:tblGrid>{"".join(rows_xml)}</a:tbl></a:graphicData></a:graphic></p:graphicFrame>')
        else:
            geom = {"ellipse": "ellipse", "roundrect": "roundRect", "line": "line"}.get(sh.geom, "rect")
            fill = (f'<a:solidFill><a:srgbClr val="{sh.fill.upper()}"/></a:solidFill>' if sh.fill else "<a:noFill/>")
            ln = (f'<a:ln w="{int(sh.line_w or 12700)}"><a:solidFill><a:srgbClr val="{sh.line.upper()}"/></a:solidFill></a:ln>'
                  if sh.line else "<a:ln><a:noFill/></a:ln>")
            ph = ""
            if sh.role in ("title",):
                ph = '<p:ph type="title"/>'
            elif sh.role == "body":
                ph = '<p:ph idx="1"/>'
            if sh.paras:
                anchor = {"ctr": "ctr", "b": "b"}.get(sh.anchor, "t")
                body = (f'<p:txBody><a:bodyPr wrap="square" lIns="{int(sh.inset[0])}" tIns="{int(sh.inset[1])}" rIns="{int(sh.inset[2])}" '
                        f'bIns="{int(sh.inset[3])}" anchor="{anchor}"><a:normAutofit/></a:bodyPr><a:lstStyle/>'
                        + "".join(_para_xml(p, links) for p in sh.paras) + "</p:txBody>")
            else:
                body = ""
            is_box = bool(sh.paras) and not (sh.fill or sh.line)
            cnv = f'<p:cNvSpPr txBox="1"/>' if (is_box and not ph) else "<p:cNvSpPr/>"
            shapes.append(
                f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="{name or f"Shape {sid}"}"/>{cnv}<p:nvPr>{ph}</p:nvPr></p:nvSpPr>'
                f'<p:spPr>{_xfrm(sh.x, sh.y, sh.w, sh.h)}<a:prstGeom prst="{geom}"><a:avLst/></a:prstGeom>{fill}{ln}</p:spPr>{body}</p:sp>')
    xml = (XML + f'<p:sld {NSDECL}><p:cSld>{bg}<p:spTree>{GRP}{"".join(shapes)}</p:spTree></p:cSld>'
           f'<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>')
    return xml, rels


def write_pptx(deck: Deck, path) -> None:
    media: dict = {}
    links: dict = {}
    slides_xml, slide_rels = [], []
    for s in deck.slides or [Slide()]:
        xml, rels = _slide_xml(s, deck, media, links)
        slides_xml.append(xml)
        slide_rels.append(rels)
    n = len(slides_xml)
    W, H = int(deck.width), int(deck.height)
    types = (XML + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/>'
             '<Default Extension="jpeg" ContentType="image/jpeg"/><Default Extension="gif" ContentType="image/gif"/>'
             '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
             '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>'
             '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>'
             '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
             '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
             '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
             + "".join(f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>' for i in range(1, n + 1))
             + "</Types>")
    root_rels = (XML + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 f'<Relationship Id="rId1" Type="{REL}/officeDocument" Target="ppt/presentation.xml"/>'
                 '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                 f'<Relationship Id="rId3" Type="{REL}/extended-properties" Target="docProps/app.xml"/></Relationships>')
    pres = (XML + f'<p:presentation {NSDECL}><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
            '<p:sldIdLst>' + "".join(f'<p:sldId id="{255 + i}" r:id="rId{i + 1}"/>' for i in range(1, n + 1)) + "</p:sldIdLst>"
            f'<p:sldSz cx="{W}" cy="{H}"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>')
    pres_rels = (XML + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 f'<Relationship Id="rId1" Type="{REL}/slideMaster" Target="slideMasters/slideMaster1.xml"/>'
                 + "".join(f'<Relationship Id="rId{i + 1}" Type="{REL}/slide" Target="slides/slide{i}.xml"/>' for i in range(1, n + 1))
                 + f'<Relationship Id="rId{n + 2}" Type="{REL}/theme" Target="theme/theme1.xml"/></Relationships>')
    clr = ('<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" '
           'accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>')
    margin = int(0.5 * EMU_PER_IN)
    ph_title = (f'<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>'
                f'<p:spPr>{_xfrm(margin, 0.3 * EMU_PER_IN, W - 2 * margin, 1.0 * EMU_PER_IN)}</p:spPr>'
                '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="en-US"/><a:t>Title</a:t></a:r></a:p></p:txBody></p:sp>')
    ph_body = (f'<p:sp><p:nvSpPr><p:cNvPr id="3" name="Content"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph idx="1"/></p:nvPr></p:nvSpPr>'
               f'<p:spPr>{_xfrm(margin, 1.5 * EMU_PER_IN, W - 2 * margin, H - 1.9 * EMU_PER_IN)}</p:spPr>'
               '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:pPr lvl="0"/><a:r><a:rPr lang="en-US"/><a:t>Text</a:t></a:r></a:p></p:txBody></p:sp>')
    levels = lambda sz, bullet: "".join(  # noqa: E731
        f'<a:lvl{i}pPr marL="{342900 * i}" indent="-342900"><a:buFont typeface="Arial"/>{"<a:buChar char=" + quoteattr(chr(8226)) + "/>" if bullet else "<a:buNone/>"}'
        f'<a:defRPr sz="{max(1400, sz - 400 * (i - 1))}"><a:solidFill><a:schemeClr val="tx1"/></a:solidFill><a:latin typeface="+mn-lt"/></a:defRPr></a:lvl{i}pPr>'
        for i in range(1, 6))
    master = (XML + f'<p:sldMaster {NSDECL}><p:cSld><p:bg><p:bgRef idx="1001"><a:schemeClr val="bg1"/></p:bgRef></p:bg><p:spTree>{GRP}{ph_title}{ph_body}</p:spTree></p:cSld>'
              f'{clr}<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst><p:txStyles>'
              f'<p:titleStyle>{levels(4400, False)}</p:titleStyle><p:bodyStyle>{levels(2800, True)}</p:bodyStyle><p:otherStyle>{levels(1800, False)}</p:otherStyle>'
              '</p:txStyles></p:sldMaster>')
    master_rels = (XML + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   f'<Relationship Id="rId1" Type="{REL}/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
                   f'<Relationship Id="rId2" Type="{REL}/theme" Target="../theme/theme1.xml"/></Relationships>')
    layout = (XML + f'<p:sldLayout {NSDECL} type="obj" preserve="1"><p:cSld name="Title and Content"><p:spTree>{GRP}{ph_title}{ph_body}</p:spTree></p:cSld>'
              '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>')
    layout_rels = (XML + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   f'<Relationship Id="rId1" Type="{REL}/slideMaster" Target="../slideMasters/slideMaster1.xml"/></Relationships>')
    core = (XML + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:title>{escape(deck.title)}</dc:title><dc:creator>ShapeShifter</dc:creator></cp:coreProperties>')
    app = (XML + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
           f'<Application>ShapeShifter</Application><Slides>{n}</Slides></Properties>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("ppt/presentation.xml", pres)
        z.writestr("ppt/_rels/presentation.xml.rels", pres_rels)
        z.writestr("ppt/slideMasters/slideMaster1.xml", master)
        z.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", master_rels)
        z.writestr("ppt/slideLayouts/slideLayout1.xml", layout)
        z.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", layout_rels)
        z.writestr("ppt/theme/theme1.xml", THEME)
        z.writestr("docProps/core.xml", core)
        z.writestr("docProps/app.xml", app)
        for i, (xml, rels) in enumerate(zip(slides_xml, slide_rels), 1):
            z.writestr(f"ppt/slides/slide{i}.xml", xml)
            link_rels = "".join(f'<Relationship Id="{rid}" Type="{REL}/hyperlink" Target={quoteattr(url)} TargetMode="External"/>'
                                for url, rid in links.items() if f'r:id="{rid}"' in xml)
            body = "".join(f'<Relationship Id="{rid}" Type="{REL}/{kind}" Target="{target}"/>' for rid, kind, target in rels)
            z.writestr(f"ppt/slides/_rels/slide{i}.xml.rels",
                       XML + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       f'<Relationship Id="rIdLy" Type="{REL}/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>{body}{link_rels}</Relationships>')
        for fname, data in media.values():
            z.writestr(f"ppt/media/{fname}", data)


# ===================================================================== ODP

ODF = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "svg": "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0",
    "presentation": "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
}


def _o(tag: str) -> str:
    p, n = tag.split(":")
    return "{%s}%s" % (ODF[p], n)


def _cm(emu: float) -> str:
    return f"{emu / EMU_PER_CM:.3f}cm"


_ODF_HEAD = "".join(f'xmlns:{p}="{u}" ' for p, u in ODF.items())


def write_odp(deck: Deck, path) -> None:
    styles: dict[tuple, str] = {}       # text style key -> name
    pstyles: dict[str, str] = {}        # alignment -> paragraph style name
    gstyles: dict[tuple, str] = {}      # (fill, line, line_w) -> graphic style name
    pictures: list[tuple[str, bytes]] = []

    def tstyle(r: Run, default_size) -> str:
        key = (r.size or default_size, r.bold, r.italic, r.underline or bool(r.link), r.color, r.font)
        if key not in styles:
            styles[key] = f"T{len(styles) + 1}"
        return styles[key]

    def pstyle(align: str) -> str:
        return pstyles.setdefault(align, f"P{len(pstyles) + 1}")

    def gstyle(fill, line, lw) -> str:
        key = (fill, line, lw)
        return gstyles.setdefault(key, f"G{len(gstyles) + 1}")

    def para(p: Para) -> str:
        runs = []
        for r in p.runs:
            if r.text == "\n":
                runs.append("<text:line-break/>")
                continue
            span = f'<text:span text:style-name="{tstyle(r, p.size)}">{escape(r.text)}</text:span>'
            runs.append(f'<text:a xlink:type="simple" xlink:href={quoteattr(r.link)}>{span}</text:a>' if r.link else span)
        return f'<text:p text:style-name="{pstyle(p.align)}">{"".join(runs)}</text:p>'

    def text_body(paras: list[Para]) -> str:
        out, stack = [], []                          # stack of open list levels
        def close_to(depth):
            while len(stack) > depth:
                out.append("</text:list-item></text:list>")
                stack.pop()
        for p in paras:
            if not p.bullet:
                close_to(0)
                out.append(para(p))
                continue
            kind = "L2" if p.bullet == "#" else "L1"
            depth = p.level + 1
            if len(stack) >= depth and stack[depth - 1] != kind:
                close_to(depth - 1)
            if len(stack) >= depth:
                close_to(depth)
                out.append("</text:list-item><text:list-item>")
            else:
                while len(stack) < depth:
                    out.append(f'<text:list text:style-name="{kind}"><text:list-item>')
                    stack.append(kind)
            out.append(para(p))
        close_to(0)
        return "".join(out)

    pages = []
    for i, s in enumerate(deck.slides or [Slide()], 1):
        parts = [f'<draw:page draw:name="page{i}" draw:style-name="dp{i}" draw:master-page-name="Default">']
        for sh in s.shapes:
            geo = f'svg:x="{_cm(sh.x)}" svg:y="{_cm(sh.y)}" svg:width="{_cm(sh.w)}" svg:height="{_cm(sh.h)}"'
            if sh.kind == "picture" and sh.image:
                ext = sh.image_ext if sh.image_ext in ("png", "gif") else ("jpeg" if sh.image_ext in ("jpg", "jpeg") else "png")
                data = sh.image
                if ext == "png" and sh.image_ext != "png":
                    from .slides_out import _image_bytes
                    data = _image_bytes(sh.image)
                name = f"Pictures/img{len(pictures) + 1}.{ext}"
                pictures.append((name, data))
                parts.append(f'<draw:frame draw:style-name="gr0" {geo}><draw:image xlink:href="{name}" xlink:type="simple" '
                             f'xlink:show="embed" xlink:actuate="onLoad"/></draw:frame>')
            elif sh.kind == "table" and sh.rows:
                cols = max(len(r) for r in sh.rows)
                rows = "".join("<table:table-row>" + "".join(
                    f'<table:table-cell office:value-type="string"><text:p>{escape(r[c] if c < len(r) else "")}</text:p></table:table-cell>'
                    for c in range(cols)) + "</table:table-row>" for r in sh.rows)
                parts.append(f'<draw:frame draw:style-name="gr0" {geo}><table:table table:name="Table{i}">'
                             f'<table:table-column table:number-columns-repeated="{cols}"/>{rows}</table:table></draw:frame>')
            elif sh.paras:
                cls = {"title": ' presentation:class="title"', "body": ' presentation:class="outline"'}.get(sh.role, "")
                parts.append(f'<draw:frame draw:style-name="{gstyle(sh.fill, sh.line, sh.line_w)}" {geo}{cls}>'
                             f'<draw:text-box>{text_body(sh.paras)}</draw:text-box></draw:frame>')
            elif sh.geom == "line":
                parts.append(f'<draw:line draw:style-name="{gstyle(None, sh.line or "000000", sh.line_w or 12700)}" '
                             f'svg:x1="{_cm(sh.x)}" svg:y1="{_cm(sh.y)}" svg:x2="{_cm(sh.x + sh.w)}" svg:y2="{_cm(sh.y + sh.h)}"/>')
            else:
                tag = "draw:ellipse" if sh.geom == "ellipse" else "draw:rect"
                parts.append(f'<{tag} draw:style-name="{gstyle(sh.fill, sh.line, sh.line_w)}" {geo}/>')
        if s.notes.strip():
            parts.append('<presentation:notes><draw:frame presentation:class="notes" svg:x="1cm" svg:y="14cm" svg:width="18cm" svg:height="10cm">'
                         f'<draw:text-box><text:p>{escape(s.notes)}</text:p></draw:text-box></draw:frame></presentation:notes>')
        parts.append("</draw:page>")
        pages.append("".join(parts))

    auto = []
    for (size, bold, italic, under, color, font), name in styles.items():
        props = [f'fo:font-size="{size or 18:.1f}pt"']
        if bold:
            props.append('fo:font-weight="bold"')
        if italic:
            props.append('fo:font-style="italic"')
        if under:
            props.append('style:text-underline-style="solid" style:text-underline-width="auto" style:text-underline-color="font-color"')
        if color:
            props.append(f'fo:color="#{color}"')
        if font:
            props.append(f'fo:font-family={quoteattr(font)}')
        auto.append(f'<style:style style:name="{name}" style:family="text"><style:text-properties {" ".join(props)}/></style:style>')
    for align, name in pstyles.items():
        a = {"ctr": "center", "r": "end", "just": "justify"}.get(align, "start")
        auto.append(f'<style:style style:name="{name}" style:family="paragraph"><style:paragraph-properties fo:text-align="{a}"/></style:style>')
    for (fill, line, lw), name in gstyles.items():
        f = f'draw:fill="solid" draw:fill-color="#{fill}"' if fill else 'draw:fill="none"'
        ln = (f'draw:stroke="solid" svg:stroke-color="#{line}" svg:stroke-width="{(lw or 12700) / EMU_PER_CM:.3f}cm"' if line else 'draw:stroke="none"')
        auto.append(f'<style:style style:name="{name}" style:family="graphic"><style:graphic-properties {f} {ln}/></style:style>')
    auto.append('<style:style style:name="gr0" style:family="graphic"><style:graphic-properties draw:fill="none" draw:stroke="none"/></style:style>')
    for i, s in enumerate(deck.slides or [Slide()], 1):
        fill = f'draw:fill="solid" draw:fill-color="#{s.bg}"' if s.bg else 'draw:fill="none"'
        auto.append(f'<style:style style:name="dp{i}" style:family="drawing-page"><style:drawing-page-properties {fill}/></style:style>')

    def lst(name, bullet):
        levels = "".join(
            (f'<text:list-level-style-bullet text:level="{i}" text:bullet-char="•">' if bullet else
             f'<text:list-level-style-number text:level="{i}" style:num-suffix="." style:num-format="1">') +
            f'<style:list-level-properties text:list-level-position-and-space-mode="label-alignment">'
            f'<style:list-level-label-alignment text:label-followed-by="listtab" text:list-tab-stop-position="{0.6 * i:.1f}cm" '
            f'fo:text-indent="-0.6cm" fo:margin-left="{0.6 * i:.1f}cm"/></style:list-level-properties>' +
            ("</text:list-level-style-bullet>" if bullet else "</text:list-level-style-number>")
            for i in range(1, 9))
        return f'<text:list-style style:name="{name}">{levels}</text:list-style>'

    content = (XML + f'<office:document-content {_ODF_HEAD}office:version="1.2"><office:automatic-styles>{"".join(auto)}'
               f'{lst("L1", True)}{lst("L2", False)}</office:automatic-styles><office:body><office:presentation>'
               f'{"".join(pages)}</office:presentation></office:body></office:document-content>')
    w_cm, h_cm = f"{deck.width / EMU_PER_CM:.3f}cm", f"{deck.height / EMU_PER_CM:.3f}cm"
    styles_xml = (XML + f'<office:document-styles {_ODF_HEAD}office:version="1.2"><office:automatic-styles>'
                  f'<style:page-layout style:name="PM1"><style:page-layout-properties fo:page-width="{w_cm}" fo:page-height="{h_cm}" '
                  'fo:margin-top="0cm" fo:margin-bottom="0cm" fo:margin-left="0cm" fo:margin-right="0cm" style:print-orientation="landscape"/>'
                  '</style:page-layout></office:automatic-styles><office:master-styles>'
                  '<style:master-page style:name="Default" style:page-layout-name="PM1"/></office:master-styles></office:document-styles>')
    manifest = (XML + '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">'
                '<manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.presentation"/>'
                '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
                '<manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>'
                + "".join(f'<manifest:file-entry manifest:full-path="{n}" manifest:media-type="image/{n.rsplit(".", 1)[-1]}"/>' for n, _ in pictures)
                + "</manifest:manifest>")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.presentation")
        z.writestr("content.xml", content, zipfile.ZIP_DEFLATED)
        z.writestr("styles.xml", styles_xml, zipfile.ZIP_DEFLATED)
        z.writestr("META-INF/manifest.xml", manifest, zipfile.ZIP_DEFLATED)
        for name, data in pictures:
            z.writestr(name, data)


# --------------------------------------------------------------------- ODP reader

_UNIT = {"cm": EMU_PER_CM, "mm": EMU_PER_CM / 10, "in": EMU_PER_IN, "pt": EMU_PER_PT, "px": EMU_PER_IN / 96}


def _len(v: str | None, default: float = 0.0) -> float:
    if not v:
        return default
    m = re.match(r"(-?[\d.]+)\s*([a-z%]*)", v)
    if not m or m.group(2) not in _UNIT:
        return default
    return float(m.group(1)) * _UNIT[m.group(2)]


def read_odp(path) -> Deck:
    with zipfile.ZipFile(path) as z:
        content = ET.fromstring(z.read("content.xml"))
        try:
            styles = ET.fromstring(z.read("styles.xml"))
        except KeyError:
            styles = None
        names = set(z.namelist())

        def blob(href: str) -> bytes | None:
            href = href.lstrip("./")
            return z.read(href) if href in names else None

        tprops: dict[str, dict] = {}
        pprops: dict[str, str] = {}
        gprops: dict[str, dict] = {}
        lists: dict[str, bool] = {}                  # list style name -> numbered?
        for root in (styles, content):
            if root is None:
                continue
            for st in root.iter(_o("style:style")):
                name = st.get(_o("style:name"))
                tp = st.find(_o("style:text-properties"))
                if tp is not None:
                    size = tp.get(_o("fo:font-size"))
                    tprops[name] = {"size": _len(size) / EMU_PER_PT if size and size.endswith("pt") else None,
                                    "bold": tp.get(_o("fo:font-weight")) == "bold", "italic": tp.get(_o("fo:font-style")) == "italic",
                                    "underline": bool(tp.get(_o("style:text-underline-style"), "none") != "none"),
                                    "color": (tp.get(_o("fo:color")) or "").lstrip("#") or None, "font": tp.get(_o("fo:font-family"))}
                pp = st.find(_o("style:paragraph-properties"))
                if pp is not None and pp.get(_o("fo:text-align")):
                    pprops[name] = {"center": "ctr", "end": "r", "right": "r", "justify": "just"}.get(pp.get(_o("fo:text-align")), "l")
                gp = st.find(_o("style:graphic-properties"))
                if gp is not None:
                    gprops[name] = {"fill": (gp.get(_o("draw:fill-color")) or "").lstrip("#") or None if gp.get(_o("draw:fill")) == "solid" else None,
                                    "line": (gp.get(_o("svg:stroke-color")) or "").lstrip("#") or None if gp.get(_o("draw:stroke")) == "solid" else None,
                                    "line_w": _len(gp.get(_o("svg:stroke-width")))}
                dp = st.find(_o("style:drawing-page-properties"))
                if dp is not None:
                    gprops.setdefault(name, {})["bg"] = (dp.get(_o("draw:fill-color")) or "").lstrip("#") or None if dp.get(_o("draw:fill")) == "solid" else None
            for ls in root.iter(_o("text:list-style")):
                first = ls[0] if len(ls) else None
                lists[ls.get(_o("style:name"))] = first is not None and first.tag.endswith("number")

        deck = Deck()
        if styles is not None:
            pl = styles.find(f".//{_o('style:page-layout-properties')}")
            if pl is not None:
                deck.width = _len(pl.get(_o("fo:page-width")), deck.width)
                deck.height = _len(pl.get(_o("fo:page-height")), deck.height)

        def runs_of(el, base_size, inherited=None):
            out = []
            def walk(node, st):
                for ch in node:
                    tag = ch.tag.split("}")[-1]
                    nst = dict(st)
                    if tag == "span":
                        nst.update({k: v for k, v in tprops.get(ch.get(_o("text:style-name"), ""), {}).items() if v})
                    if tag in ("span", "a"):
                        link = ch.get(_o("xlink:href")) if tag == "a" else st.get("link")
                        nst["link"] = link
                        if ch.text:
                            out.append(Run(ch.text, size=nst.get("size"), bold=nst.get("bold", False), italic=nst.get("italic", False),
                                           underline=nst.get("underline", False), color=nst.get("color"), font=nst.get("font"), link=nst.get("link")))
                        walk(ch, nst)
                    elif tag == "line-break":
                        out.append(Run("\n"))
                    elif tag == "s":
                        out.append(Run(" " * int(ch.get(_o("text:c"), "1"))))
                    if ch.tail:
                        out.append(Run(ch.tail, size=st.get("size"), bold=st.get("bold", False), italic=st.get("italic", False),
                                       underline=st.get("underline", False), color=st.get("color"), font=st.get("font"), link=st.get("link")))
            first = {k: v for k, v in tprops.get(el.get(_o("text:style-name"), ""), {}).items() if v}
            if el.text:
                out.append(Run(el.text, size=first.get("size"), bold=first.get("bold", False), italic=first.get("italic", False),
                               color=first.get("color"), font=first.get("font")))
            walk(el, first)
            return out

        def paras_of(node, role, level=0, numbered=False):
            size = {"title": 44.0, "body": 28.0}.get(role, 18.0)
            out = []
            for ch in node:
                tag = ch.tag.split("}")[-1]
                if tag in ("p", "h"):
                    para = Para(runs_of(ch, size), level=level, align=pprops.get(ch.get(_o("text:style-name"), ""), "l"), size=size)
                    out.append(para)
                elif tag == "list":
                    num = lists.get(ch.get(_o("text:style-name"), ""), False) or numbered
                    for item in ch.findall(_o("text:list-item")):
                        sub = paras_of(item, role, level + (1 if node.tag.endswith("list-item") else 0), num)
                        for k, p in enumerate(sub):
                            if k == 0 and not p.bullet and p.level == level + (1 if node.tag.endswith("list-item") else 0):
                                p.bullet = "#" if num else "•"
                        out += sub
            return out

        body = content.find(f".//{_o('office:presentation')}")
        for page in (body if body is not None else []):
            if page.tag != _o("draw:page"):
                continue
            slide = Slide()
            slide.bg = gprops.get(page.get(_o("draw:style-name"), ""), {}).get("bg")
            for el in page:
                tag = el.tag.split("}")[-1]
                if tag == "notes":
                    slide.notes = "\n".join("".join(p.itertext()) for p in el.iter(_o("text:p")) if "".join(p.itertext()).strip())
                    continue
                if tag not in ("frame", "rect", "ellipse", "line", "custom-shape"):
                    continue
                if tag == "line":
                    x1, y1, x2, y2 = (_len(el.get(_o(f"svg:{a}"))) for a in ("x1", "y1", "x2", "y2"))
                    g = gprops.get(el.get(_o("draw:style-name"), ""), {})
                    slide.shapes.append(Shape(kind="line", x=min(x1, x2), y=min(y1, y2), w=abs(x2 - x1), h=abs(y2 - y1),
                                              line=g.get("line") or "000000", line_w=g.get("line_w") or 12700.0, geom="line"))
                    continue
                geo = dict(x=_len(el.get(_o("svg:x"))), y=_len(el.get(_o("svg:y"))), w=_len(el.get(_o("svg:width"))), h=_len(el.get(_o("svg:height"))))
                g = gprops.get(el.get(_o("draw:style-name"), ""), {})
                cls = el.get(_o("presentation:class"), "")
                role = {"title": "title", "subtitle": "subtitle", "outline": "body"}.get(cls, "")
                img = el.find(_o("draw:image"))
                tb = el.find(_o("draw:text-box"))
                tbl = el.find(_o("table:table"))
                if img is not None and img.get(_o("xlink:href")):
                    data = blob(img.get(_o("xlink:href")))
                    if data:
                        ext = img.get(_o("xlink:href")).rsplit(".", 1)[-1].lower()
                        slide.shapes.append(Shape(kind="picture", image=data, image_ext="jpg" if ext == "jpeg" else ext, **geo))
                elif tbl is not None:
                    rows = [["".join(c.itertext()).strip() for c in r.findall(_o("table:table-cell"))] for r in tbl.iter(_o("table:table-row"))]
                    slide.shapes.append(Shape(kind="table", rows=rows, **geo))
                else:
                    container = tb if tb is not None else el
                    paras = [p for p in paras_of(container, role or "other") if p.text.strip()]
                    geom = {"ellipse": "ellipse"}.get(tag, "rect")
                    if paras or g.get("fill") or g.get("line"):
                        slide.shapes.append(Shape(kind="text" if paras else "rect", paras=paras, fill=g.get("fill"), line=g.get("line"),
                                                  line_w=g.get("line_w") or 0.0, role=role, geom=geom, **geo))
            deck.slides.append(slide)
        return deck


def read_odp_deck(path) -> Deck:
    return read_odp(path)
