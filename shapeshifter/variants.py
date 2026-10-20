"""Different ways of reaching the same target, offered after looking inside the file.

A variant travels in the target key: ``png`` is the default way, ``png#frames`` an alternative.
The last conversion step receives the part after ``#`` as ``ctx.variant``. Which alternatives are
worth asking about depends on facts about the file (does the GIF have several frames? the workbook
several sheets? the document several pages? does the video have sound?), which are read lazily
and only when the person actually picks that target.
"""

from __future__ import annotations

import logging
import re
import zipfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

ANIM_SRC = {"gif", "webp", "apng", "avif", "png", "tiff", "heic"}
STILL_DST = {"png", "jpg", "webp", "bmp", "tiff", "tga", "qoi", "ico", "avif", "heic", "gif"}
KEEPS_ANIMATION = {"png", "webp", "avif", "tiff", "gif", "heic"}
SHEET_SRC = {"xlsx", "ods", "xls", "xlsm", "sqlite"}
SHEET_DST = {"csv", "tsv"}
IMAGE_PAGE_DST = {"png", "jpg", "webp"}
DECK_SRC = {"pptx", "odp", "ppsx", "potx", "ppt"}
VIDEO_CONTAINERS = {"mp4", "mkv", "webm", "mov", "avi", "wmv", "m4v", "flv", "ts", "3gp", "mpg", "ogv"}
CHARS_PER_PAGE = 3000


# ------------------------------------------------------------------- the facts

@dataclass
class Facts:
    path: Path
    fmt: str
    _cache: dict = field(default_factory=dict)

    def get(self, name: str):
        if name not in self._cache:
            try:
                self._cache[name] = getattr(self, "_" + name)()
            except Exception:                                     # noqa: BLE001 - unknown: assume the simplest case
                log.debug("fact %s failed for %s", name, self.path, exc_info=True)
                self._cache[name] = _DEFAULTS.get(name, 0)
        return self._cache[name]

    # -- pictures
    def _frames(self) -> int:
        from PIL import Image
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except Exception:                                         # noqa: BLE001
            pass
        with Image.open(self.path) as im:
            return int(getattr(im, "n_frames", 1))

    def _side(self) -> int:
        from PIL import Image
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except Exception:                                         # noqa: BLE001
            pass
        with Image.open(self.path) as im:
            return max(im.size)

    def _exif(self) -> int:
        from PIL import Image
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except Exception:                                         # noqa: BLE001
            pass
        with Image.open(self.path) as im:
            ex = im.getexif()
            gps = bool(ex.get_ifd(0x8825)) if hasattr(ex, "get_ifd") else False
            return 1 if (len(ex) > 1 or gps or im.info.get("xmp") or im.info.get("exif") and len(ex) > 0) else 0

    def _images(self) -> int:
        import pymupdf
        with pymupdf.open(str(self.path)) as doc:
            return sum(len(doc[i].get_images()) for i in range(min(doc.page_count, 20)))

    # -- spreadsheets / databases
    def _sheets(self) -> int:
        f = self.fmt
        if f in ("xlsx", "xlsm"):
            with zipfile.ZipFile(self.path) as z:
                return len(re.findall(rb"<sheet\b", z.read("xl/workbook.xml")))
        if f == "xls":
            from .native import legacy
            return len([n for n, rows in legacy.read_xls(self.path.read_bytes()).items()])
        if f == "ods":
            with zipfile.ZipFile(self.path) as z:
                return len(re.findall(rb"<table:table\b", z.read("content.xml")))
        if f == "sqlite":
            import sqlite3
            con = sqlite3.connect(f"{self.path.as_uri()}?mode=ro&immutable=1", uri=True)
            try:
                return con.execute("SELECT count(*) FROM sqlite_master WHERE type IN ('table','view') "
                                   "AND name NOT LIKE 'sqlite_%'").fetchone()[0]
            finally:
                con.close()
        return 1

    # -- documents
    def _pages(self) -> int:
        f = self.fmt
        if f == "pdf":
            import pymupdf
            with pymupdf.open(str(self.path)) as doc:
                return doc.page_count
        if f == "ppt":
            from .native import legacy
            return len(legacy.read_ppt(self.path.read_bytes()))
        if f == "doc":
            from .native import legacy
            return max(1, round(len(legacy.doc_text(self.path.read_bytes())) / CHARS_PER_PAGE + 0.49))
        if f in DECK_SRC:
            with zipfile.ZipFile(self.path) as z:
                names = z.namelist()
                if f == "odp":
                    return len(re.findall(rb"<draw:page\b", z.read("content.xml")))
                return len([n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)])
        if f == "docx":
            with zipfile.ZipFile(self.path) as z:
                try:
                    m = re.search(rb"<Pages>(\d+)</Pages>", z.read("docProps/app.xml"))
                    if m and int(m.group(1)) > 0:
                        return int(m.group(1))
                except KeyError:
                    pass
                text = re.sub(rb"<[^>]+>", b"", z.read("word/document.xml"))
                breaks = z.read("word/document.xml").count(b'w:type="page"')
                return max(1 + breaks, round(len(text) / CHARS_PER_PAGE + 0.49))
        if f == "odt":
            with zipfile.ZipFile(self.path) as z:
                try:
                    m = re.search(rb'meta:page-count="(\d+)"', z.read("meta.xml"))
                    if m:
                        return int(m.group(1))
                except KeyError:
                    pass
                return max(1, round(len(re.sub(rb"<[^>]+>", b"", z.read("content.xml"))) / CHARS_PER_PAGE + 0.49))
        if f in ("md", "html", "txt", "rst", "tex", "rtf", "epub", "json", "csv", "tsv", "xml", "yaml"):
            size = self.path.stat().st_size
            return max(1, round(min(size, 5_000_000) / (CHARS_PER_PAGE * 1.2) + 0.49))
        return 1

    # -- audio / video
    def _media(self):
        from .backends import media
        return media.probe(self.path)

    def _audio(self) -> int:
        return 1 if self.get("media").has_audio else 0

    def _video(self) -> int:
        return 1 if self.get("media").has_video else 0

    def _subs(self) -> int:
        return self.get("media").subs

    def _duration(self) -> float:
        return self.get("media").duration or 0.0

    def _size(self) -> int:
        return self.path.stat().st_size


