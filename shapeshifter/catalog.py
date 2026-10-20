"""Configured file types: the built-in catalogue.

Each entry teaches ShapeShifter a format explicitly (name, category, extensions, how to
recognise it, and how it relates to other formats). Everything else is still inferred.

Relations:
  family "text"  -> plain-text based. Gets a visible edge X -> txt (it *is* text) and a
                    latent edge txt -> X (only used by routes / learned reverse conversions).
  family "code"  -> source code / config text: like "text", plus code -> html/md/pdf rendering.
  is_a  "<fmt>"  -> the content is a valid <fmt> (e.g. apng is_a png): visible edge X -> fmt,
                    latent fmt -> X.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Entry:
    id: str
    name: str
    category: str
    exts: tuple = ()
    family: str = ""            # "", "text", "code"
    is_a: str = ""              # parent format id
    magic: tuple = ()           # ((offset, bytes, confidence), ...)
    sensitive: bool = False     # private keys, password stores: restricted conversions
    lang: str = ""              # syntax name for code rendering


def E(id, name, category, exts=(), **kw):
    return Entry(id, name, category, tuple(exts) or (id,), **kw)


def M(*sigs):
    """M((0, b"..."), (4, b"...", 0.9))"""
    return tuple((s[0], s[1], s[2] if len(s) > 2 else 0.95) for s in sigs)


# --------------------------------------------------------------- source code
_CODE = [
    ("py", "Python source", ["py", "pyw", "pyi"], "python"),
    ("ts", "TypeScript source", ["ts", "mts", "cts"], "typescript"),
    ("tsx", "TypeScript JSX", ["tsx"], "tsx"), ("jsx", "JavaScript JSX", ["jsx"], "jsx"),
    ("mjs", "JavaScript module", ["mjs", "cjs"], "javascript"),
    ("java", "Java source", ["java"], "java"), ("kt", "Kotlin source", ["kt", "kts"], "kotlin"),
    ("scala", "Scala source", ["scala", "sc"], "scala"), ("groovy", "Groovy source", ["groovy", "gvy"], "groovy"),
    ("c", "C source", ["c"], "c"), ("h", "C header", ["h"], "c"),
    ("cpp", "C++ source", ["cpp", "cc", "cxx", "c++"], "cpp"), ("hpp", "C++ header", ["hpp", "hh", "hxx"], "cpp"),
    ("cs", "C# source", ["cs", "csx"], "csharp"), ("fs", "F# source", ["fs", "fsx", "fsi"], "fsharp"),
    ("vb", "Visual Basic source", ["vb"], "vbnet"), ("go", "Go source", ["go"], "go"),
    ("rs", "Rust source", ["rs"], "rust"), ("swift", "Swift source", ["swift"], "swift"),
    ("m", "Objective-C source", ["m", "mm"], "objectivec"), ("rb", "Ruby source", ["rb", "rake", "gemspec"], "ruby"),
    ("php", "PHP source", ["php", "phtml"], "php"), ("pl", "Perl source", ["pl", "pm", "t"], "perl"),
    ("lua", "Lua source", ["lua"], "lua"), ("r", "R source", ["r", "rmd"], "r"),
    ("jl", "Julia source", ["jl"], "julia"), ("dart", "Dart source", ["dart"], "dart"),
    ("ex", "Elixir source", ["ex", "exs"], "elixir"), ("erl", "Erlang source", ["erl", "hrl"], "erlang"),
    ("hs", "Haskell source", ["hs", "lhs"], "haskell"), ("clj", "Clojure source", ["clj", "cljs", "edn"], "clojure"),
    ("elm", "Elm source", ["elm"], "elm"), ("nim", "Nim source", ["nim"], "nim"),
    ("zig", "Zig source", ["zig"], "zig"), ("sql", "SQL script", ["sql", "ddl", "psql"], "sql"),
    ("bash", "Bash script", ["bash", "zsh", "fish", "ksh"], "bash"), ("asm", "Assembly source", ["asm", "s", "nasm"], "asm"),
    ("f90", "Fortran source", ["f90", "f95", "f03", "for", "f"], "fortran"), ("pas", "Pascal source", ["pas", "pp", "dpr"], "pascal"),
    ("d", "D source", ["d"], "d"), ("ml", "OCaml source", ["ml", "mli"], "ocaml"),
    ("lisp", "Lisp source", ["lisp", "lsp", "el", "scm", "rkt"], "lisp"), ("tcl", "Tcl script", ["tcl"], "tcl"),
    ("awk", "AWK script", ["awk"], "awk"), ("cmake", "CMake script", ["cmake"], "cmake"),
    ("gradle", "Gradle script", ["gradle"], "groovy"), ("tf", "Terraform config", ["tf", "tfvars", "hcl"], "hcl"),
    ("nix", "Nix expression", ["nix"], "nix"), ("proto", "Protocol Buffers schema", ["proto"], "protobuf"),
    ("graphql", "GraphQL schema", ["graphql", "gql"], "graphql"), ("css", "CSS stylesheet", ["css"], "css"),
    ("scss", "Sass stylesheet", ["scss", "sass"], "scss"), ("less", "Less stylesheet", ["less"], "less"),
    ("styl", "Stylus stylesheet", ["styl"], "stylus"), ("vue", "Vue component", ["vue"], "vue"),
    ("svelte", "Svelte component", ["svelte"], "svelte"), ("astro", "Astro component", ["astro"], "astro"),
    ("dockerfile", "Dockerfile", ["dockerfile", "containerfile"], "dockerfile"),
    ("makefile", "Makefile", ["makefile", "mk", "mak"], "makefile"), ("cu", "CUDA source", ["cu", "cuh"], "cuda"),
    ("glsl", "Shader source", ["glsl", "vert", "frag", "hlsl", "wgsl"], "glsl"),
    ("sol", "Solidity source", ["sol"], "solidity"), ("v", "Verilog source", ["v", "sv", "vhd", "vhdl"], "verilog"),
    ("ino", "Arduino sketch", ["ino"], "cpp"), ("ipynb_src", "Jupyter script", ["jupytext"], "python"),
    ("tex", "LaTeX source", ["tex", "ltx", "sty", "cls"], "latex"), ("bib", "BibTeX database", ["bib"], "bibtex"),
    ("diff", "Patch / diff", ["diff", "patch"], "diff"), ("gcode", "G-code toolpath", ["gcode", "nc", "gco"], "gcode"),
]

# ------------------------------------------------------------ plain-text kinds
_TEXT = [
    ("log", "Log file", ["log", "out"]), ("nfo", "NFO info file", ["nfo", "diz"]),
    ("env", "Environment variables", ["env"]), ("properties", "Java properties", ["properties"]),
    ("gitignore", "Ignore rules", ["gitignore", "dockerignore", "npmignore", "gitattributes"]),
    ("editorconfig", "EditorConfig", ["editorconfig"]), ("adoc", "AsciiDoc", ["adoc", "asciidoc", "asc"]),
    ("org", "Org-mode document", ["org"]), ("textile", "Textile document", ["textile"]),
    ("wiki", "Wiki markup", ["wiki", "mediawiki"]), ("srt", "SubRip subtitles", ["srt"]),
    ("vtt", "WebVTT subtitles", ["vtt"]), ("ass", "Advanced SubStation subtitles", ["ass", "ssa"]),
    ("sbv", "YouTube subtitles", ["sbv"]), ("lrc", "Synced lyrics", ["lrc"]),
    ("ics", "Calendar (iCalendar)", ["ics", "ical", "ifb"]), ("vcf", "Contacts (vCard)", ["vcf", "vcard"]),
    ("eml", "E-mail message", ["eml"]), ("mbox", "Mailbox", ["mbox", "mbx"]),
    ("mhtml", "Web archive (MHTML)", ["mhtml", "mht"]), ("pem", "PEM certificate / key", ["pem", "crt", "cer", "key", "csr", "pub", "p7b"]),
    ("sshpub", "SSH public key", ["sshpub"]), ("pgp", "PGP armored data", ["asc_pgp", "gpgarmor"]),
    ("obj", "Wavefront OBJ 3D model", ["obj"]), ("stla", "STL 3D model (text)", ["stla"]),
    ("ply", "PLY 3D model", ["ply"]), ("dxf", "AutoCAD DXF drawing", ["dxf"]),
    ("gpx", "GPS track (GPX)", ["gpx"]), ("kml", "Google Earth (KML)", ["kml"]),
    ("geojson", "GeoJSON", ["geojson"]), ("topojson", "TopoJSON", ["topojson"]),
    ("jsonl", "JSON Lines", ["jsonl", "ndjson"]), ("json5", "JSON5", ["json5", "jsonc"]),
    ("ipynb", "Jupyter notebook", ["ipynb"]), ("har", "HTTP archive", ["har"]),
    ("webmanifest", "Web app manifest", ["webmanifest"]), ("sarif", "SARIF analysis report", ["sarif"]),
    ("gltf", "glTF 3D scene", ["gltf"]), ("rss", "RSS feed", ["rss"]), ("atom", "Atom feed", ["atom"]),
    ("xhtml", "XHTML page", ["xhtml", "xht"]), ("xsd", "XML schema", ["xsd"]), ("xsl", "XSL stylesheet", ["xsl", "xslt"]),
    ("xliff", "XLIFF translation", ["xliff", "xlf"]), ("plist", "Property list", ["plist"]),
    ("dae", "COLLADA 3D scene", ["dae"]), ("musicxml", "MusicXML score", ["musicxml", "mxl_xml"]),
    ("drawio", "draw.io diagram", ["drawio"]), ("csproj", "MSBuild project", ["csproj", "vbproj", "vcxproj", "props", "targets"]),
    ("resx", ".NET resources", ["resx"]), ("svgz_text", "SVG text", ["svg_text"]),
]

# ------------------------------------------------------- binary / container kinds
_BINARY = [
    # images
    E("apng", "Animated PNG", "image", ["apng"], is_a="png"),
    E("cur", "Windows cursor", "image", ["cur"], magic=M((0, b"\x00\x00\x02\x00", 0.7))),
    E("ani", "Animated cursor", "image", ["ani"]),
    E("dib", "Device-independent bitmap", "image", ["dib"], is_a="bmp"),
    E("xcf", "GIMP image", "image", ["xcf"], magic=M((0, b"gimp xcf"))),
    E("exr", "OpenEXR image", "image", ["exr"], magic=M((0, b"\x76\x2f\x31\x01"))),
    E("hdr", "Radiance HDR image", "image", ["hdr", "pic"], magic=M((0, b"#?RADIANCE"), (0, b"#?RGBE"))),
    E("jxl", "JPEG XL image", "image", ["jxl"], magic=M((0, b"\xff\x0a"), (0, b"\x00\x00\x00\x0cJXL \r\n\x87\n", 1.0))),
    E("bpg", "BPG image", "image", ["bpg"], magic=M((0, b"BPG\xfb"))),
    E("flif", "FLIF image", "image", ["flif"], magic=M((0, b"FLIF"))),
    E("xbm", "X bitmap", "image", ["xbm"]), E("xpm", "X pixmap", "image", ["xpm"], magic=M((0, b"/* XPM */"))),
    E("sgi", "SGI image", "image", ["sgi", "rgb", "bw"], magic=M((0, b"\x01\xda", 0.6))),
    E("fits", "FITS astronomy image", "image", ["fits", "fit", "fts"], magic=M((0, b"SIMPLE  ="))),
    E("dng", "Digital negative (RAW)", "image", ["dng"]), E("cr2", "Canon RAW", "image", ["cr2", "cr3", "crw"]),
    E("nef", "Nikon RAW", "image", ["nef", "nrw"]), E("arw", "Sony RAW", "image", ["arw", "srf", "sr2"]),
    E("orf", "Olympus RAW", "image", ["orf"], magic=M((0, b"IIRO"))), E("rw2", "Panasonic RAW", "image", ["rw2"], magic=M((0, b"IIU\x00"))),
    E("raf", "Fujifilm RAW", "image", ["raf"], magic=M((0, b"FUJIFILMCCD-RAW"))),
    E("emf", "Windows metafile (EMF)", "image", ["emf", "emz"], magic=M((40, b" EMF"))),
    E("wmf", "Windows metafile", "image", ["wmf"], magic=M((0, b"\xd7\xcd\xc6\x9a"))),
    E("ps", "PostScript", "document", ["ps", "eps", "epsf"], magic=M((0, b"%!PS"), (0, b"\xc5\xd0\xd3\xc6"))),
    E("ai", "Illustrator artwork", "image", ["ai"], is_a="pdf"),
    E("djvu", "DjVu document", "document", ["djvu", "djv"], magic=M((0, b"AT&TFORM"))),
    E("dpx", "DPX film image", "image", ["dpx"], magic=M((0, b"SDPX"), (0, b"XPDS"))),
    E("avifs", "AVIF image sequence", "image", ["avifs"], is_a="avif"),
    E("heics", "HEIC image sequence", "image", ["heics", "hif"], is_a="heic"),
    # audio
    E("m4b", "Audiobook (M4B)", "audio", ["m4b"], is_a="m4a"), E("oga", "Ogg audio", "audio", ["oga"], is_a="ogg"),
    E("mka", "Matroska audio", "audio", ["mka"]), E("weba", "WebM audio", "audio", ["weba"]),
    E("ape", "Monkey's Audio", "audio", ["ape"], magic=M((0, b"MAC "))),
    E("wv", "WavPack audio", "audio", ["wv"], magic=M((0, b"wvpk"))),
    E("tta", "True Audio", "audio", ["tta"], magic=M((0, b"TTA1"))),
    E("caf", "Core Audio file", "audio", ["caf"], magic=M((0, b"caff"))),
    E("au", "Sun audio", "audio", ["au", "snd"], magic=M((0, b".snd"))),
    E("voc", "Creative Voice audio", "audio", ["voc"], magic=M((0, b"Creative Voice File"))),
    E("spx", "Speex audio", "audio", ["spx"]), E("dts", "DTS audio", "audio", ["dts"], magic=M((0, b"\x7f\xfe\x80\x01", 0.8))),
    E("ra", "RealAudio", "audio", ["ra", "ram"], magic=M((0, b".ra\xfd"))),
    E("w64", "Wave64 audio", "audio", ["w64"]), E("mpc", "Musepack audio", "audio", ["mpc"], magic=M((0, b"MPCK"), (0, b"MP+"))),
    E("mid", "MIDI music", "audio", ["mid", "midi", "kar"], magic=M((0, b"MThd"))),
    E("mod", "Tracker module", "audio", ["mod", "xm", "s3m", "it"]),
    # video
    E("mts", "AVCHD video", "video", ["mts", "m2ts", "m2t"]), E("vob", "DVD video", "video", ["vob"]),
    E("m2v", "MPEG-2 video", "video", ["m2v", "mpe", "mpv"]), E("asf", "ASF video", "video", ["asf"]),
    E("rm", "RealMedia video", "video", ["rm", "rmvb"], magic=M((0, b".RMF"))),
    E("f4v", "Flash MP4 video", "video", ["f4v"]), E("3g2", "3GPP2 video", "video", ["3g2"]),
    E("mxf", "MXF video", "video", ["mxf"], magic=M((0, b"\x06\x0e\x2b\x34\x02\x05\x01\x01", 1.0))),
    E("nut", "NUT video", "video", ["nut"], magic=M((0, b"nut/multimedia container"))),
    E("y4m", "YUV4MPEG video", "video", ["y4m"], magic=M((0, b"YUV4MPEG2"))),
    E("ivf", "IVF video", "video", ["ivf"], magic=M((0, b"DKIF"))), E("dv", "DV video", "video", ["dv"]),
    E("ogm", "Ogg media", "video", ["ogm"]), E("qt", "QuickTime movie", "video", ["qt"], is_a="mov"),
    E("divx", "DivX video", "video", ["divx", "xvid"], is_a="avi"), E("h264", "Raw H.264 stream", "video", ["h264", "264"]),
    E("hevc", "Raw H.265 stream", "video", ["hevc", "h265", "265"]),
    E("swf", "Flash animation", "video", ["swf"], magic=M((0, b"FWS"), (0, b"CWS"), (0, b"ZWS"))),
    # documents
    E("chm", "Compiled HTML help", "document", ["chm"], magic=M((0, b"ITSF"))),
    E("wpd", "WordPerfect document", "document", ["wpd", "wp"], magic=M((0, b"\xffWPC"))),
    E("pages", "Apple Pages document", "document", ["pages"]), E("key_doc", "Apple Keynote", "document", ["keynote"]),
    E("numbers", "Apple Numbers", "data", ["numbers"]), E("one", "OneNote section", "document", ["one"]),
    E("msg", "Outlook message", "document", ["msg"]), E("azw", "Kindle e-book", "document", ["azw", "azw3", "kfx"]),
    E("lit", "Microsoft Reader e-book", "document", ["lit"], magic=M((0, b"ITOLITLS"))),
    E("cbr", "Comic book (RAR)", "archive", ["cbr"]), E("cb7", "Comic book (7z)", "archive", ["cb7"], is_a="7z"),
    E("dotx", "Word template", "document", ["dotx"], is_a="docx"), E("xltx", "Excel template", "data", ["xltx"], is_a="xlsx"),
    E("potx", "PowerPoint template", "document", ["potx"], is_a="pptx"),
    E("ott", "OpenDocument text template", "document", ["ott"], is_a="odt"),
    # data
    E("parquet", "Apache Parquet table", "data", ["parquet"], magic=M((0, b"PAR1"))),
    E("avro", "Apache Avro data", "data", ["avro"], magic=M((0, b"Obj\x01"))),
    E("arrow", "Apache Arrow data", "data", ["arrow", "feather"], magic=M((0, b"ARROW1"))),
    E("hdf5", "HDF5 data", "data", ["h5", "hdf5", "hdf"], magic=M((0, b"\x89HDF\r\n\x1a\n", 1.0))),
    E("npy", "NumPy array", "data", ["npy", "npz"], magic=M((0, b"\x93NUMPY"))),
    E("mat", "MATLAB data", "data", ["mat"], magic=M((0, b"MATLAB 5.0"))),
    E("mdb", "Access database", "data", ["mdb"], magic=M((4, b"Standard Jet DB"))),
    E("accdb", "Access database", "data", ["accdb"], magic=M((4, b"Standard ACE DB"))),
    E("dbf", "dBASE table", "data", ["dbf"]), E("bplist", "Binary property list", "data", ["bplist"], magic=M((0, b"bplist00", 1.0))),
    E("torrent", "BitTorrent metainfo", "data", ["torrent"], magic=M((0, b"d8:announce"), (0, b"d4:info"), (0, b"d13:announce-list"))),
    E("glb", "glTF binary 3D scene", "model", ["glb"], magic=M((0, b"glTF"))),
    E("stl", "STL 3D model", "model", ["stl"]), E("fbx", "Autodesk FBX", "model", ["fbx"], magic=M((0, b"Kaydara FBX Binary"))),
    E("3ds", "3D Studio model", "model", ["3ds"], magic=M((0, b"MM", 0.4))),
    E("blend", "Blender scene", "model", ["blend"], magic=M((0, b"BLENDER"))),
    E("step", "STEP CAD model", "model", ["step", "stp"], magic=M((0, b"ISO-10303-21"))),
    E("dwg", "AutoCAD drawing", "model", ["dwg"], magic=M((0, b"AC10"))),
    # fonts
    E("ttf", "TrueType font", "font", ["ttf"], magic=M((0, b"\x00\x01\x00\x00", 0.7), (0, b"true", 0.8))),
    E("otf", "OpenType font", "font", ["otf"], magic=M((0, b"OTTO"))),
    E("woff", "Web font (WOFF)", "font", ["woff"], magic=M((0, b"wOFF", 1.0))),
    E("woff2", "Web font (WOFF2)", "font", ["woff2"], magic=M((0, b"wOF2", 1.0))),
    E("ttc", "TrueType collection", "font", ["ttc"], magic=M((0, b"ttcf"))),
    E("eot", "Embedded OpenType font", "font", ["eot"]),
    # security
    E("der", "DER certificate / key", "security", ["der"]),
    E("p12", "PKCS#12 keystore", "security", ["p12", "pfx"], sensitive=True),
    E("jks", "Java keystore", "security", ["jks", "keystore"], magic=M((0, b"\xfe\xed\xfe\xed")), sensitive=True),
    E("kdbx", "KeePass database", "security", ["kdbx", "kdb"], magic=M((0, b"\x03\xd9\xa2\x9a")), sensitive=True),
    E("gpg", "PGP encrypted data", "security", ["gpg", "pgp", "sig"], sensitive=True),
    # archives & disk images
    E("zst", "Zstandard-compressed file", "archive", ["zst", "zstd"], magic=M((0, b"\x28\xb5\x2f\xfd", 1.0))),
    E("lz4", "LZ4-compressed file", "archive", ["lz4"], magic=M((0, b"\x04\x22\x4d\x18", 1.0))),
    E("lzip", "Lzip-compressed file", "archive", ["lz"], magic=M((0, b"LZIP"))),
    E("lzma", "LZMA-compressed file", "archive", ["lzma"], magic=M((0, b"\x5d\x00\x00", 0.6))),
    E("cab", "Windows cabinet", "archive", ["cab"], magic=M((0, b"MSCF"))),
    E("iso", "Disc image (ISO)", "archive", ["iso"], magic=M((32769, b"CD001", 1.0))),
    E("dmg", "macOS disk image", "archive", ["dmg"]), E("deb", "Debian package", "archive", ["deb"], magic=M((0, b"!<arch>\ndebian", 1.0))),
    E("ar", "Unix archive", "archive", ["ar", "a", "lib"], magic=M((0, b"!<arch>\n"))),
    E("rpm", "RPM package", "archive", ["rpm"], magic=M((0, b"\xed\xab\xee\xdb"))),
    E("cpio", "cpio archive", "archive", ["cpio"], magic=M((0, b"070701"), (0, b"070707"))),
    E("xar", "XAR archive", "archive", ["xar", "pkg"], magic=M((0, b"xar!"))),
    E("whl", "Python wheel", "archive", ["whl", "egg"], is_a="zip"), E("nupkg", "NuGet package", "archive", ["nupkg"], is_a="zip"),
    E("vsix", "VS Code / Visual Studio extension", "archive", ["vsix"], is_a="zip"),
    E("xpi", "Firefox add-on", "archive", ["xpi"], is_a="zip"), E("crx", "Chrome extension", "archive", ["crx"], magic=M((0, b"Cr24"))),
    E("vhd", "Virtual hard disk", "archive", ["vhd"]), E("vhdx", "Virtual hard disk (VHDX)", "archive", ["vhdx"], magic=M((0, b"vhdxfile"))),
    E("vmdk", "VMware disk", "archive", ["vmdk"], magic=M((0, b"KDMV"))), E("qcow2", "QEMU disk", "archive", ["qcow2", "qcow"], magic=M((0, b"QFI\xfb"))),
    E("wim", "Windows image", "archive", ["wim", "esd"], magic=M((0, b"MSWIM"))),
    E("squashfs", "SquashFS image", "archive", ["squashfs", "sqfs"], magic=M((0, b"hsqs"))),
    # programs (refused, but now named precisely)
    E("wasm", "WebAssembly module", "program", ["wasm"], magic=M((0, b"\x00asm", 1.0))),
    E("class", "Java class file", "program", ["class"], magic=M((0, b"\xca\xfe\xba\xbe", 0.6))),
    E("pyc", "Compiled Python", "program", ["pyc", "pyo"]), E("pickle", "Python pickle (can run code)", "program", ["pkl", "pickle"]),
    E("dex", "Android bytecode", "program", ["dex"], magic=M((0, b"dex\n"))),
    E("appimage", "Linux AppImage", "program", ["appimage"]), E("msix", "Windows app package", "program", ["msix", "appx", "msixbundle"]),
    E("ocx", "ActiveX control", "program", ["ocx", "drv"]), E("efi", "EFI program", "program", ["efi"]),
]

# Text-format entries need no magic; their markers live in identify.TEXT_MARKERS.
_TEXT_CATEGORY = {"srt": "subtitle", "vtt": "subtitle", "ass": "subtitle", "sbv": "subtitle",
                  "lrc": "subtitle", "pem": "security", "sshpub": "security", "pgp": "security",
                  "obj": "model", "stla": "model", "ply": "model", "dxf": "model", "gltf": "model",
                  "dae": "model", "gpx": "data", "kml": "data", "geojson": "data", "topojson": "data",
                  "jsonl": "data", "json5": "data", "ipynb": "document", "har": "data",
                  "webmanifest": "data", "sarif": "data", "rss": "document", "atom": "document",
                  "xhtml": "document", "xsd": "data", "xsl": "code", "xliff": "data", "plist": "data",
                  "musicxml": "document", "drawio": "document", "csproj": "code", "resx": "data",
                  "ics": "data", "vcf": "data", "eml": "document", "mbox": "document",
                  "mhtml": "document", "svgz_text": "image"}

# Valid-JSON / valid-XML kinds keep their parent's data conversions.
_TEXT_IS_A = {"geojson": "json", "topojson": "json", "ipynb": "json", "har": "json",
              "webmanifest": "json", "sarif": "json", "gltf": "json", "rss": "xml", "atom": "xml",
              "xhtml": "html", "xsd": "xml", "xsl": "xml", "xliff": "xml", "dae": "xml",
              "musicxml": "xml", "drawio": "xml", "csproj": "xml", "resx": "xml", "kml": "xml",
              "gpx": "xml", "svgz_text": "svg"}


def _build() -> dict[str, Entry]:
    out: dict[str, Entry] = {}
    for fid, name, exts, lang in _CODE:
        out[fid] = Entry(fid, name, "code", tuple(exts), family="code", lang=lang)
    for fid, name, exts in _TEXT:
        out[fid] = Entry(fid, name, _TEXT_CATEGORY.get(fid, "document"), tuple(exts),
                         family="text", is_a=_TEXT_IS_A.get(fid, ""),
                         sensitive=False)
    for e in _BINARY:
        out[e.id] = e
    return out


CATALOG: dict[str, Entry] = _build()

# Plain-text formats that already have their own converters still count as text.
BUILTIN_TEXT = {"txt", "md", "html", "csv", "tsv", "json", "yaml", "toml", "ini", "xml", "svg",
                "rst", "rtf_text"}


def text_family() -> set[str]:
    return {e.id for e in CATALOG.values() if e.family in ("text", "code")} | BUILTIN_TEXT


def code_formats() -> dict[str, str]:
    return {e.id: e.lang for e in CATALOG.values() if e.family == "code"}


def register() -> None:
    """Merge the catalogue into the format tables (names, categories, extensions)."""
    from . import formats
    for e in CATALOG.values():
        formats.CATEGORY_OF.setdefault(e.id, e.category)
        formats.NAMES.setdefault(e.id, e.name)
        for x in e.exts:
            if x != e.id and x not in formats.CATEGORY_OF and x not in formats.ALIASES:
                formats.ALIASES[x] = e.id


def signatures() -> list[tuple[str, int, bytes, float]]:
    return [(e.id, off, magic, conf) for e in CATALOG.values() for off, magic, conf in e.magic]
