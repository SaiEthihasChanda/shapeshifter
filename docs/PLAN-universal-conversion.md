# Plan: content-aware, graph-based conversion

**Goal:** when the converter is triggered on *any* file, including one with an unknown or
wrong extension, the app works out what the file is from its contents and offers every
conversion it can reach. Native and library-backed converters plug into the same system.

**Status (ShapeShifter 2.0): all phases implemented.**

| Phase | Where |
|---|---|
| 1. Graph + planner | `planner.py`, `engine.py` (fallback to the next engine on `Unsupported`) |
| 2. Identification | `identify.py` |
| 3. Unknown-file UX | Fix name, a two-stage **Treat as** choice instead of a literal inner ring, Inspect (`inspector.py`) |
| 4. Native codecs | `native/` (PNG, BMP, TGA, PNM, QOI, ICO, GIF/JPEG decode, PDF writer, Markdown/HTML/text, WAV/AIFF, XLSX) |
| 5. Bundled installer | `build.ps1`, `packaging/` |
| 6. Format descriptions | `custom.py` (YAML: alias / embedded / records / text-table) |
| (opt) Magika | `identify._probe_magika`, installable from Settings › Engines (source installs only) |

**2.3 additions**

| Feature | Where |
|---|---|
| Document fidelity: HTML/MD/text → PDF through MuPDF Story (pictures, tables, links, fonts, scripts); ligature-safe text layer | `backends/mupdf.py` |
| pandoc keeps pictures (`--embed-resources`, `--extract-media`, `--resource-path`) | `backends/external.py` |
| PDF → HTML / SVG; scanned pages fall back to OCR | `backends/mupdf.py` |
| Native DOCX writer; native PDF writer gains pictures, tables, numbered lists | `native/docx.py`, `native/pdfwrite.py`, `native/docs.py` (image blocks, inline spans, escapes) |
| OCR engine (Windows.Media.Ocr via PowerShell): image → TXT/MD/HTML/DOCX/searchable PDF, scanned PDF → searchable PDF | `ocr.py`, `backends/ocr.py` |
| Planner: lossy routes cost extra, so picture-keeping routes win ties | `planner.py` |

**2.1 additions**

| Feature | Where |
|---|---|
| Configured catalogue: 260 entries / 360+ known types, with content markers, JSON subtypes and XML roots | `catalog.py`, `identify.py` |
| Native converters for configured kinds (certificates, subtitles, geodata, PIM, mail, notebooks, plist/env/jsonl/json5/torrent, meshes, fonts, code rendering) | `native/*.py` via `native/registry.py` |
| Family edges (visible child→parent and text→TXT; latent parent→child and TXT→text kinds) | `backends/family.py` |
| `planner.route()` (uses latent edges, longer chains), graph export, route finder | `planner.py`, Settings |
| Learned reverse conversions | `learned.py`, `engine._learned_options` |
| Sensitive files (private keys, keystores): PEM↔DER only | `identify._sensitivity`, `engine.SENSITIVE_OK` |
| Concentric rings, staggered rotating reveal, type-to-search | `wheel.py` |

---

## 1. Today vs. target

| | Today | Target |
|---|---|---|
| Identifying the file | Extension only (`formats.ext_of`) | Contents first; the extension is only a hint |
| What it can become | Fixed `src → dst` lists in each backend | Worked out from a graph of decoders and encoders |
| Adding a format | Edit a backend's source/target sets | Register one decoder and/or encoder; its conversions appear automatically |
| Multi-step conversions | No | Yes, e.g. PDF → pages → image → WEBP |
| Unknown file | "No conversions available" | Detected type, confidence, possible conversions, or an explanation of what was found |
| Right-click menu | Registered per known extension | Registered for all files (`*`) |

---

## 2. Architecture

