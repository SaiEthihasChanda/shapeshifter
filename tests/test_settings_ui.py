"""The Settings window's revamped layout: the nav is split into a primary group (General, Wheel,
Hotkeys, Formats, Engines) and, below a divider, an Advanced group (Advanced, Graph) with About
pinned to the bottom. Power-user controls (quality, OCR language, ring count, hover-to-convert,
learned reverse conversions, custom formats, graph export) live only on the Advanced page."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QSlider  # noqa: E402

from shapeshifter import config, settings_app as sa  # noqa: E402


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "STATE_FILE", tmp_path / "state.json")
    win = sa.SettingsWindow(controller=None)
    win.resize(1180, 820)
    win.show()
    app.processEvents()
    yield win, app
    win.close()


def test_nav_is_grouped_primary_then_advanced_then_about(window):
    win, app = window
    assert [win.nav_primary.item(i).text() for i in range(win.nav_primary.count())] == \
        ["General", "Wheel", "Hotkeys", "Formats", "Engines"]
    assert [win.nav_advanced.item(i).text() for i in range(win.nav_advanced.count())] == \
        ["Advanced", "Graph"]
    assert [win.nav_about.item(i).text() for i in range(win.nav_about.count())] == ["About"]
    assert win.stack.count() == 8


def test_every_nav_row_opens_the_right_page(window):
    win, app = window
    expect = [(win.nav_primary, 0, sa.GeneralPage), (win.nav_primary, 1, sa.WheelPage),
             (win.nav_primary, 2, sa.HotkeysPage), (win.nav_primary, 3, sa.FormatsPage),
             (win.nav_primary, 4, sa.EnginesPage), (win.nav_advanced, 0, sa.AdvancedPage),
             (win.nav_advanced, 1, sa.GraphPage), (win.nav_about, 0, sa.AboutPage)]
    for lw, row, cls in expect:
        lw.setCurrentRow(row)
        app.processEvents()
        assert isinstance(win.stack.currentWidget(), cls), (lw, row, cls)
        # Selecting a row in one list clears the current row in the others.
        for other in (win.nav_primary, win.nav_advanced, win.nav_about):
            if other is not lw:
                assert other.currentRow() == -1


def test_general_and_wheel_no_longer_have_power_user_controls(window):
    """The controls that moved to Advanced aren't duplicated on their old pages."""
    win, app = window
    general = win.stack.widget(0)
    for attr in ("ocr_lang", "ocr_note"):
        assert not hasattr(general, attr), f"GeneralPage still has {attr}"
    wheel = win.stack.widget(1)
    for attr in ("dwell", "max_segments_spin", "dwell_on", "dwell_slider"):
        assert not hasattr(wheel, attr), f"WheelPage still has {attr}"


def test_formats_page_only_has_the_tree_and_filters(window):
    """Route finder, graph export, learned reverse and custom formats moved off this page."""
    win, app = window
    formats = win.stack.widget(3)
    for attr in ("route_from", "route_to", "route_result", "learned_list", "custom_list",
                "graph_info", "learn_cb"):
        assert not hasattr(formats, attr), f"FormatsPage still has {attr}"
    assert hasattr(formats, "tree") and hasattr(formats, "search") and hasattr(formats, "cat")
    assert formats.tree.topLevelItemCount() > 0


def test_advanced_page_has_the_moved_controls(window):
    win, app = window
    adv = win.page_of(sa.AdvancedPage)
    assert adv is not None
    for attr in ("ocr_lang", "ocr_note", "max_segments_spin", "dwell_on", "dwell_slider",
                "learn_cb", "learned_count", "custom_list", "graph_export_result"):
        assert hasattr(adv, attr), f"AdvancedPage is missing {attr}"
    assert adv.max_segments_spin.value() == config.DEFAULTS["max_segments"]


def test_wheel_reset_stays_on_the_wheel_page_and_syncs_advanced(window):
    """Resetting wheel settings must not jump the visible page to Advanced, and the Advanced
    page's ring-count / hover-to-convert controls must reflect the new values."""
    win, app = window
    win.nav_advanced.setCurrentRow(0)                # build Advanced first, like a real session
    app.processEvents()
    adv = win.stack.currentWidget()
    adv.max_segments_spin.setValue(7)
    app.processEvents()
    assert win.cfg["max_segments"] == 7

    win.nav_primary.setCurrentRow(1)                 # Wheel
    app.processEvents()
    wheel_index = win.stack.currentIndex()
    wheel = win.stack.currentWidget()
    wheel._reset()                                   # rebuilds the page (a fresh instance), as "Reset" always has
    app.processEvents()

    assert win.stack.currentIndex() == wheel_index    # still on Wheel, not bounced to Advanced
    assert isinstance(win.stack.currentWidget(), sa.WheelPage)
    assert win.cfg["max_segments"] == config.DEFAULTS["max_segments"]
    assert win.page_of(sa.AdvancedPage).max_segments_spin.value() == config.DEFAULTS["max_segments"]


def test_quality_slider_on_advanced_page_persists_to_config(window):
    """The JPEG-quality slider lives only on Advanced now; moving it must still reach config.json."""
    win, app = window
    win.nav_advanced.setCurrentRow(0)
    app.processEvents()
    adv = win.stack.currentWidget()
    # Several sliders live on this page; find the one that starts at the JPEG-quality default.
    jpeg_slider = next(s for s in adv.findChildren(QSlider) if s.value() == config.DEFAULTS["jpeg_quality"])
    jpeg_slider.setValue(55)
    app.processEvents()
    win.flush()
    assert win.cfg["jpeg_quality"] == 55
    assert config.load()["jpeg_quality"] == 55


def test_rebuild_all_keeps_the_current_page_visible(window):
    win, app = window
    win.nav_primary.setCurrentRow(3)                 # Formats
    app.processEvents()
    formats = win.stack.currentWidget()
    win.rebuild_all()
    app.processEvents()
    assert win.stack.currentIndex() == 3
    assert win.stack.currentWidget() is not formats  # rebuilt, but still the Formats page
    assert isinstance(win.stack.currentWidget(), sa.FormatsPage)
