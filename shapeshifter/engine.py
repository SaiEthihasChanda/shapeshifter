"""Identifies files, lists what they can become, and performs the in-place conversion."""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import planner
from .backends import ALL_BACKENDS, Backend, Context, ConversionError, RiskyFile
from .formats import canonical, file_ext, split_name
from .identify import identify
from .planner import TargetOption

log = logging.getLogger(__name__)

_available: list[Backend] | None = None


def backends() -> list[Backend]:
    global _available
    if _available is None:
        _available = []
        for b in ALL_BACKENDS:
            try:
                if b.available():
                    _available.append(b)
            except Exception:
                log.exception("Backend %s failed availability check", b.name)
    return _available


def supported_sources() -> set[str]:
    out: set[str] = set()
    for b in backends():
        out |= b.sources()
    return out


# ------------------------------------------------------------------ options

def plausible_formats(path: str | Path) -> list[str]:
    """When identification is unsure, the candidate formats worth offering (best first)."""
    ident = identify(path)
    if ident.risky or not ident.fmt or ident.confidence >= 0.6:
        return []
    fmts = []
    for c in ident.candidates:
        if c.confidence >= 0.4 and c.fmt not in fmts and planner.plan(c.fmt):
            fmts.append(c.fmt)
    return fmts[:3] if len(fmts) >= 2 else []


def options_for(path: str | Path, include_hidden_engine_targets: bool = False,
                fmt: str | None = None) -> list[TargetOption]:
    """Everything one file can become, including Fix-name and Inspect entries.

    ``fmt`` forces the source format (the user picked it from the "Treat as" choices).
    """
    ident = identify(path)
    if ident.risky:
        return []
    if fmt is None:
        choices = plausible_formats(path)
        if choices:
            # Unsure what this is: let the user pick first, then show conversions.
            opts = [TargetOption(key=f"assume:{f}", kind="assume", fmt=f) for f in choices]
            return opts + [TargetOption(key="inspect", kind="inspect")]
    src = fmt or ident.fmt
    opts: list[TargetOption] = []
    if ident.needs_rename and not fmt:
        opts.append(TargetOption(key=f"rename:{ident.fmt}", kind="rename", fmt=ident.fmt))
    elif fmt and ident.ext_fmt != fmt:
        opts.append(TargetOption(key=f"rename:{fmt}", kind="rename", fmt=fmt))
    if src:
        for o in planner.plan(src).values():
            if (o.on_wheel or include_hidden_engine_targets) and o.fmt != src:
                opts.append(o)
        opts += _learned_options(src, {o.key for o in opts})
        if _can_fill_forms(src):
            opts.append(TargetOption(key="fill", kind="fill"))
        opts = _filter_by_content(path, src, _apply_specs(path, src, _group_variants(path, src, opts)))
    if ident.details.get("sensitive"):
        # Private keys & keystores: only re-encodings of the same secret, never documents.
        opts = [o for o in opts if o.kind in ("rename", "inspect") or o.fmt in SENSITIVE_OK]
    if not src or (ident.confidence < 0.6 and not fmt):
        opts.append(TargetOption(key="inspect", kind="inspect"))
    return planner.ordered(opts, src)


SENSITIVE_OK = {"pem", "der"}

# One target, several behaviours. The wheel shows the target once; choosing it asks which way
# (after looking inside the file, so it only asks when there's really a choice).
VARIANT_GROUPS = {"docx": ("docxpic",), "pdf": ("pdfocr",)}
VARIANT_TEXT = {
    ("docx", "docx"): ("Text (OCR)", "Recognises the text so you can edit it. Pictures and layout aren't kept."),
    ("docx", "docxpic"): ("Picture", "Puts the picture on a Word page so you can add text next to it."),
    ("pdf", "pdf"): ("Picture", "A PDF page showing the picture."),
    ("pdf", "pdfocr"): ("Searchable", "The same page plus an invisible text layer, so it can be searched and copied."),
}


def _group_variants(path, src: str, opts: list[TargetOption]) -> list[TargetOption]:
    import dataclasses
    by_key = {o.key: o for o in opts}
    if src == "pdf" and "pdfocr" in by_key and not _is_scanned_pdf(path):
        opts = [o for o in opts if o.key != "pdfocr"]          # "OCR PDF" only makes sense for scans
        by_key.pop("pdfocr")
    out = list(opts)
    for base, extras in VARIANT_GROUPS.items():
        present = [by_key[k] for k in (base, *extras) if k in by_key and by_key[k].kind == "convert"]
        if len(present) < 2 or present[0].key != base:
            continue
        variants = tuple(dataclasses.replace(o, variant_label=VARIANT_TEXT[(base, o.key)][0],
                                             variant_note=VARIANT_TEXT[(base, o.key)][1])
                         for o in present)
        top = dataclasses.replace(present[0], variants=variants, warnings=[])
        out = [top if o.key == base else o for o in out if o.key not in extras]
    return out


