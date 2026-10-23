"""Conversion graph: which formats a source can reach, by which path, and what is lost.

Nodes are formats; each backend contributes edges (one conversion step). For a source
format we search for the cheapest path (≤ MAX_STEPS) to every reachable format, so a
format added to any backend automatically gains every conversion that follows from it.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from functools import lru_cache

from .backends import ALL_BACKENDS, Backend
from .backends.archives import EXTRACT
from .backends.media import REMUX
from .formats import PSEUDO_FORMATS, category, file_ext, label_of, name_of, rank

log = logging.getLogger(__name__)

MAX_STEPS = 3
HOP_PENALTY = 1.0     # prefer fewer steps
LOSS_PENALTY = 1.5    # prefer lossless routes

LOSSLESS = {"png", "bmp", "tiff", "tga", "qoi", "gif", "ico", "psd", "dds", "icns", "pcx", "ppm",
            "svg", "jp2", "wav", "flac", "aiff"}
LOSSY = {"jpg", "webp", "avif", "heic", "mp3", "aac", "m4a", "ogg", "opus", "wma", "ac3", "amr"}
ALPHA = {"png", "webp", "gif", "tiff", "ico", "tga", "qoi", "avif", "heic", "psd", "svg", "dds",
         "icns"}
ANIMATED = {"gif", "webp"}

WARNINGS = {
    "lossy": "Lossy — some quality is lost",
    "recompress": "Re-compressed — slight quality loss",
    "reencode": "Video is re-encoded (slower, slight quality loss)",
    "no_alpha": "Transparency becomes white",
    "first_frame": "Only the first frame is kept",
    "no_audio": "No sound",
    "per_page": "May produce several files (one per page or table)",
    "first_sheet": "Only the first sheet/table is kept",
    "text_only": "Only the text is kept",
    "layout": "Layout may change",
    "ocr": "Text recognised by OCR — expect a few mistakes; formatting and pictures aren't kept",
    "reflow": "Reflowed — the page layout is approximated",
    "table_layout": "Tables are found from the page layout; check the columns",
    "table_picture": "Table read from a picture by OCR; check the columns and numbers",
    "raw_preview": "Uses the full-size JPEG preview stored in the RAW file, not the raw sensor data",
    "legacy": "Old binary Office format: the text and values are recovered, not the formatting or pictures",
    "video_frame": "Saves a picture taken from the video",
    "slides_layout": "Slides are redrawn from their contents; fonts, effects and animations are simplified",
    "slides_outline": "Keeps the text, tables and pictures, not the slide layout",
    "pages_as_pictures": "Each page becomes a picture, so the text isn't editable",
}


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    backend: Backend
    cost: float
    terminal: bool
    on_wheel: bool
    flags: frozenset
    first_only: bool = False
    latent: bool = False      # only usable inside routes / learned reverse conversions
    latent_next: bool = False  # only a latent step may follow


# Steps that throw content away are only allowed directly on the user's file, never
# hidden in the middle of a chain.
CONTENT_DROPPING = {"first_frame", "first_sheet", "text_only"}

# Anything that loses something costs extra, so a route that keeps the pictures (DOCX ->
# HTML -> PDF -> PNG) beats an equally long one that keeps only the text.
INFERRING_BACKENDS = {"ocr", "picturedoc"}
LOSSY_FLAGS = {"lossy", "recompress", "reencode"} | CONTENT_DROPPING


@dataclass
class TargetOption:
    key: str                         # "png" | "extract" | "rename:png" | "assume:csv" | "inspect"
    kind: str                        # convert | extract | rename | assume | inspect
    fmt: str | None = None
    path: tuple[Edge, ...] = ()
    cost: float = 0.0
    on_wheel: bool = True
    warnings: list[str] = field(default_factory=list)
    learned: bool = False            # offered because of an earlier reverse conversion
    variants: tuple = ()             # different ways to reach this one target; the wheel asks which
    variant_label: str = ""          # when shown as one of those ways
    variant_note: str = ""
    applies: str = ""               # fact about the file that makes this way worth offering (variants.check)
    title: str = ""                 # wheel label of an action (same-format task)
    terms: tuple = ()               # words that find it when typing to search

    @property
    def label(self) -> str:
        if self.variant_label:
            return self.variant_label
        if self.title:
            return self.title
        if self.kind == "rename":
            return "." + file_ext(self.fmt).upper()
        if self.kind == "assume":
            return self.fmt.upper() + "?"
        if self.kind == "extract":
            return "EXTRACT"
        if self.kind == "inspect":
            return "INSPECT"
        if self.kind == "meta":
            return "PROPERTIES"
        if self.kind == "fill":
            return "FILL FORM"
        if self.kind == "combine":
            return "ONE " + label_of(self.fmt)
        return label_of(self.fmt)

    @property
    def sublabel(self) -> str:
        if self.kind == "rename":
            return "Fix name"
        if self.kind == "assume":
            return "Treat as"
        if self.kind == "extract":
            return "Unpack here"
        if self.kind == "inspect":
            return "What is it?"
        if self.kind == "meta":
            return "Edit details"
        if self.kind == "combine":
            return "Combine"
        if self.kind == "action":
            return "Choose" if self.variant_label else ("Asks how" if self.variants else "Same format")
        if self.variants:
            return "Asks how"
        if self.variant_label:
            return "Choose"
        if self.inferred:
            return "Inferred"
        if self.learned:
            return "Learned"
        return category(self.fmt).capitalize()

    @property
    def inferred(self) -> bool:
        """Comes from reading the file's contents (OCR, picture embedding, form filling)."""
        if self.variants or self.variant_label:
            return False
        return (self.kind == "fill" or (self.kind == "convert" and (
            self.fmt in PSEUDO_FORMATS or any(
                e.backend.name in INFERRING_BACKENDS
                or (e.backend.name == "tables" and category(e.src) == "image") for e in self.path))))

    @property
    def category(self) -> str:
        if self.kind in ("rename", "inspect", "action", "meta"):
            return "tool"
        if self.kind == "extract":
            return "archive"
        if self.inferred:
            return "inferred"
        return category(self.fmt)   # convert / assume

    @property
    def via(self) -> str:
        """e.g. 'LibreOffice → PyMuPDF (via PDF)'."""
        engines = " → ".join(dict.fromkeys(ENGINE_NAMES.get(e.backend.name, e.backend.name)
                                           for e in self.path))
        mids = [e.dst.upper() for e in self.path[:-1]]
        return engines + (f" (via {', '.join(mids)})" if mids else "")

    @property
    def description(self) -> str:
        if self.variant_note:
            return self.variant_note
        if self.kind == "combine":
            return (f"Combines all the selected files into one {label_of(self.fmt)}, in name order. "
                    "The originals are kept.")
        if self.variants:
            return "Asks how: " + " or ".join(v.variant_label.lower() for v in self.variants)
        if self.kind == "rename":
            return f"Rename to .{file_ext(self.fmt)} — the contents are {name_of(self.fmt)}"
        if self.kind == "inspect":
            return "Show what the file contains"
        if self.kind == "meta":
            return ("Shows the details stored in the file (author, title, dates, camera, location, tags) and lets you "
                    "change or remove them. The content itself is not touched.")
        if self.kind == "assume":
            return f"Not sure what this is — treat it as {name_of(self.fmt)}, then pick a format"
        if self.kind == "extract":
            return "Unpacks the archive into a folder next to it"
        if self.kind == "fill":
            return ("Opens the form with its blanks, check boxes and signature line found for you. "
                    "Type the answers; the original picture is kept and a filled copy is saved beside it.")
        if self.fmt == "docxpic":
            return ("Puts the picture on a Word page so you can type next to it or over it. "
                    "Convert the document back to an image to bake the text in.")
        if self.fmt == "pdfocr":
            return ("Adds an invisible text layer (OCR) so the scan can be searched and copied "
                    "from. The page looks exactly the same.")
        return "; ".join(self.warnings) or "Converts without notable loss"


