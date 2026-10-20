"""Interactive view of the conversion graph (Settings › Graph).

Every file type is a node (coloured by category, sized by how connected it is); every
conversion step is a one-way edge. Hover a node to light up what it becomes (green) and
what it's made from (blue); click it for details; drag nodes; zoom with the wheel; drag the
background to pan; find routes; toggle steps on/off the wheel.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (QCheckBox, QComboBox, QCompleter, QFrame, QGraphicsItem,
                               QGraphicsPathItem, QGraphicsScene, QGraphicsView, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPushButton, QSplitter,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import planner
from .formats import CATEGORY_COLORS, category, name_of

OUT_COLOR = QColor("#3ecf7a")       # what the focused type becomes
IN_COLOR = QColor("#5aa2ff")        # what it's made from
ROUTE_COLOR = QColor("#ffb020")
EDGE_COLOR = QColor(150, 155, 170)


class NodeItem(QGraphicsItem):
    def __init__(self, view: "GraphView", fmt: str, degree: int):
        super().__init__()
        self.view, self.fmt, self.degree = view, fmt, degree
        self.r = 7 + min(15, math.sqrt(degree) * 1.9)
        self.color = QColor(CATEGORY_COLORS.get(category(fmt), "#8a8f9c"))
        self.edges: list[EdgeItem] = []
        self.state = "normal"        # normal | dim | focus | out | in | route
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                      | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                      | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.setZValue(2)
        self.setToolTip(f"{name_of(fmt)} (.{fmt}) — {category(fmt)}")

    def boundingRect(self) -> QRectF:
        return QRectF(-self.r - 40, -self.r - 2, 2 * self.r + 80, 2 * self.r + 22)

    def shape(self):
        p = QPainterPath()
        p.addEllipse(QPointF(0, 0), self.r, self.r)
        return p

    def paint(self, painter: QPainter, option, widget=None):
        lod = option.levelOfDetailFromTransform(painter.worldTransform())
        c = QColor(self.color)
        if self.state == "dim":
            c.setAlpha(45)
        painter.setPen(QPen(QColor("#ffffff") if self.state in ("focus", "route") else
                            c.darker(140), 3 if self.state in ("focus", "route") else 1))
        painter.setBrush(c)
        painter.drawEllipse(QPointF(0, 0), self.r, self.r)
        show_label = self.state in ("focus", "out", "in", "route") or lod > 0.75 or \
            (lod > 0.35 and self.r > 16)
        if show_label and self.state != "dim":
            f = QFont("Segoe UI")
            f.setPixelSize(11 if self.state == "normal" else 12)
            f.setBold(self.state in ("focus", "route"))
            painter.setFont(f)
            painter.setPen(self.view.text_color)
            painter.drawText(QRectF(-self.r - 40, self.r + 1, 2 * self.r + 80, 16),
                             Qt.AlignmentFlag.AlignHCenter, self.fmt.upper())

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            for e in self.edges:
                e.update_path()
        return super().itemChange(change, value)

    def hoverEnterEvent(self, e):
        self.view.hover_node(self)

    def hoverLeaveEvent(self, e):
        self.view.hover_node(None)

    def mousePressEvent(self, e):
        super().mousePressEvent(e)
        self.view.select_node(self.fmt)


class EdgeItem(QGraphicsPathItem):
    def __init__(self, src: NodeItem, dst: NodeItem, steps: list):
        super().__init__()
        self.src, self.dst, self.steps = src, dst, steps
        self.latent = all(s.latent for s in steps)
        self.state = "normal"        # normal | dim | out | in | route | hidden
        self.arrow = None
        self.setZValue(0)
        self.setAcceptHoverEvents(False)
        src.edges.append(self)
        dst.edges.append(self)
        self.update_path()
        self.apply_style()

    def update_path(self):
        a, b = self.src.pos(), self.dst.pos()
        dx, dy = b.x() - a.x(), b.y() - a.y()
        length = math.hypot(dx, dy) or 1.0
        ux, uy = dx / length, dy / length
        start = QPointF(a.x() + ux * self.src.r, a.y() + uy * self.src.r)
        end = QPointF(b.x() - ux * (self.dst.r + 2), b.y() - uy * (self.dst.r + 2))
        # Bend slightly to the left so A->B and B->A don't overlap.
        bend = min(40.0, length * 0.12)
        ctrl = QPointF((start.x() + end.x()) / 2 - uy * bend, (start.y() + end.y()) / 2 + ux * bend)
        path = QPainterPath(start)
        path.quadTo(ctrl, end)
        self.arrow = None
        if self.state in ("out", "in", "route"):
            # Arrowhead along the curve's end tangent (drawn separately in paint()).
            tx, ty = end.x() - ctrl.x(), end.y() - ctrl.y()
            tl = math.hypot(tx, ty) or 1.0
            tx, ty = tx / tl, ty / tl
            size = 9
            left = QPointF(end.x() - tx * size - ty * size * 0.5, end.y() - ty * size + tx * size * 0.5)
            right = QPointF(end.x() - tx * size + ty * size * 0.5, end.y() - ty * size - tx * size * 0.5)
            self.arrow = QPolygonF([end, left, right])
        self.prepareGeometryChange()
        self.setPath(path)

    def boundingRect(self):
        r = super().boundingRect()
        return r.united(self.arrow.boundingRect()).adjusted(-2, -2, 2, 2) if self.arrow else r

    def paint(self, painter, option, widget=None):
        painter.setPen(self.pen())
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self.path())
        if self.arrow is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.pen().color())
            painter.drawPolygon(self.arrow)

    def apply_style(self):
        if self.state == "hidden":
            self.setVisible(False)
            return
        self.setVisible(True)
        color = {"out": OUT_COLOR, "in": IN_COLOR, "route": ROUTE_COLOR}.get(self.state, EDGE_COLOR)
        c = QColor(color)
        c.setAlpha({"normal": 38, "dim": 10}.get(self.state, 230))
        width = {"route": 3.2, "out": 1.8, "in": 1.8}.get(self.state, 0.8)
        pen = QPen(c, width)
        pen.setCosmetic(True)
        if self.latent:
            pen.setStyle(Qt.PenStyle.DashLine)
        self.setPen(pen)
        self.setZValue(1 if self.state in ("out", "in", "route") else 0)
        self.update_path()


class GraphView(QGraphicsView):
    node_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
        self.setMinimumHeight(420)
        self.text_color = self.palette().color(self.foregroundRole())
        self.nodes: dict[str, NodeItem] = {}
        self.edges: list[EdgeItem] = []
        self.show_latent = True
        self.category_filter = ""
        self.selected: str | None = None
        self.route_edges: set[EdgeItem] = set()
        self._relax_left = 0
        self._relax_timer = QTimer(self, interval=16)
        self._relax_timer.timeout.connect(self._relax_step)

    # ------------------------------------------------------------- building

    def build(self):
        self.scene_.clear()
        self.nodes.clear()
        self.edges.clear()
        nodes, steps = planner.graph_nodes_edges()
        nodes.discard("extract")
        grouped: dict[tuple[str, str], list] = defaultdict(list)
        for s in steps:
            if s.dst != "extract" and s.src != s.dst:
                grouped[(s.src, s.dst)].append(s)
        degree = defaultdict(int)
        for a, b in grouped:
            degree[a] += 1
            degree[b] += 1
        for fmt in nodes:
            item = NodeItem(self, fmt, degree[fmt])
            self.nodes[fmt] = item
            self.scene_.addItem(item)
        self._layout_clusters()
        for (a, b), group in grouped.items():
            if a in self.nodes and b in self.nodes:
                e = EdgeItem(self.nodes[a], self.nodes[b], group)
                self.edges.append(e)
                self.scene_.addItem(e)
        self.restyle()
        self.fit_all()

    def _layout_clusters(self):
        """Categories become clusters on a big circle; busy types sit in each cluster's centre."""
        by_cat: dict[str, list[NodeItem]] = defaultdict(list)
        for n in self.nodes.values():
            by_cat[category(n.fmt)].append(n)
        cats = sorted(by_cat, key=lambda c: -len(by_cat[c]))
        radii = {c: 40 + math.sqrt(len(by_cat[c])) * 34 for c in cats}
        circumference = sum(2 * r + 70 for r in radii.values())
        big = circumference / (2 * math.pi)
        angle = 0.0
        for c in cats:
            r = radii[c]
            angle += (r + 35) / big
            cx, cy = big * math.cos(angle), big * math.sin(angle)
            angle += (r + 35) / big
            members = sorted(by_cat[c], key=lambda n: -n.degree)
            ring, idx, placed = 0, 0, 0
            while placed < len(members):
                cap = 1 if ring == 0 else 6 * ring
                rr = ring * 40
                for k in range(min(cap, len(members) - placed)):
                    a = 2 * math.pi * k / cap + ring * 0.4
                    members[placed].setPos(cx + rr * math.cos(a), cy + rr * math.sin(a))
                    placed += 1
                ring += 1

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # Keep the whole graph in view until the user starts zooming/panning.
        if self.nodes and not getattr(self, "_user_zoomed", False):
            self.fit_all()

    def fit_all(self):
        rect = self.scene_.itemsBoundingRect().adjusted(-40, -40, 40, 40)
        self.scene_.setSceneRect(rect.adjusted(-2000, -2000, 2000, 2000))
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

    # --------------------------------------------------------- interaction

    def wheelEvent(self, e):
        self._user_zoomed = True
        factor = 1.18 if e.angleDelta().y() > 0 else 1 / 1.18
        zoom = self.transform().m11() * factor
        if 0.05 < zoom < 8:
            self.scale(factor, factor)

    def hover_node(self, node: NodeItem | None):
        if self.selected:              # a click-selected node keeps the focus
            return
        self.restyle(node.fmt if node else None)

    def select_node(self, fmt: str | None, center: bool = False):
        self.selected = fmt
        self.route_edges = set()
        self.restyle(fmt)
        if fmt and center and fmt in self.nodes:
            self.centerOn(self.nodes[fmt])
        self.node_selected.emit(fmt or "")

    def mousePressEvent(self, e):
        if self.itemAt(e.position().toPoint()) is None or \
                isinstance(self.itemAt(e.position().toPoint()), EdgeItem):
            if self.selected and e.button() == Qt.MouseButton.LeftButton:
                self.select_node(None)
        super().mousePressEvent(e)

    def restyle(self, focus: str | None = None):
        focus = focus or self.selected
        neigh_out, neigh_in = set(), set()
        for e in self.edges:
            visible = (self.show_latent or not e.latent)
            if self.category_filter and category(e.src.fmt) != self.category_filter and \
                    category(e.dst.fmt) != self.category_filter:
                visible = False
            if not visible:
                e.state = "hidden"
            elif self.route_edges:
                e.state = "route" if e in self.route_edges else "dim"
            elif focus:
                if e.src.fmt == focus:
                    e.state = "out"
                    neigh_out.add(e.dst.fmt)
                elif e.dst.fmt == focus:
                    e.state = "in"
                    neigh_in.add(e.src.fmt)
                else:
                    e.state = "dim"
            else:
                e.state = "normal"
            e.apply_style()
        route_nodes = {e.src.fmt for e in self.route_edges} | {e.dst.fmt for e in self.route_edges}
        for fmt, n in self.nodes.items():
            in_cat = not self.category_filter or category(fmt) == self.category_filter
            if self.route_edges:
                n.state = "route" if fmt in route_nodes else "dim"
            elif focus:
                n.state = ("focus" if fmt == focus else "out" if fmt in neigh_out else
                           "in" if fmt in neigh_in else "dim")
            else:
                n.state = "normal" if in_cat else "dim"
            n.setZValue(3 if n.state in ("focus", "route", "out", "in") else 2)
            n.update()

    def show_route(self, path_steps) -> None:
        self.selected = None
        self.route_edges = {e for e in self.edges
                            if any((s.src, s.dst) == (e.src.fmt, e.dst.fmt) for s in path_steps)}
        self.restyle()
        if self.route_edges:
            rect = QRectF()
            for e in self.route_edges:
                rect = rect.united(e.sceneBoundingRect())
            self.fitInView(rect.adjusted(-120, -120, 120, 120), Qt.AspectRatioMode.KeepAspectRatio)

    # ------------------------------------------------------- spring layout

    def relax(self, iterations: int = 90):
        self._relax_left = iterations
        self._relax_timer.start()

    def _relax_step(self):
        """One Fruchterman–Reingold iteration (neighbours attract, everyone repels)."""
        items = list(self.nodes.values())
        if not items or self._relax_left <= 0:
            self._relax_timer.stop()
            return
        k = 70.0
        temp = 6.0 + 30.0 * self._relax_left / 90
        pos = {n: (n.pos().x(), n.pos().y()) for n in items}
        disp = {n: [0.0, 0.0] for n in items}
        # Repulsion via a coarse grid: only nearby nodes push each other.
        cell = 3 * k
        grid: dict[tuple[int, int], list[NodeItem]] = defaultdict(list)
        for n, (x, y) in pos.items():
            grid[(int(x // cell), int(y // cell))].append(n)
        for (gx, gy), bucket in grid.items():
            near = [m for dx in (-1, 0, 1) for dy in (-1, 0, 1) for m in grid.get((gx + dx, gy + dy), ())]
            for n in bucket:
                x, y = pos[n]
                for m in near:
                    if m is n:
                        continue
                    ddx, ddy = x - pos[m][0], y - pos[m][1]
                    d2 = ddx * ddx + ddy * ddy or 0.01
                    f = k * k / d2
                    disp[n][0] += ddx * f
                    disp[n][1] += ddy * f
        for e in self.edges:
            if e.state == "hidden":
                continue
            (x1, y1), (x2, y2) = pos[e.src], pos[e.dst]
            ddx, ddy = x2 - x1, y2 - y1
            d = math.hypot(ddx, ddy) or 0.01
            f = d / k * (0.25 if e.latent else 1.0)
            disp[e.src][0] += ddx * f / d * 8
            disp[e.src][1] += ddy * f / d * 8
            disp[e.dst][0] -= ddx * f / d * 8
            disp[e.dst][1] -= ddy * f / d * 8
        for n in items:
            dx, dy = disp[n]
            d = math.hypot(dx, dy) or 0.01
            step = min(d, temp)
            if not n.isUnderMouse():
                n.setPos(pos[n][0] + dx / d * step, pos[n][1] + dy / d * step)
        self._relax_left -= 1
        if self._relax_left <= 0:
            self._relax_timer.stop()

    def shuffle(self):
        self._layout_clusters()
        for n in self.nodes.values():
            n.setPos(n.pos() + QPointF(random.uniform(-6, 6), random.uniform(-6, 6)))
        self.fit_all()


# ===================================================================== sphere

GOLDEN_ANGLE = math.pi * (3 - math.sqrt(5))
CAT_ORDER = ["image", "video", "audio", "document", "data", "code", "subtitle", "model", "font",
             "security", "archive", "program", "inferred", "tool"]


class SNode:
    """A node on the sphere (unit vector v) and its projection for the current frame."""
    __slots__ = ("fmt", "degree", "r", "color", "v", "state", "sx", "sy", "z", "k")

    def __init__(self, fmt, degree, v):
        self.fmt, self.degree, self.v = fmt, degree, v
        self.r = 4.5 + min(9.0, math.sqrt(degree) * 1.15)
        self.color = QColor(CATEGORY_COLORS.get(category(fmt), "#8a8f9c"))
        self.state = "normal"
        self.sx = self.sy = self.z = 0.0
        self.k = 1.0


class SEdge:
    __slots__ = ("src", "dst", "steps", "latent", "state", "arc")

    def __init__(self, src: SNode, dst: SNode, steps):
        self.src, self.dst, self.steps = src, dst, steps
        self.latent = all(s.latent for s in steps)
        self.state = "normal"
        self.arc = _arc(src.v, dst.v, 8)

    def isVisible(self) -> bool:
        return self.state != "hidden"


def _arc(a, b, n: int = 10):
    """Points along the great circle a->b, lifted off the surface (longer arcs lift more)."""
    dot = max(-1.0, min(1.0, a[0] * b[0] + a[1] * b[1] + a[2] * b[2]))
    omega = math.acos(dot)
    if omega < 1e-6:
        return [a, b]
    if math.pi - omega < 1e-3:                  # antipodal: bend through any perpendicular
        b = (b[0] + 0.01, b[1] + 0.02, b[2])
        nb = math.sqrt(sum(c * c for c in b))
        b = tuple(c / nb for c in b)
        omega = math.acos(max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b)))))
    so = math.sin(omega)
    lift = 0.04 + 0.22 * omega / math.pi
    pts = []
    for i in range(n + 1):
        t = i / n
        wa, wb = math.sin((1 - t) * omega) / so, math.sin(t * omega) / so
        h = 1 + lift * math.sin(math.pi * t)
        pts.append(((wa * a[0] + wb * b[0]) * h, (wa * a[1] + wb * b[1]) * h,
                    (wa * a[2] + wb * b[2]) * h))
    return pts


