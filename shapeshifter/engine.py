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
    if ident.details.get("sensitive"):
        # Private keys & keystores: only re-encodings of the same secret, never documents.
        opts = [o for o in opts if o.kind in ("rename", "inspect") or o.fmt in SENSITIVE_OK]
    if not src or (ident.confidence < 0.6 and not fmt):
        opts.append(TargetOption(key="inspect", kind="inspect"))
    return planner.ordered(opts, src)


SENSITIVE_OK = {"pem", "der"}


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
    return base if re.fullmatch(r"[a-z0-9]{1,5}", ext) else src.name


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
    target = target if target == "extract" else canonical(target)
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
                      resource_dirs=list(dict.fromkeys([cur.parent, src.parent])))
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
