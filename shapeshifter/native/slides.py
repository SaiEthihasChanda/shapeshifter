"""Presentations: one slide model (``Deck``) that PPTX and ODP files are read into, and that can
be drawn to PDF, turned into an outline (text / Markdown / HTML / Word), or written back out as PPTX
or ODP. Slides are approximated from their shapes: text boxes, pictures, tables and simple
shapes keep their position, size, colour and text formatting; animations, SmartArt, charts and
exotic shapes are simplified.
"""

from __future__ import annotations

import colorsys
import io
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

EMU_PER_PT = 12700
EMU_PER_IN = 914400
EMU_PER_CM = 360000

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def q(tag: str) -> str:
    prefix, name = tag.split(":")
    return "{%s}%s" % (NS[prefix], name)


# ------------------------------------------------------------------- the model

@dataclass
class Run:
    text: str
    size: float | None = None            # points
    bold: bool = False
    italic: bool = False
    underline: bool = False
    color: str | None = None             # "rrggbb"
    font: str | None = None
    link: str | None = None


@dataclass
class Para:
    runs: list[Run] = field(default_factory=list)
    level: int = 0
    bullet: str | None = None            # "•" etc., or "#" for a numbered item
    align: str = "l"                     # l | ctr | r | just
    size: float | None = None            # default size when a run has none

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)


@dataclass
class Shape:
    kind: str                            # text | picture | table | rect | ellipse | line | roundrect
    x: float = 0.0                       # EMU, slide coordinates
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    paras: list[Para] = field(default_factory=list)
    image: bytes | None = None
    image_ext: str = "png"
    rows: list[list[str]] = field(default_factory=list)
    fill: str | None = None
    line: str | None = None
    line_w: float = 0.0                  # EMU
    role: str = ""                       # title | subtitle | body | "" (placeholder role)
    anchor: str = "t"                    # vertical text anchor: t | ctr | b
    inset: tuple[float, float, float, float] = (91440, 45720, 91440, 45720)   # l, t, r, b
    name: str = ""
    geom: str = "rect"                  # outline of a filled/outlined shape
    col_w: list[float] = field(default_factory=list)   # table column widths (EMU)
    row_h: list[float] = field(default_factory=list)   # table row heights (EMU)


@dataclass
class Slide:
    shapes: list[Shape] = field(default_factory=list)
    notes: str = ""
    bg: str | None = None
    bg_image: bytes | None = None

    @property
    def title(self) -> str:
        for s in self.shapes:
            if s.role in ("title", "ctrTitle") and s.paras:
                return " ".join(p.text for p in s.paras if p.text).strip()
        return ""


@dataclass
class Deck:
    width: float = 12192000.0            # 13.333 in
    height: float = 6858000.0            # 7.5 in
    slides: list[Slide] = field(default_factory=list)
    title: str = ""


# --------------------------------------------------------------------- colours

DEFAULT_THEME = {"dk1": "000000", "lt1": "ffffff", "dk2": "44546a", "lt2": "e7e6e6", "accent1": "4472c4",
                 "accent2": "ed7d31", "accent3": "a5a5a5", "accent4": "ffc000", "accent5": "5b9bd5",
                 "accent6": "70ad47", "hlink": "0563c1", "folHlink": "954f72"}
DEFAULT_CLRMAP = {"bg1": "lt1", "tx1": "dk1", "bg2": "lt2", "tx2": "dk2"}
PRESET = {"black": "000000", "white": "ffffff", "red": "ff0000", "green": "00ff00", "blue": "0000ff",
          "yellow": "ffff00", "gray": "808080"}


def _adjust(hexcolor: str, node) -> str:
    """Apply lumMod / lumOff / tint / shade children of a colour element."""
    r, g, b = (int(hexcolor[i:i + 2], 16) / 255 for i in (0, 2, 4))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    for ch in node:
        tag = ch.tag.split("}")[-1]
        try:
            v = int(ch.get("val", "100000")) / 100000
        except ValueError:
            continue
        if tag == "lumMod":
            l *= v
        elif tag == "lumOff":
            l += v
        elif tag == "tint":
            l = l * v + (1 - v)
        elif tag == "shade":
            l *= v
    r, g, b = colorsys.hls_to_rgb(h, max(0.0, min(1.0, l)), s)
    return "%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


