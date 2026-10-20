"""Format metadata: canonical ids, extensions, names, categories and display order."""

from __future__ import annotations

from pathlib import Path

# Extensions that refer to the same format. Keys are aliases, values canonical ids.
ALIASES = {
    "jpeg": "jpg", "jpe": "jpg", "jfif": "jpg",
    "tif": "tiff",
    "heif": "heic",
    "yml": "yaml",
    "htm": "html",
    "markdown": "md",
    "aif": "aiff",
    "mpeg": "mpg",
    "tar.gz": "tgz", "tar.bz2": "tbz2", "tbz": "tbz2", "tar.xz": "txz",
    "sqlite3": "sqlite", "db3": "sqlite", "db": "sqlite",
    "pgm": "ppm", "pbm": "ppm", "pnm": "ppm",
    "cfg": "ini", "conf": "ini",
}

# Multi-part extensions, checked before the last suffix.
COMPOUND_EXTS = ("tar.gz", "tar.bz2", "tar.xz")

# Extension written for a format when it differs from its id.
PRIMARY_EXT = {"tgz": "tar.gz", "tbz2": "tar.bz2", "txz": "tar.xz", "pdfocr": "pdf"}

# Output-only "formats": a variant of a real format that gets its own wheel segment.
# (A "searchable PDF" is still a .pdf; it just carries a recognised-text layer.)
PSEUDO_FORMATS = {"pdfocr"}
LABELS = {"pdfocr": "OCR PDF"}

# category -> formats, in the order they should appear on the wheel.
CATEGORIES = {
    "image": ["jpg", "png", "webp", "avif", "heic", "gif", "bmp", "tiff", "ico", "tga", "qoi",
              "jp2", "pcx", "ppm", "svg", "psd", "dds", "icns", "eps"],
    "video": ["mp4", "mkv", "webm", "mov", "avi", "wmv", "flv", "m4v", "mpg", "ts", "3gp", "ogv"],
    "audio": ["mp3", "wav", "flac", "m4a", "ogg", "opus", "aac", "wma", "aiff", "ac3", "amr"],
    "document": ["pdf", "docx", "odt", "rtf", "doc", "txt", "md", "html", "epub", "xps", "oxps",
                 "mobi", "fb2", "cbz", "rst", "tex", "pptx", "odp", "ppt", "xml", "docm"],
    "data": ["csv", "tsv", "json", "yaml", "xlsx", "ods", "xls", "toml", "ini", "sqlite", "xlsm"],
    "archive": ["zip", "7z", "tar", "tgz", "txz", "tbz2", "gz", "bz2", "xz", "rar", "jar", "apk"],
    "program": ["exe", "dll", "msi", "com", "scr", "sys", "bat", "cmd", "ps1", "vbs", "vbe", "js",
                "jse", "wsf", "wsh", "hta", "cpl", "lnk", "reg", "elf", "macho", "sh"],
    # Filled from catalog.py:
    "code": [], "subtitle": [], "security": [], "model": [], "font": [],
}

CATEGORY_OF = {ext: cat for cat, exts in CATEGORIES.items() for ext in exts}
CATEGORY_OF["pdfocr"] = "document"

