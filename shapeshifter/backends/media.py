"""Audio and video conversions with ffmpeg."""

from __future__ import annotations

import logging
from dataclasses import dataclass
import os
import re
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from ..formats import CATEGORIES
from .base import NO_WINDOW, Backend, Cancelled, Context, ConversionError, find_exe, target_ext
from ..system import exe_name

log = logging.getLogger(__name__)

# Configured kinds ffmpeg can read (see catalog.py) on top of the main lists.
EXTRA_VIDEO = {"mts", "vob", "m2v", "asf", "rm", "f4v", "3g2", "mxf", "nut", "y4m", "ivf", "dv",
               "ogm", "h264", "hevc", "qt", "divx"}
EXTRA_AUDIO = {"m4b", "oga", "mka", "weba", "ape", "wv", "tta", "caf", "au", "voc", "spx", "dts",
               "ra", "w64", "mpc", "mod"}
VIDEO = set(CATEGORIES["video"]) | EXTRA_VIDEO
AUDIO = set(CATEGORIES["audio"]) | EXTRA_AUDIO
# Targets offered on the wheel. Rarer containers are still accepted as sources and
# can be produced from the command line (`shapeshifter convert --to flv ...`).
AUDIO_TARGETS = {"mp3", "wav", "flac", "m4a", "ogg", "opus", "aac", "wma", "aiff", "ac3"}
EXTRACT_TARGETS = {"mp3", "wav", "flac", "m4a", "ogg", "opus", "aac"}
VIDEO_TARGETS = {"mp4", "mkv", "webm", "mov", "avi", "wmv"}
CLI_ONLY_TARGETS = {"flv", "m4v", "mpg", "ts", "3gp", "ogv"}
FRAME_TARGETS = {"png", "jpg", "webp"}               # a picture (or several) taken from a video
SUBTITLE_TARGETS = {"srt", "vtt"}                    # subtitle tracks stored in the video
SMALL_VF = "scale='min(1280,trunc(iw/2)*2)':-2"
GIF_SMALL = ("fps=8,scale='min(320,iw)':-2:flags=lanczos,split[a][b];"
             "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4")

EVEN = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
H264 = ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-vf", EVEN]
GIF_FILTER = ("fps=12,scale='min(640,iw)':-2:flags=lanczos,split[a][b];"
              "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4")

# target -> (encoder that must exist, ffmpeg output arguments)
ENCODE = {
    "mp4": ("libx264", H264 + ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]),
    "m4v": ("libx264", H264 + ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]),
    "mov": ("libx264", H264 + ["-c:a", "aac", "-b:a", "192k"]),
    "mkv": ("libx264", H264 + ["-c:a", "aac", "-b:a", "192k"]),
    "flv": ("libx264", H264 + ["-c:a", "aac", "-b:a", "160k"]),
    "ts": ("libx264", H264 + ["-c:a", "aac", "-b:a", "192k"]),
    "3gp": ("libx264", ["-c:v", "libx264", "-profile:v", "baseline", "-crf", "26",
                        "-pix_fmt", "yuv420p", "-vf", "scale='min(1280,iw)':-2",
                        "-c:a", "aac", "-b:a", "96k"]),
    "webm": ("libvpx-vp9", ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1",
                            "-deadline", "good", "-cpu-used", "4", "-c:a", "libopus", "-b:a", "128k"]),
    "avi": ("mpeg4", ["-c:v", "mpeg4", "-q:v", "3", "-vtag", "xvid", "-c:a", "libmp3lame", "-q:a", "3"]),
    "wmv": ("wmv2", ["-c:v", "wmv2", "-b:v", "4M", "-c:a", "wmav2", "-b:a", "192k"]),
    "mpg": ("mpeg2video", ["-c:v", "mpeg2video", "-q:v", "3", "-c:a", "mp2", "-b:a", "192k"]),
    "ogv": ("libtheora", ["-c:v", "libtheora", "-q:v", "7", "-c:a", "libvorbis", "-q:a", "4"]),
    "gif": ("gif", ["-vf", GIF_FILTER, "-loop", "0", "-an"]),
    "mp3": ("libmp3lame", ["-vn", "-c:a", "libmp3lame", "-q:a", "2"]),
    "wav": ("pcm_s16le", ["-vn", "-c:a", "pcm_s16le"]),
    "flac": ("flac", ["-vn", "-c:a", "flac"]),
    "m4a": ("aac", ["-vn", "-c:a", "aac", "-b:a", "192k"]),
    "aac": ("aac", ["-vn", "-c:a", "aac", "-b:a", "192k"]),
    "ogg": ("libvorbis", ["-vn", "-c:a", "libvorbis", "-q:a", "5"]),
    "opus": ("libopus", ["-vn", "-c:a", "libopus", "-b:a", "128k"]),
    "wma": ("wmav2", ["-vn", "-c:a", "wmav2", "-b:a", "192k"]),
    "aiff": ("pcm_s16be", ["-vn", "-c:a", "pcm_s16be"]),
    "ac3": ("ac3", ["-vn", "-c:a", "ac3", "-b:a", "384k"]),
    # not run through the generic path; listed so their encoders are checked
    "png": ("png", []), "jpg": ("mjpeg", []), "webp": ("libwebp", []),
    "srt": ("subrip", []), "vtt": ("webvtt", []),
}

