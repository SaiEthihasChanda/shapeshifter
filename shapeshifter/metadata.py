"""View and edit a file's metadata: EXIF in pictures, document properties (author, title, …) in PDF / Word / Excel /
PowerPoint / OpenDocument, tags in audio and video, and the file's own dates.

Every format has a handler that can ``read`` fields and ``write`` changes to a new file next to the original; ``apply``
then swaps it in (the original goes to the Recycle Bin, or the edit is saved beside it, following the usual setting).
JPEG and PNG pictures are edited without touching the picture data, so there is no quality loss.
"""

from __future__ import annotations

import io
import logging
import os
import re
import shutil
import struct
import subprocess
import tempfile
import zipfile
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from .core import NO_WINDOW, ConversionError

log = logging.getLogger(__name__)


@dataclass
class Field:
    key: str
    label: str
    value: str = ""
    group: str = "Description"
    writable: bool = True
    hint: str = ""


GROUPS = ["Description", "People and rights", "Dates", "Camera", "Location", "Technical", "File"]
DATE_FMT = "%Y-%m-%d %H:%M:%S"


# ------------------------------------------------------------------ helpers

def _fmt_exif_date(v) -> str:
    s = str(v or "").strip().replace("\x00", "")
    m = re.match(r"(\d{4}):(\d\d):(\d\d)[ T](\d\d):(\d\d):(\d\d)", s)
    return f"{m[1]}-{m[2]}-{m[3]} {m[4]}:{m[5]}:{m[6]}" if m else s


def _to_exif_date(text: str) -> str:
    t = text.strip()
    m = re.match(r"(\d{4})[-:/](\d\d?)[-:/](\d\d?)(?:[ T](\d\d?):(\d\d)(?::(\d\d))?)?$", t)
    if not m:
        raise ConversionError(f"'{text}' isn't a date. Use YYYY-MM-DD or YYYY-MM-DD HH:MM:SS.")
    y, mo, d, h, mi, s = m.groups()
    return f"{int(y):04d}:{int(mo):02d}:{int(d):02d} {int(h or 0):02d}:{int(mi or 0):02d}:{int(s or 0):02d}"


def _to_iso_date(text: str) -> str:
    return datetime.strptime(_to_exif_date(text), "%Y:%m:%d %H:%M:%S").strftime("%Y-%m-%dT%H:%M:%SZ")


def _file_fields(path: Path) -> list[Field]:
    st = path.stat()
    local = lambda ts: datetime.fromtimestamp(ts).strftime(DATE_FMT)       # noqa: E731
    return [Field("file_created", "Created", local(getattr(st, "st_birthtime", st.st_ctime)), "File", True, "The file's own creation date"),
            Field("file_modified", "Modified", local(st.st_mtime), "File", True, "The file's own last-modified date"),
            Field("file_size", "Size", f"{st.st_size:,} bytes", "File", False),
            Field("file_path", "Location", str(path.parent), "File", False)]


def set_file_times(path: Path, created: str | None = None, modified: str | None = None) -> None:
    """Set the creation and / or modification date shown in Explorer / Finder (local time, YYYY-MM-DD HH:MM:SS)."""
    from .system import set_created_time

    def stamp(text: str) -> float:
        return datetime.strptime(_to_exif_date(text), "%Y:%m:%d %H:%M:%S").timestamp()

    try:
        if modified:
            st = os.stat(path)
            os.utime(path, (st.st_atime, stamp(modified)))
        if created:
            set_created_time(path, stamp(created))
    except (OSError, ValueError) as e:
        raise ConversionError("Couldn't change the file's dates.") from e


# =============================================================== pictures

EXIF_TAGS = {"title": 0x010E, "author": 0x013B, "copyright": 0x8298, "software": 0x0131, "make": 0x010F, "model": 0x0110,
             "modified": 0x0132}
EXIF_SUB = {"taken": 0x9003, "digitized": 0x9004, "lens": 0xA434}
GPS_IFD, EXIF_IFD = 0x8825, 0x8769
PNG_TEXT = {"title": "Title", "author": "Author", "comments": "Description", "copyright": "Copyright", "software": "Software",
            "created": "Creation Time", "subject": "Source"}
IMG_FORMATS = {"jpg", "png", "webp", "tiff", "heic", "avif"}


