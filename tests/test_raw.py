"""Camera RAW files: the full-size preview JPEG inside is extracted and turned the right way up."""

import io
import struct
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from shapeshifter import engine, planner
from shapeshifter.backends import raw
from shapeshifter.core import Context

sys.path.insert(0, str(Path(__file__).parent))
from test_ocr import CFG                                  # noqa: E402


def jpeg(size, color=(10, 120, 200), marker=True) -> bytes:
    im = Image.new("RGB", size, color)
    if marker:
        ImageDraw.Draw(im).rectangle((5, 5, 40, 25), fill=(250, 250, 0))      # top-left corner marker
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def make_raw(path: Path, big=(1200, 800), thumb=(160, 120), orientation=1, byte_order="<") -> Path:
    """A TIFF-style container: header, one IFD (with the orientation), a thumbnail and the big preview."""
    e = byte_order
    head = (b"II*\x00" if e == "<" else b"MM\x00*") + struct.pack(e + "I", 8)
    ifd = struct.pack(e + "H", 1) + struct.pack(e + "HHI", 0x0112, 3, 1) + struct.pack(e + "HH", orientation, 0) + struct.pack(e + "I", 0)
    body = head + ifd + b"\x00" * 64 + jpeg(thumb) + b"\x11" * 300 + jpeg(big) + b"\x22" * 100
    path.write_bytes(body)
    return path


def run(path, target="jpg"):
    return engine.convert_inplace(path, target, Context(cfg=dict(CFG, original="keep"))).outputs


class TestExtraction:
    def test_the_biggest_preview_is_found(self, tmp_path):
        data = make_raw(tmp_path / "a.nef").read_bytes()
        cands = raw.preview_candidates(data)
        assert cands[0][0] == 1200 * 800                                   # tiny thumbnails (< 2 KB) are ignored

    def test_jpeg_end_handles_stuffed_bytes_and_garbage(self):
        data = b"junk" + jpeg((300, 200)) + b"\xff\xd8\xff more junk"
        end = raw.jpeg_end(data, 4)
        assert Image.open(io.BytesIO(data[4:end])).size == (300, 200)
        assert raw.jpeg_end(b"\xff\xd8\xff\xe0\x00\x04", 0) is None

    def test_a_preview_that_ends_exactly_at_the_end_of_the_file(self):
        noisy = io.BytesIO()
        Image.effect_noise((300, 200), 90).convert("RGB").save(noisy, "JPEG")
        data = b"II*\x00" + b"\x00" * 20 + noisy.getvalue()            # nothing follows the end-of-image marker
        assert raw.preview_candidates(data)[0][0] == 300 * 200

    def test_orientation_is_read_in_either_byte_order(self, tmp_path):
        for order in "<>":
            assert raw.tiff_orientation(make_raw(tmp_path / "o.nef", orientation=6, byte_order=order).read_bytes()) == 6
        assert raw.tiff_orientation(b"nonsense") == 1


class TestConversion:
    def test_the_camera_jpeg_comes_out_byte_for_byte(self, tmp_path):
        p = make_raw(tmp_path / "IMG_1.cr2")
        (out,) = run(p)
        assert out.name == "IMG_1.jpg"
        assert Image.open(out).size == (1200, 800)
        assert out.read_bytes() in p.read_bytes()                      # untouched: no second JPEG compression

    @pytest.mark.parametrize("ext", ["nef", "cr2", "dng", "arw"])
    def test_every_family(self, tmp_path, ext):
        (out,) = run(make_raw(tmp_path / f"x.{ext}"))
        assert Image.open(out).size == (1200, 800)

    def test_a_sideways_shot_is_turned_upright(self, tmp_path):
        (out,) = run(make_raw(tmp_path / "s.nef", orientation=6))
        im = Image.open(out)
        assert im.size == (800, 1200)                                   # was shot in portrait
        # the corner marker (yellow) started top-left; turned 90 degrees clockwise it sits top-right
        assert im.convert("RGB").getpixel((800 - 20, 15))[:2] == pytest.approx((250, 250), abs=25)

    def test_other_formats_go_through_the_jpeg(self, tmp_path):
        p = make_raw(tmp_path / "x.nef")
        assert Image.open(run(p, "png")[0]).size == (1200, 800)
        assert len(run(p, "pdf")) == 1
        opt = planner.plan("nef")["png"]
        assert [e.backend.name for e in opt.path] == ["raw", "images"]
        assert any("preview" in w for w in opt.warnings)

    def test_dng_goes_to_the_raw_reader_not_pillow(self):
        assert planner.plan("dng")["jpg"].path[0].backend.name == "raw"

    def test_no_preview_means_a_clear_error(self, tmp_path):
        (tmp_path / "empty.nef").write_bytes(b"II*\x00\x08\x00\x00\x00" + b"\x00" * 3000)
        with pytest.raises(Exception, match="No preview"):
            run(tmp_path / "empty.nef")

    def test_in_the_wheel_options(self, tmp_path):
        opts = {o.key for o in engine.options_for(make_raw(tmp_path / "w.nef"))}
        assert {"jpg", "png", "webp", "pdf"} <= opts