```
            ┌──────────────────────┐
 file ───▶  │ 1. Identify          │  magic bytes · containers · text sniffing · decoder sniff
            └─────────┬────────────┘
                      ▼  Identification(format, confidence, evidence)
            ┌──────────────────────┐
            │ 2. Format registry   │  format → which models it decodes to / encodes from
            └─────────┬────────────┘
                      ▼
            ┌──────────────────────┐
            │ 3. Conversion graph  │  shortest / least-lossy path to every reachable format
            └─────────┬────────────┘
                      ▼  [TargetOption(format, path, warnings)]
            ┌──────────────────────┐
            │ 4. Wheel / CLI       │  shows options, warnings and the detected type
            └─────────┬────────────┘
                      ▼
            ┌──────────────────────┐
            │ 5. Executor          │  runs the path step by step, then the existing in-place swap
            └──────────────────────┘
```

### 2.1 Identification (`shapeshifter/identify/`)

A pipeline of probes. Each probe returns zero or more candidates:
`Candidate(format_id, confidence 0–1, evidence: str, details: dict)`.
Only the first ~64 KB is read, plus the last 64 KB for formats whose index sits at the end (ZIP, PDF).

| Order | Probe | What it recognises |
|---|---|---|
| 1 | **Magic signatures**: a data table (`signatures.json`) of offset + bytes (+ mask) | PNG, JPEG, GIF, BMP, WEBP, TIFF, PDF, ICO, PSD, FLAC, OGG, MP3 (ID3/frame sync), WAV, ZIP, GZIP, 7z, RAR, SQLite, ELF/PE, … |
| 2 | **Container probes**: look inside wrappers | **ZIP:** read the file list, so `word/` means DOCX, `xl/` XLSX, `ppt/` PPTX, a `mimetype` entry means ODF/EPUB, only images means CBZ, `META-INF/MANIFEST.MF` means JAR. **RIFF:** the type code gives WAVE, AVI or WEBP. **ISO-BMFF** (MP4 family): the brand code gives mp4, mov, m4a, 3gp, heic or avif. **EBML:** the doc type gives mkv or webm. **OGG:** the first packet gives Vorbis, Opus or Theora. **GZIP:** decompress the start and identify again (svgz, tar.gz). |
| 3 | **Text probes**, only if the bytes look like text (BOM / UTF-8 / low binary ratio) | JSON, NDJSON, XML (the root element tells SVG, XHTML, RSS, plist, KML apart), HTML, CSV/TSV (delimiter detection with consistent column counts), YAML, TOML, INI, Markdown, SRT/VTT subtitles, plain text |
| 4 | **Decoder sniff**: ask every registered decoder `can_decode(head) -> confidence` | Whatever the decoders know that the table doesn't |
| 5 | **ffprobe** (if ffmpeg is installed): media streams, codecs, duration | Any audio/video ffmpeg understands, even with no extension |
| 6 | **Optional ML fallback** (Google *Magika*, off by default) | 200+ content types; used only when everything else is unsure |

**Resolution rules**
- The highest confidence wins. More specific beats generic (DOCX beats ZIP).
- The extension adds a small bonus as a tie-breaker. If it disagrees with a strong content match, keep the content result and flag a **mismatch** (e.g. "this `.jpg` is actually a PNG").
- Results are cached per (path, size, mtime) for the life of the tray app.

**Output used by the UI:** `Identification(best, alternatives, mismatch, evidence[], summary)`.
For an unidentified file the evidence still says something useful: "ZIP archive containing 3 XML files", "UTF-16 text, 12 columns", or "binary, high entropy (probably compressed/encrypted)".

### 2.2 Format registry (`shapeshifter/registry/`)

A single data-driven list replaces `formats.py`:

```python
Format(id="png", name="PNG image", extensions=["png"], mime="image/png",
       category="image", signature_ids=["png"])
```

Codecs register against it:

```python
@decoder("png", to=Raster, cost=1, native=True)
def decode_png(src, ctx) -> Raster: ...

@encoder(Raster, "webp", cost=2, lossy=True, engine="pillow")
def encode_webp(img: Raster, dst, ctx): ...

@transcoder("mp4", "webm", cost=5, engine="ffmpeg")    # direct file→file (ffmpeg, LibreOffice, pandoc)
def ffmpeg_transcode(src, dst, ctx): ...
```

Each codec declares whether it's available (e.g. ffmpeg found) and what it loses
(`drops_alpha`, `first_page_only`, `drops_audio`, `flattens_layers`, `lossy`).

### 2.3 Intermediate models (`shapeshifter/models.py`)

