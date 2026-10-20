"""The shared document model (a list of blocks) with Markdown, HTML and text readers/writers.

Block text keeps inline Markdown (**bold**, *italic*, `code`, [link](url)); writers render
it to HTML or strip it for plain text and PDF.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser


@dataclass
class Block:
    kind: str                    # h1..h6 | p | li | oli | quote | code | hr | table | img
    text: str = ""               # for img: the alt text
    rows: list[list[str]] = field(default_factory=list)   # tables
    level: int = 0               # list nesting
    src: str = ""                # img: file path (relative to the document) or data: URI


IMG_ONLY = re.compile(r"^!\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)$")


# ---------------------------------------------------------------- inline spans

@dataclass
class Span:
    text: str = ""
    bold: bool = False
    italic: bool = False
    code: bool = False
    strike: bool = False
    href: str = ""
    img: str = ""                # image source (text holds the alt)


_HTML_IMG = re.compile(r"<img\b([^>]*)>", re.I)


def _html_img_attrs(tag_attrs: str) -> tuple[str, str]:
    """(alt, src) from the attribute text of a raw <img> tag."""
    attrs = {k.lower(): v for k, v in re.findall(r"([\w:-]+)\s*=\s*\"([^\"]*)\"", tag_attrs)}
    attrs.update({k.lower(): v for k, v in re.findall(r"([\w:-]+)\s*=\s*'([^']*)'", tag_attrs)})
    return html.unescape(attrs.get("alt", "")), html.unescape(attrs.get("src", ""))


_INLINE = [
    ("img", re.compile(r"!\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")),
    ("htmlimg", _HTML_IMG),
    ("link", re.compile(r"\[([^\]]+)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")),
    ("code", re.compile(r"`([^`]+)`")),
    ("bold", re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")),
    ("strike", re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")),
    ("italic", re.compile(r"(?<![\w*_])([*_])(?=[^\s*_])(.+?)(?<=[^\s*_])\1(?![\w*_])")),
]


_ESCAPABLE = r"\\`*_{}\[\]()#+\-.!~|<>"
_ESC_RX = re.compile(rf"\\([{_ESCAPABLE}])")


def escape_markdown(text: str) -> str:
    """Make arbitrary text (OCR output, file names) safe to put in a Markdown paragraph."""
    t = re.sub(r"([\\`*_\[\]~<>])", r"\\\1", text)
    return re.sub(r"(?m)^(\s*)([#+\-]|\d+[.)])(\s)", r"\1\\\2\3", t)


def _protect(text: str) -> str:
    """Hide backslash-escaped characters from the tokenizer (private-use stand-ins)."""
    return _ESC_RX.sub(lambda m: chr(0xE000 + ord(m.group(1))), text)


def _restore(text: str) -> str:
    return re.sub("[\ue000-\ue0ff]", lambda m: chr(ord(m.group(0)) - 0xE000), text)


def parse_inline(text: str, base: Span | None = None) -> list[Span]:
    """Inline Markdown -> styled runs (nesting supported: **bold *and italic***).
    Backslash escapes (\\*) are honoured."""
    if base is None:
        spans = parse_inline(_protect(text), Span())
        for s in spans:
            s.text, s.href, s.img = _restore(s.text), _restore(s.href), _restore(s.img)
        return spans
    out: list[Span] = []
    pos = 0
    while pos < len(text):
        best = None
        for kind, rx in _INLINE:
            m = rx.search(text, pos)
            if m and (best is None or m.start() < best[1].start()):
                best = (kind, m)
        if best is None:
            out.append(Span(text[pos:], base.bold, base.italic, base.code, base.strike, base.href))
            break
        kind, m = best
        if m.start() > pos:
            out.append(Span(text[pos:m.start()], base.bold, base.italic, base.code, base.strike, base.href))
        style = Span(bold=base.bold, italic=base.italic, code=base.code, strike=base.strike, href=base.href)
        if kind == "img":
            out.append(Span(m.group(1), img=m.group(2)))
        elif kind == "htmlimg":                    # raw <img> inside Markdown (pandoc writes these)
            alt, src = _html_img_attrs(m.group(1))
            out.append(Span(alt, img=src) if src else Span(m.group(0)))
        elif kind == "code":
            out.append(Span(m.group(1), style.bold, style.italic, True, style.strike, style.href))
        elif kind == "link":
            style.href = m.group(2)
            out.extend(parse_inline(m.group(1), style))
        elif kind == "bold":
            style.bold = True
            out.extend(parse_inline(m.group(2), style))
        elif kind == "italic":
            style.italic = True
            out.extend(parse_inline(m.group(2), style))
        else:
            style.strike = True
            out.extend(parse_inline(m.group(1), style))
        pos = m.end()
    return [s for s in out if s.text or s.img]


# ------------------------------------------------------------------ Markdown

def parse_markdown(src: str) -> list[Block]:
    lines = src.replace("\r\n", "\n").split("\n")
    blocks: list[Block] = []
    para: list[str] = []
    i = 0

    def end_para():
        if para:
            text = " ".join(s.strip() for s in para)
            m = IMG_ONLY.match(text)
            raw = re.fullmatch(r"<img\b([^>]*)>", text.strip(), re.I)
            if m:
                blocks.append(Block("img", m.group(1), src=m.group(2)))
            elif raw and _html_img_attrs(raw.group(1))[1]:
                alt, src = _html_img_attrs(raw.group(1))
                blocks.append(Block("img", alt, src=src))
            else:
                blocks.append(Block("p", text))
            para.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            end_para()
            fence = stripped[:3]
            code = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(fence):
                code.append(lines[i])
                i += 1
            blocks.append(Block("code", "\n".join(code)))
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", stripped)
        if m:
            end_para()
            blocks.append(Block(f"h{len(m.group(1))}", m.group(2)))
        elif re.match(r"^([-*_])(\s*\1){2,}$", stripped):
            end_para()
            blocks.append(Block("hr"))
        elif stripped.startswith(">"):
            end_para()
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip()[1:].strip())
                i += 1
            blocks.append(Block("quote", " ".join(quote)))
            continue
        elif re.match(r"^\s*([-*+]|\d+[.)])\s+", line):
            end_para()
            m = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", line)
            kind = "oli" if m.group(2)[0].isdigit() else "li"
            blocks.append(Block(kind, m.group(3), level=len(m.group(1).expandtabs(4)) // 2))
        elif "|" in stripped and i + 1 < len(lines) and \
                re.match(r"^\s*\|?\s*:?-{3,}", lines[i + 1]):
            end_para()
            rows = []
            while i < len(lines) and "|" in lines[i]:
                if not re.match(r"^\s*\|?\s*:?-{3,}", lines[i]):
                    rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            blocks.append(Block("table", rows=rows))
            continue
        elif not stripped:
            end_para()
        elif para and re.match(r"^(=+|-+)$", stripped):   # setext heading
            text = " ".join(para)
            para.clear()
            blocks.append(Block("h1" if stripped[0] == "=" else "h2", text))
        else:
            para.append(line)
        i += 1
    end_para()
    return blocks


def inline_html(text: str) -> str:
    """Inline Markdown -> HTML. Built from parse_inline so that images, links and nested
    styles come out the same here, in DOCX and in PDF."""
    parts = []
    for s in parse_inline(text):
        if s.img:
            parts.append(f'<img src="{html.escape(s.img, quote=True)}" alt="{html.escape(s.text, quote=True)}">')
            continue
        t = html.escape(s.text, quote=False).replace("\n", "<br>\n")
        if s.code:
            t = f"<code>{t}</code>"
        if s.bold:
            t = f"<strong>{t}</strong>"
        if s.italic:
            t = f"<em>{t}</em>"
        if s.strike:
            t = f"<del>{t}</del>"
        if s.href:
            t = f'<a href="{html.escape(s.href, quote=True)}">{t}</a>'
        parts.append(t)
    return "".join(parts)


def plain(text: str) -> str:
    return "".join(s.text for s in parse_inline(text))


CSS = ("body{font-family:Segoe UI,Helvetica,Arial,sans-serif;max-width:46em;margin:2em auto;"
       "padding:0 1em;line-height:1.55;color:#1d1f27}code,pre{font-family:Consolas,monospace;"
       "background:#f3f4f7;border-radius:4px}pre{padding:.8em;overflow:auto}code{padding:0 .25em}"
       "blockquote{border-left:3px solid #ccd;margin-left:0;padding-left:1em;color:#555}"
       "table{border-collapse:collapse}td,th{border:1px solid #ccd;padding:.3em .6em}")


def to_html(blocks: list[Block], title: str = "", css: bool = True) -> str:
    """Blocks -> a complete HTML page. ``css=False`` leaves styling to the consumer
    (the PDF layout engine applies its own page styles)."""
    out = []
    lists: list[str] = []

    def close_lists(level: int = -1):
        while len(lists) > level + 1:
            out.append(f"</{lists.pop()}>")

    for b in blocks:
        if b.kind in ("li", "oli"):
            tag = "ol" if b.kind == "oli" else "ul"
            close_lists(b.level)
            if len(lists) == b.level + 1 and lists[-1] != tag:   # list type changed
                out.append(f"</{lists.pop()}>")
            while len(lists) <= b.level:
                lists.append(tag)
                out.append(f"<{tag}>")
            out.append(f"<li>{inline_html(b.text)}</li>")
            continue
        close_lists()
        if b.kind.startswith("h"):
            out.append(f"<{b.kind}>{inline_html(b.text)}</{b.kind}>")
        elif b.kind == "code":
            out.append(f"<pre><code>{html.escape(b.text)}</code></pre>")
        elif b.kind == "quote":
            out.append(f"<blockquote><p>{inline_html(b.text)}</p></blockquote>")
        elif b.kind == "hr":
            out.append("<hr>")
        elif b.kind == "img":
            out.append(f'<p><img src="{html.escape(b.src, quote=True)}" '
                       f'alt="{html.escape(b.text, quote=True)}"></p>')
        elif b.kind == "table":
            rows = b.rows
            out.append("<table>")
            for r, row in enumerate(rows):
                cell = "th" if r == 0 else "td"
                out.append("<tr>" + "".join(f"<{cell}>{inline_html(c)}</{cell}>" for c in row) + "</tr>")
            out.append("</table>")
        else:
            out.append(f"<p>{inline_html(b.text)}</p>")
    close_lists()
    head = (f'<!DOCTYPE html>\n<html><head><meta charset="utf-8"><title>{html.escape(title)}'
            f"</title>{f'<style>{CSS}</style>' if css else ''}</head>\n<body>\n")
    return head + "\n".join(out) + "\n</body></html>\n"


def to_markdown(blocks: list[Block]) -> str:
    out = []
    counters: dict[int, int] = {}
    for b in blocks:
        if b.kind.startswith("h") and b.kind != "hr":
            out.append("#" * int(b.kind[1]) + " " + b.text + "\n")
        elif b.kind in ("li", "oli"):
            if b.kind == "oli":
                counters[b.level] = counters.get(b.level, 0) + 1
                mark = f"{counters[b.level]}."
            else:
                mark = "-"
            out.append("  " * b.level + f"{mark} {b.text}")
            continue
        elif b.kind == "code":
            out.append("```\n" + b.text + "\n```\n")
        elif b.kind == "quote":
            out.append("> " + b.text + "\n")
        elif b.kind == "hr":
            out.append("---\n")
        elif b.kind == "img":
            out.append(f"![{b.text}]({b.src})\n")
        elif b.kind == "table" and b.rows:
            out.append("| " + " | ".join(b.rows[0]) + " |")
            out.append("|" + "---|" * len(b.rows[0]))
            out += ["| " + " | ".join(r) + " |" for r in b.rows[1:]]
            out.append("")
        else:
            out.append(b.text.replace("\n", "  \n") + "\n")      # hard line breaks
        counters.clear()
    return "\n".join(out).rstrip() + "\n"


def to_text(blocks: list[Block]) -> str:
    out = []
    for b in blocks:
        if b.kind in ("li", "oli"):
            out.append("  " * b.level + "• " + plain(b.text))
        elif b.kind == "hr":
            out.append("-" * 40)
        elif b.kind == "img":
            if b.text:
                out.append(f"[image: {b.text}]\n")
        elif b.kind == "table":
            out += ["\t".join(plain(c) for c in r) for r in b.rows]
            out.append("")
        elif b.kind.startswith("h"):
            t = plain(b.text)
            out.append(t + "\n" + ("=" if b.kind == "h1" else "-") * len(t) + "\n")
        else:
            out.append(plain(b.text) + "\n")
    return "\n".join(out).rstrip() + "\n"


def pdf_blocks(blocks: list[Block]) -> list[tuple]:
    """Blocks for the native PDF writer: (kind, text[, extra]) tuples."""
    out = []
    counters: dict[int, int] = {}
    for b in blocks:
        if b.kind != "oli":
            counters.clear()
        if b.kind == "table":
            out.append(("table", "", [[plain(c) for c in r] for r in b.rows]))
        elif b.kind == "oli":
            counters[b.level] = counters.get(b.level, 0) + 1
            out.append(("oli", plain(b.text), f"{counters[b.level]}."))
        elif b.kind == "code":
            out.append(("code", b.text))
        elif b.kind == "img":
            out.append(("img", b.text, b.src))
        else:
            out.append((b.kind, plain(b.text)))
    return out


# ---------------------------------------------------------------------- HTML

class _HtmlToBlocks(HTMLParser):
    BLOCK = {"p", "div", "section", "article", "header", "footer", "main", "li", "h1", "h2", "h3",
             "h4", "h5", "h6", "blockquote", "pre", "tr", "br", "hr", "table", "ul", "ol"}
    SKIP = {"script", "style", "head", "noscript", "template", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[Block] = []
        self.buf: list[str] = []
        self.kind = "p"
        self.skip = 0
        self.lists: list[str] = []
        self.pre = 0
        self.row: list[str] | None = None
        self.table: list[list[str]] | None = None
        self.href: list[str] = []

    def _flush(self):
        text = "".join(self.buf)
        text = text if self.pre else re.sub(r"\s+", " ", text).strip()
        if text:
            level = max(0, len(self.lists) - 1)
            self.blocks.append(Block(self.kind, text, level=level))
        self.buf = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        a = dict(attrs)
        if tag in ("td", "th"):
            self.buf = []
            return
        if tag == "table":
            self._flush()
            self.table = []
            return
        if tag == "tr":
            self.row = []
            return
        if tag in self.BLOCK:
            self._flush()
            if tag in ("ul", "ol"):
                self.lists.append(tag)
            self.kind = {"li": "oli" if self.lists and self.lists[-1] == "ol" else "li",
                         "blockquote": "quote", "pre": "code"}.get(tag, tag if tag[0] == "h" and
                                                                  tag[1:].isdigit() else "p")
            if tag == "pre":
                self.pre += 1
            if tag == "hr":
                self.blocks.append(Block("hr"))
        elif tag in ("strong", "b"):
            self.buf.append("**")
        elif tag in ("em", "i"):
            self.buf.append("*")
        elif tag == "code" and not self.pre:
            self.buf.append("`")
        elif tag == "a":
            self.href.append(a.get("href") or "")
            self.buf.append("[")
        elif tag == "img":
            src = a.get("src") or ""
            if self.row is not None or not src:       # inside a table cell: keep inline
                self.buf.append(f"![{a.get('alt') or ''}]({src})")
            else:
                self._flush()
                self.blocks.append(Block("img", a.get("alt") or "", src=src))

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in ("td", "th") and self.row is not None:
            self.row.append(re.sub(r"\s+", " ", "".join(self.buf)).strip())
            self.buf = []
        elif tag == "tr" and self.row is not None and self.table is not None:
            self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            if self.table:
                self.blocks.append(Block("table", rows=self.table))
            self.table = None
        elif tag in ("strong", "b"):
            self.buf.append("**")
        elif tag in ("em", "i"):
            self.buf.append("*")
        elif tag == "code" and not self.pre:
            self.buf.append("`")
        elif tag == "a":
            href = self.href.pop() if self.href else ""
            self.buf.append(f"]({href})" if href else "]")
        elif tag in self.BLOCK:
            self._flush()
            if tag == "pre":
                self.pre = max(0, self.pre - 1)
            if tag in ("ul", "ol") and self.lists:
                self.lists.pop()
            self.kind = "p"

    def handle_data(self, data):
        if not self.skip:
            self.buf.append(data)

    def close(self):
        super().close()
        self._flush()


def parse_html(src: str) -> list[Block]:
    p = _HtmlToBlocks()
    p.feed(src)
    p.close()
    return p.blocks


def html_title(src: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", src, re.I | re.S)
    return html.unescape(m.group(1).strip()) if m else ""


def parse_text(src: str) -> list[Block]:
    paras = re.split(r"\n\s*\n", src.replace("\r\n", "\n").strip())
    return [Block("p", p.strip()) for p in paras if p.strip()]
