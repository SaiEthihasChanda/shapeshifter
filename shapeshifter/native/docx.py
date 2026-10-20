"""Native DOCX writer (zip + WordprocessingML): no third-party code.

Takes the shared document model (docs.Block) and writes headings, styled runs, hyperlinks,
bullet/numbered lists, tables, code blocks, quotes, rules and embedded images.
"""

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from . import docs
from .images_util import load_image_ref, size_of, to_png_or_jpeg
from .registry import register

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = (f'xmlns:w="{W}" xmlns:r="{R}" '
      'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
      'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
      'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"')

TEXT_WIDTH_EMU = 5731510            # A4 with 1" margins
EMU_PER_PX = 9525                   # 96 dpi
_BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")

STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="{W}">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Calibri" w:cs="Calibri"/><w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="en-US"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="264" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
{''.join(f'<w:style w:type="paragraph" w:styleId="Heading{i}"><w:name w:val="heading {i}"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="{360 - 40 * i}" w:after="120"/><w:outlineLvl w:val="{i - 1}"/></w:pPr><w:rPr><w:b/><w:color w:val="1F3864"/><w:sz w:val="{[40, 32, 28, 26, 24, 22][i - 1]}"/></w:rPr></w:style>' for i in range(1, 7))}
<w:style w:type="character" w:styleId="Hyperlink"><w:name w:val="Hyperlink"/><w:rPr><w:color w:val="0563C1"/><w:u w:val="single"/></w:rPr></w:style>
<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:tblPr><w:tblBorders><w:top w:val="single" w:sz="4" w:color="888888"/><w:left w:val="single" w:sz="4" w:color="888888"/><w:bottom w:val="single" w:sz="4" w:color="888888"/><w:right w:val="single" w:sz="4" w:color="888888"/><w:insideH w:val="single" w:sz="4" w:color="888888"/><w:insideV w:val="single" w:sz="4" w:color="888888"/></w:tblBorders><w:tblCellMar><w:left w:w="100" w:type="dxa"/><w:right w:w="100" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>
</w:styles>"""

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Default Extension="png" ContentType="image/png"/>
<Default Extension="jpeg" ContentType="image/jpeg"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>"""

ROOT_RELS = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="{R}/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>"""


def _x(text: str) -> str:
    return escape(_BAD_XML.sub("", text))


