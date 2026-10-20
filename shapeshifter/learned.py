"""Reverse conversions ShapeShifter has learned from what you converted.

After a successful A -> B conversion, B -> A is remembered. Next time a B file is
selected, A is offered (with a warning) if any route exists, including best-effort
routes through latent steps such as B -> text -> A, even when that loses data.
Stored in %LOCALAPPDATA%\\ShapeShifter\\learned.json.
"""

from __future__ import annotations

import json
import logging
import time

from .config import DATA_DIR

log = logging.getLogger(__name__)
FILE = DATA_DIR / "learned.json"


def _load() -> dict:
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError:
        log.exception("Could not save learned conversions")


def record(src_fmt: str, dst_fmt: str) -> None:
    """A conversion src -> dst succeeded: remember dst -> src."""
    from .formats import PSEUDO_FORMATS
    if not src_fmt or not dst_fmt or src_fmt == dst_fmt or dst_fmt in PSEUDO_FORMATS:
        return
    data = _load()
    key = f"{dst_fmt}>{src_fmt}"
    item = data.get(key, {"count": 0, "first": time.time()})
    item["count"] += 1
    item["last"] = time.time()
    data[key] = item
    _save(data)


def pairs() -> list[tuple[str, str, dict]]:
    """[(from, to, info)] most recent first."""
    out = []
    for key, info in _load().items():
        if ">" in key:
            a, b = key.split(">", 1)
            out.append((a, b, info))
    return sorted(out, key=lambda t: -t[2].get("last", 0))


def targets_for(fmt: str) -> list[str]:
    return [b for a, b, _ in pairs() if a == fmt]


def forget(src: str, dst: str) -> None:
    data = _load()
    data.pop(f"{src}>{dst}", None)
    _save(data)


def clear() -> None:
    _save({})
