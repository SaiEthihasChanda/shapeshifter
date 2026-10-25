"""User-described formats: teach ShapeShifter a format without writing code.

Each file in %APPDATA%\\ShapeShifter\\formats\\*.yaml (or .json) describes one format:

    id: slog                       # short id (letters/digits)
    name: Sensor log
    extensions: [slog]
    category: data                 # image | audio | video | document | data | archive
    match:                         # any rule matching identifies the file
      - magic: "534C4F47"          #   hex bytes at `offset` (default 0)
        offset: 0
      - ascii: "SLOG"              #   text at `offset`
      - text_regex: "^#SENSOR"     #   regex against the first 4 KB as text
    decode:                        # how to read it (one of):
      type: alias                  #   it *is* another format:            as: png
      type: embedded               #   another format after a header:     as: png, offset: 16
                                   #     (or search: "89504E47" to find where it starts)
      type: records                #   fixed-size binary records -> table:
                                   #     header: 16, endian: little,
                                   #     fields: [{name: t, type: u32}, {name: v, type: f32},
                                   #              {name: tag, type: str, size: 8}]
      type: text-table             #   text lines -> table:
                                   #     delimiter: ";"  or  regex: '(?P<t>\\d+) (?P<v>\\S+)'
                                   #     skip: 1, header: true

Once loaded, the format is identified by content and gets every conversion reachable
from what it decodes to (e.g. an embedded PNG gets all image conversions).
"""

from __future__ import annotations

import json
import logging
import re
import struct
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .core import Backend, Context, ConversionError
from .config import CONFIG_DIR

log = logging.getLogger(__name__)

FORMATS_DIR = CONFIG_DIR / "formats"
TABLE_TARGETS = {"csv", "tsv", "json", "yaml", "xlsx", "toml", "xml"}
FIELD_TYPES = {"u8": "B", "i8": "b", "u16": "H", "i16": "h", "u32": "I", "i32": "i",
               "u64": "Q", "i64": "q", "f32": "f", "f64": "d", "bool": "?"}


@dataclass
class Spec:
    id: str
    name: str
    extensions: list[str]
    category: str
    match: list[dict]
    decode: dict
    path: Path
    error: str | None = None
    struct_fmt: str = ""
    field_names: list[str] = field(default_factory=list)

    @property
    def kind(self) -> str:
        return self.decode.get("type", "")


def _load_file(p: Path) -> dict:
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".json":
        return json.loads(text)
    import yaml
    return yaml.safe_load(text) or {}


def _validate(raw: dict, p: Path) -> Spec:
    from .formats import CATEGORIES
    fid = str(raw.get("id", p.stem)).lower()
    if not re.fullmatch(r"[a-z0-9_]{1,16}", fid):
        raise ValueError("id must be 1–16 letters, digits or _")
    category = raw.get("category", "data")
    if category not in CATEGORIES or category == "program":
        raise ValueError(f"category must be one of image, audio, video, document, data, archive")
    match = raw.get("match") or []
    if isinstance(match, dict):
        match = [match]
    exts = [str(e).lower().lstrip(".") for e in raw.get("extensions", [])]
    if not match and not exts:
        raise ValueError("give at least one match rule or extension")
    for m in match:
        if not any(k in m for k in ("magic", "ascii", "text_regex")):
            raise ValueError("each match rule needs magic, ascii or text_regex")
        if "magic" in m:
            bytes.fromhex(str(m["magic"]).replace(" ", ""))
        if "text_regex" in m:
            re.compile(m["text_regex"], re.M)
    decode = raw.get("decode") or {}
    spec = Spec(fid, str(raw.get("name", fid.upper() + " file")), exts, category, match, decode, p)
    kind = decode.get("type")
    if kind in ("alias", "embedded"):
        if not decode.get("as"):
            raise ValueError("decode.as is required (the format inside)")
        if kind == "embedded" and "offset" not in decode and "search" not in decode:
            raise ValueError("embedded needs offset or search")
    elif kind == "records":
        endian = {"little": "<", "big": ">"}[decode.get("endian", "little")]
        fmt = endian
        for f in decode.get("fields", []):
            t = f.get("type")
            if t == "str":
                fmt += f"{int(f.get('size', 1))}s"
            elif t in FIELD_TYPES:
                fmt += FIELD_TYPES[t]
            elif t == "pad":
                fmt += f"{int(f.get('size', 1))}x"
                continue
            else:
                raise ValueError(f"unknown field type {t!r}")
            spec.field_names.append(str(f.get("name", f"field{len(spec.field_names) + 1}")))
        if not spec.field_names:
            raise ValueError("records need at least one field")
        spec.struct_fmt = fmt
    elif kind == "text-table":
        if "regex" in decode:
            re.compile(decode["regex"])
    else:
        raise ValueError("decode.type must be alias, embedded, records or text-table")
    return spec


