"""E-mail (EML / mbox) and web archives (MHTML)."""

from __future__ import annotations

import email
import email.policy
import html
import mailbox
import re
import tempfile
from pathlib import Path

from ..core import ConversionError
from . import docs
from .registry import register


def _parse(data: bytes):
    return email.message_from_bytes(data, policy=email.policy.default)


def _bodies(msg) -> tuple[str, str]:
    """(plain text, html) bodies, either may be empty."""
    plain = htm = ""
    try:
        part = msg.get_body(preferencelist=("plain",))
        plain = part.get_content() if part else ""
        part = msg.get_body(preferencelist=("html",))
        htm = part.get_content() if part else ""
    except Exception:
        pass
    if not plain and not htm and not msg.is_multipart():
        payload = msg.get_payload(decode=True) or b""
        plain = payload.decode(msg.get_content_charset() or "utf-8", "replace")
    return plain, htm


def _headers(msg) -> list[tuple[str, str]]:
    return [(h, str(msg[h])) for h in ("From", "To", "Cc", "Date", "Subject") if msg[h]]


def _attachments(msg) -> list[str]:
    return [p.get_filename() for p in msg.iter_attachments() if p.get_filename()] \
        if msg.is_multipart() else []


def message_text(msg) -> str:
    plain, htm = _bodies(msg)
    if not plain and htm:
        plain = docs.to_text(docs.parse_html(htm))
    head = "\n".join(f"{k}: {v}" for k, v in _headers(msg))
    att = _attachments(msg)
    tail = ("\n\nAttachments: " + ", ".join(att)) if att else ""
    return f"{head}\n\n{plain.strip()}{tail}\n"


@register("eml", "txt")
def eml_to_txt(data: bytes, src, dst, ctx) -> bytes:
    return message_text(_parse(data)).encode("utf-8")


@register("eml", "html")
def eml_to_html(data: bytes, src, dst, ctx) -> bytes:
    msg = _parse(data)
    plain, htm = _bodies(msg)
    rows = "".join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(v)}</td></tr>" for k, v in _headers(msg))
    body = re.sub(r"(?is)<script.*?</script>", "", htm) if htm else \
        f"<pre style='white-space:pre-wrap'>{html.escape(plain)}</pre>"
    title = html.escape(str(msg["Subject"] or "E-mail"))
    return (f'<!DOCTYPE html><html><head><meta charset="utf-8"><title>{title}</title>'
            f"<style>{docs.CSS}</style></head><body><table>{rows}</table><hr>{body}</body></html>").encode()


@register("eml", "md")
def eml_to_md(data: bytes, src, dst, ctx) -> bytes:
    msg = _parse(data)
    plain, htm = _bodies(msg)
    body = docs.to_markdown(docs.parse_html(htm)) if htm and not plain else plain
    head = "\n".join(f"**{k}:** {v}  " for k, v in _headers(msg))
    return f"# {msg['Subject'] or 'E-mail'}\n\n{head}\n\n---\n\n{body.strip()}\n".encode("utf-8")


@register("mhtml", "html")
def mhtml_to_html(data: bytes, src, dst, ctx) -> bytes:
    msg = _parse(data)
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            return part.get_content().encode("utf-8")
    raise ConversionError("The web archive has no HTML page")


@register("mbox", "txt")
def mbox_to_txt(data: bytes, src, dst, ctx) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "box.mbox"
        p.write_bytes(data)
        box = mailbox.mbox(str(p), factory=lambda f: email.message_from_binary_file(
            f, policy=email.policy.default))
        texts = [message_text(m) for m in box]
        box.close()
    if not texts:
        raise ConversionError("The mailbox is empty")
    return ("\n" + "=" * 72 + "\n").join(texts).encode("utf-8")


@register("mbox", "eml", multi=True)
def mbox_to_eml(data: bytes, src, dst, ctx):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "box.mbox"
        p.write_bytes(data)
        box = mailbox.mbox(str(p))
        out = [(f"-{i:03d}", m.as_bytes()) for i, m in enumerate(box, 1)]
        box.close()
    if not out:
        raise ConversionError("The mailbox is empty")
    return out
