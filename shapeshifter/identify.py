"""Work out what a file is from its contents, not its name.

Pipeline (see docs/PLAN-universal-conversion.md §2.1):
  1. magic signatures         4. ffprobe (if installed) for audio/video
  2. container probes         5. Magika ML model (optional, last resort)
  3. text probes
The extension is only a tie-breaker; disagreements are reported as a *mismatch*.
Also decides whether a file is too risky to touch (programs, scripts, macros, …).
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import zipfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .formats import CATEGORY_OF, ext_of, name_of

log = logging.getLogger(__name__)

HEAD = 64 * 1024

# Extensions we never process, whatever they contain.
RISKY_EXTS = {"exe", "dll", "msi", "com", "scr", "sys", "bat", "cmd", "ps1", "psm1", "vbs", "vbe",
              "js", "jse", "wsf", "wsh", "hta", "cpl", "lnk", "reg", "msc", "jar", "apk", "sh",
              "appx", "msix", "inf", "pif", "url"}

# Formats that are interchangeable enough that a mismatch isn't worth flagging.
# (Plain .txt is deliberately absent: a .txt holding CSV/JSON should say so.)
EQUIVALENT = [{"mp4", "m4v", "mov", "m4a", "3gp", "m4b", "f4v", "3g2", "qt"}, {"xps", "oxps"},
              {"zip", "cbz", "whl", "nupkg", "vsix", "xpi", "jar", "apk"},
              {"docx", "dotx"}, {"xlsx", "xltx"}, {"pptx", "potx"},
              {"md", "rst"}, {"ini", "toml", "properties", "editorconfig", "env"},
              {"xml", "svg", "html", "xhtml", "rss", "atom", "kml", "gpx", "plist", "dae", "xsd",
               "xsl", "xliff", "musicxml", "drawio", "csproj", "resx"},
              {"json", "geojson", "topojson", "ipynb", "har", "webmanifest", "sarif", "gltf"},
              {"tiff", "dng", "cr2", "nef", "arw"}, {"ogg", "ogv", "opus", "oga", "spx", "ogm"},
              {"wmv", "wma", "asf"}, {"mpg", "ts", "mts", "vob", "m2v"}, {"pem", "der"},
              {"mkv", "mka", "webm", "weba"}, {"ttf", "otf"}, {"pdf", "ai"}, {"ps", "eps"},
              {"ico", "cur"}, {"png", "apng"}, {"bmp", "dib"}, {"avi", "divx"},
              {"eml", "mhtml", "mbox"}]
# Equivalent for mismatch purposes, but the bytes differ: always use the content's name.
DISTINCT_ENCODINGS = [{"pem", "der"}, {"ttf", "otf"}, {"eml", "mbox"}, {"eml", "mhtml"},
                      {"ps", "eps"}]


@dataclass
class Candidate:
    fmt: str
    confidence: float
    evidence: str


@dataclass
class Identification:
    path: str
    fmt: str | None = None               # best guess (None = unknown)
    confidence: float = 0.0
    ext_fmt: str = ""                    # what the extension claims
    mismatch: bool = False               # content and extension disagree
    candidates: list[Candidate] = field(default_factory=list)
    risky: str | None = None             # reason we refuse to touch the file
    details: dict = field(default_factory=dict)

    @property
    def summary(self) -> str:
        if self.risky:
            return f"Not converted: {self.risky}"
        if not self.fmt:
            return self.details.get("guess", "Unknown file type")
        s = name_of(self.fmt)
        if self.mismatch:
            s += f" (named .{self.ext_fmt})" if self.ext_fmt else " (no extension)"
        elif self.confidence < 0.6:
            s = "Probably " + s[0].lower() + s[1:]
        return s

    @property
    def needs_rename(self) -> bool:
        return bool(self.fmt) and self.confidence >= 0.8 and (self.mismatch or not self.ext_fmt)


# ------------------------------------------------------------------ signatures

def _sig(data: bytes, offset: int, magic: bytes) -> bool:
    return data[offset:offset + len(magic)] == magic


SIGNATURES: list[tuple[str, int, bytes, float]] = [
    ("png", 0, b"\x89PNG\r\n\x1a\n", 1.0),
    ("jpg", 0, b"\xff\xd8\xff", 0.95),
    ("gif", 0, b"GIF87a", 1.0), ("gif", 0, b"GIF89a", 1.0),
    ("bmp", 0, b"BM", 0.6),
    ("tiff", 0, b"II*\x00", 0.9), ("tiff", 0, b"MM\x00*", 0.9),
    ("ico", 0, b"\x00\x00\x01\x00", 0.7),
    ("icns", 0, b"icns", 0.95),
    ("psd", 0, b"8BPS", 0.95),
    ("dds", 0, b"DDS ", 0.95),
    ("qoi", 0, b"qoif", 0.95),
    ("jp2", 0, b"\x00\x00\x00\x0cjP  \r\n\x87\n", 1.0),
    ("pdf", 0, b"%PDF-", 1.0),
    ("flac", 0, b"fLaC", 1.0),
    ("mp3", 0, b"ID3", 0.9),
    ("flv", 0, b"FLV\x01", 1.0),
    ("mpg", 0, b"\x00\x00\x01\xba", 0.9),
    ("wmv", 0, b"\x30\x26\xb2\x75\x8e\x66\xcf\x11", 0.9),   # ASF: wmv or wma
    ("amr", 0, b"#!AMR", 1.0),
    ("ac3", 0, b"\x0b\x77", 0.5),
    ("7z", 0, b"7z\xbc\xaf\x27\x1c", 1.0),
    ("rar", 0, b"Rar!\x1a\x07", 1.0),
    ("bz2", 0, b"BZh", 0.8),
    ("xz", 0, b"\xfd7zXZ\x00", 1.0),
    ("sqlite", 0, b"SQLite format 3\x00", 1.0),
    ("rtf", 0, b"{\\rtf", 1.0),
    ("doc", 0, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", 0.7),   # OLE2: doc/xls/ppt/msi
    ("exe", 0, b"MZ", 0.95),
    ("elf", 0, b"\x7fELF", 1.0),
    ("macho", 0, b"\xcf\xfa\xed\xfe", 1.0), ("macho", 0, b"\xca\xfe\xba\xbe", 0.8),
    ("lnk", 0, b"L\x00\x00\x00\x01\x14\x02\x00", 1.0),
]


def _probe_signatures(head: bytes, ext_fmt: str, size: int = 0) -> list[Candidate]:
    from . import catalog
    out = []
    for fmt, off, magic, conf in SIGNATURES + catalog.signatures():
        if _sig(head, off, magic):
            where = "starts with" if off == 0 else f"has at offset {off}"
            out.append(Candidate(fmt, conf, f"{where} the {name_of(fmt)} signature"))
    # PLY 3D models start with a text header even when the body is binary.
    if re.match(rb"ply\r?\nformat (ascii|binary_little_endian|binary_big_endian) ", head):
        out.append(Candidate("ply", 0.98, "PLY header"))
    # DER (ASN.1): a SEQUENCE whose length covers exactly the whole file.
    if len(head) > 4 and head[0] == 0x30 and head[1] in (0x81, 0x82, 0x83):
        n = head[1] & 0x7F
        length = int.from_bytes(head[2:2 + n], "big")
        if size and length + 2 + n == size:
            from .native import certs
            kind = certs.der_kind(head if size <= len(head) else b"")
            out.append(Candidate("der", 0.9, f"ASN.1 DER structure{f' ({kind})' if kind else ''}"))
    # MP3 without ID3 / AAC ADTS: frame sync
    if len(head) > 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        if (head[1] & 0x06) == 0:
            out.append(Candidate("aac", 0.7, "starts with an AAC (ADTS) frame header"))
        elif not _sig(head, 0, b"\xff\xd8"):
            out.append(Candidate("mp3", 0.7, "starts with an MPEG audio frame header"))
    # MPEG transport stream: sync byte every 188 bytes
    if len(head) >= 188 * 4 and all(head[i * 188] == 0x47 for i in range(4)):
        out.append(Candidate("ts", 0.9, "MPEG-TS packets (0x47 every 188 bytes)"))
    # Netpbm: P1..P6 followed by whitespace and a number
    if re.match(rb"P[1-6][ \t\r\n]+(#[^\n]*\n\s*)*\d", head[:64]):
        out.append(Candidate("ppm", 0.85, "Netpbm header (P1–P6)"))
    # MOBI: "BOOKMOBI" at offset 60
    if _sig(head, 60, b"BOOKMOBI"):
        out.append(Candidate("mobi", 1.0, "Mobipocket header at offset 60"))
    # TAR: "ustar" at offset 257
    if _sig(head, 257, b"ustar"):
        out.append(Candidate("tar", 1.0, "TAR header (ustar)"))
    # ASF: audio-only files are WMA
    for c in out:
        if c.fmt == "wmv" and ext_fmt == "wma":
            c.fmt = "wma"
    # OLE2 compound files: decide by extension (doc/xls/ppt/msi share the container)
    for c in out:
        if c.fmt == "doc" and ext_fmt in ("xls", "ppt", "msi"):
            c.fmt = ext_fmt
    return out


# ------------------------------------------------------------------ containers

BMFF_BRANDS = {
    "heic": "heic", "heix": "heic", "hevc": "heic", "heim": "heic", "heis": "heic",
    "mif1": "heic", "msf1": "heic", "avif": "avif", "avis": "avif",
    "qt  ": "mov", "M4A ": "m4a", "M4B ": "m4a", "M4V ": "m4v", "M4VH": "m4v",
    "3gp4": "3gp", "3gp5": "3gp", "3gp6": "3gp", "3g2a": "3gp",
}


def _probe_containers(path: Path, head: bytes, ext_fmt: str) -> list[Candidate]:
    out = []
    # RIFF
    if _sig(head, 0, b"RIFF") and len(head) >= 12:
        kind = head[8:12]
        fmt = {b"WAVE": "wav", b"AVI ": "avi", b"WEBP": "webp"}.get(kind)
        if fmt:
            out.append(Candidate(fmt, 1.0, f"RIFF container of type {kind.decode()!r}"))
    if _sig(head, 0, b"FORM") and head[8:12] in (b"AIFF", b"AIFC"):
        out.append(Candidate("aiff", 1.0, "IFF container of type AIFF"))
    # ISO base media (MP4 family): ftyp box
    if _sig(head, 4, b"ftyp") and len(head) >= 12:
        brand = head[8:12].decode("latin-1")
        compat = head[16:64].decode("latin-1")
        fmt = BMFF_BRANDS.get(brand)
        if not fmt and ("avif" in compat):
            fmt = "avif"
        if not fmt and any(b in compat for b in ("heic", "mif1")):
            fmt = "heic"
        fmt = fmt or "mp4"
        out.append(Candidate(fmt, 0.95, f"ISO media file, brand {brand.strip()!r}"))
    # Matroska / WebM
    if _sig(head, 0, b"\x1a\x45\xdf\xa3"):
        fmt = "webm" if b"webm" in head[:64] else "mkv"
        out.append(Candidate(fmt, 0.95, "EBML (Matroska) header, doc type " + fmt))
    # Ogg: codec in the first packet
    if _sig(head, 0, b"OggS"):
        fmt = "opus" if b"OpusHead" in head[:256] else "ogv" if b"theora" in head[:256] else "ogg"
        out.append(Candidate(fmt, 0.95, f"Ogg container ({fmt})"))
    # Gzip: look inside
    if _sig(head, 0, b"\x1f\x8b"):
        out.extend(_probe_gzip(path))
    # ZIP family
    if _sig(head, 0, b"PK\x03\x04") or _sig(head, 0, b"PK\x05\x06"):
        out.extend(_probe_zip(path, ext_fmt))
    return out


def _probe_gzip(path: Path) -> list[Candidate]:
    import gzip
    try:
        with gzip.open(path, "rb") as f:
            inner = f.read(1024)
    except Exception:
        return [Candidate("gz", 0.8, "gzip header, but the data is damaged")]
    if _sig(inner, 257, b"ustar"):
        return [Candidate("tgz", 1.0, "gzip-compressed TAR archive")]
    return [Candidate("gz", 0.95, "gzip-compressed file")]


ODF_MIMES = {
    "application/epub+zip": "epub",
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
    "application/vnd.oasis.opendocument.presentation": "odp",
}
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")


def _probe_zip(path: Path, ext_fmt: str) -> list[Candidate]:
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            mimetype = ""
            if "mimetype" in names:
                mimetype = zf.read("mimetype")[:100].decode("ascii", "ignore").strip()
    except Exception:
        return [Candidate("zip", 0.6, "ZIP signature, but the archive index is unreadable")]
    lower = [n.lower() for n in names]
    has = lambda prefix: any(n.startswith(prefix) for n in lower)  # noqa: E731
    macros = any(n.endswith("vbaproject.bin") for n in lower)
    details = f"ZIP archive with {len(names)} entries"
    if mimetype in ODF_MIMES:
        return [Candidate(ODF_MIMES[mimetype], 1.0, f"ZIP with mimetype {mimetype!r}")]
    if "[content_types].xml" in lower:
        if has("word/"):
            return [Candidate("docm" if macros else "docx", 1.0, "Office Open XML (word/)")]
        if has("xl/"):
            return [Candidate("xlsm" if macros else "xlsx", 1.0, "Office Open XML (xl/)")]
        if has("ppt/"):
            return [Candidate("pptx", 1.0, "Office Open XML (ppt/)")]
    if any(n.endswith(".fdseq") for n in lower) or has("documents/1/pages/"):
        return [Candidate("oxps" if ext_fmt == "oxps" else "xps", 1.0, "XPS document structure")]
    if "androidmanifest.xml" in lower:
        return [Candidate("apk", 1.0, "Android package (AndroidManifest.xml)")]
    if "meta-inf/manifest.mf" in lower and any(n.endswith(".class") for n in lower):
        return [Candidate("jar", 1.0, "Java archive (.class files + manifest)")]
    files = [n for n in lower if not n.endswith("/")]
    if files and all(n.endswith(IMAGE_EXTS) or n.endswith(".xml") for n in files) \
            and sum(n.endswith(IMAGE_EXTS) for n in files) >= 2 and ext_fmt == "cbz":
        return [Candidate("cbz", 1.0, f"ZIP of {len(files)} images (comic book)")]
    return [Candidate("zip", 0.95, details)]


# ------------------------------------------------------------------------ text

def _decode_text(data: bytes) -> str | None:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            return None
    if b"\x00" in data[:4096]:
        return None
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return None
    sample = text[:4096]
    printable = sum(ch.isprintable() or ch in "\r\n\t" for ch in sample)
    return text if sample and printable / len(sample) > 0.95 else None


XML_ROOTS = {"svg": "svg", "html": "html", "fictionbook": "fb2", "rss": "rss", "feed": "atom",
             "kml": "kml", "gpx": "gpx", "plist": "plist", "collada": "dae", "xliff": "xliff",
             "schema": "xsd", "stylesheet": "xsl", "transform": "xsl", "score-partwise": "musicxml",
             "score-timewise": "musicxml", "mxfile": "drawio", "project": "csproj", "root": "xml",
             "x3d": "xml"}

# (format, regex on the first 4 KB, confidence, evidence); checked before generic sniffing.
TEXT_MARKERS = [
    ("pgp", r"\A-----BEGIN PGP [A-Z ]+-----", 0.98, "PGP armor header"),
    ("pem", r"\A-----BEGIN [A-Z0-9 ]+-----", 0.98, "PEM -----BEGIN----- header"),
    ("pem", r"^-----BEGIN [A-Z0-9 ]+-----\r?\n[A-Za-z0-9+/=\r\n:, -]+-----END ", 0.9,
     "PEM block"),
    ("sshpub", r"\A(ssh-(rsa|ed25519|dss)|ecdsa-sha2-nistp\d+|sk-ssh-\S+) AAAA", 0.95,
     "OpenSSH public key"),
    ("ics", r"\ABEGIN:VCALENDAR", 0.98, "BEGIN:VCALENDAR"),
    ("vcf", r"\ABEGIN:VCARD", 0.98, "BEGIN:VCARD"),
    ("vtt", r"\AWEBVTT", 0.98, "WEBVTT header"),
    ("ass", r"\A\[Script Info\]", 0.95, "[Script Info] section"),
    ("srt", r"\A\d+\s*\r?\n\d{1,2}:\d\d:\d\d[,.]\d{3} --> \d{1,2}:\d\d:\d\d[,.]\d{3}", 0.95,
     "numbered cues with --> timings"),
    ("sbv", r"\A\d+:\d\d:\d\d\.\d{3},\d+:\d\d:\d\d\.\d{3}\r?\n", 0.9, "SBV cue timings"),
    ("lrc", r"\A(\[(ar|ti|al|by|length|offset):[^\]]*\]\s*)*\[\d\d:\d\d[.:]\d\d\]", 0.85,
     "[mm:ss.xx] lyric timestamps"),
    ("mbox", r"\AFrom \S+ +\w{3} \w{3} +\d+ ", 0.9, "mbox 'From ' separator"),
    ("mhtml", r"(?is)\A(From|Subject|Date|MIME-Version|Snapshot-Content-Location):.*?"
              r"Content-Type: multipart/related", 0.95, "MIME multipart/related web archive"),
    ("eml", r"(?im)\A((Received|Return-Path|Delivered-To|From|To|Date|Message-ID|MIME-Version|"
            r"X-[\w-]+|Subject|Content-Type):.*\r?\n([ \t].*\r?\n)*){3,}", 0.88, "e-mail headers"),
    ("stl", r"\Asolid\b.*\r?\n\s*facet normal ", 0.95, "ASCII STL solid/facet"),
    ("dxf", r"\A\s*0\s*\r?\nSECTION\s*\r?\n\s*2\s*\r?\n(HEADER|CLASSES|TABLES|BLOCKS|ENTITIES)", 0.95,
     "DXF SECTION groups"),
    ("diff", r"\A(diff --git |--- \S.*\r?\n\+\+\+ \S|Index: \S)", 0.9, "unified diff header"),
    ("tex", r"\\documentclass(\[[^\]]*\])?\{|\\begin\{document\}", 0.9, "LaTeX \\documentclass"),
    ("bib", r"(?im)^@(article|book|inproceedings|incollection|misc|online|techreport)\s*\{", 0.9,
     "BibTeX entries"),
    ("dockerfile", r"(?m)\A(#.*\n|\s*\n)*(ARG .*\n)*FROM \S+(?:.*\n)+?\s*(RUN|CMD|COPY|ENTRYPOINT|WORKDIR) ",
     0.85, "FROM … RUN/COPY instructions"),
]

SHEBANGS = {"python": "py", "python3": "py", "node": "mjs", "deno": "ts", "ruby": "rb",
            "perl": "pl", "php": "php", "lua": "lua", "bash": "bash", "zsh": "bash", "sh": "bash",
            "fish": "bash", "Rscript": "r", "julia": "jl", "tclsh": "tcl", "awk": "awk"}


def _json_subtype(obj) -> tuple[str, str] | None:
    if not isinstance(obj, dict):
        return None
    t = obj.get("type")
    if t in ("FeatureCollection", "Feature", "GeometryCollection", "Point", "LineString",
             "Polygon", "MultiPoint", "MultiLineString", "MultiPolygon"):
        return "geojson", f'GeoJSON "type": "{t}"'
    if t == "Topology" and "objects" in obj:
        return "topojson", "TopoJSON topology"
    if "nbformat" in obj and "cells" in obj:
        return "ipynb", "Jupyter notebook (nbformat + cells)"
    if isinstance(obj.get("log"), dict) and "entries" in obj["log"]:
        return "har", "HTTP archive log.entries"
    if isinstance(obj.get("asset"), dict) and "version" in obj["asset"]:
        return "gltf", "glTF asset block"
    if "sarif" in str(obj.get("$schema", "")).lower() or ("runs" in obj and "version" in obj
                                                          and str(obj["version"]).startswith("2.")):
        return "sarif", "SARIF report"
    if ("short_name" in obj or "name" in obj) and ("start_url" in obj or "icons" in obj) \
            and "display" in obj:
        return "webmanifest", "web app manifest fields"
    return None


def _probe_text(path: Path, head: bytes, size: int) -> list[Candidate]:
    # Avoid cutting a multi-byte character at the end of the sample.
    text = _decode_text(head if size <= HEAD else head[:-4])
    if text is None:
        return []
    out = []
    s = text.lstrip("﻿ \t\r\n")
    low = s[:2048].lower()
    sample = s[:4096]
    for fmt, rx, conf, ev in TEXT_MARKERS:
        if re.search(rx, sample, re.M):
            out.append(Candidate(fmt, conf, ev))
            break
    if s.startswith("#!"):
        tokens = s[2:].split("\n", 1)[0].split()
        prog = tokens[0].replace("\\", "/").rsplit("/", 1)[-1] if tokens else ""
        if prog == "env":                      # #!/usr/bin/env [-S] python3
            prog = next((t for t in tokens[1:] if not t.startswith("-")), "")
        interp = re.sub(r"[\d.]+$", "", prog)
        fmt = SHEBANGS.get(prog) or SHEBANGS.get(interp, "bash")
        out.append(Candidate(fmt, 0.85, f"#! line runs {prog or 'a shell'}"))
    if not out and s[:1] in "{[":
        try:
            data = path.read_bytes() if size <= 20 * 1024 ** 2 else None
            if data is not None:
                obj = json.loads(_decode_text(data) or "")
                sub = _json_subtype(obj)
                out.append(Candidate(sub[0], 0.97, sub[1]) if sub else
                           Candidate("json", 0.95, "valid JSON"))
        except Exception:
            lines = [ln for ln in s.splitlines()[:20] if ln.strip()]
            try:
                if len(lines) >= 2 and all(isinstance(json.loads(ln), (dict, list))
                                           for ln in lines[:-1]):
                    out.append(Candidate("jsonl", 0.85, "one JSON value per line (JSON Lines)"))
            except Exception:
                if re.search(r"^\s*(//|/\*)", s, re.M) or re.search(r",\s*[}\]]", s):
                    out.append(Candidate("json5", 0.6, "JSON with comments / trailing commas"))
    if not out and (low.startswith("<!doctype html") or low.startswith("<html")
                    or "<html" in low[:500]):
        out.append(Candidate("html", 0.9, "HTML markup"))
    elif not out and s.startswith("<"):
        m = re.search(r"<(?!\?|!)([A-Za-z_][\w:.-]*)", s[:4096])
        if m:
            root = m.group(1).split(":")[-1].lower()
            fmt = XML_ROOTS.get(root, "xml")
            if fmt == "csproj" and path.suffix.lower() not in (".csproj", ".vbproj", ".vcxproj",
                                                               ".props", ".targets"):
                fmt = "xml"
            out.append(Candidate(fmt, 0.9 if fmt != "xml" else 0.8, f"XML with root <{root}>"))
    if not out:
        lines = [ln for ln in s.splitlines() if ln.strip()][:40]
        obj_lines = sum(bool(re.match(r"(v|vt|vn|f|o|g|usemtl|mtllib) ", ln)) for ln in lines)
        if obj_lines >= max(3, len(lines) // 2) and any(ln.startswith("v ") for ln in lines):
            out.append(Candidate("obj", 0.85, "Wavefront v/f records"))
        gcode = sum(bool(re.match(r"(G[0-9]+|M[0-9]+|T[0-9]+)\b", ln)) for ln in lines)
        if gcode >= max(4, len(lines) // 2):
            out.append(Candidate("gcode", 0.8, "G/M machine commands"))
    if not out:
        lines = [ln for ln in s.splitlines() if ln.strip()][:40]
        out.extend(_probe_text_lines(s, lines))
    # Readable text is always at least plain text (a weak, generic candidate).
    out.append(Candidate("txt", 0.45 if not out or max(c.confidence for c in out) < 0.5 else 0.4,
                         "readable text"))
    return out


def _probe_text_lines(s: str, lines: list[str]) -> list[Candidate]:
    out = []
    if len(lines) >= 2:
        for delim, fmt in (("\t", "tsv"), (",", "csv"), (";", "csv"), ("|", "csv")):
            counts = [ln.count(delim) for ln in lines[:20]]
            if counts[0] >= 1 and all(c == counts[0] for c in counts):
                conf = 0.8 if len(lines) >= 3 else 0.6
                out.append(Candidate(fmt, conf, f"{counts[0] + 1} columns separated by {delim!r}"))
                break
    if re.search(r"^\[[^\]]+\]\s*$", s, re.M) and re.search(r"^\s*[\w.-]+\s*=", s, re.M):
        try:
            import tomllib
            tomllib.loads(s)
            out.append(Candidate("toml", 0.6, "TOML tables and key = value pairs"))
        except Exception:
            out.append(Candidate("ini", 0.6, "INI sections and key = value pairs"))
    elif sum(bool(re.match(r"^\s*[\w\"' .-]+:\s", ln)) for ln in lines) >= max(2, len(lines) // 2):
        try:
            import yaml
            if isinstance(yaml.safe_load(s), (dict, list)):
                out.append(Candidate("yaml", 0.6, "YAML key: value structure"))
        except Exception:
            pass
    md = sum(bool(re.match(r"^(#{1,6} |[-*+] |\d+\. |```|> )", ln)) for ln in lines)
    if md >= 2 or re.search(r"\[[^\]]+\]\([^)]+\)", s):
        out.append(Candidate("md", 0.55, "Markdown headings, lists or links"))
    return out


# --------------------------------------------------------------- media + ML

FFPROBE_FORMATS = {"matroska": "mkv", "webm": "webm", "mov": "mp4", "mp4": "mp4", "avi": "avi",
                   "asf": "wmv", "flv": "flv", "mpegts": "ts", "mpeg": "mpg", "mp3": "mp3",
                   "wav": "wav", "flac": "flac", "ogg": "ogg", "aac": "aac", "aiff": "aiff",
                   "ac3": "ac3", "amr": "amr", "gif": "gif"}


def _probe_ffprobe(path: Path) -> list[Candidate]:
    from .backends.media import probe_format
    out = probe_format(path)
    for token in out.split(","):
        if token in FFPROBE_FORMATS:
            return [Candidate(FFPROBE_FORMATS[token], 0.75, f"ffprobe reads it as {out!r}")]
    return []


MAGIKA_LABELS = {"jpeg": "jpg", "markdown": "md", "tar": "tar", "gzip": "gz", "sevenzip": "7z",
                 "javascript": "js", "powershell": "ps1", "batch": "bat", "vba": "vbs",
                 "shell": "sh", "pebin": "exe", "elf": "elf", "macho": "macho", "mp4": "mp4"}


def magika_available() -> bool:
    from importlib.util import find_spec
    return find_spec("magika") is not None


@lru_cache(maxsize=1)
def _magika():
    from magika import Magika
    return Magika()


def _probe_magika(path: Path) -> list[Candidate]:
    if not magika_available():
        return []
    try:
        res = _magika().identify_path(path)
        out = getattr(res, "output", None) or getattr(res, "prediction", None)
        label = getattr(out, "label", None) or getattr(out, "ct_label", None)
        score = float(getattr(res, "score", None) or getattr(out, "score", 0.5) or 0.5)
    except Exception:
        log.exception("Magika failed")
        return []
    if not label or label in ("unknown", "txt", "empty"):
        return []
    fmt = MAGIKA_LABELS.get(str(label), str(label))
    return [Candidate(fmt, min(0.7, score), f"Magika AI model says {label!r} ({score:.0%})")]


# ------------------------------------------------------------------- analysis

def _entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    n = len(data)
    return -sum(c / n * math.log2(c / n) for c in counts if c)


def _equivalent(a: str, b: str) -> bool:
    return a == b or any(a in g and b in g for g in EQUIVALENT)


@lru_cache(maxsize=256)
def _identify_cached(path: str, size: int, mtime: float, deep: bool) -> Identification:
    p = Path(path)
    ext_fmt = ext_of(p)
    ident = Identification(path=path, ext_fmt=ext_fmt)
    with open(p, "rb") as f:
        head = f.read(HEAD)
    ident.details.update(size=size, head=head[:256], entropy=_entropy(head[:16384]))

    from . import custom
    cands = custom.probe(head, ext_fmt) + _probe_signatures(head, ext_fmt, size) \
        + _probe_containers(p, head, ext_fmt)
    if not cands or max(c.confidence for c in cands) < 0.6:
        cands += _probe_text(p, head, size)
    if deep and (not cands or max(c.confidence for c in cands) < 0.6):
        cands += _probe_ffprobe(p)
    if deep and (not cands or max(c.confidence for c in cands) < 0.5):
        cands += _probe_magika(p)
    # Formats with weak or no signatures (TGA, PCX, …): trust a known extension.
    if ext_fmt and ext_fmt in CATEGORY_OF and not any(c.confidence >= 0.6 for c in cands):
        cands.append(Candidate(ext_fmt, 0.5, f"by its .{ext_fmt} extension"))

    # Rank: confidence, a small bonus when the extension agrees, specific beats generic.
    def score(c: Candidate) -> float:
        s = c.confidence
        if ext_fmt and _equivalent(c.fmt, ext_fmt):
            s += 0.08
        if c.fmt in ("zip", "txt", "xml", "doc"):
            s -= 0.02
        return s

    merged: dict[str, Candidate] = {}
    for c in cands:
        if c.fmt not in merged or c.confidence > merged[c.fmt].confidence:
            merged[c.fmt] = c
    ident.candidates = sorted(merged.values(), key=score, reverse=True)

    if ident.candidates:
        best = ident.candidates[0]
        ident.fmt, ident.confidence = best.fmt, best.confidence
        if ext_fmt and _equivalent(best.fmt, ext_fmt) and best.confidence >= 0.4:
            # Content is consistent with the extension: two agreeing signals.
            ident.confidence = max(best.confidence, 0.85)
        # Keep the extension's (more specific) name when it's an equivalent format.
        if ext_fmt and ext_fmt != best.fmt and _equivalent(best.fmt, ext_fmt) \
                and ext_fmt in CATEGORY_OF and {ext_fmt, best.fmt} not in DISTINCT_ENCODINGS:
            ident.fmt = ext_fmt
        ident.mismatch = bool(ext_fmt) and best.confidence >= 0.8 \
            and not _equivalent(best.fmt, ext_fmt)
    else:
        ident.details["guess"] = _describe_unknown(head, ident.details["entropy"], size)

    ident.risky = _assess_risk(p, ident, ext_fmt)
    ident.details["sensitive"] = _sensitivity(p, ident, head)
    return ident


def _sensitivity(p: Path, ident: Identification, head: bytes) -> str:
    """'' or why the file is sensitive (private keys, keystores): conversions are limited."""
    from . import catalog
    fmt = ident.fmt or ""
    entry = catalog.CATALOG.get(fmt)
    if entry and entry.sensitive:
        return f"it's a {entry.name.lower()}"
    if fmt in ("pem", "pgp"):
        labels = re.findall(rb"-----BEGIN ([A-Z0-9 ]+)-----", head[:65536])
        if any(b"PRIVATE" in lb or b"ENCRYPTED" in lb or lb.startswith(b"PGP PRIVATE")
               for lb in labels):
            return "it contains a private key"
    if fmt == "der":
        from .native import certs
        try:
            data = p.read_bytes() if p.stat().st_size < 1024 * 1024 else head
        except OSError:
            data = head
        if certs.der_kind(data) == "private key":
            return "it contains a private key"
    return ""


def _describe_unknown(head: bytes, entropy: float, size: int) -> str:
    if size == 0:
        return "Empty file"
    if entropy > 7.5:
        return "Unknown binary data (looks compressed or encrypted)"
    return "Unknown binary file"


def _assess_risk(p: Path, ident: Identification, ext_fmt: str) -> str | None:
    fmt = ident.fmt or ""
    if ext_fmt in RISKY_EXTS:
        return f"it's a program, script or shortcut (.{ext_fmt})."
    if CATEGORY_OF.get(fmt) == "program" and ident.confidence >= 0.8:
        disguised = ext_fmt and CATEGORY_OF.get(ext_fmt) != "program"
        return (f"it's actually a {name_of(fmt).lower()} disguised as .{ext_fmt}." if disguised
                else f"it's a {name_of(fmt).lower()}.")
    if fmt in ("docm", "xlsm"):
        return "it contains Office macros."
    if fmt in ("zip", "7z", "tar", "tgz", "tbz2", "txz", "cbz", "jar"):
        from .backends.archives import inspect_archive
        from .backends.base import RiskyFile
        try:
            inspect_archive(p, fmt)
        except RiskyFile as e:
            return str(e)[0].lower() + str(e)[1:]
        except Exception:
            pass   # damaged archives fail later with a normal error
    if fmt == "pdf":
        try:
            head = p.read_bytes()[:1024 * 1024] if p.stat().st_size < 50 * 1024 ** 2 else b""
            if b"/Encrypt" in head:
                return "the PDF is encrypted."
        except OSError:
            pass
    return None


def identify(path: str | os.PathLike, deep: bool = True) -> Identification:
    """Identify a file. ``deep`` allows slower probes (ffprobe, Magika)."""
    from . import custom
    custom.load_specs()          # registers user-described formats and their extensions
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        # Nonexistent (e.g. the settings preview): fall back to the extension alone.
        ext_fmt = ext_of(p)
        return Identification(path=str(p), fmt=ext_fmt or None, confidence=0.5 if ext_fmt else 0,
                              ext_fmt=ext_fmt)
    if not p.is_file():
        return Identification(path=str(p), details={"guess": "Not a file"})
    try:
        return _identify_cached(str(p), st.st_size, st.st_mtime, deep)
    except OSError as e:
        return Identification(path=str(p), details={"guess": f"Can't read the file: {e}"})


def clear_cache():
    _identify_cached.cache_clear()
