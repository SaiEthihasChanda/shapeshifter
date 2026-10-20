"""Colour themes for the wheel."""

from __future__ import annotations

from dataclasses import dataclass, field

from .formats import CATEGORY_COLORS


@dataclass(frozen=True)
class Theme:
    key: str
    name: str
    ring: str                  # wheel background
    hub: str                   # centre circle
    hub_hover: str
    text: str
    text_dim: str
    hover_text: str = "#ffffff"   # label colour on the highlighted segment
    ring_alpha: int = 252
    seg_idle_alpha: int = 46      # segment tint when not hovered
    seg_hover_alpha: int = 255
    outline: str | None = None    # segment outline colour
    glow: bool = False            # neon glow around the hovered segment
    shadow: bool = True
    categories: dict = field(default_factory=lambda: dict(CATEGORY_COLORS))
    accent: str | None = None     # forces a single colour for every segment


THEMES: dict[str, Theme] = {t.key: t for t in [
    Theme("midnight", "Midnight", ring="#18191e", hub="#24252c", hub_hover="#3a3b44",
          text="#f0f1f5", text_dim="#a0a3af"),
    Theme("daylight", "Daylight", ring="#f6f7fa", hub="#ffffff", hub_hover="#e6e8ef",
          text="#1d1f27", text_dim="#6b6f7d", outline="#dfe1e8", seg_idle_alpha=38),
    Theme("neon", "Neon", ring="#07070c", hub="#0f0f18", hub_hover="#1b1b2a",
          text="#ffffff", text_dim="#8a8aa0", seg_idle_alpha=22, glow=True,
          categories={"image": "#00f5d4", "video": "#ff9e00", "audio": "#c77dff",
                      "document": "#4cc9f0", "data": "#80ff72",
                      "archive": "#ffd60a", "tool": "#9aa0b4"}),
    Theme("frosted", "Frosted glass", ring="#262a33", hub="#323743", hub_hover="#454b59",
          text="#f4f6fb", text_dim="#b4bac8", ring_alpha=185, seg_idle_alpha=70,
          outline="#28ffffff"),  # Qt reads 8-digit hex as #AARRGGBB
    Theme("pastel", "Pastel", ring="#fbf8f4", hub="#ffffff", hub_hover="#f1ebe3",
          text="#3b3530", text_dim="#8c8379", hover_text="#2b2622", seg_idle_alpha=70,
          categories={"image": "#9fd8cb", "video": "#ffc49b", "audio": "#c9b6f2",
                      "document": "#a9c8f5", "data": "#b8e0a8",
                      "archive": "#f3d98b", "tool": "#c9c3bb"}),
    Theme("contrast", "High contrast", ring="#000000", hub="#000000", hub_hover="#333333",
          text="#ffffff", text_dim="#ffff00", hover_text="#000000", seg_idle_alpha=0,
          outline="#ffffff", shadow=False, accent="#ffff00"),
]}

DEFAULT_THEME = "midnight"


def resolve(cfg: dict) -> Theme:
    return THEMES.get(cfg.get("theme", DEFAULT_THEME), THEMES[DEFAULT_THEME])


def segment_color(theme: Theme, cfg: dict, category: str) -> str:
    """Colour of a segment, honouring the theme's forced accent and the user's choice."""
    if theme.accent:
        return theme.accent
    if not cfg.get("color_by_category", True):
        return cfg.get("accent_color", "#7c8cff")
    return theme.categories.get(category) or CATEGORY_COLORS.get(category, "#888888")