def _filter_by_content(path, src: str, opts: list[TargetOption]) -> list[TargetOption]:
    """Drop targets that can't work for this particular file: audio from a silent video, subtitles
    from a video that has none."""
    from .backends import media
    if src not in media.VIDEO or not media.ffmpeg_path():
        return opts
    info = media.probe(path)
    drop = set()
    if not info.has_audio:
        drop |= media.EXTRACT_TARGETS | media.AUDIO_TARGETS
    if not info.subs:
        drop |= media.SUBTITLE_TARGETS
    if not info.has_video and info.has_audio:
        drop |= media.FRAME_TARGETS | {"gif"}
    return [o for o in opts if not (o.kind == "convert" and o.fmt in drop)]


def _apply_specs(path, src: str, opts: list[TargetOption]) -> list[TargetOption]:
    """Offer alternative ways of reaching a target (every frame, every sheet, first page…)."""
    import dataclasses

    from . import variants
    out = []
    for o in opts:
        if o.kind != "convert" or not o.path or o.variants or not o.fmt:
            out.append(o)
            continue
        last = o.path[-1]
        specs = variants.specs_for(src, o.fmt, last.src, last.backend.name)
        # Only ask when this file really has another way to go (several frames, sheets, pages…).
        specs = specs[:1] + [sp for sp in specs[1:] if variants.check(sp.applies, path, src)]
        if len(specs) < 2:
            out.append(o)
            continue
        ways = tuple(dataclasses.replace(o, key=variants.variant_key(o.fmt, sp), variant_label=sp.label,
                                         variant_note=sp.note, applies=sp.applies, variants=())
                     for sp in specs)
        out.append(dataclasses.replace(o, variants=ways, warnings=[]))
    return out


def _is_scanned_pdf(path) -> bool:
    """True if some page is only a picture (no text layer), so OCR would add something."""
    try:
        import pymupdf
        with pymupdf.open(path) as doc:
            if doc.needs_pass:
                return False
            for i, page in enumerate(doc):
                if i >= 8:
                    break
                if not page.get_text("text").strip() and page.get_images():
                    return True
        return False
    except Exception:                                             # noqa: BLE001 - can't tell: keep the option
        return True


def _has_text(path, cfg: dict) -> bool:
    from . import ocr
    try:
        page, _ = ocr.recognize_file(path, cfg)
    except Exception:                                             # noqa: BLE001 - OCR unavailable
        return False
    return sum(ch.isalnum() for ch in page.text()) >= 4


def resolve_variants(paths: list, option: TargetOption, cfg: dict) -> list[TargetOption]:
    """The variants of ``option`` that make sense once the files have been looked at.

    One result means no question needs asking. Text recognition is only worth offering when
    there is text to recognise."""
    keep = list(option.variants)
    if len(keep) < 2:
        return keep or [option]
    uses_ocr = [v for v in keep if any(e.backend.name == "ocr" for e in v.path)]
    if uses_ocr and not any(_has_text(p, cfg) for p in paths[:5]):
        keep = [v for v in keep if v not in uses_ocr]
    # ways that only make sense for some files (several frames / sheets / pages, a soundtrack…)
    from . import variants
    fmts = {str(p): identify(p).fmt or "" for p in paths[:5]}
    keep = [v for v in keep if not v.applies or any(variants.check(v.applies, p, f) for p, f in fmts.items())]
    return keep or [option]


def _can_fill_forms(src: str) -> bool:
    """Forms are filled on a picture, which needs the OCR engine to find the blanks."""
    from . import ocr
    from .backends.images import ImageBackend
    pictures = src in ImageBackend().sources() and src not in ("svg", "eps", "psd", "dds", "icns")
    return (pictures or src == "pdf") and ocr.available()