Every format decodes to, and encodes from, one of a few shared shapes:

| Model | Holds | Example formats |
|---|---|---|
| `Raster` | Frames (pixels, mode, alpha), frame durations, DPI, ICC/EXIF | PNG, JPG, GIF, WEBP, ICO, BMP, TGA, QOI |
| `Pages` | Page list (vector or raster) | PDF, XPS, SVG, EPUB render |
| `Audio` | Samples (or a stream handle), rate, channels, tags | WAV, FLAC, MP3, OGG |
| `Media` | An opaque handle for audio/video, with streams from ffprobe. Video is never piped frame by frame through Python; ffmpeg does it in one step. | MP4, MKV, MOV, … |
| `Table` | Named sheets → rows | CSV, TSV, XLSX, ODS |
| `Tree` | JSON-like data | JSON, YAML, TOML, XML (data), plist |
| `Doc` | Structured text: headings, paragraphs, lists, tables, images | DOCX, MD, HTML, ODT, EPUB, TXT |
| `Text` | Plain text + encoding | TXT, SRT, LOG |
| `Archive` | File list + bytes | ZIP, TAR, GZ, 7z, CBZ |

Adapters between models count as edges too, e.g. `Pages → Raster` (render), `Raster → Pages`
(image on a page), `Table ↔ Tree`, `Doc → Text`, `Media → Audio` (extract soundtrack).

### 2.4 Conversion graph and planner (`shapeshifter/planner.py`)

- **Nodes:** formats and models. **Edges:** decoders, encoders, model adapters and transcoders, each with a cost and loss flags.
- For a source, run Dijkstra over the graph, capped at 4 hops, to find the cheapest path to every reachable format.
  - Cost = speed weight + quality weight (lossy hops cost more) + a small per-hop penalty.
  - A lossless transcoder (e.g. an MKV → MP4 remux) beats decode + re-encode.
- The result is a list of `TargetOption(format, path, cost, warnings)`, where warnings are a summary of the path's loss flags ("transparency will be flattened", "only page 1").
- Cached per source format id. The graph is rebuilt when engines change, e.g. when ffmpeg is installed.
- Settings' **Formats & mappings** page is generated from the graph. Hiding pairs keeps working through the `src>dst` keys.

### 2.5 Executor

- Runs the path one step at a time, keeping intermediate results in memory when they are small (Raster, Table) and in the hidden scratch folder when they aren't (transcoder output).
- Each step reports progress, weighted by the step's cost, into the existing progress ring.
- It ends in the existing in-place swap: unique name, timestamps, Recycle Bin.
- **Safety:** pixel and decompression-size limits (zip bombs, huge images), a per-step timeout, and optionally running decoders for untrusted files in a child process so a crash can't take down the tray app.

---

## 3. Unknown-file experience

| Situation | Wheel shows |
|---|---|
| Content identified, extension right | As today |
| Content identified, **extension wrong or missing** | Centre: "PNG (named .dat)". Ring: all PNG conversions, plus a **"Fix extension → .png"** segment that just renames the file. |
| Partly identified (e.g. "ZIP with XML inside") | The conversions the container supports (e.g. → 7z/tar, *Extract here*), plus an **"Inspect…"** segment |
| Several plausible types (low confidence) | The top 2–3 types as an inner ring; pick one and the outer ring fills with its conversions |
| Nothing recognised | "Unknown binary file", with an **"Inspect…"** segment |

The **Inspect…** window (also a tray/settings tool) shows: detected candidates with
confidence and evidence, a hex view of the header, an entropy graph, readable strings,
the file list for containers, and suggestions such as "install ffmpeg to handle this"
or "describe this format" (phase 6).

The right-click "Convert to…" entry moves to `HKCU\Software\Classes\*\shell` so it
appears on every file, not just known extensions. Hotkeys and mouse triggers already
work on any file.

---

## 4. Native vs. library codecs

Every codec sits behind the same interface, so native code can replace a library
one format at a time without changing anything else.

