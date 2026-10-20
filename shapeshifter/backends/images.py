"""Raster image conversions with Pillow (+ pillow-heif for HEIC)."""

from __future__ import annotations

from pathlib import Path

from .base import Backend, Context, ConversionError, RiskyFile, target_ext

# Refuse to decode images beyond this (decompression-bomb protection). Pillow raises
# DecompressionBombError at 2x this value and only warns between 1x and 2x.
MAX_PIXELS = 250_000_000 // 2

READ = {"jpg", "png", "webp", "avif", "heic", "gif", "bmp", "tiff", "ico", "tga", "qoi",
        "jp2", "pcx", "ppm", "psd", "dds", "icns",
        # configured kinds Pillow can also open
        "apng", "cur", "dib", "xbm", "xpm", "sgi", "fits", "wmf", "emf", "dng", "avifs", "heics"}
WRITE = {"jpg", "png", "webp", "avif", "heic", "gif", "bmp", "tiff", "ico", "tga", "qoi", "pdf"}

PIL_FORMAT = {
    "jpg": "JPEG", "png": "PNG", "webp": "WEBP", "avif": "AVIF", "heic": "HEIF", "gif": "GIF",
    "bmp": "BMP", "tiff": "TIFF", "ico": "ICO", "tga": "TGA", "qoi": "QOI", "pdf": "PDF",
}
NO_ALPHA = {"jpg", "bmp", "pdf"}
KEEPS_EXIF = {"jpg", "png", "webp", "avif", "heic", "tiff"}
ANIMATED = {"gif", "webp", "avif", "png"}  # png = APNG
MULTIPAGE = {"tiff", "pdf"}


class ImageBackend(Backend):
    name = "images"
    base_cost = 1.0

    def terminal(self, src, dst):
        return dst == "pdf"  # an image wrapped in a PDF has nothing more to offer

    def __init__(self):
        self._read: set[str] | None = None
        self._write: set[str] | None = None

    def _init(self):
        # Capability check without importing Pillow's plugins (keeps the wheel fast to open).
        if self._read is not None:
            return
        from importlib.util import find_spec
        self._read, self._write = set(READ), set(WRITE)
        if find_spec("pillow_heif") is None:
            self._read.discard("heic")
            self._write.discard("heic")
        if find_spec("PIL._avif") is None:
            self._read.discard("avif")
            self._write.discard("avif")

    def _load_plugins(self):
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except Exception:
            pass

    def sources(self):
        self._init()
        return self._read

    def targets(self, src):
        self._init()
        return self._write - {src}

    def convert(self, src: Path, dst: Path, ctx: Context) -> list[Path]:
        from PIL import Image, ImageOps

        self._load_plugins()
        tgt = target_ext(dst)
        cfg = ctx.cfg
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS
        try:
            im = Image.open(src)
        except Image.DecompressionBombError as e:
            raise RiskyFile("The image is too large to open safely "
                            f"(over {MAX_PIXELS // 1_000_000} megapixels).") from e
        except Exception as e:
            raise ConversionError(f"Can't open image: {e}") from e

        with im:
            n_frames = getattr(im, "n_frames", 1)
            info = dict(im.info)
            kwargs: dict = {}

            if tgt == "jpg":
                kwargs.update(quality=cfg["jpeg_quality"], optimize=True)
            elif tgt == "webp":
                kwargs.update(quality=cfg["webp_quality"], method=4)
            elif tgt in ("avif", "heic"):
                kwargs.update(quality=cfg["avif_quality"])
            elif tgt == "tiff":
                kwargs.update(compression="tiff_lzw")
            elif tgt == "pdf":
                kwargs.update(resolution=float(info.get("dpi", (96, 96))[0] or 96))

            if tgt in KEEPS_EXIF:
                if info.get("exif"):
                    kwargs["exif"] = info["exif"]
                if info.get("icc_profile") and tgt != "heic":
                    kwargs["icc_profile"] = info["icc_profile"]

            variant = ctx.variant
            multi = n_frames > 1 and (tgt in ANIMATED or tgt in MULTIPAGE) and variant not in ("first", "frames", "sheet")

            def save_still(frame, out: Path):
                if tgt not in KEEPS_EXIF:
                    frame = ImageOps.exif_transpose(frame)
                frame = _prepare(frame, tgt)
                kw = dict(kwargs)
                if tgt == "ico":
                    frame = _square(frame)
                    side = frame.size[0]
                    kw["sizes"] = [(s, s) for s in (16, 24, 32, 48, 64, 128, 256) if s <= side] or [(side, side)]
                frame.save(out, PIL_FORMAT[tgt], **kw)

            if variant in ("frames", "sheet") and n_frames > 1:
                loaded = []
                for i in range(n_frames):
                    ctx.check_cancel()
                    im.seek(i)
                    loaded.append(im.copy().convert("RGBA"))
                    ctx.progress((i + 1) / n_frames * 0.7)
                if variant == "frames":
                    width = len(str(n_frames))
                    outs = []
                    for i, fr in enumerate(loaded):
                        out = dst.with_name(f"{dst.stem}-{i + 1:0{width}d}{dst.suffix}")
                        save_still(fr if tgt not in NO_ALPHA else fr, out)
                        outs.append(out)
                    ctx.progress(1.0)
                    return outs
                save_still(_sprite_sheet(loaded), dst)
                ctx.progress(1.0)
                return [dst]
            if multi:
                frames = []
                durations = []
                for i in range(n_frames):
                    ctx.check_cancel()
                    im.seek(i)
                    frames.append(_prepare(im.copy(), tgt))
                    durations.append(im.info.get("duration", info.get("duration", 100)))
                    ctx.progress((i + 1) / n_frames * 0.8)
                if tgt in ANIMATED:
                    kwargs.update(duration=durations, loop=info.get("loop", 0))
                    if tgt == "gif":
                        kwargs["disposal"] = 2
                first, rest = frames[0], frames[1:]
                first.save(dst, PIL_FORMAT[tgt], save_all=True, append_images=rest, **kwargs)
            else:
                im.seek(0)
                save_still(im.copy(), dst)
        ctx.progress(1.0)
        return [dst]


