"""Subtitles and synced lyrics: SRT, WebVTT, ASS/SSA, SBV, LRC <-> each other and -> text."""

from __future__ import annotations

import re

from ..core import ConversionError
from .registry import register

FORMATS = ["srt", "vtt", "ass", "sbv", "lrc"]


def _t(s: str) -> float:
    """'01:02:03,456' / '1:02:03.45' / '02:03.45' -> seconds."""
    s = s.strip().replace(",", ".")
    parts = s.split(":")
    secs = float(parts[-1])
    if len(parts) >= 2:
        secs += int(parts[-2]) * 60
    if len(parts) == 3:
        secs += int(parts[-3]) * 3600
    return secs


def _fmt(t: float, sep: str = ",", hours: bool = True, cs: bool = False) -> str:
    t = max(0.0, t)
    h, rem = divmod(int(round(t * 1000)), 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    frac = f"{ms // 10:02d}" if cs else f"{ms:03d}"
    return (f"{h:02d}:" if hours else "") + f"{m:02d}:{s:02d}{sep}{frac}"


def parse(text: str, fmt: str) -> list[tuple[float, float, str]]:
    text = text.replace("\r\n", "\n").lstrip("﻿")
    cues = []
    if fmt in ("srt", "vtt"):
        for block in re.split(r"\n\s*\n", text):
            m = re.search(r"([\d:.,]+)\s*-->\s*([\d:.,]+)[^\n]*\n(.*)", block, re.S)
            if m:
                body = re.sub(r"<[^>]+>", "", m.group(3)).strip()
                cues.append((_t(m.group(1)), _t(m.group(2)), body))
    elif fmt == "sbv":
        for block in re.split(r"\n\s*\n", text):
            m = re.match(r"\s*([\d:.]+),([\d:.]+)\n(.*)", block, re.S)
            if m:
                cues.append((_t(m.group(1)), _t(m.group(2)), m.group(3).strip()))
    elif fmt == "ass":
        fields = None
        for line in text.split("\n"):
            if line.startswith("Format:") and fields is None and "Text" in line:
                fields = [f.strip() for f in line[7:].split(",")]
            elif line.startswith("Dialogue:"):
                parts = line[9:].split(",", len(fields or range(10)) - 1)
                f = fields or ["Layer", "Start", "End", "Style", "Name", "MarginL", "MarginR",
                               "MarginV", "Effect", "Text"]
                row = dict(zip(f, parts))
                body = re.sub(r"\{[^}]*\}", "", row.get("Text", "")).replace("\\N", "\n").replace("\\n", "\n")
                cues.append((_t(row.get("Start", "0")), _t(row.get("End", "0")), body.strip()))
    elif fmt == "lrc":
        stamped = []
        for line in text.split("\n"):
            stamps = re.findall(r"\[(\d+:\d\d(?:[.:]\d+)?)\]", line)
            body = re.sub(r"\[[^\]]*\]", "", line).strip()
            for s in stamps:
                # [mm:ss.xx] or the rarer [mm:ss:xx]
                stamped.append((_t(".".join(s.rsplit(":", 1)) if s.count(":") == 2 else s), body))
        stamped.sort()
        for i, (start, body) in enumerate(stamped):
            end = stamped[i + 1][0] if i + 1 < len(stamped) else start + 4
            cues.append((start, end, body))
    if not cues:
        raise ConversionError("No subtitle cues found")
    return cues


def write(cues, fmt: str) -> str:
    if fmt == "srt":
        return "\n".join(f"{i}\n{_fmt(a)} --> {_fmt(b)}\n{t}\n" for i, (a, b, t) in enumerate(cues, 1))
    if fmt == "vtt":
        return "WEBVTT\n\n" + "\n".join(f"{_fmt(a, '.')} --> {_fmt(b, '.')}\n{t}\n" for a, b, t in cues)
    if fmt == "sbv":
        return "\n".join(f"{_fmt(a, '.', True)[1:]},{_fmt(b, '.', True)[1:]}\n{t}\n" for a, b, t in cues)
    if fmt == "lrc":
        return "".join(f"[{_fmt(a, '.', False, True)}]{t.replace(chr(10), ' ')}\n" for a, _, t in cues)
    if fmt == "ass":
        head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n\n"
                "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
                "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, "
                "Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
                "MarginV, Encoding\nStyle: Default,Arial,56,&H00FFFFFF,&H000000FF,&H00000000,"
                "&H64000000,0,0,0,0,100,100,0,0,1,2,1,2,60,60,50,1\n\n[Events]\n"
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
        lines = [f"Dialogue: 0,{_fmt(a, '.', True, True)[1:]},{_fmt(b, '.', True, True)[1:]},"
                 f"Default,,0,0,0,,{t.replace(chr(10), chr(92) + 'N')}" for a, b, t in cues]
        return head + "\n".join(lines) + "\n"
    if fmt == "txt":
        return "\n".join(t for _, _, t in cues) + "\n"
    raise ConversionError(f"Can't write {fmt}")


@register(FORMATS, FORMATS + ["txt"])
def convert(data: bytes, src, dst, ctx) -> bytes:
    return write(parse(data.decode("utf-8-sig", "replace"), src), dst).encode("utf-8")
