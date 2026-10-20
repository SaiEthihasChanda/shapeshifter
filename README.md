# ShapeShifter

Convert any file to another format **in place**, straight from File Explorer. Select a
file, press **Ctrl+Alt+C** (or right-click › *Convert to…*), and a wheel of everything that
file can become pops up under your mouse. Move to a format, click, done: `holiday.jpg`
becomes `holiday.png` in the same folder, and the original goes to the Recycle Bin.

ShapeShifter works out what a file **really** is from its contents, so renamed,
extension-less and unknown files work too. You can also teach it formats it has never
seen.

## Install

**Download:** get `ShapeShifter-Setup.exe` from the [latest release](https://github.com/SaiEthihasChanda/shapeshifter/releases/latest)
and check it against `SHA256SUMS.txt`. The installer is unsigned, so SmartScreen may warn: choose *More info > Run anyway*.
Or build it yourself with `build.ps1` (needs [Inno Setup 6](https://jrsoftware.org/isinfo.php); falls back to a plain self-extracting setup without it).
- Installs for your account only, into `%LOCALAPPDATA%\Programs\ShapeShifter`. No admin rights needed.
- Everything is included (Python, libraries, ffmpeg). Nothing else to install.
- `/S` gives a silent install, and `/S /NOSTARTUP` also skips starting with Windows.
- An existing *Inplace Converter* install is migrated automatically: settings, hotkeys and theme carry over, and the old install is removed.

Uninstall from **Settings › Apps › Installed apps › ShapeShifter**. You can keep or remove your settings.

**From source** (developers): `install.cmd` / `install.ps1` installs into a private Python
environment. It needs Python 3.10+ from python.org; Anaconda and Store builds don't work.

## Use

| How | What |
|---|---|
| **Ctrl+Alt+C** | Opens the wheel for the files selected in Explorer or on the desktop. |
| **Ctrl+Alt+Shift+C** | Repeats your last conversion on the selection, with no wheel. |
| Your own shortcuts | Key combos or **mouse buttons**: middle click, Mouse 4/5, or modifier + click/scroll. Each one can open the wheel, repeat the last conversion, or convert straight to a format. Mouse triggers only act over Explorer and the desktop. |
| Right-click › **Convert to…** | Available on every file. On Windows 11 it's under *Show more options*, or Shift+right-click. |
| Start Menu › **ShapeShifter** | Opens the settings app. |

On the wheel:
- **Mouse:** point at a format and click, or turn on *convert by hovering*.
- **More than 12 options:** they spread over **concentric rings** (10 in the inner ring, then 16, 22, …), which rotate in one after another when the wheel opens.
- **Type to search:** just start typing (`p`, `pn`, `jpeg`, `.webp`). Matching formats light up, the best match is selected, and Enter converts. Backspace edits, and the first Esc clears the search.
- **Keyboard:** arrow keys or Tab (cycling through the matches while searching), Enter to convert, Esc cancels or stops.
- **!** badge: the option loses something (lossy, transparency flattened, first frame or sheet only, several steps). Hover it to see what.
- **Special segments:**
  - **.PNG – Fix name:** the content doesn't match the name, so this renames the file.
  - **EXTRACT:** unpacks an archive into a folder.
  - **CSV? / YAML? – Treat as:** ShapeShifter isn't sure what the file is. Pick one, and the wheel shows that format's conversions.
  - **INSPECT:** shows what an unknown file contains.
  - **Inferred group (pink):** options that only exist because ShapeShifter looked *inside* the file: **FILL FORM**, and text recognition (OCR) to TXT, Markdown and HTML.
  - **Targets that ask how:** a target such as **DOCX** or **PDF** is shown once, even when it can mean different things. For a picture, DOCX can be *Text (OCR)* or *Picture*, and PDF can be *Picture* or *Searchable*. Choosing it makes ShapeShifter look at the file first: if the picture has no text it just converts (a plain PDF or a Word page with the picture), and if it has text the wheel asks which way you want. The centre button (or Esc) goes back. A PDF only offers **OCR PDF** when it has scanned pages.

## Configured and inferred file types

ShapeShifter knows about **360+ file types** explicitly: name, category, extensions, how to
recognise them, and how they relate to other types (the *catalogue*, `shapeshifter/catalog.py`).
Anything else is still inferred from its contents. Configured kinds include:

| Kind | Examples | What they can become |
|---|---|---|
| Certificates & keys | PEM, DER, CRT, CER, CSR | PEM ↔ DER, certificate details as text or JSON. **Private keys** can only be re-encoded (PEM ↔ DER), never turned into documents. |
| Subtitles & lyrics | SRT, WebVTT, ASS/SSA, SBV, LRC | Each other, plain text |
| Geodata | GPX, KML, GeoJSON | Each other, CSV of points |
| Calendars & contacts | ICS, VCF | CSV/JSON, and back from CSV/JSON |
| E-mail & web archives | EML, MBOX, MHTML | Text, Markdown, HTML; a mailbox splits into messages |
| Notebooks | IPYNB | Markdown, HTML, Python script |
| Data | JSON Lines, JSON5, plist / binary plist, .env, .properties, torrent | JSON, CSV, YAML, TOML, … |
| 3D models | OBJ, STL (text/binary), PLY | Each other (geometry) |
| Fonts | TTF, OTF, WOFF | WOFF ↔ TTF/OTF |
| Source code | 80+ languages (Python, Rust, Go, C#, TS, SQL, …) | Rendered as HTML, Markdown (fenced) or PDF; plain text |
| Archives | ZST, LZMA, AR/DEB, wheels, NuGet, VSIX, … | Extract here, ZIP, 7z, TAR |
| Recognised (identify only) | Parquet, HDF5, glTF/GLB, FBX, Blender, ISO, VHDX, RAW photos, MIDI, … | Shown in Inspect |

## Learned reverse conversions

Whenever you convert **A → B**, ShapeShifter remembers that **B → A** is wanted. The next time
you select a B file, A appears on the wheel, labelled *Learned* with a warning badge. If there's
no proper converter, it uses the best route the graph can find, even a lossy one. For example,
after PEM → MD, an MD file offers PEM via MD → text → PEM.

Manage these under Settings › Formats & mappings › *Learned reverse conversions*. You can
switch learning off there, or forget individual pairs.

## The conversion graph

Inside, every file type is a **node** and every conversion step is a **one-way edge**,
labelled with the engine that performs it. A conversion is a path through this graph:
ShapeShifter searches it for the cheapest, least lossy route, which is how multi-step
conversions such as DOCX → PDF → PNG come about.

Some edges are **latent**, for example "plain text → any text-based format". They're never
offered on their own; they only complete routes such as learned reverse conversions.

**Settings › Graph** shows the whole graph as a **sphere** and lets you explore it:
- Each file type is a dot, **evenly spaced** on the sphere (a Fibonacci lattice), banded by category and sized by how connected it is.
- **Drag** to spin, scroll to zoom, or tick **Auto-rotate**. Clicking a type turns the sphere to bring it to the front.
- Switch to **Flat** for a 2D clustered layout with draggable nodes.
- **Hover** a type to light up what it becomes (green) and what it's made from (blue).
- **Click** a type to see every step in a side panel: engine, what's lost, and whether it's on the wheel. Untick a step to hide it from the wheel, or click a row to jump to that type.
- **Flat view:** scroll to zoom, drag the background to pan, and drag dots around. **Spread out** runs an animated spring layout.
- **Search** for a type, **show a route** from A to B (highlighted in orange), filter by category, or hide latent steps (dashed).

The route finder and DOT/JSON export are also under Settings › Formats & mappings.

## How files are recognised

The extension is only a hint. ShapeShifter checks, in order:
1. **Your format descriptions** (see below)
2. **Byte signatures** (PNG, PDF, 7z, SQLite, EXE, …)
3. **Containers:** ZIP contents (DOCX/XLSX/EPUB/ODF/CBZ/JAR), RIFF, MP4-family brands, Matroska, Ogg, gzip
4. **Text sniffing:** JSON, CSV/TSV, XML/SVG/HTML, YAML, TOML, INI, Markdown
5. **ffmpeg's media probe**
6. **Optional:** Google's Magika AI model (Settings › Engines)

## Documents keep their pictures and formatting

Whenever ShapeShifter *creates* a PDF or Word document, the pictures, links, bold/italic, tables,
lists and non-Latin text come along:

| Conversion | How |
|---|---|
| HTML / Markdown / text → **PDF** | PyMuPDF's layout engine: images (files or embedded), tables with borders, links (also internal ones), styles, code blocks and every script (Latin, Cyrillic, Greek, CJK…). The text is real, copyable text (ligatures like "fi" are stored as two letters, so searching works). |
| DOCX / ODT / EPUB → HTML, Markdown | pandoc, with the pictures **embedded** (HTML) or **extracted** into a `<name>_files` folder next to the file (Markdown, RST, LaTeX). Previously they were silently dropped. |
| DOCX → **image** | DOCX → HTML → PDF → image: pictures kept. No LibreOffice needed. |
| PDF → **HTML** | Text plus pictures (embedded), reflowed. Scanned pages are recognised with OCR instead. |
| PDF → **DOCX**, Markdown | via HTML, so pictures are kept. |
| PDF → **SVG** | One vector file per page: text as outlines, pictures embedded. The most faithful non-raster copy. |
| Markdown / HTML / text → **DOCX** | pandoc if installed, otherwise ShapeShifter's own Word writer (headings, bold/italic/code, links, lists, tables, quotes, pictures). |

PDF → PNG/JPG/WEBP/TIFF renders each page exactly as MuPDF displays it, with pictures, transparency,
vectors, annotations and form fields (checked pixel-for-pixel). Resolution is *Settings › General ›
PDF → image resolution* (150 DPI by default; raise it for sharper text). Rasterising can't keep
selectable text or links: use PDF → SVG or *OCR PDF* when you need those.

Limits: remote images (`http://…`) are never downloaded; the native PDF writer
(*Prefer ShapeShifter's own converters*) keeps pictures, tables and lists but flattens inline
bold/italic/links, which the PyMuPDF engine keeps.

## Text recognition (OCR)

ShapeShifter reads text from scans and photos using the recogniser built into **Windows 10/11**
(nothing to install; it needs at least one OCR language in Windows Settings › Time & language ›
Language & region).

| From | To | Notes |
|---|---|---|
| Any image (JPG, PNG, TIFF, HEIC, WEBP, …) | **TXT, Markdown, HTML, DOCX (Text)** | Paragraphs are rebuilt from line positions: prose is re-flowed, short lines (forms, addresses) keep their breaks, and clearly larger lines become headings. |
| Any image | **PDF (Searchable)** | The image on a page with an invisible text layer: looks identical, but searchable and copyable. JPEGs are embedded untouched. (Command line: `--to pdfocr`.) |
| Any image | **DOCX (Picture)** | The picture on a Word page, no recognition: for adding text next to it, then converting back to an image or PDF. (Command line: `--to docxpic`.) |
| Scanned PDF | **OCR PDF** | Adds the text layer to pages that have none. |
| Scanned PDF | TXT, HTML, DOCX, Markdown | Pages without a text layer are recognised automatically. |

Set the language under *Settings › General › Text recognition* ("Automatic" uses your Windows
languages), and use *Settings › Engines › Test OCR* to check it works. OCR is for printed text;
handwriting is unreliable, and tables, columns and form boxes are not reconstructed. The
recognised text can contain mistakes, so those wheel options carry an amber **!** badge.

Typical workflow: *scan → DOCX (fill it in) → image or PDF*. The typed text ends up in the image as
pixels, not as selectable text; follow it with *OCR PDF* if you want it searchable.

## Filling in a form that is only a picture

Pick **FILL FORM** on the wheel for a photo or scan of a form, or for a **PDF** (scanned, or an ordinary PDF with printed blanks). ShapeShifter finds the blanks next to labels
(a line or free space), square check boxes and signature lines, and opens a window with the form on the left
as a live preview and the fields on the right.

- Type the answers, tick boxes, and add a signature: **Draw…** it with the mouse or **Choose image…** (a scan or photo of
  your signature; the white background is removed). It remembers your signature for next time.
- Missed a field? Press **+ Text**, **+ Check box** or **+ Signature**, then click where it goes on the form.
- Pick the writing style (typed, handwriting, typewriter), ink colour and size. **Save filled image** writes
  `form (filled).png` next to the original, which is never changed.
- **PDFs:** every page (up to 30) is read, and you move between them with *Previous / Next page*. The saved copy is
  `form (filled).pdf`: your answers are laid over the original pages, so their text and sharpness are kept. If the PDF has
  real fillable fields, those are used and filled in natively, so they stay editable in any PDF reader.
- **Several files:** select more than one form and each opens in its own window.
- From the command line: `shapeshifter fill form.png --list`, or `shapeshifter fill form.png --set "Full name=Ada" --tick Student --sign sig.png`.

Detection uses Windows OCR plus a look at the pixels, so very skewed, low-contrast or handwritten forms may need
fields added by hand. Pages that are rotated by the PDF itself (sideways scans) aren't read well yet, and password-protected
PDFs can't be filled.

## Risky files are refused

These are never converted, and the wheel says why:
- programs and scripts, including ones **disguised under another extension**
- archives with unsafe paths, links, password protection, or bomb-like compression
- Office files with macros
- encrypted PDFs
- images too large to decode safely

## Engines

Every conversion step is an edge in one graph. The planner picks the cheapest, least
lossy route, including **multi-step** routes such as comic → PDF → PNG pages or TXT → Markdown → DOCX.

| Engine | Handles |
|---|---|
| **ShapeShifter native** (from scratch, no third-party code) | PNG (all variants), BMP, TGA, PPM/PGM/PBM, QOI, ICO, GIF and baseline JPEG decoding, PDF writing (JPEGs embedded losslessly), Markdown ↔ HTML ↔ text → PDF, WAV ↔ AIFF, XLSX read/write |
| Pillow (+ pillow-heif) | JPEG, WEBP, AVIF, HEIC, TIFF, animated GIF/WEBP/APNG, … |
| PyMuPDF | PDF/SVG/e-books → images, text, HTML, SVG; HTML/Markdown/text → PDF (layout engine) |
| Windows OCR | Images and scanned PDFs → text, Markdown, HTML, DOCX, searchable PDF |
| ffmpeg | All audio and video, video → GIF, soundtrack extraction |
| Built-in data | CSV, TSV, XLSX, SQLite, JSON, YAML, TOML, XML, INI |
| Built-in archives | ZIP ↔ 7z ↔ TAR/.gz/.bz2/.xz, Extract here |
| pandoc / LibreOffice (optional) | Rich documents: DOCX, ODT, EPUB, PPTX, XLS, … |

*Settings › General › Prefer ShapeShifter's own converters* routes through the native
engine first. Anything it can't handle, such as a progressive JPEG or a very large
image, falls back to the libraries automatically.

## Teach it a new format

Settings › Formats & mappings › **Custom formats**, or **Inspect › Describe this format…**
for a template pre-filled from a sample file. A description is a small YAML file in
`%APPDATA%\ShapeShifter\formats\`:

```yaml
id: slog
name: Sensor log
extensions: [slog]
category: data
match:
  - ascii: "SLOG"              # or magic: "534C4F47", or text_regex: "^#SENSOR"
decode:
  type: records                # fixed-size binary records -> table
  header: 8
  endian: little
  fields:
    - {name: time, type: u32}
    - {name: temp, type: f32}
    - {name: tag,  type: str, size: 4}
```

Other decode types:
- `alias`: the file is really another format (`as: png`)
- `embedded`: another format sits after a header (`as: png`, `offset:` or `search:`)
- `text-table`: lines become rows (`delimiter:` or a `regex:` with named groups)

After you press *Reload*, the format is recognised by content and gets every conversion
reachable from what it decodes to.

## Settings app

| Page | Contents |
|---|---|
| General | Original file (Recycle Bin / keep / delete), keep modified date, prefer native, quality, PDF DPI, OCR language, start with Windows, right-click entry |
| Hotkeys | Keyboard and mouse shortcuts: record by pressing them, with conflict detection and live status |
| Wheel | Six themes, accent colour, size, text size, opacity, animation, position, hover-to-convert, live preview |
| Formats & mappings | Every source → target with engine, route and loss notes. Hide any pair. *Identify a file…*, custom formats, learned conversions, route finder, graph export. |
| Graph | The interactive conversion graph: explore, search, highlight routes, toggle steps on or off the wheel |
| Engines | What's installed. One-click install for ffmpeg, LibreOffice, pandoc, Magika. *Test OCR* checks Windows text recognition and lists its languages. |

## Command line

```
ShapeShifter.exe convert --to webp *.png          # or: python -m shapeshifter …
ShapeShifter.exe convert --to extract backup.7z
ShapeShifter.exe identify mystery.dat              # what is it, and how sure?
ShapeShifter.exe targets photo.heic                # what can it become (with routes/warnings)
ShapeShifter.exe selftest                          # check every engine
ShapeShifter.exe settings | daemon | install | uninstall | register
```

The command line also reaches targets that are hidden from the wheel (FLV, TS, 3GP, MPG, OGV, …).

## Building the installer

```
powershell -ExecutionPolicy Bypass -File build.ps1            # bundles ffmpeg from PATH
powershell -ExecutionPolicy Bypass -File build.ps1 -NoFFmpeg  # small; setup offers winget
```

The build runs the tests, freezes the app with PyInstaller, self-tests the frozen
`ShapeShifter.exe`, and writes `dist\ShapeShifter-Setup-<version>.exe`.

## Development

```
py -3.12 -m venv .venv
.venv\Scripts\pip install -e . pytest
.venv\Scripts\python -m pytest tests
.venv\Scripts\python -m shapeshifter open some_file.jpg
```

Design notes: `docs/PLAN-universal-conversion.md`. Logs: `%LOCALAPPDATA%\ShapeShifter\shapeshifter.log`.

## Licence

ShapeShifter is free software under the **GNU AGPL v3 or later** (see [LICENSE](LICENSE)); bundled third-party
components are listed in [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md). Maintainer notes: [docs/RELEASING.md](docs/RELEASING.md).
