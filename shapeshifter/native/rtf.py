"""Rich Text Format: read RTF into document blocks and write blocks as RTF.

Reading handles text (with bold / italic), headings from the style names, bullet and numbered
lists, tables, hyperlinks and embedded PNG / JPEG pictures, with Windows-1252 and Unicode escapes.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field

from . import docs
from .docread import _marked

DESTINATIONS = {"fonttbl", "colortbl", "stylesheet", "info", "header", "headerl", "headerr", "headerf", "footer",
                "footerl", "footerr", "footerf", "footnote", "listtable", "listoverridetable", "revtbl",
                "generator", "themedata", "colorschememapping", "latentstyles", "datastore", "xmlnstbl",
                "rsidtbl", "mmathPr", "pgdsctbl", "fldinst", "bkmkstart", "bkmkend", "shpinst", "nonshppict"}
SYMBOLS = {"par": "\n", "line": "\n", "tab": "\t", "emdash": "—", "endash": "–", "bullet": "•", "lquote": "‘",
           "rquote": "’", "ldblquote": "“", "rdblquote": "”", "~": "\u00a0", "-": "", "_": "‑", "emspace": " ",
           "enspace": " "}
TOKEN = re.compile(rb"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\([^a-zA-Z])|([{}])|([^\\{}]+)", re.S)


@dataclass
class _State:
    bold: bool = False
    italic: bool = False
    strike: bool = False
    skip: bool = False
    dest: str = ""
    link: str = ""
    uc: int = 1
    star: bool = False


@dataclass
class _Para:
    runs: list = field(default_factory=list)       # (text, bold, italic, strike, link)
    style: int = 0
    listed: bool = False
    marker: str = ""
    level: int = 0


def _decode_cp(b: int, codepage: str) -> str:
    try:
        return bytes([b]).decode(codepage)
    except (UnicodeDecodeError, LookupError):
        return bytes([b]).decode("cp1252", errors="replace")


def read_style_names(data: bytes) -> dict[int, str]:
    """{style number: name} from the stylesheet group."""
    out: dict[int, str] = {}
    start = data.find(b"{\\stylesheet")
    if start < 0:
        return out
    depth, end = 0, start
    for i in range(start, len(data)):
        c = data[i:i + 1]
        if c == b"{":
            depth += 1
        elif c == b"}":
            depth -= 1
            if depth == 0:
                end = i
                break
    for sm in re.finditer(rb"\{\\s(\d+)[^{}]*?([A-Za-z][A-Za-z0-9 ]*);\}", data[start:end]):
        out[int(sm.group(1))] = sm.group(2).decode("latin-1").strip()
    return out


def _unbold(text: str) -> str:
    """A heading written in bold doesn't need the bold marks as well."""
    if text.startswith("**") and text.endswith("**") and text.count("**") == 2:
        return text[2:-2]
    return text


def _text_of(runs) -> str:
    out = []
    for t, bold, italic, strike, link in runs:
        if link and t.strip():
            out.append(f"[{t.strip()}]({link})")
        else:
            out.append(_marked(t, bold, italic, strike))
    return "".join(out).strip()


