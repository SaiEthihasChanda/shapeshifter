"""Text recognition (OCR) with the recogniser built into the system: Windows.Media.Ocr on Windows 10/11,
Apple's Vision framework on macOS (see mac/vision.py).

Nothing to install or bundle. On Windows a short PowerShell script (run by Windows PowerShell 5.1,
which can call the WinRT API) reads images and reports lines and word boxes as JSON. OCR
languages are the ones installed in Windows (Settings > Time & language > Language &
region); "auto" uses the user's profile languages. On a Mac every language Vision knows is available.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import statistics
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .core import NO_WINDOW, ConversionError

log = logging.getLogger(__name__)

MIN_SIDE, MAX_SIDE = 1600, 5000          # recognition works best with text ~20-40 px tall


class OcrUnavailable(ConversionError):
    """OCR can't run (no engine, no language installed, …); the message says what to do."""


NO_LANGUAGE_HELP = ("Windows has no OCR language installed. Add one in Settings › Time & language › "
                    "Language & region (“Add a language”, with the Optical character recognition "
                    "feature ticked), then press “Re-detect” in ShapeShifter › Engines.")

PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$out = $env:SS_OUT
function Save($obj) {
    $json = $obj | ConvertTo-Json -Depth 8 -Compress
    [IO.File]::WriteAllText($out, $json, (New-Object System.Text.UTF8Encoding $false))
}
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null = [Windows.Foundation.IAsyncOperation`1, Windows.Foundation, ContentType = WindowsRuntime]
    $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
    $null = [Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]
    $null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
    $null = [Windows.Storage.FileAccessMode, Windows.Storage, ContentType = WindowsRuntime]
    $null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime]

    $asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
    function Await($op, [Type]$type) {
        $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
        $null = $task.Wait(-1)
        $task.Result
    }

    if ($env:SS_MODE -eq 'langs') {
        $langs = @([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | ForEach-Object {
            [ordered]@{ tag = $_.LanguageTag; name = $_.DisplayName } })
        $profile = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
        Save ([ordered]@{ languages = $langs; profile_ok = ($null -ne $profile);
                          max_dim = [Windows.Media.Ocr.OcrEngine]::MaxImageDimension })
        exit 0
    }

    $lang = $env:SS_LANG
    if ($lang -and $lang -ne 'auto') {
        $l = New-Object Windows.Globalization.Language($lang)
        if (-not [Windows.Media.Ocr.OcrEngine]::IsLanguageSupported($l)) {
            Save ([ordered]@{ error = 'NO_LANGUAGE'; detail = "OCR language $lang is not installed" }); exit 0
        }
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($l)
    } else {
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    }
    if ($null -eq $engine) { Save ([ordered]@{ error = 'NO_LANGUAGE'; detail = 'no OCR language' }); exit 0 }

    $pages = @()
    foreach ($path in (Get-Content -LiteralPath $env:SS_LIST -Encoding UTF8)) {
        if (-not $path) { continue }
        $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
        $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        $dec = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bmp = Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $res = Await ($engine.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
        $lines = @()
        foreach ($ln in $res.Lines) {
            $words = @()
            foreach ($w in $ln.Words) {
                $r = $w.BoundingRect
                $words += [ordered]@{ t = $w.Text; x = $r.X; y = $r.Y; w = $r.Width; h = $r.Height }
            }
            $lines += [ordered]@{ t = $ln.Text; words = $words }
        }
        $pages += [ordered]@{ width = $bmp.PixelWidth; height = $bmp.PixelHeight; lines = $lines }
        $stream.Dispose()
    }
    Save ([ordered]@{ pages = $pages })
} catch {
    Save ([ordered]@{ error = 'FAILED'; detail = ($_.Exception.Message + ' @ ' + $_.InvocationInfo.PositionMessage) })
}
"""


# ------------------------------------------------------------------- results

@dataclass
class Word:
    text: str
    x: float
    y: float
    w: float
    h: float


@dataclass
class Line:
    text: str
    words: list[Word] = field(default_factory=list)

    @property
    def box(self) -> tuple[float, float, float, float]:
        if not self.words:
            return 0.0, 0.0, 0.0, 0.0
        x0 = min(w.x for w in self.words)
        y0 = min(w.y for w in self.words)
        x1 = max(w.x + w.w for w in self.words)
        y1 = max(w.y + w.h for w in self.words)
        return x0, y0, x1 - x0, y1 - y0


@dataclass
class Page:
    width: int
    height: int
    lines: list[Line] = field(default_factory=list)

    def paragraphs(self) -> list[list[Line]]:
        """Lines grouped into paragraphs by vertical gaps."""
        lines = [ln for ln in self.lines if ln.text.strip()]
        if not lines:
            return []
        heights = [ln.box[3] for ln in lines if ln.box[3] > 0]
        med = statistics.median(heights) if heights else 20.0
        paras, cur, prev_bottom = [], [], None
        for ln in lines:
            x, y, w, h = ln.box
            if cur and prev_bottom is not None and (y - prev_bottom) > 0.7 * med:
                paras.append(cur)
                cur = []
            cur.append(ln)
            prev_bottom = y + h
        if cur:
            paras.append(cur)
        return paras

    def paragraph_texts(self) -> list[str]:
        """One string per paragraph. Prose (lines that run to the right edge) is re-flowed
        into one line; short lines (forms, addresses, lists) keep their breaks."""
        widest = max((ln.box[2] for ln in self.lines), default=0.0) or 1.0
        # A "full" line reaches the right edge of the text block AND spans a good share of the
        # page (so two short, equally long lines such as a label pair aren't called prose).
        full_width = max(0.8 * widest, 0.45 * self.width)
        out = []
        for para in self.paragraphs():
            body = [ln for ln in para[:-1]] or []
            full = sum(1 for ln in body if ln.box[2] >= full_width)
            prose = len(para) > 1 and body and full >= max(1, int(0.6 * len(body)))
            out.append((" " if prose else "\n").join(ln.text.strip() for ln in para))
        return out

    def blocks(self) -> list[tuple[str, str]]:
        """(kind, text) per paragraph: a short, clearly taller single line is a heading
        (OCR reports line heights, not font sizes, so this is a good-enough proxy)."""
        heights = [ln.box[3] for ln in self.lines if ln.box[3] > 0]
        med = statistics.median(heights) if heights else 0.0
        out = []
        for para, text in zip(self.paragraphs(), self.paragraph_texts()):
            h = max(ln.box[3] for ln in para)
            kind = "p"
            if med and len(para) == 1 and len(text) <= 120 and len(self.lines) > 2:
                if h >= 1.5 * med:
                    kind = "h1"
                elif h >= 1.25 * med:
                    kind = "h2"
            out.append((kind, text))
        return out

    def text(self) -> str:
        return "\n\n".join(self.paragraph_texts()) + ("\n" if self.lines else "")

    def html(self) -> str:
        import html as h
        return "".join(f"<{k}>{h.escape(t).replace(chr(10), '<br>')}</{k}>"
                       for k, t in self.blocks())


def _parse_page(d: dict) -> Page:
    lines = []
    for ln in _as_list(d.get("lines")):
        words = [Word(w["t"], w["x"], w["y"], w["w"], w["h"]) for w in _as_list(ln.get("words"))]
        lines.append(Line(ln.get("t", ""), words))
    return Page(int(d["width"]), int(d["height"]), lines)


def _as_list(v):
    """PowerShell's ConvertTo-Json turns one-element arrays into bare objects."""
    return [] if v is None else v if isinstance(v, list) else [v]


# -------------------------------------------------------------------- engine

def _powershell() -> str | None:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    p = Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(p) if p.is_file() else None


def available() -> bool:
    """Cheap check (no process spawned): Windows 10+, PowerShell present, and OCR hasn't been
    found unusable (no language) on this PC; or a Mac (Vision is part of macOS 10.15 and later)."""
    if sys.platform == "darwin":
        try:
            import Vision  # noqa: F401
            return True
        except ImportError:
            return False
    if sys.platform != "win32" or not _powershell():
        return False
    try:
        if sys.getwindowsversion().major < 10:
            return False
    except AttributeError:
        return False
    return config.load_state().get("ocr_ok") is not False


def _run(mode: str, env: dict, timeout: float = 180) -> dict:
    ps = _powershell()
    if not ps:
        raise OcrUnavailable("Windows PowerShell isn't available.")
    with tempfile.TemporaryDirectory(prefix="ss-ocr-") as tmp:
        out = Path(tmp) / "result.json"
        full_env = dict(os.environ, SS_MODE=mode, SS_OUT=str(out), **env)
        encoded = base64.b64encode(PS_SCRIPT.encode("utf-16-le")).decode()
        try:
            proc = subprocess.run(
                [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-EncodedCommand", encoded],
                env=full_env, capture_output=True, creationflags=NO_WINDOW, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise OcrUnavailable("OCR timed out.") from e
        if not out.exists():
            err = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace").strip()[:300]
            raise OcrUnavailable(f"OCR didn't produce a result. {err}")
        return json.loads(out.read_text(encoding="utf-8"))


def status() -> dict:
    """Probe Windows OCR: {'ok', 'languages': [(tag, name)], 'profile_ok', 'error'}.
    Also records the result so the wheel hides/shows OCR options accordingly."""
    info = {"ok": False, "languages": [], "profile_ok": False, "error": ""}
    if sys.platform == "darwin":
        try:
            from .mac import vision
            langs = vision.languages()
            info.update(languages=langs, profile_ok=True, ok=bool(langs))
        except Exception as e:
            info["error"] = str(e)
        config.save_state(ocr_ok=bool(info["ok"]))
        return info
    try:
        data = _run("langs", {}, 60)
        langs = [(d["tag"], d["name"]) for d in _as_list(data.get("languages"))]
        info.update(languages=langs, profile_ok=bool(data.get("profile_ok")), ok=bool(langs),
                    error="" if langs else NO_LANGUAGE_HELP)
        if data.get("error"):
            info.update(ok=False, error=str(data.get("detail") or data["error"]))
    except Exception as e:
        info["error"] = str(e)
    config.save_state(ocr_ok=bool(info["ok"]))
    return info


def prepare_image(src, dst: Path) -> tuple[Path, float]:
    """Normalise any readable image to an RGB PNG sized for recognition.
    Returns (path, scale) where scale = new size / original size."""
    from PIL import Image, ImageOps
    if isinstance(src, (bytes, bytearray)):
        import io
        src = io.BytesIO(src)
    with Image.open(src) as im:
        im.seek(0)
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.getchannel("A"))
            im = bg
        else:
            im = im.convert("RGB")
        w, h = im.size
        longest = max(w, h)
        scale = 1.0
        if longest < MIN_SIDE:
            scale = min(3.0, MIN_SIDE / longest)
        elif longest > MAX_SIDE:
            scale = MAX_SIDE / longest
        if scale != 1.0:
            im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
        im.save(dst, "PNG")
    return dst, scale


def recognize(images: list[Path], cfg: dict | None = None, cancel=None,
              progress=None) -> list[Page]:
    """OCR image files (PNG/JPEG/BMP/TIFF…) -> one Page each (pixel coordinates of the image)."""
    cfg = cfg or {}
    lang = str(cfg.get("ocr_language", "auto") or "auto")
    pages: list[Page] = []
    if sys.platform == "darwin":
        from .mac import vision
        for i, p in enumerate(images):
            if cancel is not None and cancel.is_set():
                from .core import Cancelled
                raise Cancelled()
            pages.append(vision.recognize_path(p, lang))
            if progress:
                progress((i + 1) / len(images))
        return pages
    batch = 6                                         # a few per PowerShell start-up
    with tempfile.TemporaryDirectory(prefix="ss-ocr-in-") as tmp:
        for start in range(0, len(images), batch):
            if cancel is not None and cancel.is_set():
                from .core import Cancelled
                raise Cancelled()
            chunk = images[start:start + batch]
            listing = Path(tmp) / f"list{start}.txt"
            listing.write_text("\n".join(str(Path(p).resolve()) for p in chunk), encoding="utf-8")
            data = _run("ocr", {"SS_LIST": str(listing), "SS_LANG": lang})
            if data.get("error") == "NO_LANGUAGE":
                config.save_state(ocr_ok=False)
                raise OcrUnavailable(NO_LANGUAGE_HELP if lang == "auto" else
                                     f"OCR language “{lang}” isn't installed in Windows. {NO_LANGUAGE_HELP}")
            if data.get("error"):
                raise OcrUnavailable(f"Windows OCR failed: {str(data.get('detail'))[:200]}")
            pages += [_parse_page(p) for p in _as_list(data.get("pages"))]
            config.save_state(ocr_ok=True)
            if progress:
                progress(min(1.0, (start + len(chunk)) / len(images)))
    return pages


_RECENT: dict = {}                                   # last few results, so looking twice is free


def recognize_file(path, cfg: dict | None = None, cancel=None) -> tuple[Page, float]:
    """OCR one image file of any format; returns (page, scale) with scale = OCR size / file size."""
    try:
        st = Path(path).stat()
        key = (str(Path(path).resolve()), st.st_mtime_ns, st.st_size, str((cfg or {}).get("ocr_language", "auto")))
    except OSError:
        key = None
    if key is not None and key in _RECENT:
        return _RECENT[key]
    with tempfile.TemporaryDirectory(prefix="ss-ocr-img-") as tmp:
        prepared, scale = prepare_image(path, Path(tmp) / "in.png")
        result = recognize([prepared], cfg, cancel)[0], scale
    if key is not None:
        _RECENT[key] = result
        while len(_RECENT) > 4:
            _RECENT.pop(next(iter(_RECENT)))
    return result
