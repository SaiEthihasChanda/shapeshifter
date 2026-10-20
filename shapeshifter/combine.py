"""Combine several selected files into one: pictures into a GIF, PDF, slideshow or deck; PDFs into one
PDF; spreadsheets into one workbook (a sheet each); audio or video clips into one; documents into one
document; decks into one deck. The files are taken in name order (frame2 before frame10) and are
never removed. This is the inverse of "every frame / sheet / page" and of "video -> pictures".
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

from .core import NO_WINDOW, Cancelled, ConversionError, Context

log = logging.getLogger(__name__)

IMAGE_FORMATS_EXCLUDED = {"svg", "eps", "psd", "dds", "icns", "ico", "cur"}
TEXT_DOCS = {"md", "html", "txt", "docx", "odt"}
DECKS = {"pptx", "odp", "ppsx", "potx"}
TABLES = {"csv", "tsv", "xlsx", "ods"}
MAX_FILES = 300
FRAME_SECONDS = 0.5            # how long each picture shows in a GIF
SLIDE_SECONDS = 3              # ...and in a slideshow video


def natural_key(path: Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", path.name)]


def natural_sorted(paths) -> list[Path]:
    return sorted((Path(p) for p in paths), key=natural_key)


def kind_of(path: Path) -> str | None:
    from .backends import media
    from .backends.images import ImageBackend
    from .identify import identify
    fmt = identify(path).fmt
    if not fmt:
        return None
    if fmt in ImageBackend().sources() and fmt not in IMAGE_FORMATS_EXCLUDED:
        return "image"
    if fmt == "pdf":
        return "pdf"
    if fmt in TABLES:
        return "table"
    if fmt in TEXT_DOCS:
        return "text"
    if fmt in DECKS:
        return "deck"
    if fmt == "ttf":
        return "font"
    if fmt in media.VIDEO:
        return "video"
    if fmt in media.AUDIO:
        return "audio"
    return None


def targets_for(paths) -> list[str]:
    """Formats the whole selection can be combined into."""
    paths = [Path(p) for p in paths]
    if not 2 <= len(paths) <= MAX_FILES:
        return []
    kinds = {kind_of(p) for p in paths}
    if None in kinds:
        return []
    from .backends import media
    enc = media.encoders() if media.ffmpeg_path() else set()
    if kinds == {"image"}:
        out = ["gif", "webp", "tiff", "pdf", "pptx"]
        if media.ffmpeg_path() and "libx264" in enc:
            out.append("mp4")
        return out
    if kinds <= {"image", "pdf"}:
        return ["pdf"]
    if kinds == {"table"}:
        return ["xlsx", "ods"]
    if kinds == {"text"}:
        return ["pdf", "docx", "md"]
    if kinds == {"deck"}:
        return ["pptx", "odp"]
    if kinds == {"font"}:
        return ["ttc"]
    if kinds == {"audio"} and media.ffmpeg_path():
        return [t for t in ("mp3", "m4a", "flac", "wav", "ogg", "opus") if media.ENCODE[t][0] in enc]
    if kinds == {"video"} and media.ffmpeg_path():
        return [t for t in ("mp4", "mkv", "webm") if media.ENCODE[t][0] in enc]
    return []


def output_name(paths: list[Path], fmt: str) -> str:
    stems = [p.stem for p in paths]
    prefix = os.path.commonprefix(stems).rstrip(" _-.0123456789(")
    if len(prefix) >= 3:
        return f"{prefix} (combined).{fmt}"
    return f"{stems[0]} and {len(paths) - 1} more.{fmt}"


# ------------------------------------------------------------------- the work

def run(paths: list[Path], fmt: str, dst: Path, ctx: Context) -> Path:
    kinds = {kind_of(p) for p in paths}
    if kinds <= {"image", "pdf"}:
        if fmt == "pdf":
            _to_pdf(paths, dst, ctx)
        elif fmt in ("gif", "webp"):
            _animation(paths, fmt, dst, ctx)
        elif fmt == "tiff":
            _tiff(paths, dst, ctx)
        elif fmt == "pptx":
            _pictures_to_deck(paths, dst, ctx)
        elif fmt == "mp4":
            _slideshow(paths, dst, ctx)
    elif kinds == {"table"}:
        _tables(paths, fmt, dst, ctx)
    elif kinds == {"text"}:
        _documents(paths, fmt, dst, ctx)
    elif kinds == {"deck"}:
        _decks(paths, fmt, dst, ctx)
    elif kinds == {"font"}:
        from .native.fonts import build_ttc
        dst.write_bytes(build_ttc([p.read_bytes() for p in paths]))
    else:
        _join_media(paths, fmt, dst, ctx, video=(kinds == {"video"}))
    if not dst.exists() or dst.stat().st_size == 0:
        raise ConversionError("Nothing was produced.")
    return dst


def _load(path: Path):
    from PIL import Image, ImageOps
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception:                                        # noqa: BLE001
        pass
    with Image.open(path) as im:
        im.seek(0)
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P"):
            rgba = im.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.getchannel("A"))
            return bg
        return im.convert("RGB")


def _fit(im, size):
    """The picture scaled to fit inside ``size`` on a white canvas, centred."""
    from PIL import Image
    if im.size == size:
        return im
    k = min(size[0] / im.width, size[1] / im.height)
    scaled = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.LANCZOS)
    canvas = Image.new("RGB", size, (255, 255, 255))
    canvas.paste(scaled, ((size[0] - scaled.width) // 2, (size[1] - scaled.height) // 2))
    return canvas


def _pictures(paths: list[Path], ctx: Context):
    out = []
    for i, p in enumerate(paths):
        ctx.check_cancel()
        out.append(_load(p))
        ctx.progress((i + 1) / len(paths) * 0.7)
    return out


def _animation(paths, fmt, dst, ctx):
    frames = _pictures(paths, ctx)
    size = frames[0].size
    frames = [_fit(f, size) for f in frames]
    ms = int(FRAME_SECONDS * 1000)
    if fmt == "gif":
        frames[0].save(dst, "GIF", save_all=True, append_images=frames[1:], duration=ms, loop=0, disposal=2)
    else:
        frames[0].save(dst, "WEBP", save_all=True, append_images=frames[1:], duration=ms, loop=0,
                       quality=ctx.cfg.get("webp_quality", 90))


def _tiff(paths, dst, ctx):
    frames = _pictures(paths, ctx)
    frames[0].save(dst, "TIFF", save_all=True, append_images=frames[1:], compression="tiff_lzw")


def _to_pdf(paths: list[Path], dst: Path, ctx: Context) -> None:
    """One page per picture (fitted to an A4-sized page); PDFs are appended page for page."""
    import io

    import pymupdf
    from .identify import identify
    out = pymupdf.open()
    for i, p in enumerate(paths):
        ctx.check_cancel()
        if identify(p).fmt == "pdf":
            try:
                with pymupdf.open(str(p)) as src:
                    if src.needs_pass:
                        raise ConversionError(f"{p.name} is password protected.")
                    out.insert_pdf(src)
            except ConversionError:
                raise
            except Exception as e:                           # noqa: BLE001
                raise ConversionError(f"Can't read {p.name}: {e}") from e
        else:
            im = _load(p)
            k = 842 / max(im.size)
            w, h = im.width * k, im.height * k
            page = out.new_page(width=w, height=h)
            raw = p.read_bytes()
            if raw[:3] == b"\xff\xd8\xff":                    # a JPEG goes in untouched
                page.insert_image(page.rect, stream=raw)
            else:
                buf = io.BytesIO()
                im.save(buf, "PNG")
                page.insert_image(page.rect, stream=buf.getvalue())
        ctx.progress((i + 1) / len(paths) * 0.9)
    out.save(str(dst), garbage=3, deflate=True)
    out.close()


def _pictures_to_deck(paths: list[Path], dst: Path, ctx: Context) -> None:
    from .backends.slides import _picture_bytes
    from .native import slides_write
    pictures = []
    for p in paths:
        ctx.check_cancel()
        pictures.append(_picture_bytes(p))
    deck = slides_write.deck_from_images(pictures, paths[0].stem)
    slides_write.write_pptx(deck, dst)


def _ffmpeg(cmd: list[str], ctx: Context, error: str) -> None:
    from .backends import media
    full = [media.ffmpeg_path(), "-y", "-hide_banner", "-nostdin", *cmd]
    proc = subprocess.Popen(full, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=NO_WINDOW,
                            text=True, encoding="utf-8", errors="replace")
    try:
        while True:
            try:
                _, err = proc.communicate(timeout=0.3)
                break
            except subprocess.TimeoutExpired:
                if ctx.cancel_event.is_set():
                    proc.kill()
                    proc.communicate()
                    raise Cancelled() from None
    except BaseException:
        if proc.poll() is None:
            proc.kill()
        raise
    if proc.returncode != 0:
        log.info("ffmpeg failed: %s", (err or "")[-600:])
        raise ConversionError(error)


def _slideshow(paths: list[Path], dst: Path, ctx: Context) -> None:
    """A video that shows each picture for a few seconds (1280 x 720, pictures fitted inside)."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="ss-show-") as tmp:
        cmd: list[str] = []
        for i, p in enumerate(paths):
            ctx.check_cancel()
            f = Path(tmp) / f"s{i:04d}.png"
            _fit(_load(p), (1280, 720)).save(f)
            cmd += ["-loop", "1", "-framerate", "25", "-t", str(SLIDE_SECONDS), "-i", str(f)]
            ctx.progress((i + 1) / len(paths) * 0.5)
        n = len(paths)
        graph = "".join(f"[{i}:v]setsar=1,format=yuv420p[v{i}];" for i in range(n))
        graph += "".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]"
        _ffmpeg(cmd + ["-filter_complex", graph, "-map", "[v]", "-r", "25", "-c:v", "libx264", "-preset", "medium",
                       "-crf", "20", "-movflags", "+faststart", str(dst)], ctx, "Couldn't make the slideshow video.")


