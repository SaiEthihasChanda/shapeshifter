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

### Mac (Apple Silicon)

**Download:** get `ShapeShifter-macOS.dmg` from the [latest release](https://github.com/SaiEthihasChanda/shapeshifter/releases/latest),
open it and drag ShapeShifter onto Applications. Needs macOS 12 or later on an M1 or newer Mac.
- The app isn't notarised by Apple, so the first time macOS says it can't check it. Open **System Settings › Privacy & Security**,
  scroll down and click **Open Anyway** next to ShapeShifter (or run `xattr -dr com.apple.quarantine /Applications/ShapeShifter.app`).
  You only do this once; updates install themselves.
- ShapeShifter lives in the **menu bar**. It adds **right-click › Quick Actions › Convert with ShapeShifter** to Finder and starts when you log in.
- Hotkeys (**⌃⌥C** opens the wheel for the files selected in Finder) and mouse triggers need two permissions, which macOS asks for
  the first time: **Accessibility** (to see the hotkey) and **Automation › Finder** (to see which files are selected). Settings › General
  shows whether they're on.
- Everything works as on Windows; text recognition uses the one built into macOS, and the original goes to the Trash.
- Uninstall from **Settings › About › Uninstall ShapeShifter…** (removes the Quick Action, start at login and settings, and moves the
  app to the Trash).

Build it yourself on a Mac with `packaging/build_mac.sh` (Python 3.10+ from python.org).

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

Manage these under Settings › Advanced › *Learned reverse conversions*. You can
switch learning off there, or forget them all.

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

The route finder is also in Settings › Graph (type a route and click *Show route*). DOT/JSON export is under Settings › Advanced.

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
vectors, annotations and form fields (checked pixel-for-pixel). Resolution is *Settings › Advanced ›
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

Set the language under *Settings › Advanced › Text recognition language* ("Automatic" uses your Windows
languages), and use *Settings › Engines › Test OCR* to check it works. OCR is for printed text;
handwriting is unreliable, and tables, columns and form boxes are not reconstructed. The
recognised text can contain mistakes, so those wheel options carry an amber **!** badge.

Typical workflow: *scan → DOCX (fill it in) → image or PDF*. The typed text ends up in the image as
pixels, not as selectable text; follow it with *OCR PDF* if you want it searchable.

## Filling in a form that is only a picture

Pick **FILL FORM** on the wheel for a photo or scan of a form, or for a **PDF** (scanned, or an ordinary PDF with printed blanks). ShapeShifter finds the blanks next to labels
(a line, a dotted line or free space), blanks inside sentences, empty table cells and boxes, square check boxes and
signature lines, and opens a window with the form on the left as a live preview and the fields on the right. A crooked
photo or scan is straightened before it is read.

- Type the answers, tick boxes, and add a signature: **Draw…** it with the mouse or **Choose image…** (a scan or photo of
  your signature; the white background is removed). It remembers your signature for next time.
- Missed a field? Press **+ Text**, **+ Check box** or **+ Signature**, then click where it goes on the form.
- **Move and resize** any field: drag its frame, or drag its edges and corners. Arrow keys nudge the selected one (Shift
  for bigger steps) and Delete removes it.
- Pick the writing for **all answers** (typed, handwriting, typewriter, or any font installed in Windows), the ink colour
  and the size; select a field to give **that answer** its own font and size. **Save filled image** writes
  `form (filled).png` next to the original, which is never changed.
- **PDFs:** every page (up to 30) is read, and you move between them with *Previous / Next page*. The saved copy is
  `form (filled).pdf`: your answers are laid over the original pages, so their text and sharpness are kept. If the PDF has
  real fillable fields, those are used and filled in natively, so they stay editable in any PDF reader.
- **Several files:** select more than one form and each opens in its own window.
- From the command line: `shapeshifter fill form.png --list`, or `shapeshifter fill form.png --set "Full name=Ada" --tick Student --sign sig.png`.

Detection uses Windows OCR plus a look at the pixels, so very skewed, low-contrast or handwritten forms may need
fields added by hand. Pages that are rotated by the PDF itself (sideways scans) aren't read well yet, and password-protected
PDFs can't be filled.

## Tables, in and out of documents

ShapeShifter finds tables and uses them as data. **PDF, Word, OpenDocument, web pages, Markdown, PowerPoint slides and even
pictures** (read with Windows OCR) can become **CSV, TSV, Excel, ODS or JSON**, and spreadsheets and CSV files can become
**HTML, Markdown, Word, PDF or ODS** tables. In a PDF, a table whose header repeats on the next page is joined into one, and
cell text is read from the cells themselves (so `14-07-2025` stays that). Tables read from a picture or a PDF's page layout carry
a warning: check the columns. Longer routes chain these steps (SQLite → Excel → Word, picture of a table → Excel → PDF).

## Presentations

**PPTX, PPSX and ODP** are read from their shapes (text boxes, bullets, pictures, tables, colours, speaker notes) and can be
drawn to **PDF**, turned into outlines (**text, Markdown, HTML, Word**), or converted **between PPTX and ODP**. **PDFs, pictures and
documents become slides** (a PDF page becomes a picture slide, with its text as alt text; Word and Markdown headings become slide
titles). Slides are redrawn, not rendered by PowerPoint, so fonts, effects and animations are simplified (the wheel says so).
Old **.ppt** files are read for their text. The PPTX files ShapeShifter writes follow the standard and open in pandoc; they
haven't been opened in PowerPoint itself.

## Ways to reach a target

A target can mean different things, so the wheel asks, but **only when the file really has a choice**:

| From | To | Ways |
|---|---|---|
| Picture | DOCX | *Text (OCR)* / *Picture* (the picture on a Word page) |
| Picture | PDF | *Picture* / *Searchable* (adds a text layer) |
| Animated GIF / WebP / TIFF | PNG, JPG, … | *Keep animation* or *First frame* / *Every frame* / *Sprite sheet* |
| Workbook or database (several sheets) | CSV, TSV | *First sheet* / *Every sheet* (one file each) |
| Several-page PDF, Word, slides | PNG, JPG, WebP | *One image per page* / *First page only* / *One tall image* |
| Video (long) | GIF | *Standard* / *Small and quick* / *First 10 seconds* |
| Video | PNG, JPG, WebP | *Middle frame* / *First frame* / *A frame every few seconds* / *Contact sheet* |
| Video | MP4, MKV, … | *Best match* / *Smaller file* / *No sound* |

Videos also offer **subtitle extraction** (SRT, VTT) when they contain subtitles, and audio targets only when they have sound.
The centre of the wheel (or Esc) goes back out of a question. From the command line: `convert --to png#frames file.gif`.

## Combining several files

Select **more than one** file and the target you pick asks one more question: **separate** (each file converted on its own) or **all in one**, the inverse of the splits above. A format with no per-file conversion of its own (PDFs into one PDF, a slideshow video) keeps a **ONE …** entry. Examples: pictures → animated **GIF /
WebP**, multi-page **TIFF**, **PDF** (a page each), a **slide deck** or a **slideshow video**; PDFs (and pictures) → one **PDF**; CSV / TSV /
Excel files → **one workbook** with a sheet each; Word, Markdown, HTML or text → one **PDF / DOCX / Markdown**; decks → one deck;
audio or video clips → one file; fonts → a **TTC**. Files are taken in name order (`frame2` before `frame10`) and are never removed.
`shapeshifter combine --to pdf *.png`.

## Doing something to a file that keeps it the same kind

Next to the conversions the wheel offers **same-format actions** (grey): **SMALLER** and **RESIZE** (pictures), **ROTATE / FLIP**,
**NO LOCATION** (inside PROPERTIES; removes camera, date and GPS data when the picture has any), PDF **SMALLER**, **SPLIT** into pages, **GET PICTURES**
and **ROTATE**, **TIDY** for JSON and XML (pretty / minify / sorted), **FIX TEXT** (UTF-8 and line endings), audio **EVEN VOLUME** and
**SMALLER**, and video **SMALLER** and **NO SOUND**. The result is a new file such as `photo (smaller).jpg` and the original follows
your usual setting (Recycle Bin by default).

## Properties (metadata)
Choose **PROPERTIES** on the wheel to see and change what is stored inside a file: EXIF in pictures (author, copyright, description, date taken, camera, GPS location), title / author / subject / keywords in PDF, Word, Excel, PowerPoint and OpenDocument files, tags in audio and video, and the file's created / modified dates. JPEG and PNG pictures are edited without recompressing, so there is no quality loss; other picture types are saved again. Tick *Remove all personal details* to strip everything, or select several files to set the same author on all of them. From the command line: `shapeshifter meta photo.jpg --set Author=Ada --no-location`.

## Older and other files

| Kind | What you get |
|---|---|
| **.doc, .xls, .ppt** (Word, Excel, PowerPoint 97–2003) | read natively: text and tables, cell values (dates included) and slide text, not formatting or pictures; LibreOffice does it better when installed |
| **RTF**, **ODT** | read and written natively, to and from Word, Markdown, HTML, text and PDF |
| **.msg** (Outlook) | to e-mail with attachments, then text, HTML, Markdown, PDF |
| **Camera RAW** (CR2, NEF, ARW, DNG, ORF, RW2, RAF) | the full-size JPEG the camera stored in the file, then any picture format. This is a preview, not developed sensor data |
| **glTF / GLB** | to and from OBJ, STL and PLY |
| **TTC** | split into separate TTFs, and fonts combined into a TTC |
| **RAR/CBR, ISO, CAB, WIM, DMG, VHD(X), VMDK, QCOW2, SquashFS, RPM, CPIO, XAR, CHM** | with **7-Zip** installed (Settings › Engines): extract, or re-pack as ZIP / 7z / TAR |

Not covered: Parquet, Arrow, Avro, HDF5, MAT (needs large libraries), DJVU, DWG, WOFF2, Apple Pages / Numbers / Keynote,
Access databases, MIDI and Flash.

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
| Built-in tables, presentations, documents | Tables ↔ data, PPTX/ODP slides, DOCX/ODT/RTF, legacy DOC/XLS/PPT, Outlook MSG, camera RAW previews, glTF |
| 7-Zip (optional) | RAR, ISO, CAB, WIM, DMG, VHD(X), … |
| pandoc / LibreOffice (optional) | Rich documents: DOCX, ODT, EPUB, PPTX, XLS, … (pandoc and LibreOffice win over the built-in readers when installed) |

*Settings › Advanced › Prefer ShapeShifter's own converters* routes through the native
engine first. Anything it can't handle, such as a progressive JPEG or a very large
image, falls back to the libraries automatically.

## Teach it a new format

Settings › Advanced › **Custom formats**, or **Inspect › Describe this format…**
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

## Updates
The tray icon shows a notification, and an **Update to x.y.z…** entry, when a newer release is published on GitHub (checked at most once a day; the only thing sent is a normal request for the public release page). **Update now** downloads the installer, checks it against the SHA-256 GitHub publishes for it, and installs over the current version; your settings and hotkeys are kept and ShapeShifter restarts. **Check for updates…** is in the tray menu and Settings › About, where you can also switch the daily check off. **Skip this version** silences one release.

## Settings app

The nav is grouped: the five pages most people use, then — below a divider — the ones only
power users need (*Advanced*, *Graph*), with *About* pinned to the bottom.

| Page | Contents |
|---|---|
| General | Original file (Recycle Bin / keep / delete), keep modified date, start with Windows, right-click entry (and, on a Mac, the two permissions hotkeys need) |
| Wheel | Six themes, accent colour, size, text size, opacity, animation, position, live preview |
| Hotkeys | Keyboard and mouse shortcuts: record by pressing them, with conflict detection and live status |
| Formats & mappings | Every source → target with engine, route and loss notes. Hide any pair. *Identify a file…* |
| Engines | What's installed. One-click install for ffmpeg, LibreOffice, pandoc, Magika. *Test OCR* checks Windows text recognition and lists its languages. |
| Advanced | Quality and PDF DPI, prefer native converters, OCR language, the wheel's ring size and hover-to-convert, learned reverse conversions, custom formats, conversion graph export |
| Graph | The interactive conversion graph: explore, search, highlight routes, toggle steps on or off the wheel |
| About | Version, licence, updates, settings/log files, reset |

## Command line

```
ShapeShifter.exe convert --to webp *.png          # or: python -m shapeshifter …
ShapeShifter.exe convert --to extract backup.7z
ShapeShifter.exe identify mystery.dat              # what is it, and how sure?
ShapeShifter.exe targets photo.heic                # what can it become (with routes/warnings)
ShapeShifter.exe convert --to png#frames anim.gif  # a specific way (see "Ways to reach a target")
ShapeShifter.exe convert --to act:resize#1280 p.jpg   # a same-format action
ShapeShifter.exe combine --to pdf scan*.png        # several files into one
ShapeShifter.exe fill form.pdf --list              # find (or fill) the blanks of a form
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

## Code signing policy

The Windows installer is not code-signed yet, so SmartScreen may warn on first run. The plan, and what the free
[SignPath Foundation](https://signpath.org) programme would provide once approved, is in
[docs/CODE-SIGNING.md](docs/CODE-SIGNING.md), together with the privacy policy: ShapeShifter collects and sends no data.
