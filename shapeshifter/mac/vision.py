"""Text recognition with Apple's Vision framework (built into macOS; nothing to install).

Returns the same Page / Line / Word structure as the Windows recogniser: lines in reading order, each with its
words and their boxes in image pixels (top-left origin).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)


def _request(lang: str):
    import Vision
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(True)
    if lang and lang != "auto":
        req.setRecognitionLanguages_([lang])
    elif req.respondsToSelector_("setAutomaticallyDetectsLanguage:"):      # macOS 13 and later
        req.setAutomaticallyDetectsLanguage_(True)
    return req


def languages() -> list[tuple[str, str]]:
    """(tag, name) of every language Vision can read on this Mac."""
    from Foundation import NSLocale
    req = _request("auto")
    tags, _err = req.supportedRecognitionLanguagesAndReturnError_(None)
    here = NSLocale.currentLocale()
    out = []
    for tag in tags or []:
        name = here.localizedStringForLanguageCode_(str(tag)) or str(tag)
        out.append((str(tag), str(name)))
    return out


def _utf16_index(text: str) -> list[int]:
    """UTF-16 offset of every character position (NSString ranges count UTF-16 units)."""
    out, n = [], 0
    for ch in text:
        out.append(n)
        n += 2 if ord(ch) > 0xFFFF else 1
    out.append(n)
    return out


def recognize_path(path, lang: str = "auto"):
    """OCR one image file -> ocr.Page (pixel coordinates of that image)."""
    import Vision
    from Foundation import NSURL, NSMakeRange
    from PIL import Image
    from ..ocr import Line, OcrUnavailable, Page, Word

    with Image.open(path) as im:
        W, H = im.size
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(NSURL.fileURLWithPath_(str(Path(path))), {})
    req = _request(lang)
    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        raise OcrUnavailable(f"macOS text recognition failed: {err}")
    lines = []
    for obs in req.results() or []:
        cands = obs.topCandidates_(1)
        if not cands:
            continue
        cand = cands[0]
        text = str(cand.string())
        idx = _utf16_index(text)
        words = []
        for m in re.finditer(r"\S+", text):
            start, end = idx[m.start()], idx[m.end()]
            box, _e = cand.boundingBoxForRange_error_(NSMakeRange(start, end - start), None)
            if box is None:
                continue
            r = box.boundingBox()                    # normalised, origin at the bottom left
            x, y = r.origin.x * W, (1.0 - r.origin.y - r.size.height) * H
            words.append(Word(m.group(), x, y, r.size.width * W, r.size.height * H))
        if words:
            lines.append(Line(text, words))
    return Page(W, H, reading_order(lines))


def reading_order(lines: list) -> list:
    """Top to bottom; lines side by side on one row (their middles within half a line of each other) left to right."""
    lines = sorted(lines, key=lambda ln: ln.box[1] + ln.box[3] / 2)
    rows: list[list] = []
    for ln in lines:
        mid = ln.box[1] + ln.box[3] / 2
        if rows:
            ref = rows[-1][0]
            if abs(mid - (ref.box[1] + ref.box[3] / 2)) < 0.5 * min(ln.box[3], ref.box[3]):
                rows[-1].append(ln)
                continue
        rows.append([ln])
    return [ln for row in rows for ln in sorted(row, key=lambda ln: ln.box[0])]