_DEFAULTS = {"frames": 1, "sheets": 1, "pages": 1, "audio": 1, "video": 1, "subs": 0, "duration": 0.0, "size": 0,
             "side": 0, "exif": 0, "images": 0}


@lru_cache(maxsize=32)
def _facts(path: str, mtime: int, size: int, fmt: str) -> Facts:
    return Facts(Path(path), fmt)


def facts_for(path, fmt: str) -> Facts:
    p = Path(path)
    try:
        st = p.stat()
        return _facts(str(p.resolve()), st.st_mtime_ns, st.st_size, fmt)
    except OSError:
        return Facts(p, fmt)


_TAG = re.compile(r"^(frames|sheets|pages|duration|size|side|images)>(\d+(?:\.\d+)?)$")


def check(tag: str, path, fmt: str) -> bool:
    """Does the file satisfy a variant's ``applies`` tag? ('' is always true.)"""
    if not tag:
        return True
    f = facts_for(path, fmt)
    m = _TAG.match(tag)
    if m:
        return float(f.get(m.group(1))) > float(m.group(2))
    return bool(f.get(tag))            # audio | video | subs


# ----------------------------------------------------------------- the choices

@dataclass(frozen=True)
class Spec:
    id: str                            # the part after '#'; "" for the default way
    backend: str                       # name of the backend doing the last step
    srcs: frozenset                    # format the last step reads
    dsts: frozenset
    label: str
    note: str
    applies: str = ""
    for_src: frozenset | None = None   # restrict to these original source formats


def _s(*items) -> frozenset:
    return frozenset(items)


