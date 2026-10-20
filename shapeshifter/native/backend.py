"""The native engine: decode to a shared model, encode from it."""

from __future__ import annotations

from pathlib import Path

from ..core import Backend, Context, target_ext
from . import audio, docs, gif, ico, jpeg, pdfwrite, png, registry, simple
from .raster import Raster

IMAGE_DECODERS = {"png": png.decode, "bmp": simple.decode_bmp, "tga": simple.decode_tga,
                  "ppm": simple.decode_pnm, "qoi": simple.decode_qoi, "gif": gif.decode,
                  "ico": ico.decode, "jpg": jpeg.decode}
IMAGE_ENCODERS = {"png": png.encode, "bmp": simple.encode_bmp, "tga": simple.encode_tga,
                  "ppm": simple.encode_pnm, "qoi": simple.encode_qoi, "ico": ico.encode}
DOC_FORMATS = {"md", "html", "txt"}
AUDIO = {"wav": (audio.read_wav, audio.write_wav), "aiff": (audio.read_aiff, audio.write_aiff)}


class NativeBackend(Backend):
    name = "native"
    native = True
    base_cost = 1.0

    def __init__(self):
        registry.load_all()

    def _builtin_targets(self, src):
        if src in IMAGE_DECODERS:
            out = set(IMAGE_ENCODERS) | {"pdf"}
            if src == "gif":
                out -= {"png", "pdf"}   # animated: leave APNG / multi-page to Pillow
            return out - {src}
        if src in DOC_FORMATS:
            return (DOC_FORMATS | {"pdf"}) - {src}
        if src in AUDIO:
            return set(AUDIO) - {src}
        return set()

    def sources(self):
        return set(IMAGE_DECODERS) | DOC_FORMATS | set(AUDIO) | {s for s, _ in registry.CONVERTERS}

    def targets(self, src):
        return self._builtin_targets(src) | {t for s, t in registry.CONVERTERS if s == src}

    def multi_output(self, src, dst):
        conv = registry.CONVERTERS.get((src, dst))
        return bool(conv and conv.multi)

    def terminal(self, src, dst):
        conv = registry.CONVERTERS.get((src, dst))
        if conv and dst not in self._builtin_targets(src):
            return conv.terminal or conv.multi
        return dst == "pdf"

    def first_step_only(self, src, dst):
        conv = registry.CONVERTERS.get((src, dst))
        return bool(conv and conv.first_only and dst not in self._builtin_targets(src))

    def cost(self, src, dst):
        conv = registry.CONVERTERS.get((src, dst))
        return conv.cost if conv and dst not in self._builtin_targets(src) else self.base_cost

    def convert(self, src: Path, dst: Path, ctx: Context) -> list[Path]:
        s = ctx.src_fmt or target_ext(src)
        t = ctx.dst_fmt or target_ext(dst)
        data = src.read_bytes()
        ctx.progress(0.1)
        conv = registry.CONVERTERS.get((s, t))
        if conv and t not in self._builtin_targets(s):
            result = conv.fn(data, s, t, ctx)
            if isinstance(result, list):            # several files
                outs = []
                for suffix, blob in result:
                    p = dst.with_name(f"{dst.stem}{suffix}{dst.suffix}")
                    p.write_bytes(blob)
                    outs.append(p)
                ctx.progress(1.0)
                return outs
            dst.write_bytes(result)
            ctx.progress(1.0)
            return [dst]
        if s in AUDIO:
            dst.write_bytes(AUDIO[t][1](AUDIO[s][0](data)))
        elif s in DOC_FORMATS:
            dst.write_bytes(self._document(data, s, t, src.stem, ctx))
        elif s == "jpg" and t == "pdf":
            # Embed the JPEG as-is: lossless and instant.
            dst.write_bytes(pdfwrite.images_to_pdf(jpeg=data, jpeg_size=jpeg.jpeg_info(data)))
        else:
            raster: Raster = IMAGE_DECODERS[s](data)
            ctx.check_cancel()
            ctx.progress(0.6)
            if t == "pdf":
                dst.write_bytes(pdfwrite.images_to_pdf(raster))
            else:
                dst.write_bytes(IMAGE_ENCODERS[t](raster))
        ctx.progress(1.0)
        return [dst]

    @staticmethod
    def _document(data: bytes, s: str, t: str, title: str, ctx=None) -> bytes:
        text = data.decode("utf-8-sig", errors="replace")
        if s == "md":
            blocks = docs.parse_markdown(text)
        elif s == "html":
            blocks = docs.parse_html(text)
            title = docs.html_title(text) or title
        else:
            blocks = docs.parse_text(text)
        if t == "html":
            return docs.to_html(blocks, title).encode("utf-8")
        if t == "md":
            return docs.to_markdown(blocks).encode("utf-8")
        if t == "txt":
            return docs.to_text(blocks).encode("utf-8")
        return pdfwrite.document_to_pdf(docs.pdf_blocks(blocks), title,
                                        list(getattr(ctx, "resource_dirs", [])))
