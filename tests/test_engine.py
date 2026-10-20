"""End-to-end conversion tests. Run: python -m pytest tests  (or python tests/test_engine.py)"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shapeshifter import config, engine  # noqa: E402
from shapeshifter.backends import Context  # noqa: E402
from shapeshifter.backends.media import ffmpeg_path  # noqa: E402


def make_samples(d: Path) -> dict[str, Path]:
    from PIL import Image, ImageDraw
    import pymupdf as fitz

    s = {}
    im = Image.new("RGBA", (120, 80), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((10, 10, 110, 70), fill=(200, 40, 40, 255))
    s["png"] = d / "alpha.png"
    im.save(s["png"])
    s["jpg"] = d / "photo.jpg"
    im.convert("RGB").save(s["jpg"])
    frames = [Image.new("RGB", (64, 64), c) for c in ("red", "green", "blue")]
    s["gif"] = d / "anim.gif"
    frames[0].save(s["gif"], save_all=True, append_images=frames[1:], duration=200, loop=0)
    s["svg"] = d / "logo.svg"
    s["svg"].write_text('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64">'
                        '<circle cx="32" cy="32" r="28" fill="teal"/></svg>')
    doc = fitz.open()
    for i in range(3):
        doc.new_page().insert_text((72, 72), f"Page {i + 1}")
    s["pdf"] = d / "doc.pdf"
    doc.save(s["pdf"])
    s["csv"] = d / "table.csv"
    s["csv"].write_text("name,age\nann,31\nbob,42\n")
    s["json"] = d / "data.json"
    s["json"].write_text(json.dumps([{"a": 1, "b": {"x": 2}}, {"a": 3}]))
    if ffmpeg_path():
        s["mp4"] = d / "clip.mp4"
        subprocess.run([ffmpeg_path(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc=duration=2:size=161x121:rate=15", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=2", "-shortest", "-c:v", "mpeg4",
                        str(s["mp4"])], check=True)
        s["wav"] = d / "tone.wav"
        subprocess.run([ffmpeg_path(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=1", str(s["wav"])], check=True)
    return s


CASES = [
    ("png", "jpg"), ("png", "webp"), ("png", "ico"), ("png", "avif"), ("png", "heic"),
    ("png", "pdf"), ("jpg", "png"), ("jpg", "bmp"), ("jpg", "tiff"), ("gif", "webp"),
    ("gif", "png"), ("gif", "mp4"), ("svg", "png"), ("svg", "pdf"), ("svg", "ico"),
    ("pdf", "png"), ("pdf", "txt"), ("pdf", "tiff"),
    ("csv", "json"), ("csv", "xlsx"), ("csv", "yaml"), ("json", "csv"), ("json", "yaml"),
    ("mp4", "mkv"), ("mp4", "webm"), ("mp4", "gif"), ("mp4", "mp3"), ("mp4", "avi"),
    ("wav", "mp3"), ("wav", "flac"), ("wav", "opus"), ("wav", "m4a"),
]


def run_all() -> int:
    cfg = dict(config.DEFAULTS, original="keep")
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        samples = make_samples(d)
        for src_ext, tgt in CASES:
            if src_ext not in samples:
                print(f"SKIP {src_ext}->{tgt} (no sample)")
                continue
            src = samples[src_ext]
            if tgt not in engine.targets_for(src):
                print(f"FAIL {src_ext}->{tgt}: not offered")
                failures += 1
                continue
            try:
                res = engine.convert_inplace(src, tgt, Context(cfg=cfg))
                sizes = [o.stat().st_size for o in res.outputs]
                assert res.outputs and all(sizes), "empty output"
                print(f"ok   {src_ext}->{tgt}: {[o.name for o in res.outputs]}")
                for o in res.outputs:
                    o.unlink()
            except Exception as e:
                failures += 1
                print(f"FAIL {src_ext}->{tgt}: {e!r}")
        # In-place behaviour: original recycled/removed, name collision handled.
        cfg_del = dict(cfg, original="delete")
        src = samples["jpg"]
        (d / "photo.png").write_bytes(b"existing")
        res = engine.convert_inplace(src, "png", Context(cfg=cfg_del))
        assert not src.exists() and res.outputs[0].name == "photo (1).png", res
        print("ok   in-place swap + collision naming")
    print(f"\n{failures} failure(s)")
    return failures


def test_all():
    assert run_all() == 0


if __name__ == "__main__":
    sys.exit(1 if run_all() else 0)