@lru_cache(maxsize=1)
def load_specs() -> tuple[Spec, ...]:
    specs = []
    if FORMATS_DIR.exists():
        for p in sorted(FORMATS_DIR.glob("*")):
            if p.suffix.lower() not in (".yaml", ".yml", ".json"):
                continue
            try:
                specs.append(_validate(_load_file(p), p))
            except Exception as e:
                specs.append(Spec(p.stem.lower(), p.stem, [], "data", [], {}, p, error=str(e)))
    _register(specs)
    return tuple(specs)


def valid_specs() -> list[Spec]:
    return [s for s in load_specs() if not s.error]


_registered: list[tuple[dict, str]] = []   # (table, key) we added, so reload can undo them


def _register(specs):
    from . import formats
    for s in specs:
        if s.error:
            continue
        for table, key, value in ((formats.CATEGORY_OF, s.id, s.category),
                                  (formats.NAMES, s.id, s.name)):
            if key not in table:
                table[key] = value
                _registered.append((table, key))
        for e in s.extensions:
            if e != s.id and e not in formats.CATEGORY_OF and e not in formats.ALIASES:
                formats.ALIASES[e] = s.id
                _registered.append((formats.ALIASES, e))


def _unregister():
    while _registered:
        table, key = _registered.pop()
        table.pop(key, None)


def reload():
    _unregister()
    load_specs.cache_clear()
    from . import identify, planner
    planner.reset()
    identify.clear_cache()
    load_specs()


# ------------------------------------------------------------- identification

def probe(head: bytes, ext_fmt: str) -> list:
    from .identify import Candidate
    out = []
    text = None
    for s in valid_specs():
        for m in s.match:
            off = int(m.get("offset", 0))
            if "magic" in m:
                magic = bytes.fromhex(str(m["magic"]).replace(" ", ""))
                if head[off:off + len(magic)] == magic:
                    out.append(Candidate(s.id, 0.97, f"matches your format description ({s.path.name})"))
                    break
            elif "ascii" in m:
                magic = str(m["ascii"]).encode("latin-1")
                if head[off:off + len(magic)] == magic:
                    out.append(Candidate(s.id, 0.97, f"matches your format description ({s.path.name})"))
                    break
            elif "text_regex" in m:
                if text is None:
                    text = head[:4096].decode("utf-8", "replace")
                if re.search(m["text_regex"], text, re.M):
                    out.append(Candidate(s.id, 0.9, f"text matches your description ({s.path.name})"))
                    break
        else:
            if ext_fmt == s.id:
                out.append(Candidate(s.id, 0.6, f"by its extension ({s.path.name})"))
    return out


# --------------------------------------------------------------------- engine

