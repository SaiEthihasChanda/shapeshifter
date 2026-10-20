"""Write document blocks as an OpenDocument text file (.odt): headings, paragraphs with bold / italic /
code / strike-through / links, bullet and numbered lists, tables, quotes, code and pictures."""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape, quoteattr

from . import docs

XML = '<?xml version="1.0" encoding="UTF-8"?>\n'
NS = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
      'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
      'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
      'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" xmlns:xlink="http://www.w3.org/1999/xlink" '
      'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0"')

STYLES = {                                  # name -> (family, properties XML)
    "Bold": ("text", '<style:text-properties fo:font-weight="bold"/>'),
    "Italic": ("text", '<style:text-properties fo:font-style="italic"/>'),
    "BoldItalic": ("text", '<style:text-properties fo:font-weight="bold" fo:font-style="italic"/>'),
    "Code": ("text", '<style:text-properties style:font-name="Courier New" fo:font-family="Courier New"/>'),
    "Strike": ("text", '<style:text-properties style:text-line-through-style="solid"/>'),
    "Quote": ("paragraph", '<style:paragraph-properties fo:margin-left="1cm" fo:margin-right="1cm"/>'
                           '<style:text-properties fo:font-style="italic"/>'),
    "CodeBlock": ("paragraph", '<style:paragraph-properties fo:margin-left="0.5cm"/>'
                               '<style:text-properties style:font-name="Courier New" fo:font-family="Courier New" fo:font-size="10pt"/>'),
    "Cell": ("table-cell", '<style:table-cell-properties fo:padding="0.1cm" fo:border="0.5pt solid #808080"/>'),
}


def _span(sp) -> str:
    text = escape(sp.text)
    style = ("BoldItalic" if sp.bold and sp.italic else "Bold" if sp.bold else "Italic" if sp.italic else "")
    if sp.code:
        style = "Code"
    if sp.strike:
        text = f'<text:span text:style-name="Strike">{text}</text:span>'
    if style:
        text = f'<text:span text:style-name="{style}">{text}</text:span>'
    if sp.href:
        text = f'<text:a xlink:type="simple" xlink:href={quoteattr(sp.href)}>{text}</text:a>'
    return text


def _inline(text: str) -> str:
    return "".join(_span(sp) for sp in docs.parse_inline(text) if not sp.img)