# Containers that can usually take the source streams unchanged (fast, lossless remux).
REMUX = {
    "mkv": ["-map", "0", "-c", "copy"],
    "mp4": ["-map", "0:v?", "-map", "0:a?", "-c", "copy", "-movflags", "+faststart"],
    "m4v": ["-map", "0:v?", "-map", "0:a?", "-c", "copy", "-movflags", "+faststart"],
    "mov": ["-map", "0:v?", "-map", "0:a?", "-c", "copy"],
}


def _bundled_dir() -> Path | None:
    """ffmpeg shipped inside the packaged app (ShapeShifter.exe / ShapeShifter.app build)."""
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "ffmpeg"
    return None


def _ffmpeg_dirs():
    local = os.environ.get("LOCALAPPDATA", "")
    return [Path(local) / "Microsoft" / "WinGet" / "Links", r"C:\ffmpeg\bin",
            r"C:\Program Files\ffmpeg\bin", Path.home() / "scoop" / "shims",
            r"C:\ProgramData\chocolatey\bin"]


@lru_cache(maxsize=1)
def ffmpeg_path() -> str | None:
    bundled = _bundled_dir()
    if bundled and (bundled / exe_name("ffmpeg")).is_file():
        return str(bundled / exe_name("ffmpeg"))
    return find_exe("ffmpeg", _ffmpeg_dirs())


@lru_cache(maxsize=1)
def ffprobe_path() -> str | None:
    ff = ffmpeg_path()
    if ff:
        sibling = Path(ff).with_name(exe_name("ffprobe"))
        if sibling.is_file():
            return str(sibling)
    return find_exe("ffprobe", _ffmpeg_dirs())


@lru_cache(maxsize=1)
def encoders() -> set[str]:
    """Encoders compiled into ffmpeg, cached on disk per ffmpeg binary."""
    import json

    from ..config import DATA_DIR

    ff = ffmpeg_path()
    if not ff:
        return set()
    cache = DATA_DIR / "ffmpeg_encoders.json"
    try:
        key = f"{ff}|{os.path.getmtime(ff)}"
    except OSError:
        key = ff
    try:
        data = json.loads(cache.read_text())
        if data.get("key") == key:
            return set(data["encoders"])
    except Exception:
        pass
    found = _query_encoders(ff)
    if found:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"key": key, "encoders": sorted(found)}))
        except OSError:
            pass
    return found


def _query_encoders(ff: str) -> set[str]:
    try:
        out = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True, text=True,
                             creationflags=NO_WINDOW, timeout=20).stdout
    except Exception:
        log.exception("ffmpeg -encoders failed")
        return set()
    found = set()
    for line in out.splitlines():
        m = re.match(r"\s*[VAS][\.A-Z]{5}\s+(\S+)", line)
        if m:
            found.add(m.group(1))
    return found


def _ffmpeg_info(src: Path) -> str:
    """ffmpeg's stream summary (stderr of `ffmpeg -i`), used when ffprobe isn't available."""
    ff = ffmpeg_path()
    if not ff:
        return ""
    try:
        return subprocess.run([ff, "-hide_banner", "-i", str(src)], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
                              timeout=30).stderr
    except Exception:
        return ""


@dataclass
class MediaInfo:
    duration: float | None = None
    has_video: bool = False
    has_audio: bool = False
    subs: int = 0
    vcodec: str = ""
    acodec: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0


def parse_info(text: str) -> MediaInfo:
    """Read ffmpeg's stream summary (stderr of `ffmpeg -i`)."""
    info = MediaInfo()
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if m:
        info.duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    for line in text.splitlines():
        m = re.search(r"Stream #\d+:\d+.*?: (Video|Audio|Subtitle): ([\w\-]+)(.*)", line)
        if not m:
            continue
        kind, codec, rest = m.groups()
        if kind == "Video" and "attached pic" not in line:
            if not info.has_video:
                info.has_video, info.vcodec = True, codec
                dims = re.search(r"(\d{2,5})x(\d{2,5})", rest)
                if dims:
                    info.width, info.height = int(dims.group(1)), int(dims.group(2))
                fps = re.search(r"([\d.]+) fps", rest)
                info.fps = float(fps.group(1)) if fps else 0.0
        elif kind == "Audio":
            if not info.has_audio:
                info.has_audio, info.acodec = True, codec
        elif kind == "Subtitle":
            info.subs += 1
    return info