class _Doc:
    def __init__(self, base_dirs: list[Path]):
        self.base_dirs = base_dirs
        self.rels: list[str] = []
        self.media: list[tuple[str, bytes]] = []
        self.links: dict[str, str] = {}
        self.n_image = 0

    def rel_id(self) -> str:
        return f"rId{len(self.rels) + 10}"

    def link(self, href: str) -> str:
        if href not in self.links:
            rid = self.rel_id()
            self.links[href] = rid
            self.rels.append(f'<Relationship Id="{rid}" Type="{R}/hyperlink" '
                             f'Target={quoteattr(href)} TargetMode="External"/>')
        return self.links[href]

    def image_xml(self, src: str, alt: str, max_width: int = TEXT_WIDTH_EMU) -> str | None:
        raw = load_image_ref(src, self.base_dirs)
        packed = to_png_or_jpeg(raw) if raw else None
        size = size_of(packed[1]) if packed else None
        if not packed or not size or not all(size):
            return None
        kind, data = packed
        self.n_image += 1
        name = f"image{self.n_image}.{'jpeg' if kind == 'jpg' else 'png'}"
        self.media.append((name, data))
        rid = self.rel_id()
        self.rels.append(f'<Relationship Id="{rid}" Type="{R}/image" Target="media/{name}"/>')
        cx, cy = size[0] * EMU_PER_PX, size[1] * EMU_PER_PX
        if cx > max_width:
            cy, cx = int(cy * max_width / cx), max_width
        n = self.n_image
        return (f'<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
                f'<wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="{n}" name="Picture {n}" descr={quoteattr(alt)}/>'
                f'<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
                f'<pic:pic><pic:nvPicPr><pic:cNvPr id="{n}" name="{name}"/><pic:cNvPicPr/></pic:nvPicPr>'
                f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
                f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
                f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
                f'</a:graphicData></a:graphic></wp:inline></w:drawing></w:r>')

    # ------------------------------------------------------------------ runs

    def runs(self, text: str, extra_rpr: str = "", max_img: int = TEXT_WIDTH_EMU) -> str:
        out = []
        for s in docs.parse_inline(text):
            if s.img:
                xml = self.image_xml(s.img, s.text, max_img)
                out.append(xml or f"<w:r><w:t xml:space=\"preserve\">{_x('[image: ' + s.text + ']')}</w:t></w:r>")
                continue
            rpr = extra_rpr
            if s.bold:
                rpr += "<w:b/>"
            if s.italic:
                rpr += "<w:i/>"
            if s.strike:
                rpr += "<w:strike/>"
            if s.code:
                rpr += '<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/><w:shd w:val="clear" w:color="auto" w:fill="F0F0F0"/>'
            if s.href:
                rpr = '<w:rStyle w:val="Hyperlink"/>' + rpr
            parts = s.text.split("\n")
            body = "".join(("<w:br/>" if i else "") + f'<w:t xml:space="preserve">{_x(p)}</w:t>'
                           for i, p in enumerate(parts))
            run = f"<w:r>{f'<w:rPr>{rpr}</w:rPr>' if rpr else ''}{body}</w:r>"
            out.append(f'<w:hyperlink r:id="{self.link(s.href)}">{run}</w:hyperlink>' if s.href else run)
        return "".join(out)

    # ---------------------------------------------------------------- blocks

    def paragraph(self, inner: str, ppr: str = "") -> str:
        return f"<w:p>{f'<w:pPr>{ppr}</w:pPr>' if ppr else ''}{inner}</w:p>"

    def block(self, b: docs.Block, counters: dict) -> str:
        k = b.kind
        if k.startswith("h") and k[1:].isdigit():
            return self.paragraph(self.runs(b.text), f'<w:pStyle w:val="Heading{k[1]}"/>')
        if k in ("li", "oli"):
            if k == "oli":
                counters[b.level] = counters.get(b.level, 0) + 1
                mark = f"{counters[b.level]}."
            else:
                mark = "•"
            left = 720 + 360 * b.level
            lead = f'<w:r><w:t>{mark}</w:t></w:r><w:r><w:tab/></w:r>'
            return self.paragraph(lead + self.runs(b.text),
                                  f'<w:spacing w:after="40"/><w:ind w:left="{left}" w:hanging="360"/>')
        if k == "quote":
            return self.paragraph(self.runs(b.text, "<w:i/><w:color w:val=\"555555\"/>"),
                                  '<w:pBdr><w:left w:val="single" w:sz="18" w:space="8" w:color="BBBBCC"/></w:pBdr>'
                                  '<w:ind w:left="480"/>')
        if k == "code":
            lines = b.text.split("\n")
            body = "".join(("<w:br/>" if i else "") + f'<w:t xml:space="preserve">{_x(ln)}</w:t>'
                           for i, ln in enumerate(lines))
            return self.paragraph(f'<w:r><w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/>'
                                  f'<w:sz w:val="19"/></w:rPr>{body}</w:r>',
                                  '<w:shd w:val="clear" w:color="auto" w:fill="F0F0F0"/>')
        if k == "hr":
            return self.paragraph("", '<w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" w:color="999999"/></w:pBdr>')
        if k == "img":
            xml = self.image_xml(b.src, b.text)
            if xml:
                return self.paragraph(xml)
            return self.paragraph(self.runs(f"[image: {b.text}]" if b.text else "[image]"))
        if k == "table":
            return self.table(b.rows)
        return self.paragraph(self.runs(b.text))

    def table(self, rows: list[list[str]]) -> str:
        if not rows:
            return ""
        cols = max(len(r) for r in rows)
        width = 9026 // max(1, cols)
        grid = "".join(f'<w:gridCol w:w="{width}"/>' for _ in range(cols))
        out = [f'<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="5000" w:type="pct"/></w:tblPr>'
               f"<w:tblGrid>{grid}</w:tblGrid>"]
        cell_img = int(TEXT_WIDTH_EMU / cols)
        for i, row in enumerate(rows):
            out.append("<w:tr>")
            for c in range(cols):
                text = row[c] if c < len(row) else ""
                shade = '<w:shd w:val="clear" w:color="auto" w:fill="EEEEEE"/>' if i == 0 else ""
                runs = self.runs(text, "<w:b/>" if i == 0 else "", cell_img)
                out.append(f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{shade}</w:tcPr>'
                           f'{self.paragraph(runs, "<w:spacing w:after=\"40\"/>")}</w:tc>')
            out.append("</w:tr>")
        out.append("</w:tbl>")
        out.append(self.paragraph(""))        # Word needs a paragraph after a table
        return "".join(out)


def blocks_to_docx(blocks: list[docs.Block], base_dirs: list[Path] | None = None,
                   title: str = "") -> bytes:
    doc = _Doc(base_dirs or [])
    counters: dict[int, int] = {}
    body = []
    for b in blocks:
        body.append(doc.block(b, counters))
        if b.kind != "oli":
            counters.clear()
    if not body:
        body.append(doc.paragraph(""))
    sect = ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" '
            'w:bottom="1440" w:left="1440" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>')
    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:document {NS}><w:body>'
                + "".join(body) + sect + "</w:body></w:document>")
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{R}/styles" Target="styles.xml"/>'
            + "".join(doc.rels) + "</Relationships>")
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>' + _x(title) + "</dc:title>"
            "<dc:creator>ShapeShifter</dc:creator></cp:coreProperties>")
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", ROOT_RELS)
        z.writestr("word/document.xml", document)
        z.writestr("word/styles.xml", STYLES)
        z.writestr("word/_rels/document.xml.rels", rels)
        z.writestr("docProps/core.xml", core)
        for name, data in doc.media:
            z.writestr(f"word/media/{name}", data)
    return buf.getvalue()


# Fallback for md/html/txt -> docx when pandoc isn't installed (pandoc is preferred when
# present because it handles more constructs); "prefer native" puts this first.
@register(["md", "html", "txt"], "docx", cost=3.5)
def text_to_docx(data: bytes, src: str, dst: str, ctx) -> bytes:
    text = data.decode("utf-8-sig", errors="replace")
    title = ""
    if src == "md":
        blocks = docs.parse_markdown(text)
    elif src == "html":
        blocks = docs.parse_html(text)
        title = docs.html_title(text)
    else:
        blocks = docs.parse_text(text)
    return blocks_to_docx(blocks, list(getattr(ctx, "resource_dirs", [])), title)
