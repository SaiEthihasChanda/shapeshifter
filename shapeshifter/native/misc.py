"""Assorted data kinds: torrents, property lists, .properties/.env, JSON Lines, JSON5."""

from __future__ import annotations

import csv
import io
import json
import plistlib
import re

from ..core import ConversionError
from .registry import register


# ------------------------------------------------------------------- bencode

def bdecode(data: bytes, pos: int = 0):
    c = data[pos:pos + 1]
    if c == b"i":
        end = data.index(b"e", pos)
        return int(data[pos + 1:end]), end + 1
    if c == b"l":
        pos += 1
        out = []
        while data[pos:pos + 1] != b"e":
            v, pos = bdecode(data, pos)
            out.append(v)
        return out, pos + 1
    if c == b"d":
        pos += 1
        out = {}
        while data[pos:pos + 1] != b"e":
            k, pos = bdecode(data, pos)
            v, pos = bdecode(data, pos)
            out[k.decode("utf-8", "replace") if isinstance(k, bytes) else k] = v
        return out, pos + 1
    if c.isdigit():
        colon = data.index(b":", pos)
        n = int(data[pos:colon])
        return data[colon + 1:colon + 1 + n], colon + 1 + n
    raise ConversionError("Corrupt bencoded data")


def _readable(v, key=""):
    if isinstance(v, bytes):
        if key in ("pieces",):
            return f"<{len(v) // 20} SHA-1 piece hashes>"
        try:
            return v.decode("utf-8")
        except UnicodeDecodeError:
            return v.hex()
    if isinstance(v, list):
        return [_readable(x) for x in v]
    if isinstance(v, dict):
        return {k: _readable(x, k) for k, x in v.items()}
    return v


@register("torrent", "json", terminal=True)
def torrent_to_json(data: bytes, src, dst, ctx) -> bytes:
    obj, _ = bdecode(data)
    return json.dumps(_readable(obj), indent=2, ensure_ascii=False).encode()


# ------------------------------------------------------------- property lists

