"""Source code and plain-text kinds -> HTML / Markdown / PDF (rendered as code)."""

from __future__ import annotations

import html

from .. import catalog
from . import docs, pdfwrite
from .registry import register

PLAIN_KINDS = {"log", "nfo", "env", "properties", "gitignore", "editorconfig", "adoc", "org",
               "textile", "wiki"}
CODE = catalog.code_formats()
# LaTeX/BibTeX get real document conversions from pandoc instead of a code listing.
SOURCES = sorted((set(CODE) | PLAIN_KINDS) - {"tex", "bib", "ipynb_src"})


def _text(data: bytes, src: str) -> str:
    if src == "nfo":
        return data.decode("cp437", "replace")       # NFO art uses the DOS code page
    return data.decode("utf-8-sig", "replace")


@register(SOURCES, "html")
def code_to_html(data: bytes, src, dst, ctx) -> bytes:
    lang = CODE.get(src, "text")
    body = f'<pre><code class="language-{lang}">{html.escape(_text(data, src))}</code></pre>'
    title = html.escape(ctx.title if getattr(ctx, "title", "") else catalog.CATALOG[src].name
                        if src in catalog.CATALOG else src)
    return (f'<!DOCTYPE html>\n<html><head><meta charset="utf-8"><title>{title}</title>'
            f"<style>{docs.CSS}pre{{white-space:pre-wrap}}</style></head><body>\n{body}\n"
            "</body></html>\n").encode()


@register(SOURCES, "md")
def code_to_md(data: bytes, src, dst, ctx) -> bytes:
    text = _text(data, src)
    fence = "````" if "```" in text else "```"
    return f"{fence}{CODE.get(src, '')}\n{text.rstrip()}\n{fence}\n".encode()


@register(SOURCES, "pdf", terminal=True)
def code_to_pdf(data: bytes, src, dst, ctx) -> bytes:
    return pdfwrite.document_to_pdf([("code", _text(data, src).expandtabs(4))])