ENGINE_NAMES = {"images": "Pillow", "mupdf": "PyMuPDF", "ffmpeg": "ffmpeg", "data": "built-in",
                "archives": "built-in", "pandoc": "pandoc", "libreoffice": "LibreOffice",
                "native": "native", "custom": "custom format", "ocr": "macOS text recognition" if sys.platform == "darwin" else "Windows OCR",
                "picturedoc": "built-in", "tables": "built-in tables", "slides": "built-in slides",
                "docread": "built-in", "raw": "built-in RAW preview", "sevenzip": "7-Zip"}


def edge_flags(src: str, dst: str, backend: Backend) -> frozenset:
    f = set()
    sc, dc = category(src), category(dst)
    if dst in LOSSY:
        if src in LOSSLESS:
            f.add("lossy")
        elif src in LOSSY or sc == "video":
            f.add("recompress")
    if sc == "image" and dst in ("jpg", "bmp", "pdf") and src in ALPHA:
        f.add("no_alpha")
    if src in ANIMATED and dc == "image" and dst not in ANIMATED | {"png", "avif", "tiff", "pdf"}:
        f.add("first_frame")
    if sc == "video" and dc == "video" and dst not in REMUX:
        f.add("reencode")
    if sc == "video" and dst == "gif":
        f |= {"no_audio", "lossy"}
    if backend.multi_output(src, dst) and dst != EXTRACT:
        f.add("per_page")
    if src in ("xlsx", "ods", "xls", "sqlite") and dst in ("csv", "tsv"):
        f.add("first_sheet")
    if dst == "txt" and sc == "document":
        f.add("text_only")
    if backend.name in ("libreoffice", "pandoc"):
        f.add("layout")
    if backend.name == "native" and sc == "document" and (dst == "pdf" or src == "html"):
        f.add("layout")
    if backend.name == "ocr":
        # A searchable PDF keeps the page exactly as it looks, so there's nothing to warn about.
        f.discard("layout")
        if dst != "pdfocr":
            f.add("ocr")
    if backend.name == "tables" and dst in ("csv", "tsv", "xlsx", "json", "ods"):
        if src == "pdf":
            f.add("table_layout")
        elif sc == "image":
            f.add("table_picture")
    if backend.name == "raw":
        f.add("raw_preview")
    if src in ("doc", "xls", "ppt") and backend.name in ("docread", "slides", "data", "tables"):
        f.add("legacy")
    if backend.name == "ffmpeg" and sc == "video" and dst in ("png", "jpg", "webp"):
        f.add("video_frame")
    if backend.name == "slides":
        if src in ("pptx", "odp", "ppsx", "potx"):
            f.add("slides_layout" if dst in ("pdf", "pptx", "odp") else "slides_outline")
        elif src == "pdf":
            f.add("pages_as_pictures")
    if backend.name == "mupdf" and src == "pdf" and dst == "html":
        f.add("reflow")
    return frozenset(f)