| Native (no third-party code) | Stays library-backed |
|---|---|
| **Images:** BMP, TGA, PPM/PGM, QOI, ICO; PNG (stdlib `zlib`); GIF (LZW); baseline JPEG decoding | JPEG encoding quality/progressive, WEBP, AVIF, HEIC, TIFF variants → Pillow / pillow-heif |
| **Audio:** WAV, AIFF, FLAC decoding | MP3, AAC, Opus, Vorbis encoding → ffmpeg |
| **Data:** CSV, TSV, JSON, XML, INI, TOML (stdlib), XLSX/ODS/DOCX/EPUB read/write (zip + xml) | YAML → PyYAML (or a native subset) |
| **Documents:** Markdown → HTML, text → PDF, image → PDF (a simple PDF writer) | Rendering PDFs → PyMuPDF; Office layout → LibreOffice; rich document conversion → pandoc |
| **Archives:** ZIP, TAR, GZ, BZ2, XZ (stdlib) | 7z / RAR → optional `py7zr` / unrar |
| **Identification:** everything in §2.1 except Magika | ffprobe (optional), Magika (optional) |

When both a native and a library codec exist, the planner prefers whichever is
cheaper. A setting can force "prefer native".

---

## 5. Phases

| # | Phase | What it delivers | Rough size |
|---|---|---|---|
| **1** | **Registry + models + planner**, with the current backends wrapped as codecs | Same features as today, but conversions come from the graph and multi-step paths work. All existing tests still pass. | Large: the core refactor |
| **2** | **Identification pipeline**: signatures, container probes, text probes, decoder sniff, ffprobe | Renamed or extension-less files work. Mismatch detection and the "Fix extension" segment. Right-click entry for all files. | Medium |
| **3** | **Unknown-file experience**: candidate inner ring, Inspect window, warnings on segments | The behaviour in §3 | Medium |
| **4** | **Native codecs, first batch**: BMP, TGA, PPM, QOI, ICO, PNG, GIF, WAV, CSV/JSON/XML/INI/TOML, XLSX read/write, image/text → PDF, ZIP/TAR | Most everyday conversions work without Pillow or openpyxl | Medium–large, one format at a time |
| **5** | **Bundled installer**: Python + libraries + ffmpeg in one package | Users install nothing else | Small–medium |
| **6** | **Format descriptions**: declarative YAML/Kaitai-style specs for custom binary formats, mapped to a model | Teach the app a new format without writing code | Large; research-ish |
| (opt) | Magika ML fallback | Better guesses for rare types | Small, but adds ~20 MB |

**Testing (runs in every phase):**
- A sample corpus with one small file per format, each also renamed to `.dat` and without an extension
- Round-trip tests (A → B → A within a tolerance)
- Planner unit tests (expected paths, loss warnings)
- Fuzzing the probes with random and truncated bytes, which must never crash or hang
- A performance budget: identify + plan in under 50 ms, so the wheel still opens instantly

---

## 6. Decisions (agreed)

1. **Multi-step and lossy conversions:** always shown, each with a warning badge that explains what is lost.
2. **Fix extension:** offered whenever the contents and the extension disagree, or the file has no extension.
3. **Archives:** in scope. ZIP ↔ 7z ↔ TAR (and .tar.gz/.bz2/.xz), plus *Extract here*.
4. **Magika:** an optional last-resort detector, installable from Settings › Engines.
5. **Risky files are refused, not isolated.** The app won't touch:
   - programs and scripts (EXE, DLL, MSI, BAT, PS1, VBS, shortcuts, …), including ones disguised under another extension
   - archives with unsafe paths (zip-slip), links pointing outside, password protection, or a suspicious compression ratio (bombs)
   - Office files that contain macros
   - images beyond a safe pixel count
   - encrypted PDFs (already refused today)

   The wheel explains why the file was refused.

## 7. Implementation notes (phase 1)

- Graph nodes are **formats**. The existing engines become file→file edges, and multi-step paths go through intermediate files in the scratch folder. The shared models (§2.3) get introduced as the native codecs arrive in phase 4. The planner already treats every edge the same way, so models slot in as more nodes.
- Some edges are **terminal**: no further step may follow them, because chaining would produce nonsense. Examples: image → PDF (that PDF has no text to extract), video → GIF, GIF → video (no soundtrack), and anything producing several files or a folder.
- Paths are at most 3 steps. Direct conversions always win over chains of equal quality.
