"""Registry of native file -> file converters contributed by the format modules.

A converter takes the source bytes and returns the output bytes, or a list of
(name_suffix, bytes) pairs when it produces several files.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class Conv:
    fn: Callable
    multi: bool = False
    terminal: bool = False      # output is a summary/rendering: nothing may follow
    cost: float = 1.0
    first_only: bool = False    # only on the user's own file, never on an intermediate


CONVERTERS: dict[tuple[str, str], Conv] = {}


def register(sources, targets, *, multi=False, terminal=False, cost=1.0, first_only=False):
    sources = [sources] if isinstance(sources, str) else list(sources)
    targets = [targets] if isinstance(targets, str) else list(targets)

    def deco(fn):
        for s in sources:
            for t in targets:
                if s != t:
                    CONVERTERS[(s, t)] = Conv(fn, multi, terminal, cost, first_only)
        return fn
    return deco


def load_all():
    """Import every module that registers converters."""
    from . import (certs, codefmt, docx, fonts, geo, mail, mesh, misc, notebook,  # noqa: F401
                   pim, subtitles)