def _learned_options(src: str, have: set[str]) -> list[TargetOption]:
    import dataclasses
    from . import config, learned
    if not config.load().get("learn_reverse", True):
        return []
    out = []
    for dst in learned.targets_for(src):
        if dst in have or dst == src:
            continue
        opt = planner.route(src, dst)
        if opt is None:
            continue
        note = f"Learned from an earlier {dst.upper()} → {src.upper()} conversion; data may be lost"
        out.append(dataclasses.replace(opt, learned=True, on_wheel=True,
                                       warnings=[note] + list(opt.warnings)))
    return out


def _common(paths: list, include_cli: bool, forced: dict | None = None) -> list[TargetOption]:
    if not paths:
        return []
    forced = forced or {}
    result = options_for(paths[0], include_cli, forced.get(str(paths[0])))
    for p in paths[1:]:
        keys = {o.key for o in options_for(p, include_cli, forced.get(str(p)))}
        result = [o for o in result if o.key in keys]
    return result


def visible_options(paths: list[str | Path], cfg: dict,
                    forced: dict | None = None) -> list[TargetOption]:
    """Options shown on the wheel: common to all files, minus pairs the user hid.
    ``forced`` maps path -> source format chosen by the user."""
    forced = forced or {}
    hidden = set(cfg.get("hidden_pairs", []))
    sources = {forced.get(str(p)) or identify(p).fmt for p in paths}
    opts = [o for o in _common(paths, False, forced)
            if not (o.kind == "convert" and any(f"{s}>{o.fmt}" in hidden for s in sources))]
    return opts[: max(1, int(cfg.get("max_segments", 16)))]


def visible_targets(paths: list[str | Path], cfg: dict) -> list[str]:
    return [o.key for o in visible_options(paths, cfg)]


def common_targets(paths: list[str | Path]) -> list[str]:
    """Every target all files can reach (wheel and command-line-only ones)."""
    return [o.key for o in _common(paths, True)]


def targets_for(path: str | Path) -> list[str]:
    return [o.key for o in options_for(path) if o.kind == "convert"]


def all_pairs() -> dict[str, list[tuple[str, str, str]]]:
    """source format -> [(target, via, description)] for the settings app."""
    out: dict[str, list[tuple[str, str, str]]] = {}
    for src in sorted(supported_sources()):
        opts = planner.ordered([o for o in planner.plan(src).values() if o.on_wheel], src)
        out[src] = [(o.key, o.via, o.description) for o in opts]
    return out


def reset() -> None:
    """Forget cached engine discovery (after installing ffmpeg/LibreOffice/pandoc)."""
    global _available
    _available = None
    from . import identify as ident_mod
    from .backends import external, media
    for fn in (media.ffmpeg_path, media.ffprobe_path, media.encoders,
               external.soffice_path, external.pandoc_path):
        fn.cache_clear()
    for b in ALL_BACKENDS:
        if hasattr(b, "_read"):
            b._read = None
    planner.reset()
    ident_mod.clear_cache()


# --------------------------------------------------------------- conversion

def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    base, ext = split_name(path)
    suffix = path.name[len(base):]
    i = 1
    while True:
        candidate = path.with_name(f"{base} ({i}){suffix}")
        if not candidate.exists():
            return candidate
        i += 1


@dataclass
class Result:
    source: Path
    outputs: list[Path] = field(default_factory=list)
    original: str = "kept"  # kept | recycled | deleted | renamed
    warning: str | None = None


def _base_name(src: Path) -> str:
    """Name without the extension being replaced. Things that don't look like an
    extension (e.g. 'report.final draft') stay part of the name."""
    base, ext = split_name(src)
    from .formats import CATEGORIES, NAMES
    known = ext in NAMES or any(ext in exts for exts in CATEGORIES.values())
    return base if known or re.fullmatch(r"[a-z0-9]{1,5}", ext) else src.name