class CustomFormatBackend(Backend):
    name = "custom"
    base_cost = 0.5

    def sources(self):
        return {s.id for s in valid_specs()}

    def _spec(self, fid: str) -> Spec:
        for s in valid_specs():
            if s.id == fid:
                return s
        raise ConversionError(f"Unknown custom format {fid}")

    def targets(self, src):
        try:
            s = self._spec(src)
        except ConversionError:
            return set()
        if s.kind in ("alias", "embedded"):
            return {s.decode["as"]}
        return set(TABLE_TARGETS)

    def convert(self, src: Path, dst: Path, ctx: Context) -> list[Path]:
        s = self._spec(ctx.src_fmt)
        data = src.read_bytes()
        if s.kind == "alias":
            dst.write_bytes(data)
        elif s.kind == "embedded":
            if "search" in s.decode:
                needle = bytes.fromhex(str(s.decode["search"]).replace(" ", ""))
                start = data.find(needle, int(s.decode.get("offset", 0)))
                if start < 0:
                    raise ConversionError(f"The embedded {s.decode['as'].upper()} wasn't found.")
            else:
                start = int(s.decode["offset"])
            end = s.decode.get("length")
            dst.write_bytes(data[start:start + int(end)] if end else data[start:])
        else:
            from .backends import data as databk
            rows = self._records(s, data) if s.kind == "records" else self._text_table(s, data)
            t = ctx.dst_fmt
            if t in databk.TABLE:
                databk._write_table(dst, t, {s.name: rows})
            else:
                databk._write_tree(dst, t, databk._records(rows))
        ctx.progress(1.0)
        return [dst]

    @staticmethod
    def _records(s: Spec, data: bytes) -> list[list]:
        size = struct.calcsize(s.struct_fmt)
        body = data[int(s.decode.get("header", 0)):]
        count = int(s.decode.get("count", 0)) or len(body) // size
        rows = [list(s.field_names)]
        for i in range(min(count, len(body) // size)):
            vals = struct.unpack_from(s.struct_fmt, body, i * size)
            rows.append([v.split(b"\0", 1)[0].decode("utf-8", "replace") if isinstance(v, bytes)
                         else round(v, 6) if isinstance(v, float) else v for v in vals])
        return rows

    @staticmethod
    def _text_table(s: Spec, data: bytes) -> list[list]:
        from .backends.data import _infer
        lines = data.decode(s.decode.get("encoding", "utf-8"), "replace").splitlines()
        lines = [ln for ln in lines[int(s.decode.get("skip", 0)):] if ln.strip()]
        rx = s.decode.get("regex")
        if rx:
            pat = re.compile(rx)
            names = list(pat.groupindex) or ["value"]
            rows = [names]
            for ln in lines:
                m = pat.search(ln)
                if m:
                    rows.append([_infer(v or "") for v in (m.groups() if not pat.groupindex
                                                           else [m.group(n) for n in names])])
            return rows
        delim = s.decode.get("delimiter", ",")
        rows = [[_infer(c.strip()) for c in ln.split(delim)] for ln in lines]
        if not s.decode.get("header", True) and rows:
            rows.insert(0, [f"column{i + 1}" for i in range(len(rows[0]))])
        return rows


# ------------------------------------------------------------------- template

TEMPLATE = """\
# ShapeShifter format description. Edit, save, then press "Reload" in
# Settings > Advanced (or restart the tray app).
# Reference: every option is explained at the top of shapeshifter/custom.py.

id: {id}
name: {name}
extensions: [{ext}]
category: data            # image | audio | video | document | data | archive

match:
  - magic: "{magic}"      # first bytes of the sample file ({sample})
    offset: 0

decode:
  # Pick ONE of these and delete the others.

  # 1) The file is really another format (just renamed):
  # type: alias
  # as: png

  # 2) Another format is embedded after a header:
  # type: embedded
  # as: png
  # search: "89504E470D0A1A0A"   # or: offset: 16

  # 3) Fixed-size binary records -> a table (CSV, JSON, XLSX, ...):
  type: records
  header: 0               # bytes to skip before the first record
  endian: little
  fields:
    - {{name: value, type: u32}}   # u8 i8 u16 i16 u32 i32 u64 i64 f32 f64 bool str pad

  # 4) Text lines -> a table:
  # type: text-table
  # delimiter: ";"           # or regex: '(?P<time>\\d+)\\s+(?P<value>\\S+)'
  # skip: 0
  # header: true
"""


def create_template(sample: str | Path | None = None) -> Path:
    FORMATS_DIR.mkdir(parents=True, exist_ok=True)
    ext, magic, name = "myfmt", "00000000", "My format"
    if sample:
        p = Path(sample)
        ext = (p.suffix.lstrip(".").lower() or "myfmt")[:16]
        ext = re.sub(r"[^a-z0-9_]", "_", ext) or "myfmt"
        with open(p, "rb") as f:
            magic = f.read(8).hex().upper() or "00"
        name = f"{ext.upper()} file"
    fid = ext
    target = FORMATS_DIR / f"{fid}.yaml"
    i = 2
    while target.exists():
        target = FORMATS_DIR / f"{fid}{i}.yaml"
        i += 1
    target.write_text(TEMPLATE.format(id=target.stem, name=name, ext=ext, magic=magic,
                                      sample=Path(sample).name if sample else "none"),
                      encoding="utf-8")
    return target