def rtf_blocks(data: bytes) -> list[docs.Block]:
    """Blocks of an RTF document: headings, paragraphs, lists, tables and pictures."""
    if not data.lstrip().startswith(b"{\\rtf"):
        raise ValueError("Not an RTF file")
    styles = read_style_names(data)
    items: list = []                      # _Para | ("table", rows) | ("img", uri)
    stack: list[_State] = []
    st = _State()
    codepage = "cp1252"
    cur = _Para()
    cell_runs: list = []
    row_cells: list[str] = []
    table_rows: list[list[str]] = []
    in_table = False
    pict: dict | None = None
    skip_chars = 0
    high: int | None = None
    link_url = [""]
    level = 0

    def runs_target():
        return cell_runs if in_table else cur.runs

    def put(t: str):
        nonlocal skip_chars
        if st.skip or not t:
            return
        if skip_chars:
            n = min(skip_chars, len(t))
            t, skip_chars = t[n:], skip_chars - n
            if not t:
                return
        if st.dest in ("pntext", "listtext"):
            cur.marker += t
            cur.listed = True
            return
        if st.dest in ("pict", "stylesheet", "fldinst"):
            if st.dest == "fldinst":
                m = re.search(r'HYPERLINK\s+"([^"]+)"', t)
                if m:
                    link_url[0] = m.group(1)
            return
        runs_target().append((t, st.bold, st.italic, st.strike, st.link))

    def flush_table():
        nonlocal table_rows
        if table_rows:
            width = max(len(r) for r in table_rows)
            items.append(("table", [r + [""] * (width - len(r)) for r in table_rows]))
            table_rows = []

    def end_paragraph():
        nonlocal cur
        if _text_of(cur.runs):
            flush_table()
            cur.level = level
            items.append(cur)
        cur = _Para()

    for m in TOKEN.finditer(data):
        word, num, hexv, sym, brace, text = m.groups()
        if brace == b"{":
            stack.append(st)
            st = _State(st.bold, st.italic, st.strike, st.skip, st.dest, st.link, st.uc)       # a new group inherits its parent
            continue
        if brace == b"}":
            if pict is not None and st.dest == "pict":
                try:
                    raw = bytes.fromhex("".join(pict["data"]))
                    if raw and pict["kind"]:
                        mime = "image/png" if pict["kind"] == "png" else "image/jpeg"
                        items.append(("img", f"data:{mime};base64,{base64.b64encode(raw).decode()}"))
                except ValueError:
                    pass
                pict = None
            st = stack.pop() if stack else _State()
            continue
        if text is not None:
            s = text.decode("latin-1").replace("\r", "").replace("\n", "")
            if pict is not None and st.dest == "pict":
                pict["data"].append(re.sub(r"\s+", "", s))
            else:
                put(s)
            continue
        if hexv is not None:
            if skip_chars:
                skip_chars -= 1
            else:
                put(_decode_cp(int(hexv, 16), codepage))
            continue
        if sym is not None:
            c = sym.decode("latin-1")
            if c == "*":
                st.star = True                                  # an optional destination: skipped unless we know it
            elif c in SYMBOLS:
                put(SYMBOLS[c])
            elif c in "{}\\":
                put(c)
            continue
        w = word.decode("ascii")
        n = int(num) if num is not None else None
        if st.star:
            st.star = False
            if w != "fldinst":
                st.skip = True
                continue
        if w in DESTINATIONS:
            st.dest = w
            if w != "fldinst":
                st.skip = True
        elif w == "ansicpg" and n:
            codepage = f"cp{n}"
        elif w == "uc" and n is not None:
            st.uc = n
        elif w == "u" and n is not None:
            code = n + 65536 if n < 0 else n
            if 0xD800 <= code <= 0xDBFF:
                high = code
            else:
                if high is not None and 0xDC00 <= code <= 0xDFFF:
                    code, high = 0x10000 + ((high - 0xD800) << 10) + (code - 0xDC00), None
                put(chr(code))
            skip_chars = st.uc
        elif w == "b":
            st.bold = n != 0
        elif w == "i":
            st.italic = n != 0
        elif w == "strike":
            st.strike = n != 0
        elif w == "plain":
            st.bold = st.italic = st.strike = False
        elif w == "pard":
            if not in_table:
                cur.style = 0                                   # list marks already seen in this paragraph stay
                level = 0
        elif w == "s" and n is not None:
            cur.style = n
        elif w in ("ls", "pnlvlblt", "pnlvlbody"):
            cur.listed = True
        elif w == "ilvl" and n is not None:
            level = n
        elif w == "pnlvl" and n:
            cur.listed, level = True, max(0, n - 1)
        elif w in ("pntext", "listtext"):
            st.dest = w
        elif w == "field":
            link_url[0] = ""
        elif w == "fldrslt":
            st.dest, st.link = "fldrslt", link_url[0]
        elif w == "pict":
            st.dest, pict = "pict", {"kind": "", "data": []}
        elif w in ("pngblip", "jpegblip") and pict is not None:
            pict["kind"] = "png" if w == "pngblip" else "jpg"
        elif w in ("wmetafile", "emfblip", "dibitmap", "wbitmap", "macpict") and pict is not None:
            pict = None
            st.skip = True
        elif w == "trowd":
            in_table, row_cells, cell_runs = True, [], []
        elif w in ("cell", "nestcell"):
            row_cells.append(_unbold(_text_of(cell_runs)))      # a bold header row is just a header
            cell_runs = []
        elif w in ("row", "nestrow"):
            if row_cells:
                table_rows.append(row_cells)
            row_cells, in_table = [], False
        elif w in ("par", "sect"):
            if in_table:
                cell_runs.append((" ", False, False, False, ""))
            else:
                end_paragraph()
        elif w in SYMBOLS:
            put(SYMBOLS[w])
        # fonts, sizes, colours, alignment and spacing don't matter for the outline
    end_paragraph()
    flush_table()
    blocks: list[docs.Block] = []
    for it in items:
        if isinstance(it, tuple):
            blocks.append(docs.Block("table", rows=it[1]) if it[0] == "table" else docs.Block("img", "Picture", src=it[1]))
            continue
        text = _text_of(it.runs)
        name = styles.get(it.style, "").lower()
        h = re.match(r"heading\s*(\d)", name)
        if h:
            blocks.append(docs.Block(f"h{min(6, int(h.group(1)))}", _unbold(text)))
        elif name == "title":
            blocks.append(docs.Block("h1", _unbold(text)))
        elif it.listed:
            blocks.append(docs.Block("oli" if re.match(r"\s*\d", it.marker) else "li", text, level=min(it.level, 5)))
        else:
            blocks.append(docs.Block("p", text))
    return blocks