def _rational(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _dms_to_deg(dms, ref) -> float | None:
    try:
        d, m, s = (_rational(x) for x in dms)
    except (TypeError, ValueError):
        return None
    deg = d + m / 60 + s / 3600
    return -deg if str(ref).upper().startswith(("S", "W")) else deg


def _deg_to_dms(value: float):
    value = abs(value)
    d = int(value)
    m = int((value - d) * 60)
    s = round(((value - d) * 60 - m) * 60, 4)
    return (float(d), float(m), float(s))


def parse_gps(text: str) -> tuple[float, float]:
    nums = re.findall(r"-?\d+(?:\.\d+)?", text.replace("°", " "))
    if len(nums) < 2:
        raise ConversionError("Enter the location as 'latitude, longitude', for example 13.0827, 80.2707.")
    lat, lon = float(nums[0]), float(nums[1])
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ConversionError("That latitude or longitude is out of range.")
    return lat, lon


def _exif_of(im):
    return im.getexif()


def _text_value(v) -> str:
    if isinstance(v, bytes):
        v = v.split(b"\x00")[0].decode("utf-8", "replace")
    return str(v).strip()


def _user_comment(raw) -> str:
    if not isinstance(raw, bytes) or len(raw) < 8:
        return _text_value(raw) if raw else ""
    head, body = raw[:8], raw[8:]
    if head.startswith(b"UNICODE"):
        for enc in ("utf-16-be", "utf-16-le"):
            try:
                return body.decode(enc).strip("\x00 ")
            except UnicodeDecodeError:
                continue
    return body.decode("utf-8", "replace").strip("\x00 ")


def _make_user_comment(text: str) -> bytes:
    return b"ASCII\x00\x00\x00" + text.encode("ascii") if text.isascii() else b"UNICODE\x00" + text.encode("utf-16-be")


def read_image(path: Path, fmt: str) -> list[Field]:
    from PIL import Image
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception:                                        # noqa: BLE001
        pass
    fields: list[Field] = []
    with Image.open(path) as im:
        ex = im.getexif()
        info = dict(im.info)
        sub = ex.get_ifd(EXIF_IFD) if hasattr(ex, "get_ifd") else {}
        gps = ex.get_ifd(GPS_IFD) if hasattr(ex, "get_ifd") else {}
        text = {}
        if fmt == "png":
            for k, v in info.items():
                if isinstance(v, str):
                    text[k] = v
        get = lambda tag: _text_value(ex.get(tag, "")) if tag in ex else ""          # noqa: E731
        fields += [Field("title", "Title or description", get(0x010E) or text.get("Title", ""), "Description", True, "EXIF ImageDescription"),
                   Field("comments", "Comment", _user_comment(sub.get(0x9286)) or text.get("Description", "") or text.get("Comment", ""), "Description"),
                   Field("author", "Author or photographer", get(0x013B) or text.get("Author", ""), "People and rights"),
                   Field("copyright", "Copyright", get(0x8298) or text.get("Copyright", ""), "People and rights"),
                   Field("software", "Software", get(0x0131) or text.get("Software", ""), "People and rights"),
                   Field("taken", "Date taken", _fmt_exif_date(sub.get(0x9003, "")) or text.get("Creation Time", ""), "Dates", True, "YYYY-MM-DD HH:MM:SS"),
                   Field("digitized", "Date digitised", _fmt_exif_date(sub.get(0x9004, "")), "Dates", True, "YYYY-MM-DD HH:MM:SS"),
                   Field("modified", "Date edited", _fmt_exif_date(ex.get(0x0132, "")), "Dates", True, "YYYY-MM-DD HH:MM:SS"),
                   Field("make", "Camera make", get(0x010F), "Camera"), Field("model", "Camera model", get(0x0110), "Camera"),
                   Field("lens", "Lens", _text_value(sub.get(0xA434, "")), "Camera")]
        lat = _dms_to_deg(gps.get(2), gps.get(1)) if gps.get(2) else None
        lon = _dms_to_deg(gps.get(4), gps.get(3)) if gps.get(4) else None
        loc = f"{lat:.6f}, {lon:.6f}" if lat is not None and lon is not None else ""
        fields.append(Field("gps", "Location", loc, "Location", True, "latitude, longitude (decimal), e.g. 13.0827, 80.2707"))
        if gps.get(6) is not None:
            alt = _rational(gps.get(6)) * (-1 if gps.get(5) in (1, b"\x01") else 1)
            fields.append(Field("altitude", "Altitude (m)", f"{alt:.1f}", "Location", False))
        tech = []
        tech.append(("Dimensions", f"{im.width} × {im.height} pixels"))
        for label, tag in (("Exposure", 0x829A), ("Aperture", 0x829D), ("Focal length", 0x920A), ("ISO", 0x8827)):
            v = sub.get(tag)
            if v is None:
                continue
            if tag == 0x829A:
                v = f"1/{round(1 / _rational(v))} s" if 0 < _rational(v) < 1 else f"{_rational(v):g} s"
            elif tag == 0x829D:
                v = f"f/{_rational(v):g}"
            elif tag == 0x920A:
                v = f"{_rational(v):g} mm"
            tech.append((label, str(v if not isinstance(v, tuple) else v[0])))
        fields += [Field(f"t_{i}", label, value, "Technical", False) for i, (label, value) in enumerate(tech)]
    return fields + _file_fields(path)


def _build_exif(path: Path, changes: dict, strip_all: bool):
    """The EXIF block of a picture after the changes (an Exif object, possibly empty)."""
    from PIL import Image
    if strip_all:
        ex = Image.Exif()
    else:
        with Image.open(path) as im:
            ex = im.getexif()
            if hasattr(ex, "get_ifd"):
                ex.get_ifd(EXIF_IFD), ex.get_ifd(GPS_IFD)
    sub, gps = ex.get_ifd(EXIF_IFD), ex.get_ifd(GPS_IFD)
    sub.pop(0x927C, None)                                    # maker notes hold offsets that would break
    for key, value in changes.items():
        empty = value in (None, "")
        if key in EXIF_TAGS:
            tag = EXIF_TAGS[key]
            if empty:
                ex.pop(tag, None)
            else:
                ex[tag] = _to_exif_date(value) if key == "modified" else value
        elif key in ("taken", "digitized"):
            tag = EXIF_SUB[key]
            sub.pop(tag, None) if empty else sub.__setitem__(tag, _to_exif_date(value))
        elif key == "lens":
            sub.pop(0xA434, None) if empty else sub.__setitem__(0xA434, value)
        elif key == "comments":
            sub.pop(0x9286, None) if empty else sub.__setitem__(0x9286, _make_user_comment(value))
        elif key == "gps":
            gps.clear()
            if not empty:
                lat, lon = parse_gps(value)
                gps[0] = b"\x02\x03\x00\x00"
                gps[1], gps[2] = ("N" if lat >= 0 else "S"), _deg_to_dms(lat)
                gps[3], gps[4] = ("E" if lon >= 0 else "W"), _deg_to_dms(lon)
    for tag, ifd in ((GPS_IFD, gps), (EXIF_IFD, sub)):
        if not ifd:
            ex.pop(tag, None)
            getattr(ex, "_ifds", {}).pop(tag, None)
    return ex


def _exif_bytes(ex) -> bytes | None:
    data = ex.tobytes()
    return data if data and len(data) > 8 else None


def _jpeg_rewrite(data: bytes, exif_tiff: bytes | None, strip_all: bool) -> bytes:
    """The JPEG with a new EXIF block (old one removed); the compressed picture is copied untouched."""
    if data[:2] != b"\xff\xd8":
        raise ConversionError("Not a JPEG file.")
    out = [b"\xff\xd8"]
    pos = 2
    kept_app0 = False
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            break
        marker = data[pos + 1]
        if marker == 0xDA:                                   # start of scan: the rest is picture data
            break
        if marker == 0xFF:
            pos += 1
            continue
        length = struct.unpack_from(">H", data, pos + 2)[0]
        seg = data[pos:pos + 2 + length]
        body = seg[4:]
        drop = False
        if marker == 0xE1 and body.startswith(b"Exif\x00\x00"):
            drop = True
        elif strip_all and (marker == 0xE1 and body.startswith(b"http://ns.adobe.com/xap")) or (strip_all and marker in (0xED, 0xFE)):
            drop = True                                       # XMP, Photoshop / IPTC, comments
        if not drop:
            out.append(seg)
            kept_app0 = kept_app0 or marker == 0xE0
        pos += 2 + length
    block = b""
    if exif_tiff:
        payload = b"Exif\x00\x00" + exif_tiff
        if len(payload) + 2 > 0xFFFF:
            raise ConversionError("The EXIF block is too large.")
        block = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    # EXIF goes straight after the start marker, ahead of the other segments
    return out[0] + block + b"".join(out[1:]) + data[pos:]


def _png_chunks(data: bytes):
    pos = 8
    while pos + 8 <= len(data):
        length, kind = struct.unpack_from(">I4s", data, pos)
        yield kind, data[pos + 8:pos + 8 + length], pos, pos + 12 + length
        pos += 12 + length


def _png_chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def _png_text_chunk(keyword: str, value: str) -> bytes:
    try:
        return _png_chunk(b"tEXt", keyword.encode("latin-1") + b"\x00" + value.encode("latin-1"))
    except UnicodeEncodeError:
        return _png_chunk(b"iTXt", keyword.encode("latin-1") + b"\x00\x00\x00\x00\x00" + value.encode("utf-8"))


def _png_rewrite(data: bytes, text: dict[str, str | None], exif_tiff: bytes | None | bool, strip_all: bool) -> bytes:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ConversionError("Not a PNG file.")
    out = [data[:8]]
    inserted = False
    new_chunks = b"".join(_png_text_chunk(k, v) for k, v in text.items() if v)
    if exif_tiff:
        new_chunks += _png_chunk(b"eXIf", exif_tiff)
    for kind, body, start, end in _png_chunks(data):
        if kind in (b"tEXt", b"iTXt", b"zTXt"):
            keyword = body.split(b"\x00", 1)[0].decode("latin-1", "replace")
            if strip_all or keyword in text:
                continue
        elif kind in (b"eXIf", b"tIME"):
            if strip_all or exif_tiff is not False and kind == b"eXIf":
                continue
        out.append(data[start:end])
        if kind == b"IHDR" and not inserted:
            out.append(new_chunks)
            inserted = True
    return b"".join(out)


def write_image(src: Path, dst: Path, fmt: str, changes: dict, strip_all: bool) -> None:
    from PIL import Image
    data = src.read_bytes()
    if fmt == "jpg":
        # with strip_all, only the fields typed in again are put back
        keep = {k: v for k, v in changes.items() if v} if strip_all else changes
        dst.write_bytes(_jpeg_rewrite(data, _exif_bytes(_build_exif(src, keep, strip_all)), strip_all))
        return
    if fmt == "png":
        keep = {k: v for k, v in changes.items() if v} if strip_all else changes
        text = {kw: (keep[k] or None) for k, kw in PNG_TEXT.items() if k in keep}
        tiff: bytes | None | bool = False
        if strip_all:
            tiff = None
        if any(k in keep for k in ("make", "model", "taken", "digitized", "lens", "gps", "author", "copyright")) or strip_all and any(k in keep for k in EXIF_TAGS):
            tiff = _exif_bytes(_build_exif(src, keep, strip_all)) or (None if strip_all else False)
        dst.write_bytes(_png_rewrite(data, text, tiff, strip_all))
        return
    # WebP, TIFF, HEIC, AVIF: the picture has to be saved again (a lossy format is encoded a second time)
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception:                                        # noqa: BLE001
        pass
    ex = _build_exif(src, changes, strip_all)
    with Image.open(src) as im:
        frame = im.copy()
        kw: dict = {}
        exif = _exif_bytes(ex)
        if exif:
            kw["exif"] = exif
        if im.info.get("icc_profile"):
            kw["icc_profile"] = im.info["icc_profile"]
        pil = {"webp": "WEBP", "tiff": "TIFF", "heic": "HEIF", "avif": "AVIF"}[fmt]
        if fmt == "webp":
            kw.update(quality=95, method=4)
        elif fmt in ("heic", "avif"):
            kw.update(quality=90)
        elif fmt == "tiff":
            kw.update(compression="tiff_lzw")
        frame.save(dst, pil, **kw)


# =============================================================== PDF

def _pdf_date(v: str) -> str:
    m = re.match(r"D:(\d{4})(\d\d)?(\d\d)?(\d\d)?(\d\d)?(\d\d)?", v or "")
    if not m:
        return v or ""
    y, mo, d, h, mi, s = (m.group(i) or ("01" if i in (2, 3) else "00") for i in range(1, 7))
    return f"{y}-{mo}-{d} {h}:{mi}:{s}"


def read_pdf(path: Path) -> list[Field]:
    import pymupdf
    with pymupdf.open(str(path)) as doc:
        if doc.needs_pass:
            raise ConversionError("The PDF is password protected.")
        md = doc.metadata or {}
        pages = doc.page_count
    return [Field("title", "Title", md.get("title", ""), "Description"), Field("subject", "Subject", md.get("subject", ""), "Description"),
            Field("keywords", "Keywords", md.get("keywords", ""), "Description", True, "separate with commas"),
            Field("author", "Author", md.get("author", ""), "People and rights"),
            Field("software", "Created with", md.get("creator", ""), "People and rights"),
            Field("producer", "PDF producer", md.get("producer", ""), "People and rights"),
            Field("created", "Created", _pdf_date(md.get("creationDate", "")), "Dates", True, "YYYY-MM-DD HH:MM:SS"),
            Field("modified", "Modified", _pdf_date(md.get("modDate", "")), "Dates", True, "YYYY-MM-DD HH:MM:SS"),
            Field("t_pages", "Pages", str(pages), "Technical", False)] + _file_fields(path)


def write_pdf(src: Path, dst: Path, changes: dict, strip_all: bool) -> None:
    import pymupdf
    with pymupdf.open(str(src)) as doc:
        if doc.needs_pass:
            raise ConversionError("The PDF is password protected.")
        md = dict(doc.metadata or {})
        names = {"title": "title", "subject": "subject", "keywords": "keywords", "author": "author", "software": "creator", "producer": "producer"}
        if strip_all:
            md = {k: "" for k in ("title", "author", "subject", "keywords", "creator", "producer", "creationDate", "modDate")}
            try:
                doc.del_xml_metadata()
            except Exception:                                # noqa: BLE001
                pass
        for key, value in changes.items():
            if key in names:
                md[names[key]] = value or ""
            elif key in ("created", "modified"):
                md["creationDate" if key == "created" else "modDate"] = ("D:" + _to_exif_date(value).replace(":", "").replace(" ", "")) if value else ""
        md = {k: v for k, v in md.items() if k in ("title", "author", "subject", "keywords", "creator", "producer", "creationDate", "modDate")}
        doc.set_metadata(md)
        doc.save(str(dst), garbage=3, deflate=True)


# ============================================================ Office / ODF

CORE_NS = {"cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties", "dc": "http://purl.org/dc/elements/1.1/",
           "dcterms": "http://purl.org/dc/terms/", "dcmitype": "http://purl.org/dc/dcmitype/", "xsi": "http://www.w3.org/2001/XMLSchema-instance"}
OOXML_MAP = {"title": ("dc", "title"), "subject": ("dc", "subject"), "author": ("dc", "creator"), "keywords": ("cp", "keywords"),
             "comments": ("dc", "description"), "last_modified_by": ("cp", "lastModifiedBy"), "category": ("cp", "category"),
             "created": ("dcterms", "created"), "modified": ("dcterms", "modified")}
APP_NS = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
ODF_NS = {"office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0", "meta": "urn:oasis:names:tc:opendocument:xmlns:meta:1.0",
          "dc": "http://purl.org/dc/elements/1.1/"}
ODF_MAP = {"title": ("dc", "title"), "subject": ("dc", "subject"), "author": ("meta", "initial-creator"), "last_modified_by": ("dc", "creator"),
           "comments": ("dc", "description"), "created": ("meta", "creation-date"), "modified": ("dc", "date")}
FRIENDLY = {"title": ("Title", "Description"), "subject": ("Subject", "Description"), "keywords": ("Keywords", "Description"),
            "comments": ("Comments", "Description"), "category": ("Category", "Description"), "author": ("Author", "People and rights"),
            "last_modified_by": ("Last modified by", "People and rights"), "company": ("Company", "People and rights"),
            "manager": ("Manager", "People and rights"), "created": ("Created", "Dates"), "modified": ("Modified", "Dates")}


def _iso_to_local(v: str) -> str:
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return dt.astimezone().strftime(DATE_FMT) if dt.tzinfo else dt.strftime(DATE_FMT)
    except ValueError:
        return v


def _zip_text(z: zipfile.ZipFile, name: str) -> bytes | None:
    try:
        return z.read(name)
    except KeyError:
        return None


def read_office(path: Path, fmt: str) -> list[Field]:
    values: dict[str, str] = {}
    with zipfile.ZipFile(path) as z:
        if fmt in ("docx", "xlsx", "pptx", "xlsm"):
            raw = _zip_text(z, "docProps/core.xml")
            if raw:
                root = ET.fromstring(raw)
                for key, (p, tag) in OOXML_MAP.items():
                    el = root.find("{%s}%s" % (CORE_NS[p], tag))
                    if el is not None and el.text:
                        values[key] = _iso_to_local(el.text) if key in ("created", "modified") else el.text
            app = _zip_text(z, "docProps/app.xml")
            if app:
                a = ET.fromstring(app)
                for key, tag in (("company", "Company"), ("manager", "Manager")):
                    el = a.find("{%s}%s" % (APP_NS, tag))
                    if el is not None and el.text:
                        values[key] = el.text
                el = a.find("{%s}Application" % APP_NS)
                if el is not None and el.text:
                    values["_application"] = el.text
        else:
            raw = _zip_text(z, "meta.xml")
            if raw:
                root = ET.fromstring(raw)
                meta = root.find("{%s}meta" % ODF_NS["office"])
                if meta is not None:
                    for key, (p, tag) in ODF_MAP.items():
                        el = meta.find("{%s}%s" % (ODF_NS[p], tag))
                        if el is not None and el.text:
                            values[key] = _iso_to_local(el.text) if key in ("created", "modified") else el.text
                    kws = [e.text for e in meta.findall("{%s}keyword" % ODF_NS["meta"]) if e.text]
                    if kws:
                        values["keywords"] = ", ".join(kws)
                    gen = meta.find("{%s}generator" % ODF_NS["meta"])
                    if gen is not None and gen.text:
                        values["_application"] = gen.text
    fields = []
    for key, (label, group) in FRIENDLY.items():
        if key in ("company", "manager") and fmt not in ("docx", "xlsx", "pptx", "xlsm"):
            continue
        if key == "category" and fmt not in ("docx", "xlsx", "pptx", "xlsm"):
            continue
        fields.append(Field(key, label, values.get(key, ""), group, True, "YYYY-MM-DD HH:MM:SS" if key in ("created", "modified") else ""))
    if values.get("_application"):
        fields.append(Field("t_app", "Made with", values["_application"], "Technical", False))
    return fields + _file_fields(path)


def _copy_zip(src: Path, dst: Path, replace: dict[str, bytes | None], extra: dict[str, bytes] | None = None) -> None:
    """A copy of a zip with some parts replaced (None removes them); order and compression are kept."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        seen = set()
        for info in zin.infolist():
            seen.add(info.filename)
            if info.filename in replace:
                if replace[info.filename] is None:
                    continue
                zout.writestr(info, replace[info.filename], compress_type=info.compress_type)
            else:
                zout.writestr(info, zin.read(info.filename), compress_type=info.compress_type)
        for name, blob in (extra or {}).items():
            if name not in seen:
                zout.writestr(name, blob, zipfile.ZIP_DEFLATED)


def write_office(src: Path, dst: Path, fmt: str, changes: dict, strip_all: bool) -> None:
    with zipfile.ZipFile(src) as z:
        ooxml = fmt in ("docx", "xlsx", "pptx", "xlsm")
        replace: dict[str, bytes | None] = {}
        extra: dict[str, bytes] = {}
        if ooxml:
            raw = _zip_text(z, "docProps/core.xml")
            for p, uri in CORE_NS.items():
                ET.register_namespace(p, uri)
            if raw:
                root = ET.fromstring(raw)
            else:
                root = ET.Element("{%s}coreProperties" % CORE_NS["cp"])
                ct = z.read("[Content_Types].xml").decode("utf-8")
                if "/docProps/core.xml" not in ct:
                    replace["[Content_Types].xml"] = ct.replace("</Types>", '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>').encode("utf-8")
                rels = z.read("_rels/.rels").decode("utf-8")
                if "core-properties" not in rels:
                    replace["_rels/.rels"] = rels.replace("</Relationships>", '<Relationship Id="rIdCore" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/></Relationships>').encode("utf-8")
            if strip_all:
                for child in list(root):
                    root.remove(child)
            for key, value in changes.items():
                if key not in OOXML_MAP:
                    continue
                p, tag = OOXML_MAP[key]
                el = root.find("{%s}%s" % (CORE_NS[p], tag))
                if not value:
                    if el is not None:
                        root.remove(el)
                    continue
                if el is None:
                    el = ET.SubElement(root, "{%s}%s" % (CORE_NS[p], tag))
                if key in ("created", "modified"):
                    el.text = _to_iso_date(value)
                    el.set("{%s}type" % CORE_NS["xsi"], "dcterms:W3CDTF")
                else:
                    el.text = value
            replace["docProps/core.xml"] = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + ET.tostring(root, encoding="utf-8")
            app = _zip_text(z, "docProps/app.xml")
            if app and (strip_all or "company" in changes or "manager" in changes):
                ET.register_namespace("", APP_NS)
                a = ET.fromstring(app)
                for key, tag in (("company", "Company"), ("manager", "Manager")):
                    if strip_all or key in changes:
                        el = a.find("{%s}%s" % (APP_NS, tag))
                        value = None if strip_all else changes.get(key)
                        if el is not None and not value:
                            a.remove(el)
                        elif value:
                            el = el if el is not None else ET.SubElement(a, "{%s}%s" % (APP_NS, tag))
                            el.text = value
                replace["docProps/app.xml"] = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + ET.tostring(a, encoding="utf-8")
            if strip_all and "docProps/custom.xml" in z.namelist():
                replace["docProps/custom.xml"] = None
        else:
            raw = _zip_text(z, "meta.xml")
            if not raw:
                raise ConversionError("This OpenDocument file has no metadata part.")
            for p, uri in ODF_NS.items():
                ET.register_namespace(p, uri)
            root = ET.fromstring(raw)
            meta = root.find("{%s}meta" % ODF_NS["office"])
            if meta is None:
                meta = ET.SubElement(root, "{%s}meta" % ODF_NS["office"])
            if strip_all:
                for child in list(meta):
                    if child.tag != "{%s}generator" % ODF_NS["meta"]:
                        meta.remove(child)
            for key, value in changes.items():
                if key == "keywords":
                    for e in meta.findall("{%s}keyword" % ODF_NS["meta"]):
                        meta.remove(e)
                    for word in [w.strip() for w in (value or "").split(",") if w.strip()]:
                        ET.SubElement(meta, "{%s}keyword" % ODF_NS["meta"]).text = word
                elif key in ODF_MAP:
                    p, tag = ODF_MAP[key]
                    el = meta.find("{%s}%s" % (ODF_NS[p], tag))
                    if not value:
                        if el is not None:
                            meta.remove(el)
                        continue
                    if el is None:
                        el = ET.SubElement(meta, "{%s}%s" % (ODF_NS[p], tag))
                    el.text = _to_iso_date(value)[:-1] if key in ("created", "modified") else value
            replace["meta.xml"] = b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")
    with zipfile.ZipFile(src) as z:
        names = set(z.namelist())
    for name in [n for n, v in replace.items() if v is not None and n not in names]:
        extra[name] = replace.pop(name)
    _copy_zip(src, dst, replace, extra)


# ============================================================ audio / video

MEDIA_KEYS = [("title", "Title", "Description"), ("artist", "Artist", "People and rights"), ("album", "Album", "Description"),
              ("album_artist", "Album artist", "People and rights"), ("composer", "Composer", "People and rights"),
              ("genre", "Genre", "Description"), ("date", "Year or date", "Dates"), ("track", "Track number", "Description"),
              ("disc", "Disc number", "Description"), ("comment", "Comment", "Description"), ("copyright", "Copyright", "People and rights")]


def _ffmpeg() -> str:
    from .backends import media
    path = media.ffmpeg_path()
    if not path:
        raise ConversionError("ffmpeg is needed to edit audio and video tags. Install it in Settings › Engines.")
    return path


def read_media(path: Path) -> list[Field]:
    from .backends import media
    text = media._ffmpeg_info(path)
    tags: dict[str, str] = {}
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("  Metadata:"):
            for ln in lines[i + 1:]:
                if not ln.startswith("    ") or ln.startswith("     "):
                    break
                k, _, v = ln.strip().partition(":")
                tags[k.strip().lower()] = v.strip()
            break
    info = media.probe(path)
    fields = [Field(k, label, tags.get(k, ""), group) for k, label, group in MEDIA_KEYS]
    tech = []
    if info.duration:
        tech.append(("Duration", f"{int(info.duration // 60)}:{int(info.duration % 60):02d}"))
    if info.has_video:
        tech.append(("Video", f"{info.width} × {info.height}, {info.vcodec}"))
    if info.has_audio:
        tech.append(("Audio", info.acodec))
    if tags.get("encoder"):
        tech.append(("Encoder", tags["encoder"]))
    fields += [Field(f"t_{i}", label, value, "Technical", False) for i, (label, value) in enumerate(tech)]
    return fields + _file_fields(path)


def write_media(src: Path, dst: Path, changes: dict, strip_all: bool) -> None:
    ext = src.suffix.lower()
    cmd = [_ffmpeg(), "-y", "-hide_banner", "-nostdin", "-i", str(src), "-map", "0", "-c", "copy"]
    cmd += ["-map_metadata", "-1"] if strip_all else ["-map_metadata", "0"]
    for key, value in changes.items():
        if any(key == k for k, _, _ in MEDIA_KEYS):
            cmd += ["-metadata", f"{key}={value or ''}"]
    if ext == ".mp3":
        cmd += ["-id3v2_version", "3"]
    cmd += [str(dst)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW, timeout=1800)
    if proc.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        log.info("ffmpeg metadata failed: %s", proc.stderr[-500:])
        raise ConversionError("The tags couldn't be written to this file.")


# ================================================================ dispatch

OFFICE = {"docx", "xlsx", "xlsm", "pptx", "odt", "ods", "odp"}
AUDIO_VIDEO = {"mp3", "m4a", "aac", "flac", "ogg", "opus", "wav", "wma", "aiff", "mp4", "mkv", "webm", "mov", "m4v", "avi", "wmv"}


def kind_of(fmt: str | None) -> str | None:
    if fmt in IMG_FORMATS:
        return "image"
    if fmt == "pdf":
        return "pdf"
    if fmt in OFFICE:
        return "office"
    if fmt in AUDIO_VIDEO:
        return "media"
    return None


def supports(fmt: str | None) -> bool:
    return kind_of(fmt) is not None


def read(path, fmt: str) -> list[Field]:
    path = Path(path)
    kind = kind_of(fmt)
    try:
        if kind == "image":
            return read_image(path, fmt)
        if kind == "pdf":
            return read_pdf(path)
        if kind == "office":
            return read_office(path, fmt)
        if kind == "media":
            return read_media(path)
    except ConversionError:
        raise
    except Exception as e:                                   # noqa: BLE001
        raise ConversionError(f"Couldn't read this file's properties: {e}") from e
    return _file_fields(path)


def common_fields(paths: list, fmts: list[str]) -> list[Field]:
    """For several files: the writable fields every one of them has, blank so only what you type changes."""
    keysets = []
    labelled: dict[str, Field] = {}
    for p, f in zip(paths, fmts):
        fields = [x for x in read(p, f) if x.writable and not x.key.startswith("file_")]
        keysets.append({x.key for x in fields})
        for x in fields:
            labelled.setdefault(x.key, x)
    shared = set.intersection(*keysets) if keysets else set()
    return [Field(x.key, x.label, "", x.group, True, x.hint) for k, x in labelled.items() if k in shared]


def _write(src: Path, dst: Path, fmt: str, changes: dict, strip_all: bool) -> None:
    kind = kind_of(fmt)
    if kind == "image":
        write_image(src, dst, fmt, changes, strip_all)
    elif kind == "pdf":
        write_pdf(src, dst, changes, strip_all)
    elif kind == "office":
        write_office(src, dst, fmt, changes, strip_all)
    elif kind == "media":
        write_media(src, dst, changes, strip_all)
    else:
        raise ConversionError(f"Can't edit the properties of a {fmt.upper()} file.")


def apply(path, fmt: str, changes: dict, strip_all: bool = False, cfg: dict | None = None) -> Path:
    """Edit a file's metadata. Keys in ``changes`` map to new text (or None / "" to clear).

    The result replaces the file (the original goes to the Recycle Bin) unless the setting says to keep originals,
    in which case it is saved as "name (edited).ext". Returns the resulting path."""
    cfg = cfg or {}
    path = Path(path)
    file_changes = {k: v for k, v in changes.items() if k.startswith("file_")}
    changes = {k: v for k, v in changes.items() if not k.startswith("file_")}
    result = path
    if changes or strip_all:
        work = Path(tempfile.mkdtemp(prefix=".ipmeta-", dir=path.parent))
        try:
            tmp = work / path.name
            _write(path, tmp, fmt, changes, strip_all)
            st = path.stat()
            action = cfg.get("original", "recycle")
            if action == "keep":
                from .engine import unique_path
                result = unique_path(path.with_name(f"{path.stem} (edited){path.suffix}"))
                shutil.move(str(tmp), result)
            else:
                if action == "recycle":
                    from send2trash import send2trash
                    send2trash(str(path))
                result = path
                shutil.move(str(tmp), result)
            if cfg.get("preserve_mtime", True) and "file_modified" not in file_changes:
                try:
                    os.utime(result, (st.st_atime, st.st_mtime))
                except OSError:
                    pass
        finally:
            shutil.rmtree(work, ignore_errors=True)
    if file_changes:
        set_file_times(result, file_changes.get("file_created") or None, file_changes.get("file_modified") or None)
    return result


def parse_assignments(items: list[str], fields: list[Field]) -> dict[str, str]:
    """'Author=Ada' -> {'author': 'Ada'}, matching the key or the label case-insensitively."""
    by = {}
    for f in fields:
        by[f.key.lower()] = f.key
        by[f.label.lower()] = f.key
    out = {}
    for item in items:
        name, sep, value = item.partition("=")
        if not sep:
            raise ConversionError(f"'{item}' should look like Name=Value.")
        key = by.get(name.strip().lower().replace("_", " ")) or by.get(name.strip().lower())
        if key is None:
            raise ConversionError(f"No property called '{name}'. Choose from: " + ", ".join(sorted({f.label for f in fields if f.writable})))
        out[key] = value.strip()
    return out


__all__ = ["Field", "GROUPS", "read", "apply", "supports", "kind_of", "common_fields", "parse_assignments", "set_file_times", "parse_gps", "io"]