@lru_cache(maxsize=32)
def _probe_cached(path: str, mtime: int, size: int) -> MediaInfo:
    return parse_info(_ffmpeg_info(Path(path)))


def probe(src) -> MediaInfo:
    """Streams and duration of a media file (cached per file version)."""
    p = Path(src)
    try:
        st = p.stat()
        return _probe_cached(str(p.resolve()), st.st_mtime_ns, st.st_size)
    except OSError:
        return MediaInfo()


def probe_format(src: Path) -> str:
    """Container name(s) as ffprobe reports them, e.g. 'matroska,webm' ('' if unknown)."""
    fp = ffprobe_path()
    if fp:
        try:
            return subprocess.run([fp, "-v", "error", "-show_entries", "format=format_name",
                                   "-of", "default=nw=1:nk=1", str(src)], capture_output=True,
                                  text=True, timeout=8, creationflags=NO_WINDOW).stdout.strip()
        except Exception:
            return ""
    m = re.search(r"Input #0, (.+?), from ", _ffmpeg_info(src))
    return m.group(1) if m else ""


def probe_duration(src: Path) -> float | None:
    fp = ffprobe_path()
    if not fp:
        m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", _ffmpeg_info(src))
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else None
    try:
        out = subprocess.run([fp, "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nw=1:nk=1", str(src)],
                             capture_output=True, text=True, creationflags=NO_WINDOW,
                             timeout=30).stdout.strip()
        return float(out) if out and out != "N/A" else None
    except Exception:
        return None