NAMES = {
    "jpg": "JPEG image", "png": "PNG image", "webp": "WebP image", "avif": "AVIF image",
    "heic": "HEIC image", "gif": "GIF image", "bmp": "Bitmap image", "tiff": "TIFF image",
    "ico": "Windows icon", "tga": "Targa image", "qoi": "QOI image", "jp2": "JPEG 2000 image",
    "pcx": "PCX image", "ppm": "Netpbm image", "svg": "SVG drawing", "psd": "Photoshop document",
    "dds": "DirectDraw texture", "icns": "macOS icon", "eps": "EPS drawing",
    "mp4": "MP4 video", "mkv": "Matroska video", "webm": "WebM video", "mov": "QuickTime video",
    "avi": "AVI video", "wmv": "Windows Media video", "flv": "Flash video", "m4v": "M4V video",
    "mpg": "MPEG video", "ts": "MPEG transport stream", "3gp": "3GP video", "ogv": "Ogg video",
    "mp3": "MP3 audio", "wav": "WAV audio", "flac": "FLAC audio", "m4a": "M4A audio",
    "ogg": "Ogg Vorbis audio", "opus": "Opus audio", "aac": "AAC audio", "wma": "Windows Media audio",
    "aiff": "AIFF audio", "ac3": "AC-3 audio", "amr": "AMR audio",
    "pdf": "PDF document", "docx": "Word document", "docm": "Word document with macros",
    "odt": "OpenDocument text", "rtf": "Rich Text document", "doc": "Word 97 document",
    "txt": "Plain text", "md": "Markdown", "html": "HTML page", "epub": "EPUB e-book",
    "xps": "XPS document", "oxps": "OpenXPS document", "mobi": "Mobipocket e-book",
    "fb2": "FictionBook e-book", "cbz": "Comic book archive", "rst": "reStructuredText",
    "tex": "LaTeX source", "pptx": "PowerPoint presentation", "odp": "OpenDocument presentation",
    "ppt": "PowerPoint 97 presentation", "xml": "XML document",
    "csv": "CSV table", "tsv": "TSV table", "json": "JSON data", "yaml": "YAML data",
    "xlsx": "Excel workbook", "xlsm": "Excel workbook with macros", "ods": "OpenDocument spreadsheet",
    "xls": "Excel 97 workbook", "toml": "TOML config", "ini": "INI config", "sqlite": "SQLite database",
    "zip": "ZIP archive", "7z": "7-Zip archive", "tar": "TAR archive", "tgz": "Gzipped TAR archive",
    "txz": "XZ TAR archive", "tbz2": "Bzip2 TAR archive", "gz": "Gzip-compressed file",
    "bz2": "Bzip2-compressed file", "xz": "XZ-compressed file", "rar": "RAR archive",
    "jar": "Java archive", "apk": "Android package",
    "pdfocr": "Searchable PDF (OCR)",
    "exe": "Windows program", "dll": "Windows library", "msi": "Windows installer",
    "elf": "Linux program", "macho": "macOS program", "lnk": "Windows shortcut",
}

# Overall preference order used to rank wheel targets (lower = shown first).
_ORDER = {ext: i for i, ext in enumerate(
    ["png", "jpg", "webp", "pdf", "mp4", "mp3", "gif", "docx", "csv", "json", "xlsx",
     "zip", "7z", "wav", "mkv", "webm", "avif", "heic", "flac", "m4a", "txt", "html", "md",
     "yaml", "tgz", "tar", "txz", "mov", "bmp", "tiff", "ico", "ogg", "opus", "avi", "odt",
     "rtf", "tsv", "epub", "aac", "wmv", "aiff", "wma", "flv", "m4v", "tga", "qoi",
     # configured kinds: useful ones before niche exporters
     "srt", "vtt", "geojson", "gpx", "kml", "stl", "obj", "ply", "pem", "der", "ttf", "otf",
     "woff", "xml", "toml", "ass", "lrc", "sbv", "jsonl", "ppm", "ics", "vcf", "plist", "env",
     "properties", "bplist"]
)}

# Category colours (base hue for the wheel segments).
CATEGORY_COLORS = {
    "image": "#2bb5a5",
    "video": "#f08a3c",
    "audio": "#9b6bff",
    "document": "#4a8fff",
    "data": "#46c06b",
    "archive": "#d4a72c",
    "tool": "#8a8f9c",
    "code": "#e0607e",
    "subtitle": "#c9a227",
    "security": "#d9534f",
    "model": "#5bc0de",
    "font": "#b07cc6",
}


def canonical(ext: str) -> str:
    ext = ext.lower().lstrip(".")
    return ALIASES.get(ext, ext)