# ------------------------------------------------------------------ writing

def _esc(text: str) -> str:
    out = []
    for ch in text:
        o = ord(ch)
        if ch in "\\{}":
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\line ")
        elif ch == "\t":
            out.append("\\tab ")
        elif 32 <= o < 127:
            out.append(ch)
        elif o <= 0xFFFF:
            out.append(f"\\u{o - 65536 if o > 32767 else o}?")
        else:
            o -= 0x10000
            hi, lo = 0xD800 + (o >> 10), 0xDC00 + (o & 0x3FF)
            out.append(f"\\u{hi - 65536}?\\u{lo - 65536}?")
    return "".join(out)


def _inline(text: str) -> str:
    out = []
    for sp in docs.parse_inline(text):
        if sp.img:
            continue
        piece = _esc(sp.text)
        if sp.code:
            piece = f"{{\\f1 {piece}}}"
        if sp.bold:
            piece = f"{{\\b {piece}}}"
        if sp.italic:
            piece = f"{{\\i {piece}}}"
        if sp.strike:
            piece = f"{{\\strike {piece}}}"
        if sp.href:
            piece = ('{\\field{\\*\\fldinst{HYPERLINK "' + sp.href.replace('"', "") + '"}}'
                     '{\\fldrslt{\\ul\\cf1 ' + piece + "}}}")
        out.append(piece)
    return "".join(out)


