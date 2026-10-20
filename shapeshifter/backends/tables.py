"""Tables between documents and data: PDF / Word / OpenDocument / web pages / Markdown / pictures
-> CSV, TSV, Excel, ODS, JSON, and spreadsheets or CSV -> HTML, Markdown, Word, PDF (plus ODS both ways).

Longer routes (a picture of a table -> Excel -> Word, SQLite -> Excel -> PDF) come from chaining
these steps with the others in the conversion graph.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..core import Backend, Context, ConversionError, target_ext

log = logging.getLogger(__name__)

DOC_SOURCES = {"pdf", "docx", "odt", "html", "md"}
DATA_SOURCES = {"csv", "tsv", "xlsx", "ods"}
DATA_TARGETS = {"csv", "tsv", "xlsx", "json", "ods"}
DOC_TARGETS = {"html", "md", "docx", "pdf"}
SINGLE_FILE_DATA = {"xlsx", "ods", "json"}          # hold every table; CSV/TSV need one file each
PICTURE_NOTE = "Tables read from a picture are approximate."


def _image_sources() -> set[str]:
    from .images import ImageBackend
    return set(ImageBackend().sources()) - {"svg", "eps", "psd", "dds", "icns", "ico", "cur"}


def _ocr_ok() -> bool:
    from .. import ocr
    return ocr.available()


class TableBackend(Backend):
    name = "tables"
    base_cost = 3.0

    def available(self):
        from importlib.util import find_spec
        return find_spec("pymupdf") is not None

    def sources(self):
        return DOC_SOURCES | DATA_SOURCES | (_image_sources() if _ocr_ok() else set())

    def targets(self, src):
        if src in DOC_SOURCES:
            return DATA_TARGETS - {src}
        if src in DATA_SOURCES:
            out = set(DOC_TARGETS)
            out |= {"ods"} if src != "ods" else {"xlsx", "csv", "tsv", "json"}
            return out - {src}
        if src in _image_sources() and _ocr_ok():
            return set(DATA_TARGETS)
        return set()

    def cost(self, src, dst):
        if src in DATA_SOURCES and dst in DATA_TARGETS:
            return 1.5                              # plain spreadsheet re-saves (ODS)
        return 4.0 if src not in DOC_SOURCES | DATA_SOURCES else 3.0

    def multi_output(self, src, dst):
        return src not in DATA_SOURCES and dst in ("csv", "tsv")      # one file per table

    def first_step_only(self, src, dst):
        # Finding tables in a document or picture only makes sense on the user's own file.
        return src not in DATA_SOURCES

    # ------------------------------------------------------------------ work

    def convert(self, src: Path, dst: Path, ctx: Context) -> list[Path]:
        s = ctx.src_fmt or target_ext(src)
        t = ctx.dst_fmt or target_ext(dst)
        tables = self._read(src, s, ctx)
        if not tables:
            raise ConversionError("No tables were found in this file.")
        ctx.progress(0.85)
        out = self._write(tables, dst, t, src.stem)
        ctx.progress(1.0)
        return out

    def _read(self, src: Path, s: str, ctx: Context):
        from ..native import tables as T
        if s == "pdf":
            return T.from_pdf(src, ctx.cfg, ctx.cancel_event, lambda f: ctx.progress(f * 0.8))
        if s == "docx":
            return T.from_docx(src)
        if s == "odt":
            return T.from_odt(src)
        if s == "ods":
            return T.from_ods(src)
        if s in ("html", "md"):
            text = src.read_bytes().decode("utf-8-sig", errors="replace")
            return T.from_html(text) if s == "html" else T.from_markdown(text)
        if s in DATA_SOURCES:                    # csv / tsv / xlsx
            from importlib.util import find_spec

            from . import data
            data._native_xlsx = bool(ctx.cfg.get("prefer_native")) or find_spec("openpyxl") is None
            sheets = data._read_table(src, s)
            return [T.Table(name, rows) for name, rows in sheets.items() if rows]
        # a picture
        from .. import forms
        rows = T.rows_from_picture(forms.load_image(src), ctx.cfg, ctx.cancel_event)
        return [T.Table("Table 1", rows)] if rows else []

    def _write(self, tables, dst: Path, t: str, stem: str) -> list[Path]:
        from ..native import tables as T
        from . import data
        if t in ("csv", "tsv"):
            if len(tables) == 1:
                data._write_table(dst, t, {"t": tables[0].rows})
                return [dst]
            outs = []
            for name, table in zip(T.safe_sheet_names(tables), tables):
                out = dst.with_name(f"{stem} - {name}{dst.suffix}")
                data._write_table(out, t, {name: table.rows})
                outs.append(out)
            return outs
        if t == "xlsx":
            names = T.safe_sheet_names(tables)
            from importlib.util import find_spec
            data._native_xlsx = find_spec("openpyxl") is None
            data._write_table(dst, "xlsx", {n: [[T.coerce(c) for c in r] for r in T.squared(tb.rows)]
                                            for n, tb in zip(names, tables)})
            return [dst]
        if t == "ods":
            T.write_ods(dst, tables)
            return [dst]
        if t == "json":
            records = {n: T.to_records(tb.rows) for n, tb in zip(T.safe_sheet_names(tables), tables)}
            data._write_tree(dst, "json", next(iter(records.values())) if len(records) == 1 else records)
            return [dst]
        title = tables[0].name if len(tables) == 1 else stem
        if t in ("html", "md"):
            dst.write_bytes(T.bytes_of(tables, t))
        elif t == "docx":
            from ..native.docx import blocks_to_docx
            dst.write_bytes(blocks_to_docx(T.to_blocks(tables), [], title))
        elif t == "pdf":
            from ..native import docs
            from .mupdf import html_to_pdf
            html_to_pdf(docs.to_html(T.to_blocks(tables), title, css=False), dst, [dst.parent], title)
        else:
            raise ConversionError(f"Can't write tables as {t}")
        return [dst]
