"""Jupyter notebooks -> Markdown / HTML / Python script / text."""

from __future__ import annotations

import html
import json

from . import docs
from .registry import register


def _src(cell) -> str:
    s = cell.get("source", "")
    return "".join(s) if isinstance(s, list) else str(s)


def _outputs(cell) -> list[str]:
    out = []
    for o in cell.get("outputs", []):
        if o.get("output_type") == "stream":
            out.append("".join(o.get("text", [])) if isinstance(o.get("text"), list) else o.get("text", ""))
        elif o.get("output_type") in ("execute_result", "display_data"):
            text = o.get("data", {}).get("text/plain")
            if text:
                out.append("".join(text) if isinstance(text, list) else text)
        elif o.get("output_type") == "error":
            out.append(f"{o.get('ename')}: {o.get('evalue')}")
    return out


def _load(data: bytes):
    nb = json.loads(data)
    lang = nb.get("metadata", {}).get("language_info", {}).get("name", "python")
    return nb.get("cells", []), lang


@register("ipynb", "md")
def to_md(data: bytes, src, dst, ctx) -> bytes:
    cells, lang = _load(data)
    parts = []
    for c in cells:
        if c.get("cell_type") == "markdown":
            parts.append(_src(c).strip())
        elif c.get("cell_type") == "code":
            parts.append(f"```{lang}\n{_src(c).rstrip()}\n```")
            for o in _outputs(c):
                parts.append("```\n" + o.rstrip() + "\n```")
    return ("\n\n".join(parts) + "\n").encode()


@register("ipynb", "html")
def to_html(data: bytes, src, dst, ctx) -> bytes:
    cells, lang = _load(data)
    body = []
    for c in cells:
        if c.get("cell_type") == "markdown":
            body.append(docs.to_html(docs.parse_markdown(_src(c))).split("<body>\n", 1)[1]
                        .rsplit("</body>", 1)[0])
        elif c.get("cell_type") == "code":
            body.append(f'<pre><code class="language-{lang}">{html.escape(_src(c))}</code></pre>')
            for o in _outputs(c):
                body.append(f'<pre class="output" style="background:#fff;border-left:3px solid #ccd">'
                            f"{html.escape(o)}</pre>")
    return (f'<!DOCTYPE html><html><head><meta charset="utf-8"><title>Notebook</title>'
            f"<style>{docs.CSS}</style></head><body>{''.join(body)}</body></html>").encode()


@register("ipynb", ["py", "txt"])
def to_script(data: bytes, src, dst, ctx) -> bytes:
    cells, _ = _load(data)
    parts = []
    for c in cells:
        if c.get("cell_type") == "code":
            parts.append(_src(c).rstrip())
        elif c.get("cell_type") == "markdown" and dst == "py":
            parts.append("\n".join("# " + ln for ln in _src(c).splitlines()))
        elif dst == "txt":
            parts.append(_src(c).rstrip())
    return ("\n\n".join(parts) + "\n").encode()
