"""Edges implied by the catalogue's family relations (see catalog.py).

Visible: a child kind -> its parent (APNG -> PNG, GeoJSON -> JSON), any text kind -> TXT.
Latent (used only by routes and learned reverse conversions): parent -> child kinds and
TXT -> any text kind. These are plain byte copies under a different name/extension.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .. import catalog
from ..core import Backend, Context


class FamilyBackend(Backend):
    name = "family"
    base_cost = 0.3

    def __init__(self):
        self._text = catalog.text_family()

    def sources(self):
        kids = {e.id for e in catalog.CATALOG.values() if e.is_a}
        parents = {e.is_a for e in catalog.CATALOG.values() if e.is_a}
        return kids | parents | self._text

    def targets(self, src):
        out = set()
        entry = catalog.CATALOG.get(src)
        if entry and entry.is_a:
            out.add(entry.is_a)
        if src in self._text and src != "txt" and not (entry and entry.sensitive):
            out.add("txt")
        return out - {src}

    def latent_targets(self, src):
        out = {e.id for e in catalog.CATALOG.values() if e.is_a == src}
        if src == "txt":
            out |= self._text - {"txt"}
        return out - {src}

    def cost(self, src, dst):
        # "It's just text" copies lose meaning; specialised converters should win.
        return 2.5 if dst == "txt" else self.base_cost

    def terminal(self, src, dst):
        return False

    def only_latent_after(self, src, dst):
        # A renamed-as-text copy may only be followed by "text -> some text kind" (used by
        # learned reverses like CSV -> TXT -> PY), never by real converters.
        return dst == "txt" and src != "txt"

    def convert(self, src: Path, dst: Path, ctx: Context) -> list[Path]:
        shutil.copyfile(src, dst)
        ctx.progress(1.0)
        return [dst]
