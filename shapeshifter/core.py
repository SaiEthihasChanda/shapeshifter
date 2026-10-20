"""Common backend interface and helpers."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .formats import ext_of


class ConversionError(Exception):
    """A conversion failed with a message suitable for showing to the user."""


class RiskyFile(ConversionError):
    """The file looks unsafe to process; we refuse instead of trying."""


class Cancelled(Exception):
    pass


@dataclass
class Context:
    cfg: dict
    progress: Callable[[float], None] = lambda f: None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    # Formats of the current step, as identified (file names may not say).
    src_fmt: str | None = None
    dst_fmt: str | None = None
    # Folders to look in for files a document refers to (images, stylesheets): the folder
    # of the file being converted first, then the folder of the user's original file.
    resource_dirs: list = field(default_factory=list)

    def check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise Cancelled()


class Backend:
    """A converter. Subclasses describe what they read/write and do the work."""

    name = "base"

    def available(self) -> bool:
        return True

    def sources(self) -> set[str]:
        """Canonical extensions this backend can read."""
        return set()

    def targets(self, src: str) -> set[str]:
        """Canonical extensions this backend can produce from ``src``."""
        return set()

    def convert(self, src: Path, dst: Path, ctx: Context) -> list[Path]:
        """Convert ``src`` into ``dst`` (inside a scratch folder).

        May write several files (e.g. one image per PDF page); returns every file produced.
        """
        raise NotImplementedError

    def can(self, src: str, dst: str) -> bool:
        return src in self.sources() and dst in self.targets(src)

    # ---- hints for the conversion planner ------------------------------------

    #: relative effort of one conversion step (speed + reliability)
    base_cost = 1.0

    def extra_targets(self, src: str) -> set[str]:
        """Targets reachable but not shown on the wheel (command line only)."""
        return set()

    def latent_targets(self, src: str) -> set[str]:
        """Steps that exist but are only used inside routes and learned reverse
        conversions (e.g. TXT -> any text-based format), never offered directly."""
        return set()

    def cost(self, src: str, dst: str) -> float:
        return self.base_cost

    def multi_output(self, src: str, dst: str) -> bool:
        """True if the step may produce several files or a folder (must be the last step)."""
        return False

    def terminal(self, src: str, dst: str) -> bool:
        """True if no further step may follow (chaining would make no sense)."""
        return self.multi_output(src, dst)

    def only_latent_after(self, src: str, dst: str) -> bool:
        """True if only a latent step may follow this one."""
        return False

    def first_step_only(self, src: str, dst: str) -> bool:
        """True if the step only makes sense on an original file, not on an intermediate
        produced by an earlier step (e.g. GIF -> video needs a real animated GIF)."""
        return False


NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_exe(name: str, extra_dirs: list[str | os.PathLike] = ()) -> str | None:
    """Locate an executable on PATH or in a list of well-known folders."""
    found = shutil.which(name)
    if found:
        return found
    for d in extra_dirs:
        for candidate in (Path(d) / f"{name}.exe", Path(d) / name):
            if candidate.is_file():
                return str(candidate)
    return None


def target_ext(dst: Path) -> str:
    return ext_of(dst)