def _tables(paths: list[Path], fmt: str, dst: Path, ctx: Context) -> None:
    """One workbook with a sheet per file (and per sheet inside a multi-sheet workbook)."""
    from importlib.util import find_spec

    from .backends import data
    from .identify import identify
    from .native import tables as T
    data._native_xlsx = bool(ctx.cfg.get("prefer_native")) or find_spec("openpyxl") is None
    tables: list[T.Table] = []
    for p in paths:
        ctx.check_cancel()
        f = identify(p).fmt
        sheets = {t.name: t.rows for t in T.from_ods(p)} if f == "ods" else data._read_table(p, f)
        for name, rows in sheets.items():
            label = p.stem if len(sheets) == 1 else f"{p.stem} - {name}"
            tables.append(T.Table(label, [list(r) for r in rows]))
    if fmt == "ods":
        T.write_ods(dst, tables)
    else:
        names = T.safe_sheet_names(tables)
        data._write_table(dst, "xlsx", {n: [[T.coerce(c) if isinstance(c, str) else c for c in r] for r in t.rows]
                                        for n, t in zip(names, tables)})


def _documents(paths: list[Path], fmt: str, dst: Path, ctx: Context) -> None:
    """Documents one after another, each starting with its file name as a heading."""
    from .identify import identify
    from .native import docread, docs
    blocks: list[docs.Block] = []
    for p in paths:
        ctx.check_cancel()
        f = identify(p).fmt
        if f == "docx":
            part, _ = docread.docx_blocks(p)
        elif f == "odt":
            part, _ = docread.odt_blocks(p)
        else:
            text = p.read_bytes().decode("utf-8-sig", errors="replace")
            part = docs.parse_markdown(text) if f == "md" else docs.parse_html(text) if f == "html" else docs.parse_text(text)
        blocks.append(docs.Block("h1", docs.escape_markdown(p.stem)))
        blocks += part
    title = output_name(paths, "x").rsplit(".", 1)[0]
    if fmt == "md":
        dst.write_text(docs.to_markdown([b for b in blocks if b.kind != "img"]), encoding="utf-8")
    elif fmt == "docx":
        from .native.docx import blocks_to_docx
        dst.write_bytes(blocks_to_docx(blocks, [paths[0].parent], title))
    else:
        from .backends.mupdf import html_to_pdf
        html_to_pdf(docs.to_html(blocks, title, css=False), dst, [p.parent for p in paths], title)