class SphereView(QWidget):
    """The conversion graph on a rotating sphere with evenly spaced nodes."""

    node_selected = Signal(str)
    CAMERA = 3.4                                # camera distance in sphere radii

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumHeight(420)
        self.setMinimumWidth(420)
        self.nodes: dict[str, SNode] = {}
        self.edges: list[SEdge] = []
        self.show_latent = True
        self.category_filter = ""
        self.selected: str | None = None
        self.hovered: str | None = None
        self.route_edges: set[SEdge] = set()
        self.yaw, self.pitch, self.zoom = 0.6, -0.35, 1.0
        self.text_color = self.palette().color(self.foregroundRole())
        self._drag = None
        self._moved = False
        self._spin = QTimer(self, interval=33)
        self._spin.timeout.connect(self._auto_step)
        self._turn = None
        self._version = 0

    # ------------------------------------------------------------- building

    def build(self):
        nodes, steps = planner.graph_nodes_edges()
        nodes.discard("extract")
        grouped: dict[tuple[str, str], list] = defaultdict(list)
        for s in steps:
            if s.dst != "extract" and s.src != s.dst and s.src in nodes and s.dst in nodes:
                grouped[(s.src, s.dst)].append(s)
        degree = defaultdict(int)
        for a, b in grouped:
            degree[a] += 1
            degree[b] += 1
        # Fibonacci lattice: N almost perfectly evenly spaced points. Sorting by category
        # (then by how connected a type is) turns categories into latitude bands.
        order = {c: i for i, c in enumerate(CAT_ORDER)}
        fmts = sorted(nodes, key=lambda f: (order.get(category(f), 99), -degree[f], f))
        n = len(fmts)
        self.nodes = {}
        for i, fmt in enumerate(fmts):
            y = 1 - 2 * (i + 0.5) / n
            rad = math.sqrt(max(0.0, 1 - y * y))
            theta = GOLDEN_ANGLE * i
            self.nodes[fmt] = SNode(fmt, degree[fmt], (math.cos(theta) * rad, y, math.sin(theta) * rad))
        self.edges = [SEdge(self.nodes[a], self.nodes[b], g) for (a, b), g in grouped.items()]
        self.restyle()

    def spacing_stats(self) -> tuple[float, float]:
        """(min, max) nearest-neighbour angle in degrees — evenly spaced means close together."""
        vs = [n.v for n in self.nodes.values()]
        nearest = []
        for i, a in enumerate(vs):
            best = min(math.acos(max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b)))))
                       for j, b in enumerate(vs) if j != i)
            nearest.append(math.degrees(best))
        return min(nearest), max(nearest)

    # ------------------------------------------------------------ projection

    def _rotate(self, v):
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        x1 = v[0] * cy + v[2] * sy
        z1 = -v[0] * sy + v[2] * cy
        y2 = v[1] * cp - z1 * sp
        z2 = v[1] * sp + z1 * cp
        return x1, y2, z2

    def _radius_px(self) -> float:
        return min(self.width(), self.height()) * 0.36 * self.zoom

    def _project(self, v):
        x, y, z = self._rotate(v)
        k = self.CAMERA / (self.CAMERA - z)
        R = self._radius_px()
        return self.width() / 2 + x * k * R, self.height() / 2 - y * k * R, z, k

    def _project_all(self):
        for n in self.nodes.values():
            n.sx, n.sy, n.z, n.k = self._project(n.v)

    def _projector(self):
        """A fast projection closure for this frame (rotation computed once)."""
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        cam, R = self.CAMERA, self._radius_px()
        ox, oy = self.width() / 2, self.height() / 2

        def proj(v):
            x, y, z = v
            x1 = x * cy + z * sy
            z1 = z * cy - x * sy
            y2 = y * cp - z1 * sp
            z2 = y * sp + z1 * cp
            k = cam / (cam - z2) * R
            return ox + x1 * k, oy - y2 * k, z2
        return proj

    def _edge_paths(self, coarse: bool):
        """Normal (unhighlighted) edges as four batched paths, cached between frames."""
        key = (self.yaw, self.pitch, self.zoom, self.width(), self.height(), self._version, coarse)
        if getattr(self, "_cache_key", None) == key:
            return self._cache_paths
        from PySide6.QtCore import QLineF
        proj = self._projector()
        groups: list[list] = [[], [], [], []]           # normal back/front, latent back/front
        step = 2 if coarse else 1
        for e in self.edges:
            if e.state != "normal":
                continue
            arc = e.arc[::step]
            if arc[-1] is not e.arc[-1]:
                arc.append(e.arc[-1])
            pts = [proj(v) for v in arc]
            front = pts[len(pts) // 2][2] > 0
            lines = groups[(2 if e.latent else 0) + (1 if front else 0)]
            for (x1, y1, _), (x2, y2, _) in zip(pts, pts[1:]):
                lines.append(QLineF(x1, y1, x2, y2))
        self._cache_key, self._cache_paths = key, groups
        return groups

    # -------------------------------------------------------------- painting

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), self.palette().color(self.backgroundRole()).darker(115))
        self._project_all()
        R = self._radius_px()
        cx, cy = self.width() / 2, self.height() / 2
        # Faint sphere outline for depth.
        k0 = self.CAMERA / math.sqrt(self.CAMERA ** 2 - 1)
        p.setPen(QPen(QColor(255, 255, 255, 18), 1))
        p.setBrush(QColor(255, 255, 255, 6))
        p.drawEllipse(QPointF(cx, cy), R * k0, R * k0)

        # Moving (dragging / spinning / turning): cheaper arcs for a smooth frame rate.
        moving = self._drag is not None or self._spin.isActive() or \
            (self._turn is not None and self._turn.state() == self._turn.State.Running)
        normal_back, normal_front, latent_back, latent_front = self._edge_paths(moving)
        proj = self._projector()
        highlighted = []
        for e in self.edges:
            if e.state in ("out", "in", "route"):
                highlighted.append((e, [proj(v) for v in e.arc]))

        def pen(color, alpha, width, dashed=False):
            c = QColor(color)
            c.setAlpha(alpha)
            pe = QPen(c, width)
            if dashed:
                pe.setStyle(Qt.PenStyle.DashLine)
            return pe

        dim_all = bool(self.route_edges) or bool(self.selected or self.hovered)
        base_a = 12 if dim_all else 30
        # Thousands of faint background edges: plain aliased segments are ~10x faster to
        # draw than antialiased dashed paths, and at this opacity they look the same.
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.setPen(pen(EDGE_COLOR, base_a // 2, 1))
        p.drawLines(normal_back)
        p.setPen(pen(IN_COLOR, base_a // 3, 1))
        p.drawLines(latent_back)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        back = sorted((n for n in self.nodes.values() if n.z <= 0), key=lambda n: n.z)
        front = sorted((n for n in self.nodes.values() if n.z > 0), key=lambda n: n.z)
        for n in back:
            self._draw_node(p, n)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.setPen(pen(EDGE_COLOR, base_a, 1))
        p.drawLines(normal_front)
        p.setPen(pen(IN_COLOR, base_a * 2 // 3, 1))
        p.drawLines(latent_front)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for e, pts in highlighted:
            color = {"out": OUT_COLOR, "in": IN_COLOR, "route": ROUTE_COLOR}[e.state]
            mid_z = pts[len(pts) // 2][2]
            alpha = 235 if mid_z > -0.2 else 120
            p.setPen(pen(color, alpha, 3.0 if e.state == "route" else 1.8, e.latent))
            p.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath(QPointF(pts[0][0], pts[0][1]))
            for x, y, _z in pts[1:]:
                path.lineTo(x, y)
            p.drawPath(path)
            # arrowhead at the destination end
            (x1, y1, *_), (x2, y2, *_) = pts[-2], pts[-1]
            dx, dy = x2 - x1, y2 - y1
            d = math.hypot(dx, dy) or 1.0
            dx, dy = dx / d, dy / d
            tip = QPointF(x2 - dx * e.dst.r * e.dst.k, y2 - dy * e.dst.r * e.dst.k)
            s = 8
            c = QColor(color)
            c.setAlpha(alpha)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawPolygon(QPolygonF([tip, QPointF(tip.x() - dx * s - dy * s * 0.5, tip.y() - dy * s + dx * s * 0.5),
                                     QPointF(tip.x() - dx * s + dy * s * 0.5, tip.y() - dy * s - dx * s * 0.5)]))
        for n in front:
            self._draw_node(p, n)
        p.end()

    def _draw_node(self, p: QPainter, n: SNode):
        depth = (n.z + 1) / 2                       # 0 = far side, 1 = nearest
        c = QColor(n.color)
        alpha = int(70 + 185 * depth)
        if n.state == "dim":
            alpha = int(alpha * 0.25)
        c.setAlpha(alpha)
        r = n.r * n.k * (0.9 + 0.2 * self.zoom)
        strong = n.state in ("focus", "route")
        p.setPen(QPen(QColor(255, 255, 255, 230), 2.5) if strong else
                 QPen(c.darker(150), 1))
        p.setBrush(c)
        p.drawEllipse(QPointF(n.sx, n.sy), r, r)
        label = (n.state in ("focus", "out", "in", "route") and n.z > -0.3) or \
            (n.state == "normal" and n.z > 0.25 and (self.zoom > 1.6 or n.degree > 40)) or \
            n.fmt == self.hovered
        if label:
            f = QFont("Segoe UI")
            f.setPixelSize(12 if strong else 11)
            f.setBold(strong)
            p.setFont(f)
            tc = QColor(self.text_color)
            tc.setAlpha(int(110 + 145 * depth))
            p.setPen(tc)
            p.drawText(QRectF(n.sx - 50, n.sy + r + 1, 100, 16), Qt.AlignmentFlag.AlignHCenter,
                       n.fmt.upper())

    # ------------------------------------------------------------ interaction

    def _node_at(self, x: float, y: float) -> SNode | None:
        best, best_d = None, 1e9
        for n in self.nodes.values():
            if n.state == "dim" and self.route_edges:
                continue
            r = n.r * n.k * (0.9 + 0.2 * self.zoom) + 3
            d = math.hypot(n.sx - x, n.sy - y)
            # prefer nodes on the near side when they overlap
            score = d - n.z * 4
            if d <= r and score < best_d:
                best, best_d = n, score
        return best

    def mousePressEvent(self, e):
        self._drag = (e.position().x(), e.position().y(), self.yaw, self.pitch)
        self._moved = False
        self._turn = None

    def mouseMoveEvent(self, e):
        x, y = e.position().x(), e.position().y()
        if self._drag and e.buttons():
            x0, y0, yaw0, pitch0 = self._drag
            if abs(x - x0) + abs(y - y0) > 3:
                self._moved = True
            R = max(60.0, self._radius_px())
            self.yaw = yaw0 + (x - x0) / R
            self.pitch = max(-1.55, min(1.55, pitch0 + (y - y0) / R))
            self.update()
            return
        self._project_all()
        n = self._node_at(x, y)
        fmt = n.fmt if n else None
        if fmt != self.hovered:
            self.hovered = fmt
            self.setCursor(Qt.CursorShape.PointingHandCursor if n else Qt.CursorShape.OpenHandCursor)
            if not self.selected and not self.route_edges:
                self.restyle(fmt)
            self.update()

    def mouseReleaseEvent(self, e):
        moved, self._drag = self._moved, None
        if moved:
            return
        n = self._node_at(e.position().x(), e.position().y())
        self.select_node(n.fmt if n else None, center=bool(n))

    def wheelEvent(self, e):
        factor = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        self.zoom = max(0.45, min(6.0, self.zoom * factor))
        self.update()

    def leaveEvent(self, e):
        if self.hovered:
            self.hovered = None
            if not self.selected and not self.route_edges:
                self.restyle()
            self.update()

    # ----------------------------------------------------------- page API

    def select_node(self, fmt: str | None, center: bool = False):
        self.selected = fmt
        self.route_edges = set()
        self.restyle(fmt)
        if fmt and center and fmt in self.nodes:
            self.turn_to(self.nodes[fmt].v)
        self.node_selected.emit(fmt or "")
        self.update()

    def turn_to(self, v, animate: bool = True):
        """Rotate so that point v faces the viewer."""
        yaw = math.atan2(-v[0], v[2])
        z1 = math.hypot(v[0], v[2])
        pitch = math.atan2(v[1], z1)
        # shortest way round
        while yaw - self.yaw > math.pi:
            yaw -= 2 * math.pi
        while yaw - self.yaw < -math.pi:
            yaw += 2 * math.pi
        if not animate:
            self.yaw, self.pitch = yaw, pitch
            self.update()
            return
        from PySide6.QtCore import QEasingCurve, QVariantAnimation
        start = (self.yaw, self.pitch)
        self._turn = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=550,
                                       easingCurve=QEasingCurve.Type.InOutCubic)

        def step(t):
            t = float(t)
            self.yaw = start[0] + (yaw - start[0]) * t
            self.pitch = start[1] + (pitch - start[1]) * t
            self.update()
        self._turn.valueChanged.connect(step)
        self._turn.start()

    def restyle(self, focus: str | None = None):
        focus = focus or self.selected
        neigh_out, neigh_in = set(), set()
        for e in self.edges:
            visible = self.show_latent or not e.latent
            if self.category_filter and category(e.src.fmt) != self.category_filter and \
                    category(e.dst.fmt) != self.category_filter:
                visible = False
            if not visible:
                e.state = "hidden"
            elif self.route_edges:
                e.state = "route" if e in self.route_edges else "dim"
            elif focus:
                if e.src.fmt == focus:
                    e.state = "out"
                    neigh_out.add(e.dst.fmt)
                elif e.dst.fmt == focus:
                    e.state = "in"
                    neigh_in.add(e.src.fmt)
                else:
                    e.state = "dim"
            else:
                e.state = "normal"
        route_nodes = {e.src.fmt for e in self.route_edges} | {e.dst.fmt for e in self.route_edges}
        for fmt, n in self.nodes.items():
            in_cat = not self.category_filter or category(fmt) == self.category_filter
            if self.route_edges:
                n.state = "route" if fmt in route_nodes else "dim"
            elif focus:
                n.state = ("focus" if fmt == focus else "out" if fmt in neigh_out else
                           "in" if fmt in neigh_in else "dim")
            else:
                n.state = "normal" if in_cat else "dim"
        self._version = getattr(self, "_version", 0) + 1    # invalidates the edge cache
        self.update()

    def show_route(self, path_steps) -> None:
        self.selected = None
        self.route_edges = {e for e in self.edges
                            if any((s.src, s.dst) == (e.src.fmt, e.dst.fmt) for s in path_steps)}
        self.restyle()
        if self.route_edges:
            pts = [e.src.v for e in self.route_edges] + [e.dst.v for e in self.route_edges]
            mx, my, mz = (sum(p[i] for p in pts) for i in range(3))
            norm = math.sqrt(mx * mx + my * my + mz * mz) or 1.0
            self.turn_to((mx / norm, my / norm, mz / norm))

    def fit_all(self):
        self.zoom = 1.0
        self.update()

    def reset(self):
        self.yaw, self.pitch, self.zoom = 0.6, -0.35, 1.0
        self.update()

    def set_auto_rotate(self, on: bool):
        if on:
            self._spin.start()
        else:
            self._spin.stop()

    def _auto_step(self):
        if self._drag is None and (self._turn is None or
                                   self._turn.state() != self._turn.State.Running):
            self.yaw += 0.006
            self.update()


class GraphPage(QWidget):
    """The Settings page: toolbar, the graph, and a details panel."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self._built = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 16)
        title = QLabel("Conversion graph")
        f = title.font()
        f.setPointSizeF(f.pointSizeF() * 1.6)
        f.setBold(True)
        title.setFont(f)
        lay.addWidget(title)
        sub = QLabel("Every file type is a node, evenly spaced on a sphere and banded by category; "
                     "every arrow is one conversion step. Drag to spin, scroll to zoom, hover a type "
                     "to see what it becomes (green) and what it's made from (blue), click it to "
                     "bring it to the front and see details. Dashed lines are latent steps, used only "
                     "inside routes and learned reverse conversions.")
        sub.setWordWrap(True)
        sub.setStyleSheet("color: palette(placeholder-text);")
        lay.addWidget(sub)

        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a type, e.g. pem")
        self.search.returnPressed.connect(self._find)
        bar.addWidget(self.search, 2)
        self.cat = QComboBox()
        self.cat.addItem("All categories", "")
        self.cat.currentIndexChanged.connect(self._filter_changed)
        bar.addWidget(self.cat, 1)
        self.latent_cb = QCheckBox("Latent steps")
        self.latent_cb.setChecked(True)
        self.latent_cb.toggled.connect(self._filter_changed)
        bar.addWidget(self.latent_cb)
        self.mode = QComboBox()
        self.mode.addItem("Sphere", "sphere")
        self.mode.addItem("Flat", "flat")
        self.mode.currentIndexChanged.connect(self._mode_changed)
        bar.addWidget(self.mode)
        self.spin_cb = QCheckBox("Auto-rotate")
        self.spin_cb.toggled.connect(lambda on: self.sphere.set_auto_rotate(on))
        bar.addWidget(self.spin_cb)
        self.spread_btn = QPushButton("Spread out")
        self.spread_btn.clicked.connect(lambda: self.flat.relax())
        self.spread_btn.setVisible(False)
        bar.addWidget(self.spread_btn)
        for text, fn in (("Reset view", self._reset), ("Fit", lambda: self.view.fit_all())):
            b = QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        lay.addLayout(bar)

        route = QHBoxLayout()
        route.addWidget(QLabel("Route from"))
        self.r_from, self.r_to = QComboBox(), QComboBox()
        for cb in (self.r_from, self.r_to):
            cb.setEditable(True)
            cb.setMinimumWidth(100)
        route.addWidget(self.r_from)
        route.addWidget(QLabel("to"))
        route.addWidget(self.r_to)
        go = QPushButton("Show route")
        go.clicked.connect(self._route)
        route.addWidget(go)
        clear = QPushButton("Clear")
        clear.clicked.connect(lambda: (self.view.select_node(None), self.route_label.setText("")))
        route.addWidget(clear)
        self.route_label = QLabel("")
        self.route_label.setWordWrap(True)
        route.addWidget(self.route_label, 1)
        lay.addLayout(route)

        split = QSplitter(Qt.Orientation.Horizontal)
        from PySide6.QtWidgets import QStackedWidget
        self.sphere = SphereView()
        self.flat = GraphView()
        self.views = QStackedWidget()
        for v in (self.sphere, self.flat):
            v.node_selected.connect(self._show_details)
            self.views.addWidget(v)
        self.view = self.sphere
        split.addWidget(self.views)
        side = QFrame()
        sl = QVBoxLayout(side)
        sl.setContentsMargins(8, 0, 0, 0)
        self.detail_title = QLabel("Click a type")
        f = self.detail_title.font()
        f.setPointSizeF(f.pointSizeF() * 1.2)
        f.setBold(True)
        self.detail_title.setFont(f)
        self.detail_title.setWordWrap(True)
        sl.addWidget(self.detail_title)
        self.detail_sub = QLabel("")
        self.detail_sub.setWordWrap(True)
        self.detail_sub.setStyleSheet("color: palette(placeholder-text);")
        sl.addWidget(self.detail_sub)
        sl.addWidget(QLabel("Becomes  (tick = shown on the wheel)"))
        self.out_tree = self._tree(["Type", "Engine", "Notes"])
        self.out_tree.itemChanged.connect(self._toggle_wheel)
        sl.addWidget(self.out_tree, 3)
        sl.addWidget(QLabel("Made from"))
        self.in_tree = self._tree(["Type", "Engine", "Notes"])
        sl.addWidget(self.in_tree, 2)
        self.legend = QLabel("")
        self.legend.setWordWrap(True)
        self.legend.setTextFormat(Qt.TextFormat.RichText)
        sl.addWidget(self.legend)
        side.setMinimumWidth(300)
        side.setMaximumWidth(460)
        split.addWidget(side)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setCollapsible(0, False)
        split.setSizes([900, 380])
        lay.addWidget(split, 1)

    def _tree(self, headers):
        t = QTreeWidget()
        t.setHeaderLabels(headers)
        t.setRootIsDecorated(False)
        t.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        t.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        t.itemDoubleClicked.connect(lambda it, _c: self.view.select_node(it.data(0, Qt.ItemDataRole.UserRole), True))
        t.itemClicked.connect(lambda it, c: c != 0 and self.view.select_node(
            it.data(0, Qt.ItemDataRole.UserRole), True))
        return t

    # -------------------------------------------------------------- data

    def ensure_built(self):
        if self._built:
            return
        self._built = True
        self.sphere.build()
        self.flat.build()
        fmts = sorted(self.view.nodes)
        for cb in (self.r_from, self.r_to):
            cb.clear()
            cb.addItems(fmts)
        while self.cat.count() > 1:
            self.cat.removeItem(1)
        self.r_from.setCurrentText("md")
        self.r_to.setCurrentText("pem")
        comp = QCompleter(fmts + [name_of(f) for f in fmts], self)
        comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        comp.setFilterMode(Qt.MatchFlag.MatchContains)
        self.search.setCompleter(comp)
        cats = sorted({category(f) for f in fmts})
        for c in cats:
            self.cat.addItem(c.capitalize(), c)
        chips = " ".join(f'<span style="color:{CATEGORY_COLORS.get(c, "#888")}">●</span> {c}'
                         for c in cats)
        n_steps = sum(len(e.steps) for e in self.view.edges)
        self.legend.setText(f"{len(self.view.nodes)} types · {len(self.view.edges)} connections "
                            f"({n_steps} engine steps)<br>{chips}")

    def refresh(self):
        if self._built:
            sel = self.view.selected
            self._built = False
            self.ensure_built()
            if sel:
                self.view.select_node(sel, True)

    def showEvent(self, e):
        super().showEvent(e)
        self.ensure_built()

    def _find(self):
        text = self.search.text().strip().lower().lstrip(".")
        for fmt in self.view.nodes:
            if fmt == text or name_of(fmt).lower() == text:
                self.view.select_node(fmt, True)
                return
        for fmt in sorted(self.view.nodes):
            if fmt.startswith(text) or text in name_of(fmt).lower():
                self.view.select_node(fmt, True)
                return

    def _filter_changed(self, *_):
        for v in (self.sphere, self.flat):
            v.show_latent = self.latent_cb.isChecked()
            v.category_filter = self.cat.currentData() or ""
            v.restyle()

    def _mode_changed(self, *_):
        selected = self.view.selected
        sphere = self.mode.currentData() == "sphere"
        self.view = self.sphere if sphere else self.flat
        self.views.setCurrentWidget(self.view)
        self.spin_cb.setVisible(sphere)
        self.spread_btn.setVisible(not sphere)
        if not sphere:
            self.sphere.set_auto_rotate(False)
            self.spin_cb.setChecked(False)
        self._filter_changed()
        self.view.select_node(selected, center=bool(selected))
        if not sphere:
            self.flat.fit_all()

    def _reset(self):
        if self.view is self.sphere:
            self.sphere.reset()
        else:
            self.flat.shuffle()

    def _route(self):
        from .formats import canonical
        a, b = canonical(self.r_from.currentText()), canonical(self.r_to.currentText())
        opt = planner.route(a, b, conditional=True)
        if not opt:
            self.view.show_route([])
            self.route_label.setText(f"No route from {a.upper()} to {b.upper()}.")
            return
        self.view.show_route(opt.path)
        steps = " → ".join([a.upper()] + [e.dst.upper() for e in opt.path])
        note = ("   (for a real file the content is checked: if it really is that format, "
                "the direct conversion is offered)" if planner.is_conditional(opt) else "")
        self.route_label.setText(steps + (f"   ⚠ {'; '.join(opt.warnings)}" if opt.warnings else "")
                                 + note)

    def _show_details(self, fmt: str):
        self.out_tree.blockSignals(True)
        self.out_tree.clear()
        self.in_tree.clear()
        if not fmt:
            self.detail_title.setText("Click a type")
            self.detail_sub.setText("")
            self.out_tree.blockSignals(False)
            return
        from .formats import ALIASES
        exts = sorted({fmt} | {a for a, c in ALIASES.items() if c == fmt})
        self.detail_title.setText(f"{name_of(fmt)}  (.{fmt})")
        hidden = set(self.win.cfg.get("hidden_pairs", [])) if self.win else set()
        outs = [e for e in self.view.edges if e.src.fmt == fmt]
        ins = [e for e in self.view.edges if e.dst.fmt == fmt]
        self.detail_sub.setText(f"Category: {category(fmt)} · extensions: "
                                f"{', '.join('.' + x for x in exts)} · becomes {len(outs)} "
                                f"types directly · made from {len(ins)}")
        for e in sorted(outs, key=lambda e: e.dst.fmt):
            it = self._row(e, e.dst.fmt)
            wheel = any(s.on_wheel for s in e.steps) and not e.latent
            if wheel:
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(0, Qt.CheckState.Unchecked if f"{fmt}>{e.dst.fmt}" in hidden
                                 else Qt.CheckState.Checked)
            self.out_tree.addTopLevelItem(it)
        for e in sorted(ins, key=lambda e: e.src.fmt):
            self.in_tree.addTopLevelItem(self._row(e, e.src.fmt))
        self.out_tree.blockSignals(False)

    def _row(self, e: EdgeItem, other: str) -> QTreeWidgetItem:
        engines = ", ".join(sorted({planner.ENGINE_NAMES.get(s.backend.name, s.backend.name)
                                    for s in e.steps}))
        flags = sorted(set().union(*(s.flags for s in e.steps)))
        notes = "; ".join(planner.WARNINGS[f] for f in flags if f in planner.WARNINGS)
        if e.latent:
            notes = ("latent (routes only)" + (f"; {notes}" if notes else ""))
        elif not any(s.on_wheel for s in e.steps):
            notes = ("command line only" + (f"; {notes}" if notes else ""))
        it = QTreeWidgetItem([other.upper(), engines, notes])
        it.setData(0, Qt.ItemDataRole.UserRole, other)
        it.setForeground(0, QColor(CATEGORY_COLORS.get(category(other), "#888")))
        it.setToolTip(2, notes)
        return it

    def _toggle_wheel(self, item: QTreeWidgetItem, col: int):
        if col != 0 or not self.win or not self.view.selected:
            return
        key = f"{self.view.selected}>{item.data(0, Qt.ItemDataRole.UserRole)}"
        hidden = set(self.win.cfg.get("hidden_pairs", []))
        if item.checkState(0) == Qt.CheckState.Checked:
            hidden.discard(key)
        else:
            hidden.add(key)
        self.win.set_value("hidden_pairs", sorted(hidden))