def _sprite_sheet(frames):
    """Every frame in a grid, as close to square as the frame count allows (at most 4096 px wide)."""
    import math

    from PIL import Image
    n = len(frames)
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    w, h = frames[0].size
    scale = min(1.0, 4096 / (cols * w))
    cw, ch = max(1, round(w * scale)), max(1, round(h * scale))
    sheet = Image.new("RGBA", (cols * cw, rows * ch), (255, 255, 255, 0))
    for i, fr in enumerate(frames):
        if fr.size != (cw, ch):
            fr = fr.resize((cw, ch), Image.LANCZOS)
        sheet.paste(fr, ((i % cols) * cw, (i // cols) * ch))
    return sheet


def _prepare(im, tgt: str):
    """Convert an image to a mode the target format can store."""
    from PIL import Image

    if im.mode in ("I;16", "I;16B", "I;16L", "I", "F"):
        # Scale high bit-depth greyscale down to 8 bits.
        im = im.convert("I").point(lambda v: v / 256).convert("L") if im.mode.startswith("I;16") \
            else im.convert("L")
    if im.mode == "P":
        im = im.convert("RGBA" if "transparency" in im.info else "RGB")
    if im.mode in ("LA", "PA"):
        im = im.convert("RGBA")

    has_alpha = im.mode == "RGBA"
    if tgt in NO_ALPHA:
        if has_alpha:
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.getchannel("A"))
            im = bg
        if tgt == "jpg" and im.mode not in ("RGB", "L", "CMYK"):
            im = im.convert("RGB")
        elif tgt != "jpg" and im.mode not in ("RGB", "L", "1"):
            im = im.convert("RGB")
    elif tgt in ("png", "webp", "avif", "heic", "tga", "qoi", "ico", "tiff", "gif"):
        if im.mode == "CMYK":
            im = im.convert("RGB")
        if tgt in ("webp", "avif", "heic", "qoi", "ico") and im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA" if has_alpha else "RGB")
    return im


def _square(im):
    """Pad an image onto a transparent square canvas (icons must be square)."""
    from PIL import Image

    im = im.convert("RGBA")
    w, h = im.size
    side = max(w, h)
    if w == h:
        canvas = im
    else:
        canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        canvas.paste(im, ((side - w) // 2, (side - h) // 2))
    if side > 256:
        canvas = canvas.resize((256, 256), Image.LANCZOS)
    return canvas
