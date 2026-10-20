"""Things to do to a file that keep it the same kind: make it smaller, resize, rotate, strip location
data, split a PDF, tidy JSON, fix line endings, even out audio, remove a video's sound…

An action is offered on the wheel next to the conversions ("same format"). Its key is
``act:<id>`` or ``act:<id>#<way>`` when it can be done in different ways (those are asked about,
like the other "how?" choices). The result is a new file next to the original, named with what was
done ("photo (smaller).jpg"); the original follows the usual setting (Recycle Bin by default).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .core import ConversionError, Context

log = logging.getLogger(__name__)

IMG_LOSSY = {"jpg", "webp", "avif", "heic"}
IMG_ALL = {"jpg", "png", "webp", "avif", "heic", "tiff", "bmp"}
IMG_META = {"jpg", "png", "webp", "avif", "heic", "tiff"}
TEXTLIKE = {"txt", "md", "csv", "tsv", "json", "xml", "yaml", "toml", "ini", "html", "srt", "vtt", "ass", "tex", "rst",
            "log", "css", "js", "py", "sql"}
AUDIO_ACT = {"mp3", "m4a", "aac", "ogg", "opus", "wav", "flac", "wma", "aiff"}
VIDEO_ACT = {"mp4", "mkv", "webm", "mov", "m4v", "avi"}


@dataclass(frozen=True)
class Way:
    id: str                    # the part after '#'
    label: str
    note: str
    applies: str = ""          # a fact the file must satisfy (variants.check)
    name: str = ""             # words for the new file's name: "photo (<name>).jpg"


@dataclass(frozen=True)
class Action:
    id: str
    title: str                 # on the wheel
    note: str
    terms: tuple
    formats: frozenset
    run: Callable              # (src: Path, work: Path, way: str, fmt: str, ctx) -> list[Path]
    ways: tuple = ()
    name: str = ""
    applies: str = ""          # whole-action condition (a fact about the file)

    def ways_for(self, path: Path, fmt: str) -> list[Way]:
        from . import variants
        return [w for w in self.ways if variants.check(w.applies, path, fmt)]


def _stem(path: Path) -> str:
    from .engine import _base_name
    return _base_name(path)


# ---------------------------------------------------------------- pictures

def _open(path: Path):
    from PIL import Image, ImageOps
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception:                                        # noqa: BLE001
        pass
    im = Image.open(path)
    return im, ImageOps


def _save_like(im, fmt: str, out: Path, ctx: Context, quality: int | None = None, keep_meta: bool = True, src_im=None):
    kw: dict = {}
    fmt_name = {"jpg": "JPEG", "png": "PNG", "webp": "WEBP", "avif": "AVIF", "heic": "HEIF", "tiff": "TIFF", "bmp": "BMP"}[fmt]
    if fmt in IMG_LOSSY:
        kw["quality"] = quality or ctx.cfg.get({"jpg": "jpeg_quality", "webp": "webp_quality"}.get(fmt, "avif_quality"), 90)
    if fmt == "jpg":
        kw.update(optimize=True, progressive=True)
        if im.mode not in ("RGB", "L", "CMYK"):
            im = im.convert("RGB")
    if fmt == "png":
        kw["optimize"] = True
    if fmt == "tiff":
        kw["compression"] = "tiff_lzw"
    if keep_meta and src_im is not None:
        info = src_im.info
        if fmt in ("jpg", "png", "webp", "tiff") and info.get("exif"):
            kw["exif"] = info["exif"]
        if info.get("icc_profile"):
            kw["icc_profile"] = info["icc_profile"]
    elif src_im is not None and src_im.info.get("icc_profile") and fmt != "bmp":
        kw["icc_profile"] = src_im.info["icc_profile"]       # colours should not change when only metadata is removed
    im.save(out, fmt_name, **kw)


def _img_smaller(src: Path, work: Path, way: str, fmt: str, ctx: Context) -> list[Path]:
    from PIL import Image
    im, ops = _open(src)
    with im:
        frame = ops.exif_transpose(im)
        out = work / src.name
        if fmt == "png":
            if way == "colors":
                rgba = frame.convert("RGBA")
                frame = rgba.quantize(colors=256, method=Image.FASTOCTREE, dither=Image.FLOYDSTEINBERG) if rgba.getextrema()[3][0] == 255 \
                    else rgba.quantize(colors=256, method=Image.FASTOCTREE)
            _save_like(frame, "png", out, ctx, keep_meta=False, src_im=im)
        else:
            q = 55 if way == "smallest" else 75
            if way == "smallest" and max(frame.size) > 2000:
                k = 2000 / max(frame.size)
                frame = frame.resize((round(frame.width * k), round(frame.height * k)), Image.LANCZOS)
            _save_like(frame, fmt, out, ctx, quality=q, keep_meta=False, src_im=im)
    return [out]


def _img_resize(src: Path, work: Path, way: str, fmt: str, ctx: Context) -> list[Path]:
    from PIL import Image
    im, ops = _open(src)
    with im:
        frame = ops.exif_transpose(im)
        if way == "half":
            size = (max(1, frame.width // 2), max(1, frame.height // 2))
        else:
            side = int(way)
            k = min(1.0, side / max(frame.size))
            size = (max(1, round(frame.width * k)), max(1, round(frame.height * k)))
        frame = frame.resize(size, Image.LANCZOS)
        out = work / src.name
        _save_like(frame, fmt, out, ctx, quality=92, keep_meta=True, src_im=im)
    return [out]


def _img_rotate(src: Path, work: Path, way: str, fmt: str, ctx: Context) -> list[Path]:
    from PIL import Image
    im, ops = _open(src)
    with im:
        frame = ops.exif_transpose(im)
        frame = {"right": lambda f: f.transpose(Image.ROTATE_270), "left": lambda f: f.transpose(Image.ROTATE_90),
                 "180": lambda f: f.transpose(Image.ROTATE_180), "flip-h": lambda f: f.transpose(Image.FLIP_LEFT_RIGHT),
                 "flip-v": lambda f: f.transpose(Image.FLIP_TOP_BOTTOM)}[way](frame)
        out = work / src.name
        _save_like(frame, fmt, out, ctx, quality=95, keep_meta=False, src_im=im)
    return [out]


def _img_private(src: Path, work: Path, way: str, fmt: str, ctx: Context) -> list[Path]:
    """The same picture with no camera, location or comment data. Colours and orientation are kept."""
    im, ops = _open(src)
    with im:
        frame = ops.exif_transpose(im)
        out = work / src.name
        _save_like(frame, fmt, out, ctx, quality=95, keep_meta=False, src_im=im)
    return [out]


# -------------------------------------------------------------------- PDFs

def _pdf_smaller(src: Path, work: Path, way: str, fmt: str, ctx: Context) -> list[Path]:
    import pymupdf
    out = work / src.name
    with pymupdf.open(str(src)) as doc:
        if doc.needs_pass:
            raise ConversionError("The PDF is password protected.")
        dpi, q = (100, 55) if way == "smallest" else (150, 75)
        try:
            doc.rewrite_images(dpi_threshold=dpi + 30, dpi_target=dpi, quality=q)
        except Exception:                                    # noqa: BLE001 - older PyMuPDF: skip the picture step
            log.info("rewrite_images unavailable")
        doc.save(str(out), garbage=4, deflate=True, clean=True)
    return [out]


def _pdf_split(src: Path, work: Path, way: str, fmt: str, ctx: Context) -> list[Path]:
    import pymupdf
    outs = []
    with pymupdf.open(str(src)) as doc:
        if doc.needs_pass:
            raise ConversionError("The PDF is password protected.")
        n = doc.page_count
        width = len(str(n))
        for i in range(n):
            ctx.check_cancel()
            part = pymupdf.open()
            part.insert_pdf(doc, from_page=i, to_page=i)
            out = work / f"{_stem(src)} - page {i + 1:0{width}d}.pdf"
            part.save(str(out), garbage=3, deflate=True)
            part.close()
            outs.append(out)
            ctx.progress((i + 1) / n)
    return outs


def _pdf_images(src: Path, work: Path, way: str, fmt: str, ctx: Context) -> list[Path]:
    import pymupdf
    outs = []
    with pymupdf.open(str(src)) as doc:
        if doc.needs_pass:
            raise ConversionError("The PDF is password protected.")
        seen = set()
        for page in doc:
            for img in page.get_images(full=True):
                xref = img[0]
                if xref in seen:
                    continue
                seen.add(xref)
                info = doc.extract_image(xref)
                if not info or len(info["image"]) < 1500:      # skip specks and masks
                    continue
                out = work / f"{_stem(src)} - image {len(outs) + 1:03d}.{info['ext'] if info['ext'] != 'jpeg' else 'jpg'}"
                out.write_bytes(info["image"])
                outs.append(out)
    if not outs:
        raise ConversionError("This PDF has no pictures to extract.")
    return outs


def _pdf_rotate(src: Path, work: Path, way: str, fmt: str, ctx: Context) -> list[Path]:
    import pymupdf
    turn = {"right": 90, "left": 270, "180": 180}[way]
    out = work / src.name
    with pymupdf.open(str(src)) as doc:
        if doc.needs_pass:
            raise ConversionError("The PDF is password protected.")
        for page in doc:
            page.set_rotation((page.rotation + turn) % 360)
        doc.save(str(out), garbage=3, deflate=True)
    return [out]


# ------------------------------------------------------------- audio / video

def _ffmpeg(cmd: list[str], ctx: Context, error: str) -> None:
    from .combine import _ffmpeg as run
    run(cmd, ctx, error)


def _audio_volume(src: Path, work: Path, way: str, fmt: str, ctx: Context) -> list[Path]:
    from .backends import media
    out = work / src.name
    args = [a for a in media.ENCODE[fmt][1]] if fmt in media.ENCODE else []
    _ffmpeg(["-i", str(src), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", *args, str(out)], ctx, "Couldn't even out the volume.")
    return [out]


def _audio_smaller(src: Path, work: Path, way: str, fmt: str, ctx: Context) -> list[Path]:
    from .backends import media
    out = work / src.name
    args = {"mp3": ["-vn", "-c:a", "libmp3lame", "-b:a", "128k"], "m4a": ["-vn", "-c:a", "aac", "-b:a", "128k"],
            "aac": ["-vn", "-c:a", "aac", "-b:a", "128k"], "ogg": ["-vn", "-c:a", "libvorbis", "-q:a", "3"],
            "opus": ["-vn", "-c:a", "libopus", "-b:a", "96k"], "wma": ["-vn", "-c:a", "wmav2", "-b:a", "128k"]}.get(
        fmt, media.ENCODE.get(fmt, ("", []))[1])
    _ffmpeg(["-i", str(src), *args, str(out)], ctx, "Couldn't make the audio smaller.")
    return [out]


def _video_smaller(src: Path, work: Path, way: str, fmt: str, ctx: Context) -> list[Path]:
    from .backends import media
    out = work / src.name
    _ffmpeg(["-i", str(src), *media._smaller(media.ENCODE[fmt][1]), str(out)], ctx, "Couldn't make the video smaller.")
    return [out]


def _video_mute(src: Path, work: Path, way: str, fmt: str, ctx: Context) -> list[Path]:
    from .backends import media
    out = work / src.name
    copy = media.REMUX.get(fmt)
    try:
        if copy:
            _ffmpeg(["-i", str(src), *copy, "-an", str(out)], ctx, "retry")
            return [out]
    except ConversionError:
        out.unlink(missing_ok=True)
    _ffmpeg(["-i", str(src), *media.ENCODE[fmt][1], "-an", str(out)], ctx, "Couldn't remove the sound.")
    return [out]


# ------------------------------------------------------------------- text

def _text_tidy(src: Path, work: Path, way: str, fmt: str, ctx: Context) -> list[Path]:
    raw = src.read_bytes()
    text = raw.decode("utf-8-sig", errors="replace")
    out = work / src.name
    if fmt == "json":
        try:
            data = json.loads(text)
        except ValueError as e:
            raise ConversionError(f"This isn't valid JSON: {e}") from e
        if way == "minify":
            out.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
        else:
            out.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=(way == "sorted")) + "\n", encoding="utf-8")
    elif fmt == "xml":
        import xml.dom.minidom as minidom
        try:
            pretty = minidom.parseString(raw).toprettyxml(indent="  ", encoding="utf-8")
        except Exception as e:                               # noqa: BLE001
            raise ConversionError(f"This isn't valid XML: {e}") from e
        out.write_bytes(b"\n".join(ln for ln in pretty.splitlines() if ln.strip()) + b"\n")
    return [out]


def decode_text(raw: bytes) -> str:
    """Text of a file in whatever encoding it uses: a byte-order mark says so; otherwise UTF-8 if it
    is valid UTF-8, UTF-16 if half the bytes are zero, and Windows-1252 as the fallback."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    if raw and raw.count(b"\x00") > len(raw) // 4:
        return raw.decode("utf-16-le" if raw[1:2] == b"\x00" else "utf-16-be", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _text_fix(src: Path, work: Path, way: str, fmt: str, ctx: Context) -> list[Path]:
    text = decode_text(src.read_bytes()).replace("\r\n", "\n").replace("\r", "\n")
    if way == "crlf":
        text = text.replace("\n", "\r\n")
    out = work / src.name
    out.write_bytes(text.encode("utf-8"))
    return [out]


# ---------------------------------------------------------------- registry

def _w(*items) -> tuple:
    return tuple(Way(*i) for i in items)


ACTIONS: list[Action] = [
    Action("smaller", "SMALLER", "Makes the picture's file smaller (JPEG, WebP, AVIF, HEIC: lower quality; PNG: optimised).",
           ("smaller", "compress", "shrink", "size", "optimize"), frozenset(IMG_LOSSY | {"png"}), _img_smaller,
           _w(("good", "Good quality", "A noticeably smaller file that looks the same.", "", "smaller"),
              ("smallest", "Smallest", "Lower quality and at most 2000 px, for sharing.", "", "smallest")), "smaller"),
    Action("resize", "RESIZE", "Makes the picture smaller in pixels: half size, or fitted to a width for the web or email.",
           ("resize", "scale", "shrink", "smaller", "width"), frozenset(IMG_ALL), _img_resize,
           _w(("half", "Half size", "Half the width and height.", "", "half size"),
              ("1920", "1920 px", "Longest side 1920 px (web, full HD).", "side>1920", "1920px"),
              ("1280", "1280 px", "Longest side 1280 px.", "side>1280", "1280px"),
              ("800", "800 px", "Longest side 800 px (email, forums).", "side>800", "800px")), "resized"),
    Action("rotate", "ROTATE", "Turns the picture or flips it.", ("rotate", "turn", "flip", "mirror", "sideways"), frozenset(IMG_ALL), _img_rotate,
           _w(("right", "Right 90°", "Turn clockwise.", "", "rotated right"), ("left", "Left 90°", "Turn counter-clockwise.", "", "rotated left"),
              ("180", "180°", "Upside down.", "", "rotated 180"), ("flip-h", "Flip sideways", "Mirror left to right.", "", "flipped"),
              ("flip-v", "Flip upside", "Mirror top to bottom.", "", "flipped vertically"))),
    Action("private", "NO LOCATION", "Removes camera, date and GPS location data from the picture (colours and orientation are kept).",
           ("private", "privacy", "exif", "gps", "location", "metadata", "strip"), frozenset(IMG_META), _img_private, (), "private", "exif"),
    Action("pdf-smaller", "SMALLER", "Compresses the PDF: scaled-down pictures and a tidied file structure.",
           ("smaller", "compress", "shrink", "size", "optimize"), frozenset({"pdf"}), _pdf_smaller,
           _w(("good", "Balanced", "Pictures at 150 dpi: usually much smaller, still sharp on screen.", "", "smaller"),
              ("smallest", "Smallest", "Pictures at 100 dpi and lower quality.", "", "smallest")), "smaller"),
    Action("split", "SPLIT", "Saves every page as its own PDF.", ("split", "pages", "separate", "extract"), frozenset({"pdf"}), _pdf_split, (), "", "pages>1"),
    Action("pdf-images", "GET PICTURES", "Saves the pictures inside the PDF as files, at their original quality.",
           ("pictures", "images", "extract", "photos"), frozenset({"pdf"}), _pdf_images, (), "", "images>0"),
    Action("pdf-rotate", "ROTATE", "Turns every page of the PDF.", ("rotate", "turn", "sideways"), frozenset({"pdf"}), _pdf_rotate,
           _w(("right", "Right 90°", "Turn clockwise.", "", "rotated right"), ("left", "Left 90°", "Turn counter-clockwise.", "", "rotated left"),
              ("180", "180°", "Upside down.", "", "rotated 180"))),
    Action("volume", "EVEN VOLUME", "Evens out the loudness (normalises it) so it doesn't jump between quiet and loud.",
           ("volume", "normalize", "loudness", "level", "quiet"), frozenset(AUDIO_ACT), _audio_volume, (), "even volume"),
    Action("audio-smaller", "SMALLER", "Re-encodes at a lower bit rate for a smaller file.", ("smaller", "compress", "bitrate", "size"),
           frozenset(AUDIO_ACT - {"wav", "flac", "aiff"}), _audio_smaller, (), "smaller"),
    Action("video-smaller", "SMALLER", "Re-encodes the video at lower quality and at most 1280 px wide.", ("smaller", "compress", "shrink", "size"),
           frozenset(VIDEO_ACT), _video_smaller, (), "smaller", "size>3000000"),
    Action("mute", "NO SOUND", "Removes the audio track (the picture isn't re-encoded when the format allows).", ("mute", "silent", "remove audio", "sound"),
           frozenset(VIDEO_ACT), _video_mute, (), "no sound", "audio"),
    Action("tidy", "TIDY", "Reformats the file so it is easy to read or as small as possible.", ("tidy", "pretty", "format", "minify", "indent", "prettify"),
           frozenset({"json", "xml"}), _text_tidy,
           _w(("pretty", "Pretty-print", "Indented and easy to read.", "", "pretty"), ("minify", "Minify", "As compact as possible.", "", "minified"),
              ("sorted", "Sorted keys", "Pretty-printed with keys in alphabetical order.", "", "sorted"))),
    Action("fix-text", "FIX TEXT", "Saves the text as UTF-8 with the line endings you choose.", ("encoding", "utf-8", "utf8", "line endings", "crlf", "lf", "fix", "text"),
           frozenset(TEXTLIKE), _text_fix,
           _w(("lf", "Unix (LF)", "UTF-8 with Unix line endings (Linux, macOS, Git).", "", "utf-8 lf"),
              ("crlf", "Windows (CRLF)", "UTF-8 with Windows line endings.", "", "utf-8 crlf"))),
]
BY_ID = {a.id: a for a in ACTIONS}


def split_key(key: str) -> tuple[str, str]:
    body = key.split(":", 1)[1]
    ident, _, way = body.partition("#")
    return ident, way


def options_for(path: Path, fmt: str) -> list:
    """Wheel entries for the actions that apply to this file."""
    import dataclasses

    from . import variants
    from .planner import TargetOption
    out = []
    for a in ACTIONS:
        if fmt not in a.formats or not variants.check(a.applies, path, fmt):
            continue
        ways = a.ways_for(path, fmt)
        top = TargetOption(key=f"act:{a.id}", kind="action", fmt=fmt, title=a.title, variant_note=a.note, terms=a.terms)
        if len(ways) > 1:
            top = dataclasses.replace(top, variants=tuple(
                TargetOption(key=f"act:{a.id}#{w.id}", kind="action", fmt=fmt, title=a.title, variant_label=w.label,
                             variant_note=w.note, terms=a.terms) for w in ways))
        elif len(ways) == 1:
            top = dataclasses.replace(top, key=f"act:{a.id}#{ways[0].id}")
        out.append(top)
    return out


def run_inplace(path: Path, key: str, fmt: str, ctx: Context):
    """Do the action; the result is a new file next to the original, which is handled per the usual setting."""
    import shutil
    import tempfile

    from .engine import Result, _hide, unique_path
    ident, way = split_key(key)
    action = BY_ID.get(ident)
    if action is None or fmt not in action.formats:
        raise ConversionError(f"Can't do that to a {fmt.upper()} file.")
    if action.ways and not way:
        way = action.ways[0].id
    name = action.name
    for w in action.ways:
        if w.id == way and w.name:
            name = w.name
    try:
        work = Path(tempfile.mkdtemp(prefix=".ipconv-", dir=path.parent))
        _hide(work)
    except OSError:
        work = Path(tempfile.mkdtemp(prefix="ipconv-"))
    result = Result(source=path)
    try:
        made = action.run(path, work, way, fmt, ctx)
        ctx.check_cancel()
        for p in made:
            if len(made) == 1 and name:
                new = f"{_stem(path)} ({name}){p.suffix}"
            else:
                new = p.name
            final = unique_path(path.parent / new)
            shutil.move(str(p), final)
            result.outputs.append(final)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    ctx.progress(1.0)
    action_cfg = ctx.cfg.get("original", "recycle")
    try:
        if action_cfg == "recycle":
            from send2trash import send2trash
            send2trash(str(path))
            result.original = "recycled"
        elif action_cfg == "delete":
            path.unlink()
            result.original = "deleted"
    except Exception as e:                                   # noqa: BLE001
        result.warning = f"Done, but the original couldn't be removed: {e}"
    return result