NATIVE_BIAS = 0.6   # native engines: cheaper with "prefer native", dearer otherwise


@lru_cache(maxsize=1)
def _edges() -> dict[str, list[Edge]]:
    from . import config
    prefer_native = bool(config.load().get("prefer_native", False))
    graph: dict[str, list[Edge]] = {}
    for b in ALL_BACKENDS:
        try:
            if not b.available():
                continue
            for src in b.sources():
                wheel = b.targets(src)
                latent = b.latent_targets(src) - wheel
                for dst in wheel | b.extra_targets(src) | latent:
                    if dst == src:
                        continue
                    flags = edge_flags(src, dst, b)
                    cost = b.cost(src, dst) + (LOSS_PENALTY if flags & LOSSY_FLAGS else 0)
                    if getattr(b, "native", False):
                        cost += -NATIVE_BIAS if prefer_native else NATIVE_BIAS
                    first_only = b.first_step_only(src, dst) or bool(flags & CONTENT_DROPPING)
                    graph.setdefault(src, []).append(Edge(
                        src, dst, b, cost, b.terminal(src, dst), dst in wheel, flags, first_only,
                        latent=dst in latent, latent_next=b.only_latent_after(src, dst)))
        except Exception:
            log.exception("Backend %s failed while building the graph", b.name)
    return graph


def _search(src: str, *, latent: bool, max_steps: int, first_only_rule: bool = True,
            latent_mid: bool = False):
    """Cheapest path (≤ max_steps) from src to every reachable node."""
    graph = _edges()
    best: dict[str, tuple[float, tuple[Edge, ...]]] = {}
    frontier: dict[str, tuple[float, tuple[Edge, ...]]] = {src: (0.0, ())}
    for _ in range(max_steps):
        nxt: dict[str, tuple[float, tuple[Edge, ...]]] = {}
        for node, (cost, path) in frontier.items():
            # Latent steps (e.g. "text -> any text format") may only finish a route, unless
            # we're looking for conditional routes ("if the file really is PEM"): then a
            # "treat as" step is allowed only as the FIRST step, i.e. a claim about the
            # user's own file, never about the output of a conversion.
            if path and (path[-1].terminal or
                         (path[-1].latent and not (latent_mid and len(path) == 1))):
                continue
            visited = {src} | {e.dst for e in path}
            for e in graph.get(node, []):
                if e.dst in visited or (e.latent and not latent):
                    continue
                if path and path[-1].latent_next and not e.latent:
                    continue
                if path and e.first_only and first_only_rule:
                    continue
                c = cost + e.cost + (HOP_PENALTY if path else 0.0)
                if e.dst not in best or c < best[e.dst][0]:
                    best[e.dst] = (c, path + (e,))
                    nxt[e.dst] = best[e.dst]
        frontier = nxt
        if not frontier:
            break
    return best