# Files known by their whole name rather than an extension.
FILENAMES = {
    "makefile": "makefile", "gnumakefile": "makefile", "dockerfile": "dockerfile",
    "containerfile": "dockerfile", "jenkinsfile": "groovy", "vagrantfile": "rb", "gemfile": "rb",
    "rakefile": "rb", "podfile": "rb", "brewfile": "rb", "cmakelists.txt": "cmake",
    "build.gradle": "gradle", "pipfile": "toml", "cargo.lock": "toml", "go.mod": "go",
    ".bashrc": "bash", ".bash_profile": "bash", ".zshrc": "bash", ".profile": "bash",
    ".bash_aliases": "bash", ".zprofile": "bash", ".npmrc": "ini", ".yarnrc": "yaml",
    ".pylintrc": "ini", ".flake8": "ini", ".gitconfig": "ini", ".gitmodules": "ini",
    ".babelrc": "json", ".prettierrc": "json", ".eslintrc": "json", ".jshintrc": "json",
    ".htaccess": "txt", ".mailmap": "txt", "license": "txt", "licence": "txt", "copying": "txt",
    "authors": "txt", "changelog": "txt", "readme": "txt", "todo": "txt", "notice": "txt",
    "codeowners": "gitignore", ".clang-format": "yaml", ".travis.yml": "yaml",
}


def split_name(path: str | Path) -> tuple[str, str]:
    """('photo', 'jpg') for photo.jpg; ('backup', 'tgz') for backup.tar.gz; ('README', 'txt')
    for well-known names; ('.gitignore', 'gitignore') for dotfiles."""
    name = Path(path).name
    low = name.lower()
    if low in FILENAMES:
        return name, FILENAMES[low]
    if low.startswith(".") and low.count(".") == 1 and len(low) > 1:
        # Dotfiles: the part after the dot is the type (".gitignore", ".env", ".editorconfig").
        # The base keeps the full name so outputs read ".gitignore.html", not ".html".
        return name, canonical(low[1:])
    for comp in COMPOUND_EXTS:
        if low.endswith("." + comp) and len(name) > len(comp) + 1:
            return name[: -len(comp) - 1], canonical(comp)
    p = Path(name)
    return (p.stem, canonical(p.suffix)) if p.suffix else (name, "")


def ext_of(path: str | Path) -> str:
    return split_name(path)[1]


def file_ext(fmt: str) -> str:
    """Extension (without dot) to write for a format id."""
    return PRIMARY_EXT.get(fmt, fmt)


def label_of(fmt: str) -> str:
    """Short text for a wheel segment."""
    return LABELS.get(fmt) or (file_ext(fmt).upper() if fmt in ("tgz", "txz", "tbz2") else fmt.upper())


def category(ext: str) -> str:
    return CATEGORY_OF.get(canonical(ext), "document")


def name_of(fmt: str) -> str:
    return NAMES.get(fmt, f"{fmt.upper()} file")


def rank(ext: str) -> tuple[int, str]:
    return (_ORDER.get(ext, 1000), ext)


def search_terms(fmt: str) -> list[str]:
    """Words a user might type to find a format on the wheel (id, extensions, name)."""
    terms = {fmt, file_ext(fmt)}
    terms |= {alias for alias, canon in ALIASES.items() if canon == fmt}
    words = name_of(fmt).replace("(", " ").replace(")", " ").replace("/", " ").split()
    terms |= {w.lower() for w in words if w.lower() not in _GENERIC_WORDS}
    return sorted(terms)


_GENERIC_WORDS = {"image", "file", "audio", "video", "document", "data", "source", "archive",
                  "table", "text", "format", "with", "and", "the", "of", "model", "3d", "font",
                  "script", "compressed", "package", "plain", "web", "page", "config"}


# The configured catalogue adds names, categories and extensions for hundreds of types.
from . import catalog as _catalog  # noqa: E402

_catalog.register()
