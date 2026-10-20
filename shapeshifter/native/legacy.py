"""Readers for the old binary Office files: Word 97-2003 (.doc), Excel 97-2003 (.xls) and PowerPoint
97-2003 (.ppt). They recover the text, tables and cell values (not the formatting or pictures).
Password-protected files are refused. Writing these formats needs LibreOffice."""

from __future__ import annotations

import re
import struct
from datetime import datetime, timedelta

from . import docs
from .ole import OleError, OleFile


class LegacyError(ValueError):
    pass


# ====================================================================== Word

def doc_text(data: bytes) -> str:
    """The main text of a .doc file as one string (paragraphs end with \\r, table cells with \\x07)."""
    try:
        ole = OleFile(data)
        word = ole.read("WordDocument")
    except (OleError, KeyError) as e:
        raise LegacyError(f"Not a Word 97-2003 document ({e})") from e
    if struct.unpack_from("<H", word, 0)[0] != 0xA5EC:
        raise LegacyError("Not a Word 97-2003 document (older Word formats aren't supported)")
    flags = struct.unpack_from("<H", word, 0x0A)[0]
    if flags & 0x0100:
        raise LegacyError("The document is password protected.")
    table = ole.read("1Table" if flags & 0x0200 else "0Table")
    csw = struct.unpack_from("<H", word, 32)[0]
    cslw_at = 34 + csw * 2
    cslw = struct.unpack_from("<H", word, cslw_at)[0]
    lw = cslw_at + 2                                         # FibRgLw97
    ccp_text = struct.unpack_from("<I", word, lw + 12)[0]
    pairs = lw + cslw * 4 + 2
    fc_clx, lcb_clx = struct.unpack_from("<II", word, pairs + 33 * 8)
    clx = table[fc_clx:fc_clx + lcb_clx]
    pos = 0
    while pos < len(clx) and clx[pos] == 1:                  # skip formatting property sets
        pos += 3 + struct.unpack_from("<H", clx, pos + 1)[0]
    if pos >= len(clx) or clx[pos] != 2:
        raise LegacyError("Couldn't find the document's text pieces.")
    plc_len = struct.unpack_from("<I", clx, pos + 1)[0]
    plc = clx[pos + 5:pos + 5 + plc_len]
    n = (plc_len - 4) // 12
    cps = struct.unpack_from(f"<{n + 1}I", plc, 0)
    out = []
    for i in range(n):
        fc = struct.unpack_from("<I", plc, (n + 1) * 4 + i * 8 + 2)[0]
        count = cps[i + 1] - cps[i]
        if fc & 0x40000000:
            start = (fc & 0x3FFFFFFF) // 2
            out.append(word[start:start + count].decode("cp1252", errors="replace"))
        else:
            out.append(word[fc:fc + count * 2].decode("utf-16-le", errors="replace"))
    text = "".join(out)
    return text[:ccp_text] if ccp_text else text


def doc_blocks(data: bytes) -> list[docs.Block]:
    raw = doc_text(data)
    # fields: keep what the reader sees (between 0x14 and 0x15), drop the code (between 0x13 and 0x14)
    res, depth_code = [], 0
    for ch in raw:
        if ch == "\x13":
            depth_code += 1
        elif ch == "\x14":
            depth_code = max(0, depth_code - 1)
        elif ch == "\x15":
            pass
        elif depth_code == 0:
            res.append(ch)
    text = "".join(res).replace("\x0b", "\n").replace("\x0c", "\n").replace("\xa0", " ")
    text = re.sub(r"[\x00-\x06\x08\x0e-\x1f]", "", text)
    blocks: list[docs.Block] = []
    row: list[str] = []
    table: list[list[str]] = []
    para = ""

    def flush_table():
        nonlocal table, row
        if row:
            table.append(row)
            row = []
        if table:
            w = max(len(r) for r in table)
            blocks.append(docs.Block("table", rows=[[docs.escape_markdown(c) for c in r] + [""] * (w - len(r)) for r in table]))
            table = []

    for ch in text:
        if ch == "\x07":                                     # ends a table cell; an empty one ends the row
            if not para.strip() and row:
                table.append(row)
                row = []
            elif para.strip() or not row:
                row.append(para.strip())
            para = ""
        elif ch == "\r":
            if row or table:
                flush_table()
            if para.strip():
                blocks.append(docs.Block("p", docs.escape_markdown(para.strip())))
            para = ""
        else:
            para += ch
    flush_table()
    if para.strip():
        blocks.append(docs.Block("p", docs.escape_markdown(para.strip())))
    return _tidy(blocks)


