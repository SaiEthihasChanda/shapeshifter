"""Minimal XLSX reader/writer (zip + XML), no third-party code.

Reads shared/inline strings, numbers, booleans and formulas' cached values for every
sheet. Writes one sheet per table with inline strings.
"""

from __future__ import annotations

import re
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "rel": "http://schemas.openxmlformats.org/package/2006/relationships"}


def _col_index(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def _col_name(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def read(path) -> dict[str, list[list]]:
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        targets = {r.get("Id"): r.get("Target") for r in rels.findall("rel:Relationship", NS)}
        sheets = {}
        for s in wb.find("m:sheets", NS).findall("m:sheet", NS):
            target = targets[s.get(f"{{{NS['r']}}}id")].lstrip("/")
            target = target if target.startswith("xl/") else "xl/" + target
            sheets[s.get("name")] = _read_sheet(z.read(target), shared)
        return sheets


def _read_sheet(xml: bytes, shared: list[str]) -> list[list]:
    root = ET.fromstring(xml)
    rows: list[list] = []
    for row in root.iter(f"{{{NS['m']}}}row"):
        r = int(row.get("r", len(rows) + 1)) - 1
        while len(rows) <= r:
            rows.append([])
        cells = rows[r]
        for i, c in enumerate(row.findall("m:c", NS)):
            col = _col_index(c.get("r")) if c.get("r") else i
            t = c.get("t")
            v = c.find("m:v", NS)
            if t == "s" and v is not None:
                val = shared[int(v.text)]
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(f"{{{NS['m']}}}t"))
            elif t == "b" and v is not None:
                val = v.text == "1"
            elif t in ("str", "e") and v is not None:
                val = v.text
            elif v is not None and v.text is not None:
                num = float(v.text)
                val = int(num) if num.is_integer() and "E" not in v.text.upper() else num
            else:
                val = None
            while len(cells) <= col:
                cells.append(None)
            cells[col] = val
    while rows and not any(v is not None for v in rows[-1]):
        rows.pop()
    width = max((len(r) for r in rows), default=0)
    return [r + [None] * (width - len(r)) for r in rows]


def _cell(ref: str, v) -> str:
    if v is None or v == "":
        return ""
    if isinstance(v, bool):
        return f'<c r="{ref}" t="b"><v>{int(v)}</v></c>'
    if isinstance(v, (int, float)):
        return f'<c r="{ref}"><v>{v}</v></c>'
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(str(v))}</t></is></c>'


def write(path, sheets: dict[str, list[list]]) -> None:
    names = [re.sub(r"[\[\]:*?/\\]", "_", str(n))[:31] or f"Sheet{i + 1}"
             for i, n in enumerate(sheets)]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType='
                             f'"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                             for i in range(len(names)))
                   + "</Types>")
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8"?>'
                   f'<Relationships xmlns="{NS["rel"]}"><Relationship Id="rId1" Type='
                   '"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                   'Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml",
                   f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{NS["m"]}" '
                   f'xmlns:r="{NS["r"]}"><sheets>'
                   + "".join(f'<sheet name="{escape(n)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
                             for i, n in enumerate(names))
                   + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{NS["rel"]}">'
                   + "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/'
                             f'officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
                             for i in range(len(names)))
                   + "</Relationships>")
        for i, rows in enumerate(sheets.values()):
            body = "".join(
                f'<row r="{r + 1}">' + "".join(_cell(f"{_col_name(c)}{r + 1}", v)
                                               for c, v in enumerate(row)) + "</row>"
                for r, row in enumerate(rows))
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml",
                       f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{NS["m"]}">'
                       f"<sheetData>{body}</sheetData></worksheet>")
