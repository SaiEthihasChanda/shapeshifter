"""Read Word (.docx) and OpenDocument text (.odt) files into document blocks: headings, paragraphs
with bold / italic / links, lists, tables and pictures. No external program needed."""

from __future__ import annotations

import base64
import posixpath
import re
import zipfile
from xml.etree import ElementTree as ET

from . import docs

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
RELNS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _mime(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower()
    return {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif"}.get(ext, "image/png")


def _data_uri(data: bytes, name: str) -> str | None:
    ext = name.rsplit(".", 1)[-1].lower()
    if ext not in ("png", "jpg", "jpeg", "gif"):                 # EMF/WMF/WebP…: let Pillow convert, else skip
        try:
            import io

            from PIL import Image
            with Image.open(io.BytesIO(data)) as im:
                buf = io.BytesIO()
                im.convert("RGBA").save(buf, "PNG")
                data, name = buf.getvalue(), "x.png"
        except Exception:                                        # noqa: BLE001
            return None
    return f"data:{_mime(name)};base64,{base64.b64encode(data).decode()}"


def _marked(text: str, bold: bool, italic: bool, strike: bool) -> str:
    if not text.strip():
        return text
    lead, core, trail = text[:len(text) - len(text.lstrip())], text.strip(), text[len(text.rstrip()):]
    core = docs.escape_markdown(core)
    if strike:
        core = f"~~{core}~~"
    if bold:
        core = f"**{core}**"
    if italic:
        core = f"*{core}*"
    return lead + core + trail


# ------------------------------------------------------------------------ DOCX

def docx_blocks(path) -> tuple[list[docs.Block], str]:
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        root = ET.fromstring(z.read("word/document.xml"))
        rels: dict[str, tuple[str, bool]] = {}
        if "word/_rels/document.xml.rels" in names:
            for rel in ET.fromstring(z.read("word/_rels/document.xml.rels")).findall(RELNS + "Relationship"):
                rels[rel.get("Id")] = (rel.get("Target", ""), rel.get("TargetMode") == "External")
        style_names: dict[str, str] = {}
        if "word/styles.xml" in names:
            for st in ET.fromstring(z.read("word/styles.xml")).iter(W + "style"):
                nm = st.find(W + "name")
                style_names[st.get(W + "styleId", "")] = (nm.get(W + "val", "") if nm is not None else "").lower()
        numbered: dict[tuple[str, str], bool] = {}                # (numId, ilvl) -> numbered?
        if "word/numbering.xml" in names:
            num_root = ET.fromstring(z.read("word/numbering.xml"))
            abstract: dict[str, dict[str, bool]] = {}
            for an in num_root.findall(W + "abstractNum"):
                lv = {}
                for l in an.findall(W + "lvl"):
                    fmt = l.find(W + "numFmt")
                    lv[l.get(W + "ilvl", "0")] = fmt is not None and fmt.get(W + "val") not in ("bullet", "none")
                abstract[an.get(W + "abstractNumId")] = lv
            for n in num_root.findall(W + "num"):
                a = n.find(W + "abstractNumId")
                for ilvl, flag in abstract.get(a.get(W + "val") if a is not None else "", {}).items():
                    numbered[(n.get(W + "numId"), ilvl)] = flag

        def media(rid: str) -> str | None:
            if rid not in rels:
                return None
            target, external = rels[rid]
            if external:
                return None
            full = posixpath.normpath(posixpath.join("word", target)) if not target.startswith("/") else target.lstrip("/")
            try:
                return _data_uri(z.read(full), full)
            except KeyError:
                return None

        blocks: list[docs.Block] = []
        title = ""

        def runs_text(p) -> tuple[str, list[str]]:
            out, images = [], []

            def walk(node, link=None):
                for ch in node:
                    tag = ch.tag
                    if tag == W + "r":
                        pr = ch.find(W + "rPr")
                        bold = pr is not None and pr.find(W + "b") is not None and pr.find(W + "b").get(W + "val") not in ("0", "false")
                        italic = pr is not None and pr.find(W + "i") is not None and pr.find(W + "i").get(W + "val") not in ("0", "false")
                        strike = pr is not None and pr.find(W + "strike") is not None
                        text = ""
                        for item in ch:
                            if item.tag == W + "t":
                                text += item.text or ""
                            elif item.tag == W + "tab":
                                text += "    "
                            elif item.tag == W + "br":
                                text += "\n"
                            elif item.tag == W + "drawing" or item.tag == W + "pict":
                                for blip in item.iter(A + "blip"):
                                    uri = media(blip.get(R + "embed", ""))
                                    if uri:
                                        images.append(uri)
                        piece = _marked(text.replace("\n", "  \n"), bold, italic, strike)
                        if link and piece.strip():
                            piece = f"[{piece.strip()}]({link})"
                        out.append(piece)
                    elif tag == W + "hyperlink":
                        target = rels.get(ch.get(R + "id", ""), ("", False))
                        walk(ch, target[0] if target[1] else link)
                    elif tag in (W + "ins", W + "smartTag", W + "sdt", W + "sdtContent"):
                        walk(ch, link)
            walk(p)
            return "".join(out).strip(), images

        def paragraph(p):
            nonlocal title
            ppr = p.find(W + "pPr")
            style_id = ""
            num = None
            if ppr is not None:
                st = ppr.find(W + "pStyle")
                style_id = st.get(W + "val", "") if st is not None else ""
                np_ = ppr.find(W + "numPr")
                if np_ is not None:
                    nid, lvl = np_.find(W + "numId"), np_.find(W + "ilvl")
                    if nid is not None and nid.get(W + "val") not in ("0", None):
                        num = (nid.get(W + "val"), lvl.get(W + "val", "0") if lvl is not None else "0")
            style = style_names.get(style_id, style_id.lower())
            text, images = runs_text(p)
            level = 0
            m = re.match(r"heading\s*(\d)", style) or re.match(r"heading(\d)", style_id.lower())
            if m:
                level = min(6, int(m.group(1)))
            elif style in ("title", "subtitle") or style_id.lower() == "title":
                level = 1 if style != "subtitle" else 2
            if text:
                if level:
                    plain = docs.plain(text)
                    title = title or plain
                    blocks.append(docs.Block(f"h{level}", text))
                elif num is not None or "list" in style:
                    ilvl = int(num[1]) if num else 0
                    blocks.append(docs.Block("oli" if num and numbered.get(num) else "li", text, level=min(ilvl, 5)))
                elif style in ("quote", "intense quote"):
                    blocks.append(docs.Block("quote", text))
                else:
                    blocks.append(docs.Block("p", text))
            for uri in images:
                blocks.append(docs.Block("img", "Picture", src=uri))

        def table(tbl):
            rows = []
            for tr in tbl.findall(W + "tr"):
                cells = []
                for tc in tr.findall(W + "tc"):
                    parts = [runs_text(p)[0] for p in tc.iter(W + "p")]
                    cells.append(" ".join(x for x in parts if x))
                    span = tc.find(f"{W}tcPr/{W}gridSpan")
                    if span is not None:
                        cells += [""] * (int(span.get(W + "val", "1")) - 1)
                rows.append(cells)
            if rows:
                blocks.append(docs.Block("table", rows=[list(r) + [""] * (max(map(len, rows)) - len(r)) for r in rows]))

        body = root.find(W + "body")
        for el in list(body) if body is not None else []:
            if el.tag == W + "p":
                paragraph(el)
            elif el.tag == W + "tbl":
                table(el)
        return blocks, title


# ------------------------------------------------------------------------- ODT

ODF = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
}


def _o(tag: str) -> str:
    p, n = tag.split(":")
    return "{%s}%s" % (ODF[p], n)


def odt_blocks(path) -> tuple[list[docs.Block], str]:
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("content.xml"))
        names = set(z.namelist())
        bold_styles, italic_styles, numbered_lists = set(), set(), set()
        for st in root.iter(_o("style:style")):
            tp = st.find(_o("style:text-properties"))
            if tp is not None:
                if tp.get(_o("fo:font-weight")) == "bold":
                    bold_styles.add(st.get(_o("style:name")))
                if tp.get(_o("fo:font-style")) == "italic":
                    italic_styles.add(st.get(_o("style:name")))
        for ls in root.iter(_o("text:list-style")):
            first = ls[0] if len(ls) else None
            if first is not None and first.tag.endswith("number"):
                numbered_lists.add(ls.get(_o("style:name")))

        def inline(el) -> str:
            out = []

            def walk(node, bold=False, italic=False, link=None):
                if node.text:
                    out.append(_wrap(node.text, bold, italic, link))
                for ch in node:
                    tag = ch.tag.split("}")[-1]
                    if tag == "span":
                        name = ch.get(_o("text:style-name"), "")
                        walk(ch, bold or name in bold_styles, italic or name in italic_styles, link)
                    elif tag == "a":
                        walk(ch, bold, italic, ch.get(_o("xlink:href")))
                    elif tag == "line-break":
                        out.append("  \n")
                    elif tag == "s":
                        out.append(" " * int(ch.get(_o("text:c"), "1")))
                    elif tag == "tab":
                        out.append("    ")
                    if ch.tail:
                        out.append(_wrap(ch.tail, bold, italic, link))

            def _wrap(text, bold, italic, link):
                piece = _marked(text, bold, italic, False)
                return f"[{piece.strip()}]({link})" if link and piece.strip() else piece
            walk(el)
            return "".join(out).strip()

        blocks: list[docs.Block] = []
        title = ""

        def pictures(el):
            for img in el.iter(_o("draw:image")):
                href = img.get(_o("xlink:href"), "")
                if href in names:
                    uri = _data_uri(z.read(href), href)
                    if uri:
                        blocks.append(docs.Block("img", "Picture", src=uri))

        def walk(container, level=0, numbered=False):
            nonlocal title
            for el in container:
                tag = el.tag.split("}")[-1]
                if tag == "h":
                    text = inline(el)
                    if text:
                        lvl = min(6, int(el.get(_o("text:outline-level"), "1") or 1))
                        title = title or docs.plain(text)
                        blocks.append(docs.Block(f"h{lvl}", text))
                elif tag == "p":
                    text = inline(el)
                    if text:
                        blocks.append(docs.Block("li" if level or numbered else "p", text, level=max(0, level - 1)) if level else docs.Block("p", text))
                    pictures(el)
                elif tag == "list":
                    num = el.get(_o("text:style-name"), "") in numbered_lists or numbered
                    for item in el.findall(_o("text:list-item")):
                        for ch in item:
                            ctag = ch.tag.split("}")[-1]
                            if ctag == "p":
                                text = inline(ch)
                                if text:
                                    blocks.append(docs.Block("oli" if num else "li", text, level=level))
                            elif ctag == "list":
                                walk([ch], level + 1, num)
                elif tag == "table":
                    rows = [[" ".join(x for x in ("".join(p.itertext()).strip() for p in c.iter(_o("text:p"))) if x)
                             for c in r.findall(_o("table:table-cell"))] for r in el.iter(_o("table:table-row"))]
                    rows = [r for r in rows if any(r)]
                    if rows:
                        blocks.append(docs.Block("table", rows=[list(r) + [""] * (max(map(len, rows)) - len(r)) for r in rows]))
                elif tag in ("section", "frame", "text-box"):
                    walk(el, level, numbered)

        body = root.find(f".//{_o('office:text')}")
        if body is not None:
            walk(body)
        return blocks, title