def convert_inplace(path: str | Path, target: str, ctx: Context,
                    src_fmt: str | None = None) -> Result:
    """Convert one file. ``src_fmt`` overrides the detected format (user's choice)."""
    src = Path(path)
    if not src.is_file():
        raise ConversionError(f"File not found: {src.name}")
    ident = identify(src)
    if ident.risky:
        raise RiskyFile("Not converted: " + ident.risky)
    if src_fmt:
        import copy
        ident = copy.copy(ident)
        ident.fmt = src_fmt
    base = _base_name(src)

    if target.startswith("rename:"):
        fmt = target.split(":", 1)[1]
        final = unique_path(src.with_name(f"{base}.{file_ext(fmt)}"))
        src.rename(final)
        log.info("Renamed %s -> %s", src, final.name)
        return Result(source=src, outputs=[final], original="renamed")

    if not ident.fmt:
        raise ConversionError("Couldn't work out what kind of file this is.")
    from . import variants
    target, variant = variants.split_target(target)
    target = target if target == "extract" else canonical(target)
    ctx.variant = variant
    sensitive = ident.details.get("sensitive")
    if sensitive and target not in SENSITIVE_OK:
        raise RiskyFile(f"Not converted: {sensitive}; it can only be re-encoded as PEM or DER.")
    # Wheel targets first; anything else reachable (learned reverse / explicit request) next.
    option = planner.plan(ident.fmt).get(target) or planner.route(ident.fmt, target)
    if option is None:
        raise ConversionError(f"No way to convert {ident.fmt.upper()} to {target.upper()}")

    log.info("Converting %s (%s) -> %s via %s", src, ident.fmt, target, option.via)
    try:
        work = Path(tempfile.mkdtemp(prefix=".ipconv-", dir=src.parent))
        _hide(work)
    except OSError:
        work = Path(tempfile.mkdtemp(prefix="ipconv-"))

    result = Result(source=src)
    try:
        produced = _run_path(src, ident.fmt, base, option, work, ctx)
        ctx.check_cancel()
        st = src.stat()
        for p in produced:
            final = unique_path(src.parent / p.name)
            shutil.move(str(p), final)
            if ctx.cfg.get("preserve_mtime", True) and final.is_file():
                try:
                    os.utime(final, (st.st_atime, st.st_mtime))
                except OSError:
                    pass
            result.outputs.append(final)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    action = ctx.cfg.get("original", "recycle")
    try:
        if action == "recycle":
            from send2trash import send2trash
            send2trash(str(src))
            result.original = "recycled"
        elif action == "delete":
            src.unlink()
            result.original = "deleted"
    except Exception as e:
        log.exception("Could not remove original %s", src)
        result.warning = f"Converted, but the original couldn't be removed: {e}"
    log.info("Done: %s -> %s (original %s)", src, [str(o) for o in result.outputs], result.original)
    if option.kind == "convert" and not sensitive and ctx.cfg.get("learn_reverse", True):
        from . import learned
        learned.record(ident.fmt, target)
    return result


def _run_path(src: Path, src_fmt: str, base: str, option: TargetOption, work: Path,
              ctx: Context) -> list[Path]:
    # Backends read the format from the extension, so stage the source under its real
    # one when the name disagrees (hard link: instant and no extra space).
    cur = src
    if split_name(src)[1] != src_fmt:
        cur = work / f"{base}.{file_ext(src_fmt)}"
        try:
            os.link(src, cur)
        except OSError:
            shutil.copy2(src, cur)

    n = len(option.path)
    produced: list[Path] = []
    for i, edge in enumerate(option.path):
        step = work / f"step{i}"
        step.mkdir()
        dst = step / base if edge.dst == "extract" else step / f"{base}.{file_ext(edge.dst)}"
        sub = Context(cfg=ctx.cfg, cancel_event=ctx.cancel_event, src_fmt=edge.src,
                      dst_fmt=edge.dst, progress=lambda f, i=i: ctx.progress((i + f) / n),
                      resource_dirs=list(dict.fromkeys([cur.parent, src.parent])),
                      variant=ctx.variant if i == n - 1 else "")
        produced = _run_step(edge, cur, dst, sub)
        if not produced:
            raise ConversionError("The converter produced no output.")
        ctx.check_cancel()
        cur = produced[0]
    return produced


def _run_step(edge, cur: Path, dst: Path, ctx: Context) -> list[Path]:
    """Run one step; if the engine can't handle this particular file, try the next one."""
    from .native.raster import Unsupported
    tried = []
    for alt in [edge] + [e for e in planner.alternatives(edge.src, edge.dst) if e.backend is not
                         edge.backend]:
        try:
            produced = [p for p in alt.backend.convert(cur, dst, ctx) if p.exists()]
            if tried:
                log.info("Fell back to %s after %s", alt.backend.name, "; ".join(tried))
            return produced
        except Unsupported as e:
            tried.append(f"{alt.backend.name}: {e}")
            for leftover in dst.parent.iterdir():
                (shutil.rmtree if leftover.is_dir() else Path.unlink)(leftover)
    raise ConversionError("No engine could handle this file (" + "; ".join(tried) + ")")


def _hide(path: Path) -> None:
    try:
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02)  # FILE_ATTRIBUTE_HIDDEN
    except Exception:
        pass
