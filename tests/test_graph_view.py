"""The interactive graph page: building, focusing, routes, and wheel toggles."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from shapeshifter import config


class FakeWindow:
    def __init__(self):
        self.cfg = dict(config.DEFAULTS, hidden_pairs=[])

    def set_value(self, key, value):
        self.cfg[key] = value


@pytest.fixture(scope="module")
def page():
    QApplication.instance() or QApplication([])
    from shapeshifter.graph_view import GraphPage
    p = GraphPage(FakeWindow())
    p.resize(1200, 800)
    p.ensure_built()
    return p


def test_builds_whole_graph(page):
    v = page.view
    assert len(v.nodes) > 200 and len(v.edges) > 1500
    assert any(e.latent for e in v.edges)
    assert all(e.src.fmt != e.dst.fmt for e in v.edges)


def test_focus_highlights_neighbours(page):
    v = page.view
    v.select_node("srt")
    out = {e.dst.fmt for e in v.edges if e.state == "out"}
    inn = {e.src.fmt for e in v.edges if e.state == "in"}
    assert {"vtt", "ass", "txt"} <= out and "vtt" in inn
    assert v.nodes["srt"].state == "focus" and v.nodes["vtt"].state in ("out", "in")
    assert v.nodes["pem"].state == "dim"                     # unrelated to subtitles
    assert v.nodes["mp4"].state in ("in", "dim")             # videos can carry subtitle tracks
    assert page.out_tree.topLevelItemCount() == len(out)
    v.select_node(None)
    assert all(e.state in ("normal", "hidden") for e in v.edges)


def test_toggle_on_wheel_updates_hidden_pairs(page):
    page.view.select_node("srt")
    row = next(page.out_tree.topLevelItem(i) for i in range(page.out_tree.topLevelItemCount())
               if page.out_tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) == "vtt")
    row.setCheckState(0, Qt.CheckState.Unchecked)
    assert "srt>vtt" in page.win.cfg["hidden_pairs"]
    row.setCheckState(0, Qt.CheckState.Checked)
    assert "srt>vtt" not in page.win.cfg["hidden_pairs"]


def test_route_and_filters(page):
    page.r_from.setCurrentText("cbz")
    page.r_to.setCurrentText("png")
    page._route()
    route = {(e.src.fmt, e.dst.fmt) for e in page.view.route_edges}
    assert route == {("cbz", "pdf"), ("pdf", "png")} and "CBZ → PDF → PNG" in page.route_label.text()
    page.view.select_node(None)
    page.latent_cb.setChecked(False)
    assert all(not e.isVisible() for e in page.view.edges if e.latent)
    page.latent_cb.setChecked(True)


def test_search_and_flat_relax(page):
    page.search.setText("subrip")
    page._find()
    assert page.view.selected == "srt"
    flat = page.flat
    before = {f: (n.pos().x(), n.pos().y()) for f, n in flat.nodes.items()}
    flat._relax_left = 10
    flat._relax_step()
    moved = sum(1 for f, n in flat.nodes.items() if (n.pos().x(), n.pos().y()) != before[f])
    assert moved > 50


def test_sphere_is_default_and_evenly_spaced(page):
    sv = page.sphere
    assert page.view is sv
    lo, hi = sv.spacing_stats()
    assert hi / lo < 1.3                     # nearest-neighbour distances within 30%
    for n in sv.nodes.values():
        assert abs(sum(c * c for c in n.v) - 1) < 1e-9      # every node on the unit sphere


def test_sphere_turns_selected_node_to_front(page):
    sv = page.sphere
    sv.select_node("pem")
    sv.turn_to(sv.nodes["pem"].v, animate=False)
    x, y, z = sv._rotate(sv.nodes["pem"].v)
    assert z > 0.999 and abs(x) < 1e-6 and abs(y) < 1e-6
    sv.select_node(None)


def test_mode_switch_keeps_selection(page):
    page.view.select_node("vtt")
    page.mode.setCurrentIndex(1)                       # flat
    assert page.view is page.flat and page.flat.selected == "vtt"
    page.mode.setCurrentIndex(0)                       # back to sphere
    assert page.view is page.sphere and page.sphere.selected == "vtt"
