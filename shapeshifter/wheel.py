"""The radial wheel: pick a target format by pointing and clicking.

``WheelView`` draws the wheel and handles hovering; ``WheelOverlay`` is the top-level
pop-up that runs conversions. The settings app embeds a ``WheelView`` as a live preview.
All geometry is in "design units" and scaled by the painter, so ``wheel_scale`` resizes
everything uniformly.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QRectF, Qt, QThread, QTimer, \
    QVariantAnimation, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QPainter, \
    QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from . import config, engine, themes
from .backends import Cancelled, Context, ConversionError
from .formats import search_terms
from .identify import identify
from .planner import TargetOption

log = logging.getLogger(__name__)

HUB_R = 56
RING_GAP = 10
# Plain "Segoe UI": resolving "Segoe UI Variable" adds ~0.4 s to the first paint.
FONT_FAMILY = "Segoe UI"

OK_GREEN = QColor("#2fbf71")
ERR_RED = QColor("#e5484d")
WARN_AMBER = QColor("#f5a524")


def _font(px: float, bold: bool = False) -> QFont:
    f = QFont(FONT_FAMILY)
    f.setPixelSize(max(6, round(px)))
    f.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    return f


def _color(spec: str, alpha: int | None = None) -> QColor:
    c = QColor(spec)
    if alpha is not None:
        c.setAlpha(max(0, min(255, alpha)))
    return c


def ring_radius(n: int) -> int:
    return 150 if n <= 8 else 168 if n <= 12 else 190 if n <= 18 else 215


# Concentric rings: up to 12 options fit one ring; beyond that the best 10 go in the
# inner ring and the rest spill outwards (each ring holds more, as it's longer).
RING_CAPS = (10, 16, 22, 28, 34)
RING_BAND = 74          # radial thickness of outer rings
RING_SPACING = 6        # gap between rings
REVEAL_STAGGER = 0.45   # each ring starts this fraction of the base animation later


def ring_layout(n: int) -> list[tuple[int, int]]:
    """[(first option index, count)] per ring."""
    if n <= 12:
        return [(0, n)] if n else []
    rings, start = [], 0
    for cap in RING_CAPS:
        if start >= n:
            break
        count = min(cap, n - start)
        rings.append((start, count))
        start += count
    if start < n:                                    # very long lists: last ring takes the rest
        first, count = rings[-1]
        rings[-1] = (first, count + n - start)
    return rings


class Ring:
    __slots__ = ("start", "count", "ri", "ro", "k")

    def __init__(self, start, count, ri, ro, k):
        self.start, self.count, self.ri, self.ro, self.k = start, count, ri, ro, k

    @property
    def span(self) -> float:
        return 360.0 / max(1, self.count)


class Worker(QThread):
    progress = Signal(float, int)       # overall fraction, index of current file
    finished_all = Signal(list, list)   # results, [(path, message)]

    def __init__(self, paths: list[str], target: str, cfg: dict, forced: dict | None = None):
        super().__init__()
        self.paths, self.target, self.cfg = paths, target, cfg
        self.forced = forced or {}
        self.cancel_event = threading.Event()

    def run(self):
        results, errors = [], []
        n = len(self.paths)
        for i, p in enumerate(self.paths):
            if self.cancel_event.is_set():
                break
            ctx = Context(cfg=self.cfg, cancel_event=self.cancel_event,
                          progress=lambda f, i=i: self.progress.emit((i + f) / n, i))
            self.progress.emit(i / n, i)
            try:
                results.append(engine.convert_inplace(p, self.target, ctx,
                                                      src_fmt=self.forced.get(p)))
            except Cancelled:
                errors.append((p, "Cancelled"))
                break
            except ConversionError as e:
                errors.append((p, str(e)))
            except Exception as e:
                log.exception("Conversion crashed for %s", p)
                errors.append((p, f"Unexpected error: {e}"))
        self.finished_all.emit(results, errors)


class WheelView(QWidget):
    """Draws the wheel for ``self.paths``/``self.targets`` and tracks the hovered segment."""

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.paths: list[str] = []
        self.targets: list[str] = []
        self.options: list[TargetOption] = []
        self.state = "choose"     # choose | busy | done | error
        self.hover = -1
        self.hub_hover = False
        self.chosen: str | None = None
        self.progress = 0.0
        self.current_index = 0
        self.message = ""
        self.message_color = QColor("#ffffff")
        self.fit = 1.0
        self._t = 1.0             # open/close animation, 0..1
        self._hv: list[float] = []
        self._dwell_since: float | None = None
        self._anim_timer = QTimer(self, interval=16)
        self._anim_timer.timeout.connect(self._tick)
        self.r_in, self.r_out = HUB_R + RING_GAP, ring_radius(0)
        self.rings: list[Ring] = []
        self.query = ""           # type-to-search text
        self._base_opacity = 1.0
        self.c = QPointF(0, 0)
        self.apply_cfg(cfg)

    # ---------------------------------------------------------------- config

    def apply_cfg(self, cfg: dict):
        self.cfg = cfg
        self.theme = themes.resolve(cfg)
        self.base_scale = max(0.5, min(2.0, float(cfg.get("wheel_scale", 1.0))))
        self.label_scale = max(0.6, min(1.8, float(cfg.get("label_scale", 1.0))))
        self.opacity = max(0.3, min(1.0, float(cfg.get("wheel_opacity", 1.0))))
        self._layout()
        self.update()

    @property
    def s(self) -> float:
        return self.base_scale * self.fit

    def set_content(self, paths: list[str], options: list):
        """``options``: TargetOption objects (or plain format ids)."""
        opts = [o if isinstance(o, TargetOption) else TargetOption(key=o, kind="convert", fmt=o)
                for o in options]
        self.paths, self.options = paths, opts
        self.targets = [o.key for o in opts]
        self._hv = [0.0] * len(opts)
        self.hover = -1
        self._layout()
        self.update()

    def option(self, key: str | None) -> TargetOption | None:
        return next((o for o in self.options if o.key == key), None)

    def source_label(self) -> str:
        forced = getattr(self, "forced", {})
        kinds = sorted({(forced.get(x) or identify(x).fmt or "?").upper()
                        for x in self.paths}) or ["?"]
        if any(o.kind == "assume" for o in self.options):
            return "?"
        return kinds[0] if len(kinds) == 1 else "MIXED"

    # -------------------------------------------------------------- geometry

    def _design_size(self) -> tuple[float, float, float]:
        """(width, height, half) of the wheel in design units."""
        layout = ring_layout(len(self.targets))
        self.rings, ri = [], HUB_R + RING_GAP
        for k, (start, count) in enumerate(layout):
            if len(layout) == 1:
                ro = ring_radius(count)
            else:
                ro = ri + (84 if k == 0 else RING_BAND)
            self.rings.append(Ring(start, count, ri, ro, k))
            ri = ro + RING_SPACING
        self.r_in = HUB_R + RING_GAP
        self.r_out = self.rings[-1].ro if self.rings else ring_radius(0)
        if getattr(self, "auto_target", None):
            half = HUB_R + 30  # hub-only pop-up for direct conversions
            return 480, 2 * half + 130, half
        half = self.r_out + 30
        return max(2 * half, 480), 2 * half + 130, half

    def _layout(self):
        w, h, half = self._design_size()
        if self.width() > 10 and self.height() > 10:
            self.fit = min(1.0, self.width() / (w * self.base_scale),
                           self.height() / (h * self.base_scale))
        s = self.s
        self.c = QPointF(self.width() / 2, (self.height() - h * s) / 2 + half * s)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layout()

    def _ring_of(self, i: int) -> tuple[Ring, int]:
        for ring in self.rings:
            if ring.start <= i < ring.start + ring.count:
                return ring, i - ring.start
        return self.rings[-1], 0

    def _hit(self, pos: QPointF) -> tuple[int, bool]:
        dx, dy = (pos.x() - self.c.x()) / self.s, (pos.y() - self.c.y()) / self.s
        dist = math.hypot(dx, dy)
        if dist <= HUB_R:
            return -1, True
        if not self.targets:
            return -1, False
        theta = math.degrees(math.atan2(dx, -dy)) % 360
        for ring in self.rings:
            outer = ring.ro + (30 if ring is self.rings[-1] else RING_SPACING / 2)
            if ring.ri - RING_SPACING / 2 <= dist <= outer:
                return ring.start + int(((theta + ring.span / 2) % 360) // ring.span), False
        return -1, False

    def ring_progress(self, k: int) -> float:
        """Reveal progress (eased 0..1) of ring k for the current open animation value."""
        n = max(1, len(self.rings))
        p = self._t * (1 + REVEAL_STAGGER * (n - 1)) - REVEAL_STAGGER * k
        p = max(0.0, min(1.0, p))
        return 1 - (1 - p) ** 3

    # ----------------------------------------------------------------- search

    def search_terms(self, opt: TargetOption) -> list[str]:
        if opt.kind == "rename":
            return ["fix", "rename", *search_terms(opt.fmt)]
        if opt.kind == "extract":
            return ["extract", "unpack"]
        if opt.kind == "inspect":
            return ["inspect", "info"]
        if opt.kind == "fill":
            return ["fill", "form", "write", "type", "sign", "signature", "checkbox", "inferred"]
        return search_terms(opt.fmt) if opt.fmt else []

    def matches(self) -> list[int]:
        """Options matching the typed query, best first ([] if no query)."""
        q = getattr(self, "query", "").lower().lstrip(".")
        if not q:
            return []
        scored = []
        for i, opt in enumerate(self.options):
            terms = self.search_terms(opt)
            label = opt.label.lower().lstrip(".")
            if label == q or q in terms:
                scored.append((0, i))
            elif label.startswith(q):
                scored.append((1, i))
            elif any(t.startswith(q) for t in terms):
                scored.append((2, i))
        return [i for _, i in sorted(scored)]

    def _polar(self, theta: float, r: float) -> QPointF:
        t = math.radians(theta)
        return QPointF(self.c.x() + r * math.sin(t), self.c.y() - r * math.cos(t))

    def _sector(self, a0: float, a1: float, ri: float, ro: float) -> QPainterPath:
        outer = QRectF(self.c.x() - ro, self.c.y() - ro, 2 * ro, 2 * ro)
        inner = QRectF(self.c.x() - ri, self.c.y() - ri, 2 * ri, 2 * ri)
        path = QPainterPath()
        path.arcMoveTo(outer, 90 - a0)
        path.arcTo(outer, 90 - a0, -(a1 - a0))
        path.arcTo(inner, 90 - a1, a1 - a0)
        path.closeSubpath()
        return path

    # ------------------------------------------------------------------ hover

    def mouseMoveEvent(self, e):
        if self.state == "choose":
            self._set_hover(*self._hit(e.position()))

    def enterEvent(self, e):
        if self.state == "choose":
            self._set_hover(*self._hit(e.position()))

    def leaveEvent(self, e):
        if self.state == "choose":
            self._set_hover(-1, False)

    def _set_hover(self, idx: int, hub: bool):
        if idx != self.hover or hub != self.hub_hover:
            self.hover, self.hub_hover = idx, hub
            self._dwell_since = time.monotonic() if idx >= 0 and self._dwell_ms() else None
            self._anim_timer.start()
            self.update()

    def _dwell_ms(self) -> int:
        return int(self.cfg.get("dwell_select_ms", 0) or 0)

    def _dwell_fraction(self) -> float:
        if self._dwell_since is None or self.state != "choose":
            return 0.0
        return min(1.0, (time.monotonic() - self._dwell_since) * 1000 / max(1, self._dwell_ms()))

    def _tick(self):
        settled = True
        for i in range(len(self._hv)):
            goal = 1.0 if i == self.hover else 0.0
            v = self._hv[i] + (goal - self._hv[i]) * 0.32
            if abs(goal - v) < 0.01:
                v = goal
            else:
                settled = False
            self._hv[i] = v
        if self._dwell_since is not None:
            settled = False
            if self._dwell_fraction() >= 1.0 and self.hover >= 0:
                self._dwell_since = None
                self.on_select(self.targets[self.hover])
        if settled:
            self._anim_timer.stop()
        self.update()

    def on_select(self, target: str):
        """Called when a segment is chosen; subclasses act on it."""

    # --------------------------------------------------------------- painting

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.paint_background(p)
        p0 = self.ring_progress(0) if self.rings else max(0.0, min(1.0, self._t))
        self._base_opacity = self.opacity
        p.setOpacity(p0 * self.opacity)
        p.translate(self.c)
        scale = self.s * (0.72 + 0.28 * p0)
        p.scale(scale, scale)
        p.translate(-self.c)
        th = self.theme
        if th.shadow and self.targets:
            outer = self.rings[-1]
            p.setOpacity(self.ring_progress(outer.k) * self.opacity)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 10))
            for i in range(8):
                r = self.r_out + 8 + i * 2.5
                p.drawEllipse(self.c, r, r)
        if self.targets:
            for ring in self.rings:
                self._paint_ring(p, ring)
        p.setOpacity(p0 * self.opacity)
        self._paint_hub(p)
        if self.cfg.get("show_caption", True) or self.state in ("done", "error"):
            self._paint_caption(p)
        p.end()

    def paint_background(self, p: QPainter):
        pass

    def _seg_color(self, opt: TargetOption | None) -> QColor:
        cat = opt.category if opt else "tool"
        return QColor(themes.segment_color(self.theme, self.cfg, cat))

    def _paint_ring(self, p: QPainter, ring: Ring):
        th = self.theme
        busy = self.state != "choose"
        reveal = self.ring_progress(ring.k)
        if reveal <= 0.001:
            return
        outermost = ring is self.rings[-1]
        # Background band (the innermost ring's band also fills the gap to the hub).
        p.setOpacity(self._base_opacity * reveal)
        band = QPainterPath()
        band.addEllipse(self.c, ring.ro + 6 if outermost else ring.ro + RING_SPACING / 2,
                        ring.ro + 6 if outermost else ring.ro + RING_SPACING / 2)
        if ring.k > 0:
            inner = QPainterPath()
            r0 = ring.ri - RING_SPACING / 2
            inner.addEllipse(self.c, r0, r0)
            band = band.subtracted(inner)
        p.fillPath(band, _color(th.ring, th.ring_alpha))

        ls = self.label_scale
        size = (15 if ring.count <= 12 else 13) if ring.k == 0 else 12
        label_font = _font(size * ls, bold=True)
        cat_font = _font(10 * ls)
        gap = math.degrees(4.0 / ring.ri)
        span = ring.span
        rotation = -(1 - reveal) * 55                  # rotating reveal
        found = set(self.matches())
        searching = bool(getattr(self, "query", ""))
        for j in range(ring.count):
            i = ring.start + j
            opt = self.options[i]
            sweep = max(0.0, min(1.0, reveal * 1.6 - (j / ring.count) * 0.6))
            if sweep <= 0.001:
                continue
            p.setOpacity(self._base_opacity * sweep * (0.28 if searching and i not in found else 1))
            a0 = j * span - span / 2 + gap / 2 + rotation
            a1 = a0 + span - gap
            h = self._hv[i] if i < len(self._hv) else 0.0
            chosen = busy and opt.key == self.chosen
            if chosen:
                h = 1.0
            active = not busy or chosen
            ri = ring.ri + 2 * h
            ro = ring.ro + (12 if outermost else 3) * h
            base = self._seg_color(opt)
            path = self._sector(a0, a1, ri, ro)

            if th.glow and h > 0.05:
                for k in range(4, 0, -1):
                    g = QColor(base)
                    g.setAlpha(int(40 * h / k))
                    p.setPen(QPen(g, k * 5))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawPath(path)

            fill = QColor(base)
            idle = th.seg_idle_alpha
            fill.setAlpha(int(idle + (th.seg_hover_alpha - idle) * h) if active else 18)
            p.fillPath(path, fill)
            if th.outline:
                p.setPen(QPen(_color(th.outline), 1.2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)
            if active:
                p.fillPath(self._sector(a0, a1, ro - 4, ro), base.lighter(115))

            # Hover-to-select progress along the outer edge.
            if i == self.hover and self._dwell_since is not None:
                frac = self._dwell_fraction()
                arc = self._sector(a0, a0 + (a1 - a0) * frac, ro - 7, ro)
                p.fillPath(arc, QColor(255, 255, 255, 220))

            mid = (a0 + a1) / 2
            pt = self._polar(mid, (ri + ro) / 2 - 2)
            if active:
                col = QColor(th.text) if h < 0.5 else QColor(th.hover_text)
            else:
                col = _color(th.text_dim, 70)
            label = opt.label
            p.setPen(col)
            fit = 5 if ring.k == 0 else 6
            font = label_font if len(label) <= fit else _font(
                label_font.pixelSize() * (fit + 0.5) / len(label), bold=True)
            p.setFont(font)
            p.drawText(QRectF(pt.x() - 45, pt.y() - 13 * ls, 90, 24 * ls),
                       Qt.AlignmentFlag.AlignCenter, label)
            if h > 0.05 and not busy and self.cfg.get("show_category_labels", True):
                c2 = QColor(th.hover_text)
                c2.setAlpha(int(200 * h))
                p.setPen(c2)
                p.setFont(cat_font)
                p.drawText(QRectF(pt.x() - 45, pt.y() + 8 * ls, 90, 15 * ls),
                           Qt.AlignmentFlag.AlignCenter, opt.sublabel)

            # Warning badge: lossy / multi-step / something gets dropped.
            if opt.warnings and active:
                bp = self._polar(mid, ro - 15)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(WARN_AMBER)
                p.drawEllipse(bp, 6.5, 6.5)
                p.setPen(QColor("#1d1f27"))
                p.setFont(_font(10, bold=True))
                p.drawText(QRectF(bp.x() - 6.5, bp.y() - 7.5, 13, 14), Qt.AlignmentFlag.AlignCenter, "!")

    def _paint_hub(self, p: QPainter):
        th = self.theme
        c = self.c
        hub_col = QColor(th.hub)
        if self.state == "done":
            hub_col = OK_GREEN
        elif self.state == "error":
            hub_col = ERR_RED
        elif self.state == "choose" and self.hub_hover:
            hub_col = QColor(th.hub_hover)
        p.setPen(QPen(_color(th.outline), 1.2) if th.outline else Qt.PenStyle.NoPen)
        p.setBrush(hub_col)
        p.drawEllipse(c, HUB_R, HUB_R)

        box = QRectF(c.x() - HUB_R, c.y() - HUB_R, 2 * HUB_R, 2 * HUB_R)
        text, dim = QColor(th.text), QColor(th.text_dim)
        src_label = self.source_label()
        ls = self.label_scale

        if self.state == "choose" and self.query:
            found = self.matches()
            self._center_text(p, box, "search", 11 * ls, dim, dy=-20)
            q = self.query if len(self.query) <= 7 else "…" + self.query[-6:]
            self._center_text(p, box, q + "▏", (22 if len(q) <= 4 else 16) * ls,
                              text if found else ERR_RED, bold=True, dy=2)
            sub = "no match" if not found else (f"{len(found)} matches" if len(found) > 1
                                                else "Enter to convert")
            self._center_text(p, box, sub, 10 * ls, dim, dy=24)
        elif self.state == "choose":
            if self.hub_hover or not self.targets:
                self._center_text(p, box, "✕", 26 * ls, text, dy=-8)
                self._center_text(p, box, "Cancel" if self.targets else "Close", 11 * ls, dim, dy=16)
            elif self.hover >= 0:
                opt = self.options[self.hover]
                top = {"rename": "rename to", "extract": "archive", "inspect": "show",
                       "assume": "treat as", "fill": "inferred"}.get(opt.kind, f"{src_label}  →")
                label = opt.label
                self._center_text(p, box, top, 11 * ls, dim, dy=-18)
                self._center_text(p, box, label, (24 if len(label) <= 5 else 17) * ls, text,
                                  bold=True, dy=6)
            else:
                self._center_text(p, box, src_label, 22 * ls, text, bold=True, dy=-6)
                if any(o.kind == "assume" for o in self.options):
                    sub = "which is it?"
                elif len(self.paths) > 1:
                    sub = f"{len(self.paths)} files"
                else:
                    sub = "unknown type" if src_label == "?" else "convert to…"
                self._center_text(p, box, sub, 11 * ls, dim, dy=18)
        elif self.state == "busy":
            accent = self._seg_color(self.option(self.chosen)
                                     or TargetOption(key="", kind="convert", fmt=self.chosen or ""))
            r = HUB_R - 7
            arc_box = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
            track = QColor(th.text)
            track.setAlpha(30)
            p.setPen(QPen(track, 6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(arc_box)
            pen = QPen(accent, 6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(arc_box, 90 * 16, -int(360 * 16 * max(0.02, self.progress)))
            self._center_text(p, box, f"{int(self.progress * 100)}%", 20 * ls, text, bold=True, dy=-4)
            opt = self.option(self.chosen)
            sub = (f"{self.current_index + 1} / {len(self.paths)}" if len(self.paths) > 1
                   else opt.label if opt else (self.chosen or "").upper())
            self._center_text(p, box, sub, 11 * ls, dim, dy=18)
        elif self.state == "done":
            pen = QPen(QColor("white"), 7)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            path = QPainterPath(QPointF(c.x() - 20, c.y() + 1))
            path.lineTo(c.x() - 6, c.y() + 15)
            path.lineTo(c.x() + 21, c.y() - 13)
            p.drawPath(path)
        elif self.state == "error":
            self._center_text(p, box, "!", 40, QColor("white"), bold=True)

    def _center_text(self, p, box, text, px, color, bold=False, dy=0):
        p.setFont(_font(px, bold))
        p.setPen(color)
        p.drawText(box.translated(0, dy), Qt.AlignmentFlag.AlignCenter, text)

    def _paint_caption(self, p: QPainter):
        th = self.theme
        hovered = self.options[self.hover] if self.state == "choose" and \
            0 <= self.hover < len(self.options) else None
        if hovered is not None and (hovered.warnings or hovered.kind != "convert"):
            # Explain the hovered choice: what gets lost, or what the tool does.
            text = "⚠  " + "  ·  ".join(hovered.warnings) if hovered.warnings \
                else hovered.description
            color = WARN_AMBER if hovered.warnings else QColor(th.text)
        elif self.state == "choose" and not self.message:
            if not self.paths:
                return
            if len(self.paths) == 1:
                ident = identify(self.paths[0])
                text = Path(self.paths[0]).name
                if ident.mismatch or not ident.fmt or ident.confidence < 0.6 \
                        or (ident.fmt and not ident.ext_fmt):
                    text += f"\n{ident.summary}"
            else:
                text = f"{len(self.paths)} files selected"
            if getattr(self, "note", ""):
                text += f"\n{self.note}"
            color = QColor(th.text)
        elif self.state == "busy":
            name = Path(self.paths[min(self.current_index, len(self.paths) - 1)]).name
            text, color = f"Converting {name}", QColor(th.text_dim)
        else:
            text, color = self.message, self.message_color
        if not text:
            return
        font = _font(13 * self.label_scale, bold=self.state in ("error", "done"))
        fm = QFontMetrics(font)
        max_w = 440
        flags = Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap
        rect = fm.boundingRect(0, 0, max_w, 200, int(flags), text)
        w, h = min(max_w, rect.width()) + 28, rect.height() + 16
        top = self.c.y() + (self.r_out if self.targets else HUB_R) + 22
        pill = QRectF(self.c.x() - w / 2, top, w, h)
        p.setPen(QPen(_color(th.outline), 1.2) if th.outline else Qt.PenStyle.NoPen)
        p.setBrush(_color(th.ring, max(th.ring_alpha, 230)))
        radius = h / 2 if h < 40 else 14
        p.drawRoundedRect(pill, radius, radius)
        p.setPen(color)
        p.setFont(font)
        p.drawText(pill.adjusted(14, 8, -14, -8), flags, text)


class WheelOverlay(WheelView):
    """Top-level pop-up wheel. Converts ``paths`` to the chosen format.

    With ``auto_target`` the choice is skipped and conversion starts immediately
    (used by the "repeat last" and "convert to X" hotkeys).
    """

    closed = Signal()

    def __init__(self, paths: list[str], cfg: dict, center: QPoint | None = None,
                 auto_target: str | None = None):
        gpos = center or QCursor.pos()
        screen = QGuiApplication.screenAt(gpos) or QGuiApplication.primaryScreen()
        self._avail = screen.availableGeometry()
        self._gpos = self._avail.center() if cfg.get("position") == "center" else gpos
        super().__init__(cfg)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setWindowTitle("ShapeShifter")

        self.worker: Worker | None = None
        self.auto_target = auto_target
        self.forced: dict[str, str] = {}      # path -> format the user said it is
        self._pressed = False
        self._was_active = False
        self._closing = False

        anim_ms = int(cfg.get("animation_ms", 170))
        self._t = 0.0 if anim_ms > 0 else 1.0
        self._open_anim = QVariantAnimation(self, startValue=0.0, endValue=1.0,
                                            duration=max(1, anim_ms),
                                            easingCurve=QEasingCurve.Type.OutCubic)
        self._open_anim.valueChanged.connect(self._set_t)
        # The search text clears itself after a pause in typing.
        self._query_timer = QTimer(self, singleShot=True, interval=4000)
        self._query_timer.timeout.connect(lambda: self.query and self.set_query(""))
        self.add_paths(paths)

    # ------------------------------------------------------------ setup / data

    def add_paths(self, paths: list[str]):
        if self.state != "choose":
            return
        merged = list(self.paths)
        risky = getattr(self, "risky", [])
        for p in paths:
            if p in merged or not Path(p).is_file():
                continue
            ident = identify(p)
            if ident.risky:
                risky.append((p, ident.risky))   # never touched
            else:
                merged.append(p)
        self.risky = risky
        if risky:
            self.note = (f"Skipped {len(risky)} risky file{'s' if len(risky) > 1 else ''}"
                         if merged else "")
        if not merged:
            self.set_content([], [])
            name, reason = risky[0] if risky else ("", "nothing to convert.")
            self._fail(f"{Path(name).name}: not converted — {reason}" if name else reason)
            return
        if self.auto_target:
            # No choice to make: show just the hub with its progress ring.
            ok = self.auto_target in engine.common_targets(merged)
            self.set_content(merged, [])
            if ok:
                QTimer.singleShot(0, lambda: self._commit(self.auto_target))
            else:
                kinds = ", ".join(sorted({(identify(p).fmt or "?").upper() for p in merged}))
                self._fail(f"Can't convert {kinds} to {self.auto_target.upper()}")
            return
        self.set_content(merged, engine.visible_options(merged, self.cfg, self.forced))
        if not self.targets:
            kinds = sorted({(identify(p).fmt or "?").upper() for p in self.paths})
            self.message = (f"No conversions available for {', '.join(kinds)}" if len(kinds) == 1
                            else "These files have no target format in common")
            self.message_color = QColor(self.theme.text_dim)
        else:
            self.message = ""
        self.update()

    def _layout(self):
        if not hasattr(self, "_avail"):
            return
        w, h, half = self._design_size()
        s = self.base_scale
        self.fit = 1.0
        W, H = int(w * s), int(h * s)
        a = self._avail
        x = min(max(self._gpos.x() - W // 2, a.left()), a.right() - W)
        y = min(max(self._gpos.y() - int(half * s), a.top()), a.bottom() - H)
        self.setGeometry(x, y, W, H)
        self.c = QPointF(W / 2, half * s)

    def paint_background(self, p: QPainter):
        # Near-transparent fill so the whole window (not only painted pixels) takes the mouse.
        p.fillRect(self.rect(), QColor(0, 0, 0, 1))

    def showEvent(self, e):
        super().showEvent(e)
        if self._t < 1.0:
            self.replay_reveal()
        QTimer.singleShot(0, self._grab_focus)

    def replay_reveal(self):
        """Run the open animation; outer rings rotate in one after another."""
        anim_ms = int(self.cfg.get("animation_ms", 170))
        if anim_ms <= 0:
            self._t = 1.0
            self.update()
            return
        rings = max(1, len(self.rings))
        self._open_anim.stop()
        self._open_anim.setDuration(int(anim_ms * (1 + REVEAL_STAGGER * (rings - 1)) * 1.4
                                        if rings > 1 else anim_ms))
        self._open_anim.setEasingCurve(QEasingCurve.Type.Linear if rings > 1
                                       else QEasingCurve.Type.OutCubic)
        self._t = 0.0
        self._open_anim.start()

    def _grab_focus(self):
        self.raise_()
        self.activateWindow()
        self.setFocus()
        try:
            from .winutil import force_foreground
            force_foreground(int(self.winId()))
        except Exception:
            log.exception("force_foreground failed")

    def _set_t(self, v):
        self._t = float(v)
        self.update()

    # ------------------------------------------------------------------ input

    def mousePressEvent(self, e):
        self._pressed = True

    def mouseReleaseEvent(self, e):
        if not self._pressed:   # release of the click that launched us
            return
        self._pressed = False
        if self.state == "choose":
            if e.button() == Qt.MouseButton.LeftButton:
                idx, hub = self._hit(e.position())
                if idx >= 0 and not hub:
                    self._commit(self.targets[idx])
                    return
            self.dismiss()
        elif self.state in ("done", "error"):
            self.dismiss()

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key.Key_Escape:
            if self.state == "busy" and self.worker:
                self.worker.cancel_event.set()
            elif self.query:
                self.set_query("")            # first Esc clears the search
            else:
                self.dismiss()
            return
        if self.state in ("done", "error"):
            if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                self.dismiss()
            return
        if self.state != "choose" or not self.targets:
            return
        # While searching, arrows cycle through the matches only.
        pool = self.matches() if self.query else list(range(len(self.targets)))
        pos = pool.index(self.hover) if self.hover in pool else -1
        if k in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_Tab) and pool:
            self._set_hover(pool[(pos + 1) % len(pool)], False)
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_Backtab) and pool:
            self._set_hover(pool[(pos - 1) % len(pool)], False)
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) or (k == Qt.Key.Key_Space and not self.query):
            if self.hover >= 0:
                self._commit(self.targets[self.hover])
        elif k == Qt.Key.Key_Backspace:
            self.set_query(self.query[:-1])
        elif e.text() and (e.text().isalnum() or e.text() in ".-_+ "):
            self.set_query(self.query + e.text().lower())

    def set_query(self, text: str):
        """Type-to-search: highlight matching formats and hover the best one."""
        self.query = text.strip() if text.strip() else ""
        self._query_timer.start()
        found = self.matches()
        if found:
            self._set_hover(found[0], False)
        elif self.query:
            self._set_hover(-1, False)
        self.update()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == e.Type.ActivationChange:
            if self.isActiveWindow():
                self._was_active = True
            elif (self._was_active and self.state == "choose"
                  and self.cfg.get("close_on_focus_loss", True)):
                self.dismiss()   # user clicked/alt-tabbed elsewhere

    # ------------------------------------------------------------- conversion

    def on_select(self, target: str):
        self._commit(target)

    def _commit(self, target: str):
        if self.state != "choose":
            return
        if target == "inspect":
            from .inspector import show_inspector
            for p in self.paths[:5]:
                show_inspector(p)
            self.dismiss()
            return
        if target == "fill":
            from .formfill import show_form_filler
            show_form_filler(self.paths[0], self.cfg)
            self.dismiss()
            return
        if target.startswith("assume:"):
            # Second stage: now that we know what it is, show what it can become.
            fmt = target.split(":", 1)[1]
            self.forced = {p: fmt for p in self.paths}
            self.query = ""
            self.set_content(self.paths, engine.visible_options(self.paths, self.cfg, self.forced))
            self.replay_reveal()
            return
        self.chosen = target
        self.state = "busy"
        self.progress = 0.0
        self._dwell_since = None
        if not target.startswith("rename:"):
            config.save_state(last_target=target)
        self.worker = Worker(list(self.paths), target, self.cfg, self.forced)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_all.connect(self._on_finished)
        self.worker.start()
        self.update()

    def _fail(self, message: str):
        self.state = "error"
        self.message, self.message_color = message, ERR_RED
        self.update()

    def _on_progress(self, frac: float, idx: int):
        self.progress, self.current_index = frac, idx
        self.update()

    def _on_finished(self, results, errors):
        self.worker = None
        self.progress = 1.0
        warnings = [r.warning for r in results if r.warning]
        if errors:
            if errors[0][1] == "Cancelled":
                self._fail("Conversion cancelled")
            elif len(self.paths) == 1:
                self._fail(errors[0][1])
            else:
                self._fail(f"{len(results)} of {len(self.paths)} converted. "
                           f"{Path(errors[0][0]).name}: {errors[0][1]}")
            return
        self.state = "done"
        outs = [o for r in results for o in r.outputs]
        text = QColor(self.theme.text)
        if warnings:
            self.message, self.message_color = warnings[0], WARN_AMBER
        elif results and all(r.original == "renamed" for r in results):
            self.message = (f"Renamed to {outs[0].name}" if len(outs) == 1
                            else f"{len(outs)} files renamed")
            self.message_color = text
        elif len(results) == 1 and len(outs) == 1:
            self.message, self.message_color = outs[0].name, text
        elif len(results) == 1:
            self.message, self.message_color = f"{len(outs)} files created", text
        else:
            self.message, self.message_color = f"{len(results)} files converted", text
        self.update()
        delay = int(self.cfg.get("done_delay_ms", 900))
        QTimer.singleShot(max(delay, 2600) if warnings else delay, self.dismiss)

    def dismiss(self):
        if self._closing:
            return
        if self.worker is not None:
            # Never abandon a running conversion; cancel it and close when it stops.
            self.worker.cancel_event.set()
            return
        log.info("Wheel dismissed (state=%s)", self.state)
        self._closing = True
        anim_ms = int(self.cfg.get("animation_ms", 170))
        if anim_ms <= 0:
            self.close()
            return
        anim = QVariantAnimation(self, startValue=self._t, endValue=0.0,
                                 duration=max(60, anim_ms * 2 // 3),
                                 easingCurve=QEasingCurve.Type.InCubic)
        anim.valueChanged.connect(self._set_t)
        anim.finished.connect(self.close)
        anim.start()

    def closeEvent(self, e):
        if self.worker is not None:
            self.worker.cancel_event.set()
            self.worker.wait(10000)
        self.closed.emit()
        super().closeEvent(e)