class Palette:
    def __init__(self, theme: dict | None = None, clrmap: dict | None = None):
        self.theme = {**DEFAULT_THEME, **(theme or {})}
        self.clrmap = {**DEFAULT_CLRMAP, **(clrmap or {})}

    def resolve(self, node) -> str | None:
        """A colour element (srgbClr / schemeClr / prstClr / sysClr, or a wrapper holding one)."""
        if node is None:
            return None
        tag = node.tag.split("}")[-1]
        if tag in ("solidFill", "fgClr", "bgClr", "gsLst"):
            for ch in node:
                got = self.resolve(ch)
                if got:
                    return got
            return None
        if tag == "srgbClr":
            base = node.get("val", "000000")
        elif tag == "schemeClr":
            name = node.get("val", "tx1")
            name = self.clrmap.get(name, name)
            base = self.theme.get(name)
            if base is None:
                return None
        elif tag == "sysClr":
            base = node.get("lastClr") or ("ffffff" if node.get("val") == "window" else "000000")
        elif tag == "prstClr":
            base = PRESET.get(node.get("val", "black"), "000000")
        else:
            return None
        base = base.lower()
        return _adjust(base, node) if len(node) else base


# ------------------------------------------------------------------ PPTX reader

def _rels(z: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    """relationship id -> (type suffix, absolute part name)"""
    path = posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")
    try:
        root = ET.fromstring(z.read(path))
    except KeyError:
        return {}
    out = {}
    for rel in root.findall(q("rel:Relationship")):
        if rel.get("TargetMode") == "External":
            out[rel.get("Id")] = (rel.get("Type", "").rsplit("/", 1)[-1], rel.get("Target", ""))
            continue
        target = rel.get("Target", "")
        full = target.lstrip("/") if target.startswith("/") else posixpath.normpath(
            posixpath.join(posixpath.dirname(part), target))
        out[rel.get("Id")] = (rel.get("Type", "").rsplit("/", 1)[-1], full)
    return out


def _xml(z, part: str):
    try:
        return ET.fromstring(z.read(part))
    except (KeyError, ET.ParseError):
        return None


def _theme(z, rels) -> dict:
    for rtype, target in rels.values():
        if rtype == "theme":
            root = _xml(z, target)
            if root is None:
                break
            out = {}
            scheme = root.find(".//" + q("a:clrScheme"))
            if scheme is not None:
                for ch in scheme:
                    name = ch.tag.split("}")[-1]
                    inner = ch[0] if len(ch) else None
                    if inner is not None:
                        if inner.tag.endswith("sysClr"):
                            out[name] = inner.get("lastClr", "000000")
                        else:
                            out[name] = inner.get("val", "000000")
            return out
    return {}


def _emu_xfrm(sp_pr) -> tuple[float, float, float, float] | None:
    x = sp_pr.find(q("a:xfrm")) if sp_pr is not None else None
    if x is None:
        return None
    off, ext = x.find(q("a:off")), x.find(q("a:ext"))
    if off is None or ext is None:
        return None
    return (float(off.get("x", 0)), float(off.get("y", 0)), float(ext.get("cx", 0)), float(ext.get("cy", 0)))


def _ph(sp):
    """(type, idx) of a placeholder shape, or None."""
    for path in ("nvSpPr", "nvPicPr", "nvGraphicFramePr"):
        ph = sp.find(f"{{{NS['p']}}}{path}/{{{NS['p']}}}nvPr/{{{NS['p']}}}ph")
        if ph is not None:
            return ph.get("type", "body"), ph.get("idx", "0")
    return None


class _PptxReader:
    def __init__(self, z: zipfile.ZipFile):
        self.z = z
        self.pres_part = "ppt/presentation.xml"
        self.pres = _xml(z, self.pres_part)
        self.pres_rels = _rels(z, self.pres_part)
        self.palette = Palette(_theme(z, self.pres_rels))
        self._layout_cache: dict[str, tuple] = {}

    # -- master / layout inheritance
    def _master_info(self, layout_part: str):
        got = self._layout_cache.get(layout_part)
        if got is not None:
            return got
        layout = _xml(self.z, layout_part)
        lrels = _rels(self.z, layout_part)
        master_part = next((t for ty, t in lrels.values() if ty == "slideMaster"), None)
        master = _xml(self.z, master_part) if master_part else None
        clrmap = {}
        styles = {"title": {}, "body": {}, "other": {}}
        if master is not None:
            cm = master.find(q("p:clrMap"))
            if cm is not None:
                clrmap = dict(cm.attrib)
            tx = master.find(q("p:txStyles"))
            if tx is not None:
                for key, tag in (("title", "p:titleStyle"), ("body", "p:bodyStyle"), ("other", "p:otherStyle")):
                    st = tx.find(q(tag))
                    if st is None:
                        continue
                    for lvl in range(1, 10):
                        pp = st.find(q(f"a:lvl{lvl}pPr"))
                        if pp is None:
                            continue
                        d = pp.find(q("a:defRPr"))
                        size = float(d.get("sz")) / 100 if d is not None and d.get("sz") else None
                        bu = pp.find(q("a:buChar"))
                        styles[key][lvl - 1] = {"size": size, "bullet": bu.get("char") if bu is not None else None,
                                                "color": d.find(q("a:solidFill")) if d is not None else None}
        info = (layout, master, Palette(self.palette.theme, clrmap), styles, master_part)
        self._layout_cache[layout_part] = info
        return info

    @staticmethod
    def _find_ph(tree, ph):
        """The layout/master placeholder a slide placeholder inherits its position from."""
        if tree is None or ph is None:
            return None
        want_type, want_idx = ph

        def norm(t):
            return "title" if t in ("title", "ctrTitle") else t
        best = None
        for sp in tree.iter(q("p:sp")):
            got = _ph(sp)
            if got is None:
                continue
            if got[1] == want_idx and norm(got[0]) == norm(want_type):
                return sp
            if norm(got[0]) == norm(want_type) and best is None:
                best = sp
        return best

    def slide_parts(self) -> list[str]:
        out = []
        for sid in self.pres.iter(q("p:sldId")):
            rid = sid.get(q("r:id"))
            if rid in self.pres_rels:
                out.append(self.pres_rels[rid][1])
        return out

    def deck(self) -> Deck:
        size = self.pres.find(q("p:sldSz"))
        deck = Deck(float(size.get("cx", 12192000)) if size is not None else 12192000.0,
                    float(size.get("cy", 6858000)) if size is not None else 6858000.0)
        for part in self.slide_parts():
            try:
                deck.slides.append(self.slide(part))
            except Exception:                              # noqa: BLE001 - one bad slide shouldn't lose the rest
                deck.slides.append(Slide())
        try:
            core = _xml(self.z, "docProps/core.xml")
            t = core.find("{http://purl.org/dc/elements/1.1/}title") if core is not None else None
            deck.title = (t.text or "").strip() if t is not None else ""
        except Exception:                                  # noqa: BLE001
            pass
        return deck

    def slide(self, part: str) -> Slide:
        root = _xml(self.z, part)
        rels = _rels(self.z, part)
        layout_part = next((t for ty, t in rels.values() if ty == "slideLayout"), None)
        layout, master, palette, styles, master_part = self._master_info(layout_part) if layout_part else (
            None, None, self.palette, {"title": {}, "body": {}, "other": {}}, None)
        slide = Slide()
        # background: slide, then layout, then master
        for src_root, src_part in ((root, part), (layout, layout_part), (master, master_part)):
            if src_root is None:
                continue
            bg = src_root.find(f"{q('p:cSld')}/{q('p:bg')}")
            if bg is None:
                continue
            fill = bg.find(f"{q('p:bgPr')}/{q('a:solidFill')}")
            if fill is not None:
                slide.bg = palette.resolve(fill)
                break
            blip = bg.find(f".//{q('a:blip')}")
            if blip is not None:
                rr = _rels(self.z, src_part)
                tgt = rr.get(blip.get(q("r:embed")))
                if tgt:
                    try:
                        slide.bg_image = self.z.read(tgt[1])
                    except KeyError:
                        pass
                    break
            ref = bg.find(q("p:bgRef"))
            if ref is not None and len(ref):
                slide.bg = palette.resolve(ref[0])
                break
        tree = root.find(f"{q('p:cSld')}/{q('p:spTree')}")
        ctx = (rels, layout, master, palette, styles)
        if tree is not None:
            self._shapes(tree, slide, ctx, (1.0, 1.0, 0.0, 0.0, 0.0, 0.0))
        notes_part = next((t for ty, t in rels.values() if ty == "notesSlide"), None)
        if notes_part:
            n = _xml(self.z, notes_part)
            if n is not None:
                texts = []
                for sp in n.iter(q("p:sp")):
                    ph = _ph(sp)
                    if ph and ph[0] == "body":
                        texts += ["".join(t.text or "" for t in p.iter(q("a:t"))) for p in sp.iter(q("a:p"))]
                slide.notes = "\n".join(x for x in texts if x.strip())
        return slide

    # group transform: (sx, sy, tx, ty, ...): x' = x * sx + tx
    def _shapes(self, tree, slide: Slide, ctx, tf) -> None:
        rels, layout, master, palette, styles = ctx
        for el in tree:
            tag = el.tag.split("}")[-1]
            if tag == "grpSp":
                gp = el.find(q("p:grpSpPr"))
                x = gp.find(q("a:xfrm")) if gp is not None else None
                ntf = tf
                if x is not None:
                    off, ext = x.find(q("a:off")), x.find(q("a:ext"))
                    ch_off, ch_ext = x.find(q("a:chOff")), x.find(q("a:chExt"))
                    if None not in (off, ext, ch_off, ch_ext) and float(ch_ext.get("cx", 0)) and float(ch_ext.get("cy", 0)):
                        sx = float(ext.get("cx")) / float(ch_ext.get("cx")) * tf[0]
                        sy = float(ext.get("cy")) / float(ch_ext.get("cy")) * tf[1]
                        tx = tf[2] + (float(off.get("x")) - float(ch_off.get("x")) * float(ext.get("cx")) / float(ch_ext.get("cx"))) * tf[0]
                        ty = tf[3] + (float(off.get("y")) - float(ch_off.get("y")) * float(ext.get("cy")) / float(ch_ext.get("cy"))) * tf[1]
                        ntf = (sx, sy, tx, ty, 0.0, 0.0)
                self._shapes(el, slide, ctx, ntf)
            elif tag in ("sp", "cxnSp"):
                shape = self._sp(el, ctx, tf)
                if shape is not None:
                    slide.shapes.append(shape)
            elif tag == "pic":
                shape = self._pic(el, ctx, tf)
                if shape is not None:
                    slide.shapes.append(shape)
            elif tag == "graphicFrame":
                shape = self._frame(el, ctx, tf)
                if shape is not None:
                    slide.shapes.append(shape)

    @staticmethod
    def _apply(tf, box):
        x, y, w, h = box
        return (x * tf[0] + tf[2], y * tf[1] + tf[3], w * tf[0], h * tf[1])

    def _box(self, el, ctx, tf, sp_pr):
        rels, layout, master, palette, styles = ctx
        box = _emu_xfrm(sp_pr)
        if box is None:
            ph = _ph(el)
            for tree in (layout, master):
                found = self._find_ph(tree, ph)
                if found is not None:
                    box = _emu_xfrm(found.find(q("p:spPr")))
                    if box:
                        break
        return self._apply(tf, box) if box else None

    def _style_color(self, el, which: str, palette):
        st = el.find(q("p:style"))
        if st is None:
            return None, 0
        ref = st.find(q(f"a:{which}"))
        if ref is None:
            return None, 0
        idx = int(ref.get("idx", "0") or 0)
        return (palette.resolve(ref[0]) if len(ref) else None), idx

    def _sp(self, el, ctx, tf) -> Shape | None:
        rels, layout, master, palette, styles = ctx
        sp_pr = el.find(q("p:spPr"))
        ph = _ph(el)
        if ph and ph[0] in ("dt", "ftr", "sldNum", "hdr"):
            return None
        box = self._box(el, ctx, tf, sp_pr)
        if box is None:
            return None
        prst = sp_pr.find(f"{q('a:prstGeom')}") if sp_pr is not None else None
        geom = prst.get("prst", "rect") if prst is not None else "rect"
        kind = {"ellipse": "ellipse", "roundRect": "roundrect", "line": "line", "straightConnector1": "line"}.get(geom, "rect")
        if el.tag.endswith("cxnSp"):
            kind = "line"
        cnv = el.find(f".//{q('p:cNvPr')}")
        shape = Shape(kind="text", x=box[0], y=box[1], w=box[2], h=box[3], name=cnv.get("name", "") if cnv is not None else "")
        # fill and outline
        fill = sp_pr.find(q("a:solidFill")) if sp_pr is not None else None
        if fill is not None:
            shape.fill = palette.resolve(fill)
        elif sp_pr is not None and sp_pr.find(q("a:noFill")) is None:
            col, idx = self._style_color(el, "fillRef", palette)
            if idx:
                shape.fill = col
        ln = sp_pr.find(q("a:ln")) if sp_pr is not None else None
        if ln is not None and ln.find(q("a:noFill")) is None:
            lf = ln.find(q("a:solidFill"))
            shape.line = palette.resolve(lf) if lf is not None else self._style_color(el, "lnRef", palette)[0]
            shape.line_w = float(ln.get("w", 9525)) * tf[0]
        elif ln is None:
            col, idx = self._style_color(el, "lnRef", palette)
            if idx and col:
                shape.line, shape.line_w = col, 12700.0
        # text
        body = el.find(q("p:txBody"))
        role = ""
        if ph:
            role = {"title": "title", "ctrTitle": "title", "subTitle": "subtitle"}.get(ph[0], "body")
        shape.role = role
        font_col = self._style_color(el, "fontRef", palette)[0]
        if body is not None:
            bpr = body.find(q("a:bodyPr"))
            if bpr is not None:
                shape.anchor = {"ctr": "ctr", "b": "b"}.get(bpr.get("anchor", "t"), "t")
                shape.inset = tuple(float(bpr.get(k, d)) for k, d in
                                    (("lIns", 91440), ("tIns", 45720), ("rIns", 91440), ("bIns", 45720)))
            scale = 1.0
            fit = bpr.find(q("a:normAutofit")) if bpr is not None else None
            if fit is not None and fit.get("fontScale"):
                scale = float(fit.get("fontScale")) / 100000
            style_kind = "title" if role == "title" else "body" if role in ("body", "subtitle") else "other"
            for p in body.findall(q("a:p")):
                para = self._para(p, palette, styles[style_kind], scale, role, font_col, rels)
                shape.paras.append(para)
            if not any(p.text.strip() for p in shape.paras):
                shape.paras = []
        if not shape.paras:
            if shape.fill is None and shape.line is None:
                return None
            shape.kind = kind if kind != "line" else "line"
        else:
            shape.kind = "text"
            if kind in ("ellipse", "roundrect") and (shape.fill or shape.line):
                shape.kind = "text"
        shape.geom = kind
        return shape

    def _para(self, p, palette, style, scale, role, font_col, rels) -> Para:
        ppr = p.find(q("a:pPr"))
        level = int(ppr.get("lvl", "0")) if ppr is not None else 0
        algn = {"ctr": "ctr", "r": "r", "just": "just"}.get(ppr.get("algn", "l") if ppr is not None else "l", "l")
        lvl_style = style.get(level) or style.get(0) or {}
        default_size = lvl_style.get("size") or {"title": 44.0, "body": 28.0}.get(
            "title" if role == "title" else "body" if role in ("body", "subtitle") else "", 18.0)
        if role == "body" and level:
            default_size = lvl_style.get("size") or max(14.0, 28.0 - 4 * level)
        bullet = None
        if ppr is not None and ppr.find(q("a:buNone")) is not None:
            bullet = None
        elif ppr is not None and ppr.find(q("a:buChar")) is not None:
            bullet = ppr.find(q("a:buChar")).get("char", "•")
        elif ppr is not None and ppr.find(q("a:buAutoNum")) is not None:
            bullet = "#"
        elif role == "body":
            bullet = lvl_style.get("bullet") or "•"
        para = Para(level=level, bullet=bullet, align=algn, size=default_size * scale)
        for node in p:
            tag = node.tag.split("}")[-1]
            if tag == "br":
                para.runs.append(Run("\n"))
            elif tag in ("r", "fld"):
                t = node.find(q("a:t"))
                if t is None or t.text is None:
                    continue
                rpr = node.find(q("a:rPr"))
                run = Run(t.text, size=(float(rpr.get("sz")) / 100 * scale) if rpr is not None and rpr.get("sz") else None)
                if rpr is not None:
                    run.bold = rpr.get("b") in ("1", "true")
                    run.italic = rpr.get("i") in ("1", "true")
                    run.underline = rpr.get("u", "none") != "none"
                    fill = rpr.find(q("a:solidFill"))
                    run.color = palette.resolve(fill) if fill is not None else None
                    lat = rpr.find(q("a:latin"))
                    run.font = lat.get("typeface") if lat is not None else None
                    hl = rpr.find(q("a:hlinkClick"))
                    if hl is not None and hl.get(q("r:id")) in rels:
                        run.link = rels[hl.get(q("r:id"))][1]
                if run.color is None:
                    run.color = font_col or (palette.resolve(lvl_style["color"]) if lvl_style.get("color") is not None else None)
                para.runs.append(run)
        return para

    def _pic(self, el, ctx, tf) -> Shape | None:
        rels, layout, master, palette, styles = ctx
        sp_pr = el.find(q("p:spPr"))
        box = self._box(el, ctx, tf, sp_pr)
        blip = el.find(f".//{q('a:blip')}")
        if box is None or blip is None:
            return None
        rid = blip.get(q("r:embed"))
        if rid not in rels:
            return None
        try:
            data = self.z.read(rels[rid][1])
        except KeyError:
            return None
        ext = rels[rid][1].rsplit(".", 1)[-1].lower()
        crop = el.find(f"{q('p:blipFill')}/{q('a:srcRect')}")
        if crop is not None and any(crop.get(k) for k in "ltrb") and ext in ("png", "jpg", "jpeg", "gif", "bmp", "tiff"):
            try:
                from PIL import Image
                with Image.open(io.BytesIO(data)) as im:
                    l, t, r, b = (int(crop.get(k, "0")) / 100000 for k in "ltrb")
                    w, h = im.size
                    im = im.crop((round(w * l), round(h * t), round(w * (1 - r)), round(h * (1 - b))))
                    buf = io.BytesIO()
                    im.save(buf, "PNG")
                    data, ext = buf.getvalue(), "png"
            except Exception:                              # noqa: BLE001 - keep the uncropped picture
                pass
        cnv = el.find(f".//{q('p:cNvPr')}")
        alt = (cnv.get("descr") or cnv.get("name") or "") if cnv is not None else ""
        return Shape(kind="picture", x=box[0], y=box[1], w=box[2], h=box[3], image=data,
                     image_ext="jpg" if ext == "jpeg" else ext, name=alt)

    def _frame(self, el, ctx, tf) -> Shape | None:
        rels, layout, master, palette, styles = ctx
        x = el.find(q("p:xfrm"))
        off, ext = (x.find(q("a:off")), x.find(q("a:ext"))) if x is not None else (None, None)
        if off is None:
            return None
        box = self._apply(tf, (float(off.get("x", 0)), float(off.get("y", 0)), float(ext.get("cx", 0)), float(ext.get("cy", 0))))
        tbl = el.find(f".//{q('a:tbl')}")
        if tbl is None:                                   # chart / SmartArt / media: mark its place
            return Shape(kind="rect", x=box[0], y=box[1], w=box[2], h=box[3], line="999999", line_w=9525.0,
                         paras=[Para([Run("[chart or diagram]", size=14, color="777777")], align="ctr")], role="")
        rows = []
        col_w = [float(g.get("w", 0)) * tf[0] for g in tbl.findall(f"{q('a:tblGrid')}/{q('a:gridCol')}")]
        row_h = [float(tr.get("h", 0)) * tf[1] for tr in tbl.findall(q("a:tr"))]
        for tr in tbl.findall(q("a:tr")):
            cells = []
            for tc in tr.findall(q("a:tc")):
                paras = ["".join(t.text or "" for t in p.iter(q("a:t"))) for p in tc.iter(q("a:p"))]
                cells.append(" ".join(x for x in paras if x).strip())
            rows.append(cells)
        return Shape(kind="table", x=box[0], y=box[1], w=box[2], h=box[3], rows=rows, col_w=col_w, row_h=row_h)


def read_pptx(path) -> Deck:
    with zipfile.ZipFile(path) as z:
        return _PptxReader(z).deck()


__all__ = ["Deck", "Slide", "Shape", "Para", "Run", "read_pptx", "NS", "q", "Palette", "EMU_PER_PT",
           "EMU_PER_IN", "EMU_PER_CM", "re"]