class MediaBackend(Backend):
    name = "ffmpeg"

    def available(self):
        return ffmpeg_path() is not None

    def sources(self):
        return VIDEO | AUDIO | {"gif"}

    def _encodable(self, exts: set[str]) -> set[str]:
        enc = encoders()
        return {e for e in exts if ENCODE[e][0] in enc}

    def targets(self, src):
        if src in VIDEO:
            t = VIDEO_TARGETS | {"gif"} | EXTRACT_TARGETS | FRAME_TARGETS | SUBTITLE_TARGETS
        elif src == "gif":
            t = {"mp4", "webm", "mov", "mkv"}
        elif src in AUDIO:
            t = set(AUDIO_TARGETS)
        else:
            return set()
        return self._encodable(t) - {src}

    def can(self, src, dst):
        if src in VIDEO and dst in CLI_ONLY_TARGETS | AUDIO_TARGETS:
            return dst != src and dst in self._encodable({dst})
        return super().can(src, dst)

    def extra_targets(self, src):
        if src in VIDEO:
            return self._encodable(CLI_ONLY_TARGETS | AUDIO_TARGETS) - {src}
        return set()

    def cost(self, src, dst):
        if dst in FRAME_TARGETS | SUBTITLE_TARGETS:
            return 2.0
        if src in VIDEO and dst in REMUX:
            return 2.0   # usually a fast lossless remux
        return 4.0 if dst in VIDEO or dst == "gif" or src == "gif" else 2.0

    def terminal(self, src, dst):
        # GIF <-> video conversions have no soundtrack; chaining further makes no sense.
        return dst == "gif" or src == "gif" or dst in FRAME_TARGETS

    def first_step_only(self, src, dst):
        return src == "gif"   # a still image turned into a GIF isn't a video

    def convert(self, src: Path, dst: Path, ctx: Context) -> list[Path]:
        tgt = ctx.dst_fmt or target_ext(dst)
        src_ext = ctx.src_fmt or target_ext(src)
        duration = probe_duration(src)
        v = ctx.variant
        if tgt in FRAME_TARGETS and src_ext in VIDEO:
            return self._frames(src, dst, ctx, v, duration or 0.0)
        if tgt in SUBTITLE_TARGETS and src_ext in VIDEO:
            return self._subtitles(src, dst, ctx, tgt)

        attempts = []
        pre: list[str] = []
        if tgt == "gif":
            enc = list(ENCODE["gif"][1])
            if v == "small":
                enc[enc.index(GIF_FILTER)] = GIF_SMALL
            elif v == "short":
                enc = ["-t", "10"] + enc
            attempts.append(enc)
        elif v == "small" and tgt in VIDEO_TARGETS | CLI_ONLY_TARGETS:
            attempts.append(_smaller(ENCODE[tgt][1]))
        elif v == "mute" and tgt in VIDEO_TARGETS | CLI_ONLY_TARGETS:
            if tgt in REMUX and src_ext in VIDEO:
                attempts.append(REMUX[tgt] + ["-an"])
            attempts.append(ENCODE[tgt][1] + ["-an"])
        else:
            if tgt in REMUX and src_ext in VIDEO:
                attempts.append(REMUX[tgt])
            attempts.append(ENCODE[tgt][1])

        error = ""
        for args in attempts:
            rc, error = self._run(src, dst, args, duration, ctx)
            if rc == 0 and dst.exists() and dst.stat().st_size > 0:
                ctx.progress(1.0)
                return [dst]
            dst.unlink(missing_ok=True)
            log.info("ffmpeg attempt failed (%s): %s", args[:4], error[-500:])
        raise ConversionError(_summarize(error))

    def _frames(self, src: Path, dst: Path, ctx: Context, variant: str, duration: float) -> list[Path]:
        """Pictures from a video: the middle frame, the first, one every few seconds, or a contact sheet."""
        tgt = target_ext(dst)
        q = {"jpg": ["-q:v", "2"], "webp": ["-quality", "90"]}.get(tgt, [])
        if variant == "every":
            step = max(1.0, round(duration / 30)) if duration else 5.0
            out = dst.with_name(f"{dst.stem}-%03d{dst.suffix}")
            rc, err = self._run(src, out, ["-vf", f"fps=1/{step:g}", *q], duration, ctx)
            files = sorted(dst.parent.glob(f"{dst.stem}-*{dst.suffix}"))
            if rc != 0 or not files:
                raise ConversionError(_summarize(err))
            ctx.progress(1.0)
            return files
        if variant == "sheet":
            step = max(0.5, duration / 20) if duration else 2.0
            rc, err = self._run(src, dst, ["-vf", f"fps=1/{step:g},scale=320:-2,tile=5x4", "-frames:v", "1", *q], duration, ctx)
        elif variant == "first":
            rc, err = self._run(src, dst, ["-frames:v", "1", *q], duration, ctx)
        else:
            rc, err = self._run(src, dst, ["-frames:v", "1", *q], duration, ctx,
                                pre=["-ss", f"{max(0.0, duration / 2):.2f}"] if duration else [])
        if rc != 0 or not dst.exists() or dst.stat().st_size == 0:
            raise ConversionError(_summarize(err))
        ctx.progress(1.0)
        return [dst]

    def _subtitles(self, src: Path, dst: Path, ctx: Context, tgt: str) -> list[Path]:
        codec = {"srt": "srt", "vtt": "webvtt"}[tgt]
        rc, err = self._run(src, dst, ["-map", "0:s:0", "-c:s", codec], None, ctx)
        if rc != 0 or not dst.exists() or dst.stat().st_size == 0:
            raise ConversionError("This video has no subtitle track that can be extracted.")
        ctx.progress(1.0)
        return [dst]

    def _run(self, src, dst, args, duration, ctx: Context, pre=()):
        cmd = [ffmpeg_path(), "-y", "-hide_banner", "-nostdin", *pre, "-i", str(src), *args,
               "-progress", "pipe:1", "-nostats", str(dst)]
        with tempfile.TemporaryFile() as errf:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errf, stdin=subprocess.DEVNULL,
                                    creationflags=NO_WINDOW, text=True, encoding="utf-8",
                                    errors="replace")
            try:
                for line in proc.stdout:
                    if ctx.cancel_event.is_set():
                        proc.kill()
                        proc.wait()
                        raise Cancelled()
                    if duration and line.startswith("out_time_us="):
                        try:
                            us = int(line.split("=", 1)[1])
                            ctx.progress(max(0.0, min(0.99, us / 1e6 / duration)))
                        except ValueError:
                            pass
                proc.wait()
            except BaseException:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                dst.unlink(missing_ok=True)
                raise
            errf.seek(0)
            err = errf.read().decode("utf-8", "replace")
        return proc.returncode, err


def _smaller(args: list[str]) -> list[str]:
    """The target's usual output arguments, at a lower quality and at most 1280 px wide."""
    out = list(args)
    for flag, value in (("-crf", None), ("-b:a", "96k"), ("-vf", SMALL_VF), ("-q:v", "6")):
        if flag in out:
            i = out.index(flag)
            if flag == "-crf":
                out[i + 1] = str(min(40, int(out[i + 1]) + 8))
            elif flag in ("-b:a", "-vf"):
                out[i + 1] = value
            elif flag == "-q:v":
                out[i + 1] = value
    if "-vf" not in out:
        out += ["-vf", SMALL_VF]
    return out


def _summarize(stderr: str) -> str:
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
    if any("does not contain any stream" in ln or "matches no streams" in ln for ln in lines):
        return "The file has no stream that fits the chosen format (e.g. no audio track)."
    interesting = [ln for ln in lines if re.search(r"error|invalid|not supported|could not|unable",
                                                    ln, re.I)]
    return "ffmpeg failed: " + ((interesting or lines or ["unknown error"])[-1])[:300]
