"""Builders for tiny .doc / .xls / .ppt files, written from the published binary-format specs
([MS-CFB], [MS-XLS], [MS-DOC], [MS-PPT]) so the readers can be tested without Office."""

from __future__ import annotations

import struct

END, FREE, FATS = 0xFFFFFFFE, 0xFFFFFFFF, 0xFFFFFFFD


def build_ole(streams: dict[str, bytes]) -> bytes:
    """A compound file holding the given top-level streams."""
    ss, ms, cutoff = 512, 64, 4096
    names = sorted(streams, key=lambda n: (len(n), n))
    small = [n for n in names if len(streams[n]) < cutoff]
    big = [n for n in names if len(streams[n]) >= cutoff]
    # ---- mini stream
    mini, mini_fat, mini_start = bytearray(), [], {}
    for n in small:
        data = streams[n]
        count = max(1, -(-len(data) // ms))
        mini_start[n] = len(mini_fat)
        for i in range(count):
            mini_fat.append(len(mini_fat) + 1 if i < count - 1 else END)
        mini += data + b"\0" * (count * ms - len(data))
    # ---- sector layout: [FAT...][dir][minifat][ministream][big streams...]
    n_entries = 1 + len(names)
    dir_sectors = -(-n_entries * 128 // ss)
    minifat_sectors = -(-len(mini_fat) * 4 // ss) if mini_fat else 0
    mini_sectors = -(-len(mini) // ss) if mini else 0
    big_sectors = {n: -(-len(streams[n]) // ss) for n in big}
    total_wo_fat = dir_sectors + minifat_sectors + mini_sectors + sum(big_sectors.values())
    n_fat = 1
    while n_fat * 128 < total_wo_fat + n_fat:
        n_fat += 1
    fat = [FREE] * (n_fat * 128)
    nxt = 0
    for _ in range(n_fat):
        fat[nxt] = FATS
        nxt += 1

    def chain(count: int) -> int:
        nonlocal nxt
        start = nxt
        for i in range(count):
            fat[nxt] = nxt + 1 if i < count - 1 else END
            nxt += 1
        return start if count else END

    dir_start = chain(dir_sectors)
    minifat_start = chain(minifat_sectors)
    mini_start_sector = chain(mini_sectors)
    big_start = {n: chain(big_sectors[n]) for n in big}
    # ---- directory
    def entry(name, kind, left, right, child, start, size):
        raw = name.encode("utf-16-le")
        return (raw.ljust(64, b"\0") + struct.pack("<HBB", len(raw) + 2 if name else 0, kind, 1)
                + struct.pack("<III", left, right, child) + b"\0" * 16 + b"\0" * 4 + b"\0" * 16
                + struct.pack("<I", start) + struct.pack("<Q", size))
    dirs = [entry("Root Entry", 5, FREE, FREE, 1 if names else FREE, mini_start_sector if mini else END, len(mini))]
    for i, n in enumerate(names):
        right = i + 2 if i + 1 < len(names) else FREE
        start = mini_start[n] if n in small else big_start[n]
        dirs.append(entry(n, 2, FREE, right, FREE, start, len(streams[n])))
    dir_bytes = b"".join(dirs).ljust(dir_sectors * ss, b"\0")
    # ---- header
    header = bytearray(512)
    header[0:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<HHHHH", header, 24, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<IIIIIIII", header, 44, n_fat, dir_start, 0, cutoff, minifat_start, minifat_sectors, END, 0)
    difat = list(range(n_fat)) + [FREE] * (109 - n_fat)
    struct.pack_into("<109I", header, 76, *difat)
    out = bytearray(header)
    out += struct.pack(f"<{len(fat)}I", *fat)
    out += dir_bytes
    if minifat_sectors:
        out += struct.pack(f"<{len(mini_fat)}I", *mini_fat).ljust(minifat_sectors * ss, b"\xff")
    if mini_sectors:
        out += bytes(mini).ljust(mini_sectors * ss, b"\0")
    for n in big:
        out += streams[n].ljust(big_sectors[n] * ss, b"\0")
    return bytes(out)


# ------------------------------------------------------------------- Word

def build_doc(text: str, unicode_text: bool = False) -> bytes:
    """A Word 97 document whose whole text is one piece."""
    n = len(text)
    word = bytearray(1024)
    struct.pack_into("<H", word, 0, 0xA5EC)
    struct.pack_into("<H", word, 2, 0x00C1)
    struct.pack_into("<H", word, 0x0A, 0x0200)                       # use the 1Table stream
    csw, cslw = 14, 22
    struct.pack_into("<H", word, 32, csw)
    struct.pack_into("<H", word, 34 + csw * 2, cslw)
    lw = 34 + csw * 2 + 2
    struct.pack_into("<I", word, lw + 12, n)                          # ccpText
    pairs = lw + cslw * 4 + 2
    struct.pack_into("<H", word, pairs - 2, 93)
    body = text.encode("utf-16-le") if unicode_text else text.encode("cp1252")
    fc = 1024
    word += body
    pcd_fc = fc if unicode_text else (fc * 2) | 0x40000000
    plc = struct.pack("<II", 0, n) + struct.pack("<HIH", 0, pcd_fc, 0)
    clx = b"\x02" + struct.pack("<I", len(plc)) + plc
    struct.pack_into("<II", word, pairs + 33 * 8, 0, len(clx))
    return build_ole({"WordDocument": bytes(word), "1Table": clx})


# ------------------------------------------------------------------ Excel

def _rec(rid: int, body: bytes = b"") -> bytes:
    return struct.pack("<HH", rid, len(body)) + body


def _bstr(s: str, cch_size: int = 2) -> bytes:
    flags = 0 if s.isascii() else 1
    raw = s.encode("utf-16-le") if flags else s.encode("latin-1")
    return len(s).to_bytes(cch_size, "little") + bytes([flags]) + raw


def build_xls(sheets: dict[str, list[list]], strings_split: bool = True) -> bytes:
    """A BIFF8 workbook. Cell values: str, int/float, ('date', serial), ('rk', int), bool."""
    sst: list[str] = []
    for rows in sheets.values():
        for row in rows:
            for v in row:
                if isinstance(v, str) and v not in sst:
                    sst.append(v)
    bof = lambda kind: _rec(0x0809, struct.pack("<HHHHII", 0x0600, kind, 0x0DBB, 0x07CC, 0, 6))  # noqa: E731
    glob = bytearray(bof(5))
    glob += _rec(0x0022, struct.pack("<H", 0))
    glob += _rec(0x041E, struct.pack("<H", 164) + _bstr("yyyy-mm-dd hh:mm"))
    for ifmt in (0, 14, 164):                                          # XF 0 general, XF 1 date, XF 2 custom date
        glob += _rec(0x00E0, struct.pack("<HH", 0, ifmt) + b"\0" * 16)
    # SST, split across a CONTINUE inside the characters of its longest string (as Excel does when a
    # record is full): the continuation record starts with a fresh "high byte" flag for the rest of the string
    sst_body = bytearray(struct.pack("<II", len(sst), len(sst)))
    spans = []
    for text in sst:
        start = len(sst_body)
        sst_body += _bstr(text)
        spans.append((start, text))
    pick = max(spans, key=lambda t: len(t[1]), default=None)
    if strings_split and pick is not None and len(pick[1]) >= 4:
        start, text = pick
        high = 0 if text.isascii() else 1
        cut = start + 3 + (len(text) // 2) * (2 if high else 1)
        glob += _rec(0x00FC, bytes(sst_body[:cut])) + _rec(0x003C, bytes([high]) + bytes(sst_body[cut:]))
    else:
        glob += _rec(0x00FC, bytes(sst_body))
    sheet_names = list(sheets)
    bound_len = sum(4 + 8 + len(n if n.isascii() else n.encode("utf-16-le")) * 1 for n in sheet_names)
    bound_placeholder = len(glob)
    boundsheets = b"".join(_rec(0x0085, struct.pack("<IBB", 0, 0, 0) + _bstr(n, 1)) for n in sheet_names)
    glob += boundsheets
    glob += _rec(0x000A)
    del bound_len
    # sheets
    sheet_bytes = []
    for rows in sheets.values():
        s = bytearray(bof(0x10))
        for r, row in enumerate(rows):
            for c, v in enumerate(row):
                if v is None:
                    continue
                if isinstance(v, bool):
                    s += _rec(0x0205, struct.pack("<HHHBB", r, c, 0, int(v), 0))
                elif isinstance(v, str):
                    s += _rec(0x00FD, struct.pack("<HHHI", r, c, 0, sst.index(v)))
                elif isinstance(v, tuple) and v[0] == "date":
                    s += _rec(0x0203, struct.pack("<HHHd", r, c, 1, v[1]))
                elif isinstance(v, tuple) and v[0] == "rk":
                    s += _rec(0x027E, struct.pack("<HHHI", r, c, 0, (v[1] << 2) | 2))
                elif isinstance(v, tuple) and v[0] == "rk100":
                    s += _rec(0x027E, struct.pack("<HHHI", r, c, 0, (v[1] << 2) | 3))
                elif isinstance(v, tuple) and v[0] == "formula":
                    s += _rec(0x0006, struct.pack("<HHH", r, c, 0) + struct.pack("<d", v[1]) + b"\0" * 6)
                elif isinstance(v, tuple) and v[0] == "formula_str":
                    s += _rec(0x0006, struct.pack("<HHH", r, c, 0) + b"\x00\0\0\0\0\0\xff\xff" + b"\0" * 6)
                    s += _rec(0x0207, _bstr(v[1]))
                else:
                    s += _rec(0x0203, struct.pack("<HHHd", r, c, 0, float(v)))
        s += _rec(0x000A)
        sheet_bytes.append(bytes(s))
    # patch the sheet offsets into the BOUNDSHEET records
    offset = len(glob)
    fixed = bytearray(glob)
    pos = bound_placeholder
    for name, sb in zip(sheet_names, sheet_bytes):
        struct.pack_into("<I", fixed, pos + 4, offset)
        pos += 4 + 6 + len(_bstr(name, 1))
        offset += len(sb)
    book = bytes(fixed) + b"".join(sheet_bytes)
    return build_ole({"Workbook": book})


# -------------------------------------------------------------- PowerPoint

def _prec(rtype: int, body: bytes, ver: int = 0, inst: int = 0) -> bytes:
    return struct.pack("<HHI", (inst << 4) | ver, rtype, len(body)) + body


def build_ppt(slides: list[tuple[str, list[str]]]) -> bytes:
    inner = b""
    for i, (title, bullets) in enumerate(slides):
        inner += _prec(0x03F3, struct.pack("<IIII", 256 + i, 0, i + 1, 0) + b"\0" * 4)
        inner += _prec(0x0F9F, struct.pack("<I", 0)) + _prec(0x0FA0, title.encode("utf-16-le"))
        inner += _prec(0x0F9F, struct.pack("<I", 1))
        body = "\r".join(bullets)
        inner += _prec(0x0FA0, body.encode("utf-16-le")) if not body.isascii() else _prec(0x0FA8, body.encode("cp1252"))
    doc = _prec(0x03E8, _prec(0x03E9, struct.pack("<IIII", 5760, 4320, 0, 0)) + _prec(0x0FF0, inner, ver=0xF, inst=0), ver=0xF)
    return build_ole({"PowerPoint Document": doc})