def _plist_safe(v):
    if isinstance(v, bytes):
        return v.hex()
    if isinstance(v, dict):
        return {str(k): _plist_safe(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plist_safe(x) for x in v]
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


@register(["plist", "bplist"], ["json", "yaml"])
def plist_to_data(data: bytes, src, dst, ctx) -> bytes:
    obj = _plist_safe(plistlib.loads(data))
    if dst == "json":
        return json.dumps(obj, indent=2, ensure_ascii=False).encode()
    import yaml
    return yaml.safe_dump(obj, sort_keys=False, allow_unicode=True).encode()


@register("bplist", "plist")
def bplist_to_xml(data: bytes, src, dst, ctx) -> bytes:
    return plistlib.dumps(plistlib.loads(data), fmt=plistlib.FMT_XML)


@register("plist", "bplist")
def xml_to_bplist(data: bytes, src, dst, ctx) -> bytes:
    return plistlib.dumps(plistlib.loads(data), fmt=plistlib.FMT_BINARY)


@register("json", ["plist", "bplist"], first_only=True)
def json_to_plist(data: bytes, src, dst, ctx) -> bytes:
    obj = json.loads(data)
    _no_nulls(obj)
    return plistlib.dumps(obj, fmt=plistlib.FMT_XML if dst == "plist" else plistlib.FMT_BINARY)


def _no_nulls(v):
    if v is None:
        raise ConversionError("Property lists can't hold null values")
    if isinstance(v, dict):
        for x in v.values():
            _no_nulls(x)
    elif isinstance(v, list):
        for x in v:
            _no_nulls(x)


# --------------------------------------------------------- .properties / .env

def _kv_lines(text: str, env: bool) -> dict:
    out = {}
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        while line.endswith("\\") and not env and i + 1 < len(lines):   # continuation
            i += 1
            line = line[:-1] + lines[i].strip()
        i += 1
        if not line or line[0] in "#!":
            continue
        if env and line.startswith("export "):
            line = line[7:]
        m = re.match(r"([^=:\s]+)\s*[=:]\s*(.*)", line) if not env else re.match(r"([\w.-]+)\s*=\s*(.*)", line)
        if m:
            v = m.group(2).strip()
            if env and len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            out[m.group(1)] = v
    return out


@register(["properties", "env"], ["json", "yaml", "toml"])
def kv_to_data(data: bytes, src, dst, ctx) -> bytes:
    obj = _kv_lines(data.decode("utf-8-sig", "replace"), src == "env")
    if dst == "json":
        return json.dumps(obj, indent=2, ensure_ascii=False).encode()
    if dst == "yaml":
        import yaml
        return yaml.safe_dump(obj, sort_keys=False, allow_unicode=True).encode()
    from ..backends.data import _dump_toml
    return _dump_toml(obj).encode()


@register("json", ["properties", "env"], first_only=True)
def data_to_kv(data: bytes, src, dst, ctx) -> bytes:
    obj = json.loads(data)
    if not isinstance(obj, dict):
        raise ConversionError("Needs a JSON object of key/value pairs")
    flat = {}

    def walk(prefix, v):
        if isinstance(v, dict):
            for k, x in v.items():
                walk(f"{prefix}.{k}" if prefix else str(k), x)
        else:
            flat[prefix] = "" if v is None else (json.dumps(v) if isinstance(v, list) else str(v))
    walk("", obj)
    if dst == "env":
        return "".join(f'{re.sub(r"[^A-Za-z0-9_]", "_", k).upper()}="{v}"\n'
                       for k, v in flat.items()).encode()
    return "".join(f"{k}={v}\n" for k, v in flat.items()).encode()


# --------------------------------------------------------------- JSON family

@register("jsonl", ["json", "csv"])
def jsonl_to(data: bytes, src, dst, ctx) -> bytes:
    rows = [json.loads(ln) for ln in data.decode("utf-8-sig").splitlines() if ln.strip()]
    if dst == "json":
        return json.dumps(rows, indent=2, ensure_ascii=False).encode()
    keys: list[str] = []
    for r in rows:
        for k in (r if isinstance(r, dict) else {}):
            if k not in keys:
                keys.append(k)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(keys)
    for r in rows:
        w.writerow([json.dumps(r.get(k)) if isinstance(r.get(k), (dict, list)) else r.get(k, "")
                    for k in keys])
    return buf.getvalue().encode()


@register("json", "jsonl", first_only=True)
def json_to_jsonl(data: bytes, src, dst, ctx) -> bytes:
    obj = json.loads(data)
    rows = obj if isinstance(obj, list) else [obj]
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode()


def strip_json5(text: str) -> str:
    """JSON5/JSONC -> JSON: drop comments, trailing commas; quote bare keys and 'strings'.
    Strings are swapped for placeholders first so the regexes never touch their contents."""
    strings: list[str] = []
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            s = text[i + 1:j]
            strings.append(json.dumps(s.replace("\\'", "'")) if c == "'" else text[i:j + 1])
            out.append(f"\x00{len(strings) - 1}\x00")
            i = j + 1
        elif text.startswith("//", i):
            i = text.find("\n", i) if "\n" in text[i:] else n
        elif text.startswith("/*", i):
            i = text.find("*/", i) + 2 if "*/" in text[i:] else n
        else:
            out.append(c)
            i += 1
    s = "".join(out)
    s = re.sub(r",(\s*[}\]])", r"\1", s)                             # trailing commas
    s = re.sub(r"([{,]\s*)([A-Za-z_$][\w$]*)(\s*:)", r'\1"\2"\3', s)  # bare keys
    return re.sub(r"\x00(\d+)\x00", lambda m: strings[int(m.group(1))], s)


@register("json5", "json")
def json5_to_json(data: bytes, src, dst, ctx) -> bytes:
    obj = json.loads(strip_json5(data.decode("utf-8-sig")))
    return json.dumps(obj, indent=2, ensure_ascii=False).encode()
