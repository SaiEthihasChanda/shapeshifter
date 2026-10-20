"""Calendars (iCalendar) and contacts (vCard) <-> CSV / JSON."""

from __future__ import annotations

import csv
import io
import json
import re

from ..core import ConversionError
from .registry import register


def _unfold(text: str) -> list[str]:
    return re.sub(r"\r?\n[ \t]", "", text).splitlines()


def _unescape(v: str) -> str:
    return v.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";") \
        .replace("\\\\", "\\")


def _escape(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def read_components(text: str, kind: str) -> list[dict]:
    """Every BEGIN:<kind> … END:<kind> block as {PROPERTY: value}."""
    items, cur = [], None
    for line in _unfold(text):
        if line.upper() == f"BEGIN:{kind}":
            cur = {}
        elif line.upper() == f"END:{kind}" and cur is not None:
            items.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            key, val = line.split(":", 1)
            name = key.split(";", 1)[0].upper()
            val = _unescape(val)
            cur[name] = f"{cur[name]}; {val}" if name in cur else val
    return items


EVENT_FIELDS = [("SUMMARY", "summary"), ("DTSTART", "start"), ("DTEND", "end"),
                ("LOCATION", "location"), ("DESCRIPTION", "description"), ("UID", "uid")]
CONTACT_FIELDS = [("FN", "name"), ("EMAIL", "email"), ("TEL", "phone"), ("ORG", "organization"),
                  ("TITLE", "title"), ("ADR", "address"), ("URL", "url"), ("NOTE", "note")]


def _rows(items, fields):
    return [{label: it.get(key, "") for key, label in fields} for it in items]


def _table(rows: list[dict], dst: str) -> bytes:
    if dst == "json":
        return json.dumps(rows, indent=2, ensure_ascii=False).encode()
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _read_rows(data: bytes, src: str) -> list[dict]:
    text = data.decode("utf-8-sig", "replace")
    if src == "json":
        obj = json.loads(text)
        return obj if isinstance(obj, list) else [obj]
    return list(csv.DictReader(io.StringIO(text)))


def _pick(row: dict, *names) -> str:
    low = {k.lower().strip(): v for k, v in row.items() if k}
    for n in names:
        if low.get(n):
            return str(low[n])
    return ""


@register("ics", ["csv", "json"])
def ics_to_table(data: bytes, src, dst, ctx) -> bytes:
    events = read_components(data.decode("utf-8-sig", "replace"), "VEVENT")
    if not events:
        raise ConversionError("No events found")
    return _table(_rows(events, EVENT_FIELDS), dst)


@register("vcf", ["csv", "json"])
def vcf_to_table(data: bytes, src, dst, ctx) -> bytes:
    cards = read_components(data.decode("utf-8-sig", "replace"), "VCARD")
    if not cards:
        raise ConversionError("No contacts found")
    return _table(_rows(cards, CONTACT_FIELDS), dst)


@register(["csv", "json"], "vcf", first_only=True)
def table_to_vcf(data: bytes, src, dst, ctx) -> bytes:
    out = []
    for r in _read_rows(data, src):
        name = _pick(r, "name", "full name", "fn", "display name")
        if not name:
            continue
        lines = ["BEGIN:VCARD", "VERSION:3.0", f"FN:{_escape(name)}", f"N:{_escape(name)};;;;"]
        for label, prop in (("email", "EMAIL"), ("phone", "TEL"), ("organization", "ORG"),
                            ("title", "TITLE"), ("url", "URL"), ("note", "NOTE")):
            v = _pick(r, label, {"phone": "telephone", "organization": "company"}.get(label, label))
            if v:
                lines.append(f"{prop}:{_escape(v)}")
        lines.append("END:VCARD")
        out.append("\r\n".join(lines))
    if not out:
        raise ConversionError("No rows with a 'name' column")
    return ("\r\n".join(out) + "\r\n").encode()


@register(["csv", "json"], "ics", first_only=True)
def table_to_ics(data: bytes, src, dst, ctx) -> bytes:
    import uuid
    events = []
    for r in _read_rows(data, src):
        start = re.sub(r"[-:]", "", _pick(r, "start", "dtstart", "date"))
        summary = _pick(r, "summary", "title", "subject", "name")
        if not start or not summary:
            continue
        lines = ["BEGIN:VEVENT", f"UID:{_pick(r, 'uid') or uuid.uuid4()}", f"SUMMARY:{_escape(summary)}",
                 f"DTSTART:{start}"]
        end = re.sub(r"[-:]", "", _pick(r, "end", "dtend"))
        if end:
            lines.append(f"DTEND:{end}")
        for label, prop in (("location", "LOCATION"), ("description", "DESCRIPTION")):
            if _pick(r, label):
                lines.append(f"{prop}:{_escape(_pick(r, label))}")
        lines.append("END:VEVENT")
        events.append("\r\n".join(lines))
    if not events:
        raise ConversionError("No rows with 'summary' and 'start' columns")
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//ShapeShifter//EN\r\n"
            + "\r\n".join(events) + "\r\nEND:VCALENDAR\r\n").encode()
