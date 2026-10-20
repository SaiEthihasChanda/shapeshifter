"""Tables as a common currency: find them in PDFs, Word/OpenDocument files, web pages, Markdown,
spreadsheets and even pictures; write them back out as data files or as documents.

Everything here works on ``Table(name, rows)`` where rows is a list of lists of cell text/values.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape


@dataclass
class Table:
    name: str
    rows: list[list] = field(default_factory=list)

    @property
    def width(self) -> int:
        return max((len(r) for r in self.rows), default=0)


def squared(rows: list[list], blank="") -> list[list]:
    """Pad ragged rows so every row has the same number of cells."""
    w = max((len(r) for r in rows), default=0)
    return [list(r) + [blank] * (w - len(r)) for r in rows]


def _clean(cell) -> str:
    if cell is None:
        return ""
    return re.sub(r"\s*\n\s*", " ", str(cell)).strip()


def _trim(rows: list[list[str]]) -> list[list[str]]:
    """Drop empty rows and columns on the edges of a table."""
    rows = squared([[_clean(c) for c in r] for r in rows])
    rows = [r for r in rows if any(r)]
    if not rows:
        return []
    keep = [i for i in range(len(rows[0])) if any(r[i] for r in rows)]
    return [[r[i] for i in keep] for r in rows]


# ------------------------------------------------------------------- readers

def from_pdf(path, cfg: dict | None = None, cancel=None, progress=None) -> list[Table]:
    """Tables of a PDF (ruled tables first, then column-aligned text). A table whose header is
    repeated on the next page is joined into one. Pages with no text layer are read by OCR."""
    import pymupdf
    from ..core import ConversionError
    try:
        doc = pymupdf.open(str(path))
    except Exception as e:                                   # noqa: BLE001
        raise ConversionError(f"Can't open the PDF: {e}") from e
    found: list[tuple[int, list[list[str]]]] = []
    scanned: list[int] = []
    with doc:
        if doc.needs_pass:
            raise ConversionError("The PDF is password protected.")
        n = doc.page_count
        for i, page in enumerate(doc):
            if cancel is not None and cancel.is_set():
                from ..core import Cancelled
                raise Cancelled()
            tabs = _page_tables(page)
            if not tabs and not page.get_text("text").strip() and page.get_images():
                scanned.append(i)
            found += [(i, t) for t in tabs]
            if progress:
                progress((i + 1) / n * (0.5 if scanned else 1.0))
        ocr_tables: list[tuple[int, list[list[str]]]] = []
        if scanned and not found:
            for k, i in enumerate(scanned):
                img = _render(doc[i])
                rows = rows_from_picture(img, cfg, cancel)
                if rows:
                    ocr_tables.append((i, rows))
                if progress:
                    progress(0.5 + (k + 1) / len(scanned) * 0.5)
        found += ocr_tables
    return _name_and_join(found)


def _render(page, dpi: int = 200):
    from PIL import Image
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _page_tables(page) -> list[list[list[str]]]:
    out: list[list[list[str]]] = []
    for strategy in ("lines", "text"):
        try:
            finder = page.find_tables(strategy=strategy)
        except Exception:                                    # noqa: BLE001 - odd page: no tables
            continue
        for t in finder.tables:
            rows = _trim(_cell_text(page, t))
            if len(rows) >= 2 and len(rows[0]) >= 2:
                if strategy == "text" and (len(rows) < 3 or len(rows[0]) < 3):
                    continue                                 # aligned text is only a table if it's clear
                out.append(rows)
        if out:
            break
    return out


def _cell_text(page, table) -> list[list[str]]:
    """The text of each cell, read from the cell's own box. (PyMuPDF's Table.extract() splits
    punctuation off words, turning "14-07-2025" into "14 07 2025 - -".)"""
    import pymupdf
    try:
        rows = []
        for row in table.rows:
            rows.append([page.get_textbox(pymupdf.Rect(c)) if c else "" for c in row.cells])
        return rows
    except Exception:                                        # noqa: BLE001 - fall back to PyMuPDF's own
        return table.extract()


def _name_and_join(found: list[tuple[int, list[list[str]]]]) -> list[Table]:
    spans: list[list[int]] = []                          # [first page, last page] per table
    tables: list[Table] = []
    for page, rows in found:
        prev = tables[-1] if tables else None
        if (prev is not None and page == spans[-1][1] + 1 and len(rows[0]) == prev.width
                and [str(c).lower() for c in rows[0]] == [str(c).lower() for c in prev.rows[0]]):
            prev.rows += rows[1:]                        # same header again: a continuation
            spans[-1][1] = page
        else:
            tables.append(Table("", rows))
            spans.append([page, page])
    seen: dict[str, int] = {}
    for t, (first, last) in zip(tables, spans):
        base = f"Page {first + 1}" if first == last else f"Pages {first + 1}-{last + 1}"
        seen[base] = seen.get(base, 0) + 1
        t.name = base if seen[base] == 1 else f"{base} table {seen[base]}"
    return tables


def _tc_text(tc) -> str:
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paras = []
    for p in tc.iter(ns + "p"):
        paras.append("".join(t.text or "" for t in p.iter(ns + "t")))
    return " ".join(x for x in paras if x).strip()


def from_docx(path) -> list[Table]:
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    body = root.find(ns + "body")
    out: list[Table] = []
    heading = ""
    for el in list(body) if body is not None else []:
        if el.tag == ns + "p":
            style = el.find(f"{ns}pPr/{ns}pStyle")
            if style is not None and str(style.get(ns + "val", "")).lower().startswith(("heading", "title")):
                heading = "".join(t.text or "" for t in el.iter(ns + "t")).strip() or heading
        elif el.tag == ns + "tbl":
            rows = []
            for tr in el.findall(ns + "tr"):
                cells: list[str] = []
                for tc in tr.findall(ns + "tc"):
                    cells.append(_tc_text(tc))
                    span = tc.find(f"{ns}tcPr/{ns}gridSpan")
                    if span is not None:
                        cells += [""] * (int(span.get(ns + "val", "1")) - 1)
                rows.append(cells)
            rows = _trim(rows)
            if rows:
                out.append(Table(heading or f"Table {len(out) + 1}", rows))
                heading = ""
    return out


ODF_NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
}


def _odf(tag: str) -> str:
    p, n = tag.split(":")
    return "{%s}%s" % (ODF_NS[p], n)


def _odf_cell_text(cell) -> str:
    parts = ["".join(p.itertext()) for p in cell.iter(_odf("text:p"))]
    return " ".join(x for x in parts if x)


def _odf_tables(path, spreadsheet: bool) -> list[Table]:
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("content.xml"))
    out: list[Table] = []
    for tbl in root.iter(_odf("table:table")):
        rows: list[list] = []
        for tr in tbl.iter(_odf("table:table-row")):
            cells: list = []
            for tc in tr:
                if tc.tag not in (_odf("table:table-cell"), _odf("table:covered-table-cell")):
                    continue
                repeat = min(int(tc.get(_odf("table:number-columns-repeated"), "1")), 200)
                vt = tc.get(_odf("office:value-type"))
                if spreadsheet and vt in ("float", "percentage", "currency"):
                    raw = tc.get(_odf("office:value"), "")
                    try:
                        value = float(raw)
                        value = int(value) if value.is_integer() else value
                    except ValueError:
                        value = _odf_cell_text(tc)
                else:
                    value = _odf_cell_text(tc)
                cells += [value] * repeat
            rrep = min(int(tr.get(_odf("table:number-rows-repeated"), "1")), 200)
            rows += [list(cells) for _ in range(rrep)] if any(c != "" for c in cells) else [list(cells)]
        while rows and not any(c != "" for c in rows[-1]):
            rows.pop()
        name = tbl.get(_odf("table:name"), f"Table {len(out) + 1}")
        if spreadsheet:
            out.append(Table(name, squared(rows)))
        else:
            rows = _trim(rows)
            if rows:
                out.append(Table(name, rows))
    return out


def from_odt(path) -> list[Table]:
    return _odf_tables(path, spreadsheet=False)


def from_ods(path) -> list[Table]:
    return _odf_tables(path, spreadsheet=True)


def from_blocks(blocks) -> list[Table]:
    out: list[Table] = []
    heading = ""
    for b in blocks:
        if b.kind.startswith("h") and b.kind[1:].isdigit():
            heading = b.text.strip() or heading
        elif b.kind == "table" and b.rows:
            rows = _trim(b.rows)
            if rows:
                out.append(Table(heading or f"Table {len(out) + 1}", rows))
                heading = ""
    return out


def from_markdown(text: str) -> list[Table]:
    from . import docs
    return from_blocks(docs.parse_markdown(text))


def from_html(text: str) -> list[Table]:
    from . import docs
    return from_blocks(docs.parse_html(text))


def rows_from_picture(image, cfg: dict | None = None, cancel=None) -> list[list[str]]:
    """A table read off a picture: OCR words are grouped into rows, then into columns wherever
    a vertical gutter of white space runs through the whole table."""
    import tempfile

    from .. import ocr
    with tempfile.TemporaryDirectory(prefix="ss-tab-") as tmp:
        path = Path(tmp) / "t.png"
        image.save(path)
        page, _ = ocr.recognize_file(path, cfg, cancel)
    return rows_from_words([(w.text, w.x, w.y, w.w, w.h) for ln in page.lines for w in ln.words])


def rows_from_words(words: list[tuple[str, float, float, float, float]]) -> list[list[str]]:
    """Group positioned words (text, x, y, w, h) into a grid of cell text."""
    words = [w for w in words if w[0].strip()]
    if len(words) < 4:
        return []
    heights = sorted(w[4] for w in words)
    med_h = heights[len(heights) // 2] or 10.0
    # --- rows: words whose vertical centres are close
    words.sort(key=lambda w: w[2] + w[4] / 2)
    rows: list[list] = []
    for w in words:
        cy = w[2] + w[4] / 2
        if rows and abs(cy - rows[-1][-1][2] - rows[-1][-1][4] / 2) <= 0.6 * med_h:
            rows[-1].append(w)
        else:
            rows.append([w])
    # --- cells within a row: split where the gap between words is wide
    gap = 1.1 * med_h
    cells_by_row: list[list[tuple[float, float, str]]] = []
    for r in rows:
        r.sort(key=lambda w: w[1])
        cells: list[list] = [[r[0]]]
        for w in r[1:]:
            prev = cells[-1][-1]
            if w[1] - (prev[1] + prev[3]) > gap:
                cells.append([w])
            else:
                cells[-1].append(w)
        cells_by_row.append([(c[0][1], c[-1][1] + c[-1][3], " ".join(x[0] for x in c)) for c in cells])
    # --- columns: stretches of x that some cell covers; the gutters between them separate columns
    x_min = min(c[0] for r in cells_by_row for c in r)
    x_max = max(c[1] for r in cells_by_row for c in r)
    span = max(1, int(x_max - x_min) + 1)
    cover = [0] * span
    for r in cells_by_row:
        for x0, x1, _ in r:
            for x in range(int(x0 - x_min), min(span, int(x1 - x_min) + 1)):
                cover[x] += 1
    columns: list[tuple[float, float]] = []
    start = None
    for x, c in enumerate(cover + [0]):
        if c and start is None:
            start = x
        elif not c and start is not None:
            columns.append((start + x_min, x + x_min))
            start = None
    # gutters narrower than a character are just letter spacing: merge columns across them
    merged: list[list[float]] = []
    for a, b in columns:
        if merged and a - merged[-1][1] < 0.5 * med_h:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    if len(merged) < 2 or len(cells_by_row) < 2:
        return []
    grid: list[list[str]] = []
    for r in cells_by_row:
        row = [""] * len(merged)
        for x0, x1, text in r:
            mid = (x0 + x1) / 2
            idx = min(range(len(merged)), key=lambda i: 0 if merged[i][0] <= mid <= merged[i][1]
                      else min(abs(mid - merged[i][0]), abs(mid - merged[i][1])))
            row[idx] = (row[idx] + " " + text).strip()
        grid.append(row)
    grid = _trim(grid)
    # A real table has most rows with several filled cells.
    filled = sum(1 for r in grid if sum(1 for c in r if c) >= 2)
    return grid if len(grid) >= 2 and filled >= 0.6 * len(grid) else []


# ------------------------------------------------------------------- writers

def coerce(cell):
    """'12' -> 12 and '3.5' -> 3.5; leave anything that could lose information (leading zeros,
    thousands separators, phone numbers) as text."""
    if not isinstance(cell, str):
        return cell
    s = cell.strip()
    if re.fullmatch(r"-?(0|[1-9]\d{0,14})", s):
        return int(s)
    if re.fullmatch(r"-?(0|[1-9]\d{0,14})\.\d{1,12}", s):
        return float(s)
    return cell


def safe_sheet_names(tables: list[Table]) -> list[str]:
    names, seen = [], set()
    for i, t in enumerate(tables):
        base = re.sub(r"[\[\]:*?/\\]", " ", t.name).strip()[:31] or f"Sheet{i + 1}"
        name, n = base, 2
        while name.lower() in seen:
            suffix = f" ({n})"
            name, n = base[:31 - len(suffix)] + suffix, n + 1
        seen.add(name.lower())
        names.append(name)
    return names


def write_ods(path, tables: list[Table]) -> None:
    """A minimal, valid OpenDocument spreadsheet."""
    names = safe_sheet_names(tables)
    out = []
    for name, t in zip(names, tables):
        out.append(f'<table:table table:name="{escape(name, {chr(34): "&quot;"})}">')
        width = max(1, t.width)
        out.append(f'<table:table-column table:number-columns-repeated="{width}"/>')
        for row in squared(t.rows):
            out.append("<table:table-row>")
            for v in row:
                v = coerce(v)
                if isinstance(v, bool):
                    out.append(f'<table:table-cell office:value-type="boolean" office:boolean-value="{str(v).lower()}"/>')
                elif isinstance(v, (int, float)):
                    out.append(f'<table:table-cell office:value-type="float" office:value="{v}"><text:p>{v}</text:p></table:table-cell>')
                elif v in ("", None):
                    out.append("<table:table-cell/>")
                else:
                    out.append(f'<table:table-cell office:value-type="string"><text:p>{escape(str(v))}</text:p></table:table-cell>')
            out.append("</table:table-row>")
        out.append("</table:table>")
    content = ('<?xml version="1.0" encoding="UTF-8"?>'
               '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
               'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
               'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" office:version="1.2">'
               "<office:body><office:spreadsheet>" + "".join(out) + "</office:spreadsheet></office:body>"
               "</office:document-content>")
    manifest = ('<?xml version="1.0" encoding="UTF-8"?>'
                '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">'
                '<manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.spreadsheet"/>'
                '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
                "</manifest:manifest>")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.spreadsheet")
        z.writestr("content.xml", content, zipfile.ZIP_DEFLATED)
        z.writestr("META-INF/manifest.xml", manifest, zipfile.ZIP_DEFLATED)


def to_blocks(tables: list[Table], title: str = ""):
    """Tables as document blocks: a heading per table when there is more than one."""
    from . import docs
    blocks = []
    for t in tables:
        if len(tables) > 1 or (t.name and not t.name.startswith("Table ")):
            blocks.append(docs.Block("h2", t.name))
        blocks.append(docs.Block("table", rows=[[("" if c is None else str(c)) for c in r] for r in squared(t.rows)]))
    return blocks


def sniff_header(rows: list[list]) -> bool:
    return bool(rows) and all(isinstance(c, str) and c for c in rows[0])


def to_records(rows: list[list]) -> list[dict]:
    if not rows:
        return []
    header = [str(c) if c not in ("", None) else f"column{i + 1}" for i, c in enumerate(rows[0])]
    seen: dict[str, int] = {}
    keys = []
    for h in header:
        seen[h] = seen.get(h, 0) + 1
        keys.append(h if seen[h] == 1 else f"{h}_{seen[h]}")
    return [{k: coerce(v) for k, v in zip(keys, squared(rows)[i])} for i in range(1, len(rows))]


def bytes_of(tables: list[Table], fmt: str) -> bytes:
    """Rendered document bytes for html / md."""
    from . import docs
    blocks = to_blocks(tables)
    if fmt == "html":
        return docs.to_html(blocks, tables[0].name if len(tables) == 1 else "Tables").encode("utf-8")
    if fmt == "md":
        return docs.to_markdown(blocks).encode("utf-8")
    raise ValueError(fmt)


__all__ = ["Table", "from_pdf", "from_docx", "from_odt", "from_ods", "from_markdown", "from_html",
           "rows_from_picture", "rows_from_words", "write_ods", "to_blocks", "to_records", "coerce",
           "safe_sheet_names", "squared", "bytes_of", "io"]