def blocks_to_odt(blocks: list[docs.Block], base_dirs=None, title: str = "") -> bytes:
    from .images_util import load_image_ref
    body: list[str] = []
    pictures: list[tuple[str, bytes]] = []
    stack: list[str] = []                     # open list kinds
    counters_open = False

    def close_lists(depth: int = 0):
        while len(stack) > depth:
            body.append("</text:list-item></text:list>")
            stack.pop()

    for b in blocks:
        k = b.kind
        if k in ("li", "oli"):
            kind = "Numbered" if k == "oli" else "Bullets"
            depth = b.level + 1
            if len(stack) >= depth and stack[depth - 1] != kind:
                close_lists(depth - 1)                         # a numbered list right after a bullet list (or back)
            if len(stack) >= depth:
                close_lists(depth)
                body.append("</text:list-item><text:list-item>")
            else:
                while len(stack) < depth:
                    body.append(f'<text:list text:style-name="{kind}"><text:list-item>')
                    stack.append(kind)
            body.append(f"<text:p>{_inline(b.text)}</text:p>")
            counters_open = True
            continue
        close_lists()
        if k.startswith("h") and k[1:].isdigit():
            body.append(f'<text:h text:outline-level="{int(k[1:])}">{_inline(b.text)}</text:h>')
        elif k == "p":
            body.append(f"<text:p>{_inline(b.text)}</text:p>")
        elif k == "quote":
            body.append(f'<text:p text:style-name="Quote">{_inline(b.text)}</text:p>')
        elif k == "code":
            for line in b.text.splitlines() or [""]:
                body.append(f'<text:p text:style-name="CodeBlock">{escape(line).replace("  ", " <text:s/>")}</text:p>')
        elif k == "hr":
            body.append("<text:p>―――――――――――</text:p>")
        elif k == "img":
            data = load_image_ref(b.src, list(base_dirs or []))
            if data:
                try:
                    from PIL import Image
                    with Image.open(io.BytesIO(data)) as im:
                        w, h = im.size
                        ext = "png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "jpeg" if data[:3] == b"\xff\xd8\xff" else None
                        if ext is None:
                            buf = io.BytesIO()
                            im.convert("RGBA").save(buf, "PNG")
                            data, ext = buf.getvalue(), "png"
                except Exception:                                  # noqa: BLE001
                    continue
                name = f"Pictures/image{len(pictures) + 1}.{ext}"
                pictures.append((name, data))
                width_cm = min(16.0, w / 96 * 2.54)
                height_cm = width_cm * h / w
                body.append(f'<text:p><draw:frame draw:name="Image{len(pictures)}" text:anchor-type="as-char" svg:width="{width_cm:.2f}cm" '
                            f'svg:height="{height_cm:.2f}cm"><draw:image xlink:href="{name}" xlink:type="simple" xlink:show="embed" '
                            'xlink:actuate="onLoad"/></draw:frame></text:p>')
        elif k == "table" and b.rows:
            cols = max(len(r) for r in b.rows)
            rows = []
            for ri, r in enumerate(b.rows):
                cells = "".join(
                    f'<table:table-cell table:style-name="Cell" office:value-type="string"><text:p>'
                    + (f'<text:span text:style-name="Bold">{escape(str(c))}</text:span>' if ri == 0 else _inline(str(c)))
                    + "</text:p></table:table-cell>" for c in list(r) + [""] * (cols - len(r)))
                rows.append(f"<table:table-row>{cells}</table:table-row>")
            body.append(f'<table:table table:name="Table{len(body)}"><table:table-column table:number-columns-repeated="{cols}"/>'
                        + "".join(rows) + "</table:table>")
            body.append("<text:p/>")
    close_lists()
    del counters_open

    auto = "".join(f'<style:style style:name="{n}" style:family="{fam}">{props}</style:style>' for n, (fam, props) in STYLES.items())

    def list_style(name: str, bullet: bool) -> str:
        levels = []
        for i in range(1, 7):
            props = (f'<style:list-level-properties text:list-level-position-and-space-mode="label-alignment">'
                     f'<style:list-level-label-alignment text:label-followed-by="listtab" text:list-tab-stop-position="{0.9 * i:.1f}cm" '
                     f'fo:text-indent="-0.6cm" fo:margin-left="{0.9 * i:.1f}cm"/></style:list-level-properties>')
            if bullet:
                levels.append(f'<text:list-level-style-bullet text:level="{i}" text:bullet-char="•">{props}</text:list-level-style-bullet>')
            else:
                levels.append(f'<text:list-level-style-number text:level="{i}" style:num-suffix="." style:num-format="1">{props}</text:list-level-style-number>')
        return f'<text:list-style style:name="{name}">{"".join(levels)}</text:list-style>'

    content = (XML + f'<office:document-content {NS} office:version="1.2"><office:automatic-styles>{auto}'
               f'{list_style("Bullets", True)}{list_style("Numbered", False)}</office:automatic-styles>'
               f'<office:body><office:text>{"".join(body) or "<text:p/>"}</office:text></office:body></office:document-content>')
    styles = (XML + f'<office:document-styles {NS} office:version="1.2"><office:automatic-styles>'
              '<style:page-layout style:name="PM1"><style:page-layout-properties fo:page-width="21cm" fo:page-height="29.7cm" '
              'fo:margin-top="2cm" fo:margin-bottom="2cm" fo:margin-left="2.2cm" fo:margin-right="2.2cm"/></style:page-layout>'
              '</office:automatic-styles><office:master-styles><style:master-page style:name="Standard" style:page-layout-name="PM1"/>'
              "</office:master-styles></office:document-styles>")
    meta = (XML + f'<office:document-meta {NS} office:version="1.2"><office:meta><dc:title>{escape(title)}</dc:title>'
            "<meta:generator>ShapeShifter</meta:generator></office:meta></office:document-meta>")
    manifest = (XML + '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">'
                '<manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>'
                '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
                '<manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>'
                '<manifest:file-entry manifest:full-path="meta.xml" manifest:media-type="text/xml"/>'
                + "".join(f'<manifest:file-entry manifest:full-path="{n}" manifest:media-type="image/{n.rsplit(".", 1)[-1]}"/>' for n, _ in pictures)
                + "</manifest:manifest>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.text")
        z.writestr("content.xml", content, zipfile.ZIP_DEFLATED)
        z.writestr("styles.xml", styles, zipfile.ZIP_DEFLATED)
        z.writestr("meta.xml", meta, zipfile.ZIP_DEFLATED)
        z.writestr("META-INF/manifest.xml", manifest, zipfile.ZIP_DEFLATED)
        for name, data in pictures:
            z.writestr(name, data)
    return buf.getvalue()