def _decks(paths: list[Path], fmt: str, dst: Path, ctx: Context) -> None:
    """All slides in one deck (the first deck's slide size; others are scaled to fit it)."""
    from .identify import identify
    from .native import slides, slides_write
    combined = None
    for p in paths:
        ctx.check_cancel()
        deck = slides_write.read_odp(p) if identify(p).fmt == "odp" else slides.read_pptx(p)
        if combined is None:
            combined = deck
            continue
        kx, ky = combined.width / deck.width, combined.height / deck.height
        for s in deck.slides:
            for sh in s.shapes:
                sh.x, sh.w, sh.y, sh.h = sh.x * kx, sh.w * kx, sh.y * ky, sh.h * ky
        combined.slides += deck.slides
    if fmt == "odp":
        slides_write.write_odp(combined, dst)
    else:
        slides_write.write_pptx(combined, dst)


def _join_media(paths: list[Path], fmt: str, dst: Path, ctx: Context, video: bool) -> None:
    """Clips one after another, re-encoded so they can differ in format and size."""
    from .backends import media
    infos = [media.probe(p) for p in paths]
    audio = all(i.has_audio for i in infos)
    cmd: list[str] = []
    for p in paths:
        cmd += ["-i", str(p)]
    n = len(paths)
    args = [a for a in media.ENCODE[fmt][1]]
    if video:
        w, h = infos[0].width or 1280, infos[0].height or 720
        w, h = w - w % 2, h - h % 2
        if "-vf" in args:                                    # scaling is done in the filter graph below
            i = args.index("-vf")
            del args[i:i + 2]
        parts = []
        for i in range(n):
            parts.append(f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v{i}]")
            if audio:
                parts.append(f"[{i}:a]aresample=44100,aformat=channel_layouts=stereo[a{i}]")
        joined = "".join(f"[v{i}]" + (f"[a{i}]" if audio else "") for i in range(n))
        parts.append(f"{joined}concat=n={n}:v=1:a={1 if audio else 0}[v]" + ("[a]" if audio else ""))
        cmd += ["-filter_complex", ";".join(parts), "-map", "[v]"] + (["-map", "[a]"] if audio else [])
        if not audio:
            args += ["-an"]                                  # some clip has no sound: the result has none
    else:
        parts = [f"[{i}:a:0]aresample=44100,aformat=channel_layouts=stereo[a{i}]" for i in range(n)]
        parts.append("".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[a]")
        cmd += ["-filter_complex", ";".join(parts), "-map", "[a]"]
    _ffmpeg(cmd + args + [str(dst)], ctx, "The clips couldn't be joined (they may use formats ffmpeg can't merge).")