def _tidy(blocks: list[docs.Block]) -> list[docs.Block]:
    # A first short line without a full stop is most likely the title.
    out = list(blocks)
    for i, b in enumerate(out[:3]):
        if b.kind == "p" and len(b.text) < 80 and not b.text.rstrip().endswith((".", ",", ";", ":")) and i == 0:
            out[i] = docs.Block("h1", b.text)
            break
    return out


# ===================================================================== Excel

DATE_FORMATS = set(range(14, 23)) | set(range(27, 37)) | set(range(45, 48)) | set(range(50, 59))
EPOCH = datetime(1899, 12, 30)


def _is_date_format(code: str) -> bool:
    c = re.sub(r'"[^"]*"|\\.|\[[^\]]*\]|_.|\*.', "", code)
    return bool(re.search(r"[ymdhs]", c, re.I)) and "general" not in c.lower()


def _rk(v: int) -> float:
    if v & 2:
        n = v >> 2
        if n & 0x20000000:
            n -= 0x40000000
        val = float(n)
    else:
        val = struct.unpack("<d", struct.pack("<Q", (v & 0xFFFFFFFC) << 32))[0]
    return val / 100 if v & 1 else val


class _Seq:
    """Bytes spread over several records (an SST and its CONTINUEs), read in order."""

    def __init__(self, segments: list[bytes]):
        self.segs, self.i, self.p = segments, 0, 0

    def _ensure(self) -> bool:
        while self.i < len(self.segs) and self.p >= len(self.segs[self.i]):
            self.i += 1
            self.p = 0
        return self.i < len(self.segs)

    def read(self, n: int) -> bytes:
        out = b""
        while n > 0 and self._ensure():
            take = self.segs[self.i][self.p:self.p + n]
            out += take
            self.p += len(take)
            n -= len(take)
        return out

    def chars(self, count: int, high: bool) -> str:
        out = []
        while count > 0:
            if self.i >= len(self.segs):
                break
            if self.p >= len(self.segs[self.i]):                  # the string continues in the next record
                self.i += 1
                self.p = 1
                if self.i >= len(self.segs):
                    break
                high = bool(self.segs[self.i][0] & 1)
                continue
            seg = self.segs[self.i]
            per = 2 if high else 1
            take = min(count, (len(seg) - self.p) // per)
            if take <= 0:
                self.p = len(seg)
                continue
            chunk = seg[self.p:self.p + take * per]
            out.append(chunk.decode("utf-16-le" if high else "latin-1", errors="replace"))
            self.p += take * per
            count -= take
        return "".join(out)


def _read_sst(segments: list[bytes]) -> list[str]:
    r = _Seq(segments)
    head = r.read(8)
    if len(head) < 8:
        return []
    total_unique = struct.unpack("<II", head)[1]
    out = []
    for _ in range(total_unique):
        h = r.read(3)
        if len(h) < 3:
            break
        cch, flags = struct.unpack("<HB", h)
        runs = struct.unpack("<H", r.read(2))[0] if flags & 8 else 0
        ext = struct.unpack("<I", r.read(4))[0] if flags & 4 else 0
        out.append(r.chars(cch, bool(flags & 1)))
        r.read(4 * runs)
        r.read(ext)
    return out


def _unicode_string(body: bytes, pos: int = 0, cch_size: int = 2) -> tuple[str, int]:
    cch = int.from_bytes(body[pos:pos + cch_size], "little")
    flags = body[pos + cch_size]
    pos += cch_size + 1
    runs = ext = 0
    if flags & 8:
        runs = struct.unpack_from("<H", body, pos)[0]
        pos += 2
    if flags & 4:
        ext = struct.unpack_from("<I", body, pos)[0]
        pos += 4
    if flags & 1:
        s, pos = body[pos:pos + cch * 2].decode("utf-16-le", errors="replace"), pos + cch * 2
    else:
        s, pos = body[pos:pos + cch].decode("latin-1", errors="replace"), pos + cch
    return s, pos + 4 * runs + ext


def _records(data: bytes, start: int = 0):
    pos = start
    while pos + 4 <= len(data):
        rid, ln = struct.unpack_from("<HH", data, pos)
        yield rid, data[pos + 4:pos + 4 + ln], pos
        pos += 4 + ln


def _number(v: float):
    return int(v) if float(v).is_integer() and abs(v) < 1e15 else v


def read_xls(data: bytes) -> dict[str, list[list]]:
    """Every worksheet of a .xls file as rows of values (dates become ISO text)."""
    try:
        ole = OleFile(data)
        book = ole.read("Workbook") if "Workbook" in ole.names() else ole.read("Book")
    except (OleError, KeyError) as e:
        raise LegacyError(f"Not an Excel 97-2003 workbook ({e})") from e
    sst_segments: list[bytes] = []
    sheets: list[tuple[str, int]] = []
    xf_formats: list[int] = []
    custom: dict[int, str] = {}
    date1904 = False
    in_sst = False
    depth = 0
    for rid, body, pos in _records(book):
        if rid == 0x0809:                                   # BOF
            depth += 1
            if depth > 1:
                break
        elif rid == 0x000A:                                 # EOF of the globals
            break
        elif rid == 0x002F:
            raise LegacyError("The workbook is password protected.")
        elif rid == 0x0022:
            date1904 = bool(struct.unpack_from("<H", body)[0])
        elif rid == 0x00FC:
            sst_segments, in_sst = [body], True
            continue
        elif rid == 0x003C and in_sst:
            sst_segments.append(body)
            continue
        elif rid == 0x0085:
            offset, _, kind = struct.unpack_from("<IBB", body, 0)
            name, _ = _unicode_string(body, 6, 1)
            if kind == 0:
                sheets.append((name, offset))
        elif rid == 0x00E0 and len(body) >= 4:
            xf_formats.append(struct.unpack_from("<H", body, 2)[0])
        elif rid == 0x041E and len(body) >= 5:
            idx = struct.unpack_from("<H", body, 0)[0]
            custom[idx], _ = _unicode_string(body, 2, 2)
        in_sst = False
    strings = _read_sst(sst_segments) if sst_segments else []

    def is_date(xf: int) -> bool:
        if xf >= len(xf_formats):
            return False
        f = xf_formats[xf]
        return f in DATE_FORMATS if f < 164 else _is_date_format(custom.get(f, ""))

    def as_date(v: float) -> str:
        if 0 <= v < 1:
            return (EPOCH + timedelta(days=v)).strftime("%H:%M:%S")              # a time of day
        dt = (datetime(1904, 1, 1) if date1904 else EPOCH) + timedelta(days=v)
        return dt.strftime("%Y-%m-%d") if abs(v - round(v)) < 1e-9 else dt.strftime("%Y-%m-%d %H:%M:%S")

    out: dict[str, list[list]] = {}
    for name, offset in sheets:
        cells: dict[tuple[int, int], object] = {}
        last_formula: tuple[int, int] | None = None
        depth = 0
        for rid, body, _pos in _records(book, offset):
            if rid == 0x0809:
                depth += 1
            elif rid == 0x000A:
                depth -= 1
                if depth <= 0:
                    break
            elif depth != 1:
                continue
            elif rid == 0x00FD and len(body) >= 10:                       # LABELSST
                r, c, _x, i = struct.unpack_from("<HHHI", body, 0)
                cells[(r, c)] = strings[i] if i < len(strings) else ""
            elif rid == 0x0203 and len(body) >= 14:                       # NUMBER
                r, c, x, v = struct.unpack_from("<HHHd", body, 0)
                cells[(r, c)] = as_date(v) if is_date(x) else _number(v)
            elif rid == 0x027E and len(body) >= 10:                       # RK
                r, c, x, v = struct.unpack_from("<HHHI", body, 0)
                val = _rk(v)
                cells[(r, c)] = as_date(val) if is_date(x) else _number(val)
            elif rid == 0x00BD and len(body) >= 6:                        # MULRK
                r, c0 = struct.unpack_from("<HH", body, 0)
                c1 = struct.unpack_from("<H", body, len(body) - 2)[0]
                for k, c in enumerate(range(c0, c1 + 1)):
                    x, v = struct.unpack_from("<HI", body, 4 + k * 6)
                    val = _rk(v)
                    cells[(r, c)] = as_date(val) if is_date(x) else _number(val)
            elif rid == 0x0204 and len(body) >= 8:                        # LABEL
                r, c, _x = struct.unpack_from("<HHH", body, 0)
                cells[(r, c)], _ = _unicode_string(body, 6, 2)
            elif rid == 0x0205 and len(body) >= 8:                        # BOOLERR
                r, c, _x, val, is_err = struct.unpack_from("<HHHBB", body, 0)
                cells[(r, c)] = ("#ERR" if is_err else bool(val))
            elif rid == 0x0006 and len(body) >= 14:                       # FORMULA (cached result)
                r, c, x = struct.unpack_from("<HHH", body, 0)
                res = body[6:14]
                if res[6:8] == b"\xff\xff":
                    last_formula = (r, c) if res[0] == 0 else None       # a string follows in a STRING record
                    if res[0] == 1:
                        cells[(r, c)] = bool(res[2])
                    elif res[0] == 2:
                        cells[(r, c)] = "#ERR"
                    elif res[0] == 3:
                        cells[(r, c)] = ""
                else:
                    v = struct.unpack("<d", res)[0]
                    cells[(r, c)] = as_date(v) if is_date(x) else _number(v)
            elif rid == 0x0207 and last_formula is not None:             # STRING
                cells[last_formula], _ = _unicode_string(body, 0, 2)
                last_formula = None
        if not cells:
            out[name] = []
            continue
        rows = max(r for r, _ in cells) + 1
        cols = max(c for _, c in cells) + 1
        grid = [[cells.get((r, c), None) for c in range(cols)] for r in range(rows)]
        out[name] = grid
    if not out:
        raise LegacyError("The workbook has no worksheets.")
    return out


# ================================================================= PowerPoint

def _ppt_records(stream: bytes, start: int, end: int):
    """(type, version, instance, body start, body end) for each record in a range."""
    pos = start
    while pos + 8 <= end:
        ver_inst, rtype, ln = struct.unpack_from("<HHI", stream, pos)
        yield rtype, ver_inst & 0xF, ver_inst >> 4, pos + 8, min(end, pos + 8 + ln)
        pos += 8 + ln


def _ppt_text(stream: bytes, rtype: int, a: int, b: int) -> list[str]:
    raw = stream[a:b]
    text = raw.decode("utf-16-le", errors="replace") if rtype == 0x0FA0 else raw.decode("cp1252", errors="replace")
    parts = [x.replace("\x0b", " ").strip() for x in re.split(r"[\r\n]+", text.replace("\x00", ""))]
    return [x for x in parts if x]


def _ppt_shape_text(stream: bytes, start: int, end: int, out: list[tuple[int, list[str]]], header: list[int]):
    """Text of every text box inside a slide (its drawing holds them, each after a TextHeaderAtom)."""
    for rtype, ver, inst, a, b in _ppt_records(stream, start, end):
        if ver == 0xF:
            _ppt_shape_text(stream, a, b, out, header)
        elif rtype == 0x0F9F and b - a >= 4:
            header[0] = struct.unpack_from("<I", stream, a)[0]
        elif rtype in (0x0FA0, 0x0FA8):
            parts = _ppt_text(stream, rtype, a, b)
            if parts:
                out.append((header[0], parts))
            header[0] = 4


def _persist_directory(stream: bytes, current_user: bytes) -> dict[int, int]:
    """persist id -> offset of the object in the stream, following the chain of edits (newest wins)."""
    directory: dict[int, int] = {}
    if len(current_user) < 20:
        return directory
    offset, seen = struct.unpack_from("<I", current_user, 16)[0], set()
    while offset and offset + 8 <= len(stream) and offset not in seen:
        seen.add(offset)
        ver_inst, rtype, ln = struct.unpack_from("<HHI", stream, offset)
        if rtype != 0x0FF5 or offset + 8 + 20 > len(stream):
            break
        last_edit, persist_offset = struct.unpack_from("<II", stream, offset + 8 + 8)
        if persist_offset + 8 <= len(stream) and struct.unpack_from("<H", stream, persist_offset + 2)[0] == 0x1772:
            plen = struct.unpack_from("<I", stream, persist_offset + 4)[0]
            pos, end = persist_offset + 8, min(len(stream), persist_offset + 8 + plen)
            while pos + 4 <= end:
                head = struct.unpack_from("<I", stream, pos)[0]
                first, count = head & 0xFFFFF, head >> 20
                pos += 4
                for i in range(count):
                    if pos + 4 > end:
                        break
                    directory.setdefault(first + i, struct.unpack_from("<I", stream, pos)[0])
                    pos += 4
        offset = last_edit
    return directory


def read_ppt(data: bytes) -> list[dict]:
    """[{title, paragraphs}] for every slide of a .ppt file, in slide order."""
    try:
        ole = OleFile(data)
        stream = ole.read("PowerPoint Document")
        try:
            current_user = ole.read("Current User")
        except KeyError:
            current_user = b""
    except (OleError, KeyError) as e:
        raise LegacyError(f"Not a PowerPoint 97-2003 presentation ({e})") from e
    order: list[int] = []                                   # persist id of each slide, in order
    outline: dict[int, list[tuple[int, list[str]]]] = {}    # text kept in the outline list, per persist id
    for rtype, ver, inst, a, b in _ppt_records(stream, 0, len(stream)):
        if rtype != 0x03E8:                                 # DocumentContainer
            continue
        for t2, v2, i2, a2, b2 in _ppt_records(stream, a, b):
            if t2 != 0x0FF0 or i2 != 0:                     # SlideListWithText, the slides' one
                continue
            current, header = None, 4
            for t3, v3, i3, a3, b3 in _ppt_records(stream, a2, b2):
                if t3 == 0x03F3 and b3 - a3 >= 4:
                    current = struct.unpack_from("<I", stream, a3)[0]
                    order.append(current)
                    outline[current] = []
                elif t3 == 0x0F9F and b3 - a3 >= 4:
                    header = struct.unpack_from("<I", stream, a3)[0]
                elif t3 in (0x0FA0, 0x0FA8) and current is not None:
                    parts = _ppt_text(stream, t3, a3, b3)
                    if parts:
                        outline[current].append((header, parts))
    directory = _persist_directory(stream, current_user)
    slides: list[dict] = []
    for pid in order:
        found: list[tuple[int, list[str]]] = []
        offset = directory.get(pid)
        if offset is not None and offset + 8 <= len(stream):
            _, rtype, ln = struct.unpack_from("<HHI", stream, offset)
            if rtype == 0x03EE:
                _ppt_shape_text(stream, offset + 8, min(len(stream), offset + 8 + ln), found, [4])
        found = found or outline.get(pid, [])
        title, paras = "", []
        for header, parts in found:
            if header in (0, 6) and not title:
                title = " ".join(parts)
            else:
                paras += parts
        slides.append({"title": title, "paragraphs": paras})
    if not slides:
        raise LegacyError("Couldn't find any slides.")
    return slides


def ppt_blocks(data: bytes) -> list[docs.Block]:
    blocks = []
    for i, s in enumerate(read_ppt(data), 1):
        blocks.append(docs.Block("h2", docs.escape_markdown(s["title"]) if s["title"] else f"Slide {i}"))
        for p in s["paragraphs"]:
            blocks.append(docs.Block("li", docs.escape_markdown(p)))
    return blocks
