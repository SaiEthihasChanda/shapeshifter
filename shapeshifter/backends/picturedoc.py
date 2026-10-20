"""Image -> Word with the picture itself on the page ("Word + picture").

Unlike the OCR route (which keeps only recognised text), this embeds the image so you can open
the .docx, type around or over it, and convert the document back to an image or PDF.
"""

from __future__ import annotations

from pathlib import Path

from ..core import Backend, Context, ConversionError


class PictureDocBackend(Backend):
    name = "picturedoc"
    base_cost = 2.0

    def sources(self):
        from .images import ImageBackend
        return set(ImageBackend().sources()) - {"svg", "eps", "psd", "dds", "icns"}

    def targets(self, src):
        return {"docxpic"}

    def terminal(self, src, dst):
        return True

    def convert(self, src: Path, dst: Path, ctx: Context) -> list[Path]:
        from ..native import docs
        from ..native.docx import blocks_to_docx

        ctx.progress(0.1)
        blocks = [docs.Block("img", src.stem, src=src.name), docs.Block("p", "")]
        data = blocks_to_docx(blocks, [src.parent], src.stem)
        if b"word/media/" not in data:
            raise ConversionError("Couldn't embed the picture in the document.")
        dst.write_bytes(data)
        ctx.progress(1.0)
        return [dst]