def specs_for(src: str, dst: str, last_src: str, backend: str) -> list[Spec]:
    """The ways to reach ``dst`` from a file of format ``src`` (default way first), if more than one."""
    out: list[Spec] = []
    if backend == "images" and last_src in ANIM_SRC and dst in STILL_DST and dst != "ico":
        keep = dst in KEEPS_ANIMATION
        out.append(Spec("", backend, _s(last_src), _s(dst), "Keep animation" if keep else "First frame",
                        "Keeps every frame in one animated file." if keep else "Only the first frame is saved."))
        if keep:
            out.append(Spec("first", backend, _s(last_src), _s(dst), "First frame", "Only the first frame is saved.", "frames>1"))
        out.append(Spec("frames", backend, _s(last_src), _s(dst), "Every frame", "One numbered image per frame.", "frames>1"))
        out.append(Spec("sheet", backend, _s(last_src), _s(dst), "Sprite sheet", "All frames laid out in one image.", "frames>1"))
    elif backend == "data" and last_src in SHEET_SRC and dst in SHEET_DST:
        what = "table" if src == "sqlite" else "sheet"
        out.append(Spec("", backend, _s(last_src), _s(dst), f"First {what}", f"Only the first {what} is saved."))
        out.append(Spec("sheets", backend, _s(last_src), _s(dst), f"Every {what}", f"One file per {what}.", "sheets>1"))
    elif backend == "mupdf" and last_src == "pdf" and dst in IMAGE_PAGE_DST:
        unit = "slide" if src in DECK_SRC else "page"
        out.append(Spec("", backend, _s("pdf"), _s(dst), f"One image per {unit}", f"A numbered image for every {unit}."))
        out.append(Spec("first", backend, _s("pdf"), _s(dst), f"First {unit} only", f"Only the first {unit} is saved.", "pages>1"))
        out.append(Spec("tall", backend, _s("pdf"), _s(dst), f"One tall image", f"Every {unit} stacked in a single image.", "pages>1"))
    elif backend == "ffmpeg" and last_src not in ("gif",) and dst in VIDEO_CONTAINERS and src not in ("gif",):
        out.append(Spec("", backend, _s(last_src), _s(dst), "Best match", "Fast, lossless copy when the format allows, otherwise re-encoded."))
        out.append(Spec("small", backend, _s(last_src), _s(dst), "Smaller file", "Re-encoded at a lower quality and at most 1280 px wide.", "size>3000000"))
        out.append(Spec("mute", backend, _s(last_src), _s(dst), "No sound", "The same video without its audio track.", "audio"))
    elif backend == "ffmpeg" and dst == "gif":
        out.append(Spec("", backend, _s(last_src), _s(dst), "Standard", "Up to 640 px wide, 12 frames per second."))
        out.append(Spec("small", backend, _s(last_src), _s(dst), "Small and quick", "320 px wide, 8 frames per second: a much smaller file."))
        out.append(Spec("short", backend, _s(last_src), _s(dst), "First 10 seconds", "Only the start of the video.", "duration>12"))
    elif backend == "ffmpeg" and dst in ("png", "jpg", "webp") and src not in ("gif",):
        out.append(Spec("", backend, _s(last_src), _s(dst), "Middle frame", "One picture from the middle of the video."))
        out.append(Spec("first", backend, _s(last_src), _s(dst), "First frame", "The very first picture."))
        out.append(Spec("every", backend, _s(last_src), _s(dst), "A frame every few seconds", "A numbered image at regular intervals.", "duration>8"))
        out.append(Spec("sheet", backend, _s(last_src), _s(dst), "Contact sheet", "A grid of frames in one image.", "duration>8"))
    return out if len(out) > 1 else []


def variant_key(dst: str, spec: Spec) -> str:
    return dst if not spec.id else f"{dst}#{spec.id}"


def split_target(target: str) -> tuple[str, str]:
    base, _, variant = target.partition("#")
    return base, variant