def _option(dst: str, cost: float, path: tuple[Edge, ...]) -> TargetOption:
    flags = set().union(*(e.flags for e in path))
    warnings = [WARNINGS[f] for f in WARNINGS if f in flags]
    if len(path) > 1:
        mids = ", ".join(e.dst.upper() for e in path[:-1])
        warnings.insert(0, f"{len(path)}-step conversion (via {mids})")
    kind = "extract" if dst == EXTRACT else "convert"
    return TargetOption(key=dst, kind=kind, fmt=None if kind == "extract" else dst, path=path,
                        cost=cost, warnings=warnings, on_wheel=all(e.on_wheel for e in path))


@lru_cache(maxsize=512)
def plan(src: str) -> dict[str, TargetOption]:
    """Cheapest path from ``src`` to every format offered on the wheel (key -> option)."""
    return {dst: _option(dst, cost, path)
            for dst, (cost, path) in _search(src, latent=False, max_steps=MAX_STEPS).items()}


@lru_cache(maxsize=2048)
def route(src: str, dst: str, conditional: bool = False) -> TargetOption | None:
    """Any way at all from src to dst, including latent steps and longer chains.
    Used by learned reverse conversions, the route finder and explicit CLI requests.

    ``conditional`` also allows a latent "treat as" step in the middle of a route
    (e.g. TXT -> PEM -> DER). Such routes only work when the file's content really is
    that format, so they're for the route finders only; for an actual file the content
    is identified anyway (a .txt holding a certificate *is* PEM and offers DER directly).
    """
    found = plan(src).get(dst)
    if found:
        return found
    best = _search(src, latent=True, max_steps=MAX_STEPS + 1)
    if dst in best:
        return _option(dst, *best[dst])
    if not conditional:
        return None
    best = _search(src, latent=True, max_steps=MAX_STEPS + 1, latent_mid=True)
    if dst not in best:
        return None
    opt = _option(dst, *best[dst])
    for e in opt.path[:-1]:
        if e.latent:
            opt.warnings.insert(0, f"Conditional: only works if the {e.src.upper()} content "
                                   f"really is {name_of(e.dst)}")
    return opt


def is_conditional(opt: TargetOption) -> bool:
    return any(e.latent for e in opt.path[:-1])


def graph_nodes_edges() -> tuple[set[str], list[Edge]]:
    edges = [e for es in _edges().values() for e in es]
    nodes = {e.src for e in edges} | {e.dst for e in edges}
    return nodes, edges


def export_graph(path, fmt: str = "dot") -> int:
    """Write the conversion graph (DOT for Graphviz, or JSON). Returns the edge count."""
    import json
    from pathlib import Path
    nodes, edges = graph_nodes_edges()
    if fmt == "json":
        Path(path).write_text(json.dumps({
            "nodes": [{"id": n, "name": name_of(n), "category": category(n)} for n in sorted(nodes)],
            "edges": [{"from": e.src, "to": e.dst, "engine": e.backend.name, "cost": round(e.cost, 2),
                       "latent": e.latent, "on_wheel": e.on_wheel, "loses": sorted(e.flags)}
                      for e in edges]}, indent=1), encoding="utf-8")
    else:
        lines = ["digraph ShapeShifter {", '  rankdir=LR; node [shape=box, fontname="Segoe UI"];']
        lines += [f'  "{e.src}" -> "{e.dst}" [label="{e.backend.name}"'
                  + (', style=dashed' if e.latent else "") + "];" for e in edges]
        Path(path).write_text("\n".join(lines + ["}"]), encoding="utf-8")
    return len(edges)


def ordered(options: list[TargetOption], src_fmt: str | None) -> list[TargetOption]:
    """Wheel order: fix-name first, same category as the source, then by category/rank."""
    src_cat = category(src_fmt) if src_fmt else "image"
    cats = [src_cat, "inferred"] + [c for c in ("image", "video", "audio", "document", "data", "code",
                                                "subtitle", "model", "font", "security", "archive")
                                    if c != src_cat]
    order = {c: i for i, c in enumerate(cats)}
    kind_order = {"assume": 0, "rename": 0, "combine": 0, "convert": 1, "fill": 1, "extract": 2, "action": 2, "meta": 2, "inspect": 3}

    def key(o: TargetOption):
        if o.kind == "assume":
            return (0, 0, (0, ""))       # keep the candidates' confidence order
        if o.kind == "fill":
            return (1, order["inferred"], (-1, ""))      # first in the inferred group
        if o.kind != "convert":
            return (kind_order[o.kind], 0, (0, ""))
        return (1, order.get(o.category, 9), rank(o.fmt))
    return sorted(options, key=key)


def alternatives(src: str, dst: str) -> list[Edge]:
    """Every engine that can do this one step, cheapest first (for fallback)."""
    return sorted((e for e in _edges().get(src, []) if e.dst == dst), key=lambda e: e.cost)


def reset():
    _edges.cache_clear()
    plan.cache_clear()
    route.cache_clear()