def _picture(src: str, base_dirs) -> str:
    import io

    from .images_util import load_image_ref
    data = load_image_ref(src, list(base_dirs or []))
    if not data:
        return ""
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
            kind = "png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "jpg" if data[:3] == b"\xff\xd8\xff" else None
            if kind is None:
                buf = io.BytesIO()
                im.convert("RGBA").save(buf, "PNG")
                data, kind = buf.getvalue(), "png"
    except Exception:                                          # noqa: BLE001
        return ""
    k = min(1.0, 8640 / (w * 15))
    hexdata = data.hex()
    lines = "\n".join(hexdata[i:i + 128] for i in range(0, len(hexdata), 128))
    blip = "pngblip" if kind == "png" else "jpegblip"
    return (f"\\pard\\qc{{\\pict\\{blip}\\picw{w}\\pich{h}\\picwgoal{int(w * 15 * k)}\\pichgoal{int(h * 15 * k)}\n{lines}}}\\par\n")


def write_rtf(blocks: list[docs.Block], base_dirs=None, title: str = "") -> bytes:
    sizes = {1: 40, 2: 34, 3: 30, 4: 26, 5: 24, 6: 22}
    styles = "{\\stylesheet{\\s0 Normal;}" + "".join(f"{{\\s{i} heading {i};}}" for i in range(1, 7)) + "}"
    out = ["{\\rtf1\\ansi\\ansicpg1252\\deff0\\uc1{\\fonttbl{\\f0\\fswiss\\fcharset0 Calibri;}"
           "{\\f1\\fmodern\\fcharset0 Courier New;}}{\\colortbl;\\red5\\green99\\blue193;}" + styles +
           f"{{\\info{{\\title {_esc(title)}}}{{\\author ShapeShifter}}}}\\viewkind4\\f0\\fs22\n"]
    numbers: dict[int, int] = {}
    for b in blocks:
        k = b.kind
        if k != "oli":
            numbers.clear()
        if k.startswith("h") and k[1:].isdigit():
            size = sizes.get(int(k[1:]), 22)
            out.append(f"\\pard\\s{min(6, int(k[1:]))}\\sb240\\sa120\\keepn{{\\b\\fs{size} {_inline(b.text)}}}\\par\n")
        elif k == "p":
            out.append(f"\\pard\\sa160 {_inline(b.text)}\\par\n")
        elif k in ("li", "oli"):
            lvl = b.level
            if k == "oli":
                numbers[lvl] = numbers.get(lvl, 0) + 1
                marker, ls = f"{numbers[lvl]}.", 2
            else:
                marker, ls = "\\u8226?", 1
            out.append(f"\\pard\\li{720 * (lvl + 1)}\\fi-360\\sa60\\ls{ls}\\ilvl{lvl}{{\\listtext {marker}\\tab}} {_inline(b.text)}\\par\n")
        elif k == "quote":
            out.append(f"\\pard\\li720\\ri720\\sa160{{\\i {_inline(b.text)}}}\\par\n")
        elif k == "code":
            for line in b.text.splitlines() or [""]:
                out.append(f"\\pard\\li360{{\\f1\\fs20 {_esc(line)}}}\\par\n")
        elif k == "hr":
            out.append("\\pard\\brdrb\\brdrs\\brdrw10\\brsp20\\par\n")
        elif k == "img":
            out.append(_picture(b.src, base_dirs))
        elif k == "table" and b.rows:
            cols = max(len(r) for r in b.rows)
            width = 9000 // cols
            cellx = "".join("\\clbrdrt\\brdrs\\brdrw10\\clbrdrl\\brdrs\\brdrw10\\clbrdrb\\brdrs\\brdrw10\\clbrdrr\\brdrs\\brdrw10"
                            f"\\cellx{width * (i + 1)}" for i in range(cols))
            for ri, row in enumerate(b.rows):
                cells = "".join("\\pard\\intbl " + (f"{{\\b {_inline(c)}}}" if ri == 0 else _inline(c)) + "\\cell "
                                for c in list(row) + [""] * (cols - len(row)))
                out.append(f"\\trowd\\trgaph108{cellx}\n{cells}\\row\n")
            out.append("\\pard\\par\n")
    out.append("}")
    return "".join(out).encode("ascii", errors="replace")
