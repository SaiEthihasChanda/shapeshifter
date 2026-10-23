"""Things you can do to a file that keep it the same kind: smaller, resize, rotate, strip location data,
split a PDF, tidy JSON, fix line endings, even out audio, remove a video's sound."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pymupdf
import pytest
from PIL import Image, ImageDraw

from shapeshifter import engine, variants
from shapeshifter.backends import media
from shapeshifter.core import Context

sys.path.insert(0, str(Path(__file__).parent))
from test_ocr import CFG                                  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not media.ffmpeg_path(), reason="ffmpeg not available")


def act(path, key, original="keep"):
    return engine.convert_inplace(path, key, Context(cfg=dict(CFG, original=original))).outputs


def opts(path):
    return {o.key: o for o in engine.options_for(path)}


def photo(path: Path, size=(1600, 1200), exif=False) -> Path:
    import random
    random.seed(3)
    im = Image.new("RGB", size)
    px = im.load()
    for x in range(0, size[0], 4):                               # busy enough that JPEG quality matters
        for y in range(0, size[1], 4):
            px[x, y] = (random.randrange(256), (x * 255) // size[0], (y * 255) // size[1])
    ImageDraw.Draw(im).rectangle((10, 10, 110, 60), fill="white")      # a marker in the top-left corner
    kw = {}
    if exif:
        ex = Image.Exif()
        ex[0x010F] = "TestCam"
        ex[0x0110] = "Model 1"
        ex[0x0112] = 1
        gps = ex.get_ifd(0x8825)
        gps[1], gps[2], gps[3], gps[4] = "N", (10.0, 30.0, 0.0), "E", (20.0, 15.0, 0.0)
        kw["exif"] = ex
    im.save(path, quality=95, **kw)
    return path


class TestPictures:
    def test_what_is_offered(self, tmp_path):
        o = opts(photo(tmp_path / "p.jpg"))
        assert [v.label for v in o["act:smaller"].variants] == ["Good quality", "Smallest"]
        assert [v.key for v in o["act:resize"].variants] == ["act:resize#half", "act:resize#1280", "act:resize#800"]   # 1600 px: no 1920
        assert [v.key for v in o["act:rotate"].variants] == [f"act:rotate#{w}" for w in ("right", "left", "180", "flip-h", "flip-v")]
        assert "act:private" not in o                                  # nothing to remove
        assert o["act:smaller"].label == "SMALLER" and o["act:smaller"].sublabel == "Asks how"
        assert o["act:smaller"].category == "tool"

    def test_private_is_offered_only_when_there_is_metadata(self, tmp_path):
        # the one-click "no location" lives inside PROPERTIES, and only when there is something to remove
        with_exif = opts(photo(tmp_path / "e.jpg", exif=True))
        assert "act:private" not in with_exif
        assert [v.key for v in with_exif["meta"].variants] == ["meta", "act:private"]
        from shapeshifter import variants
        assert variants.check("exif", photo(tmp_path / "e2.jpg", exif=True), "jpg")
        assert not variants.check("exif", photo(tmp_path / "n2.jpg"), "jpg")

    def test_smaller(self, tmp_path):
        p = photo(tmp_path / "p.jpg")
        (good,) = act(p, "act:smaller#good")
        (tiny,) = act(p, "act:smaller#smallest")
        assert good.name == "p (smaller).jpg" and tiny.name == "p (smallest).jpg"
        assert tiny.stat().st_size < good.stat().st_size < p.stat().st_size
        assert Image.open(tiny).size == (1600, 1200)                    # not larger than 2000 px: not resized

    def test_png_is_optimised_or_reduced_to_fewer_colours(self, tmp_path):
        im = Image.new("RGB", (400, 300), "white")
        ImageDraw.Draw(im).ellipse((50, 50, 350, 250), fill=(200, 30, 30))
        im.save(tmp_path / "a.png", compress_level=0)
        (opt,) = act(tmp_path / "a.png", "act:smaller#good")
        assert opt.stat().st_size < (tmp_path / "a.png").stat().st_size
        assert Image.open(opt).convert("RGB").getpixel((200, 150)) == (200, 30, 30)

    def test_resize(self, tmp_path):
        p = photo(tmp_path / "p.jpg")
        assert Image.open(act(p, "act:resize#half")[0]).size == (800, 600)
        (w,) = act(p, "act:resize#800")
        assert Image.open(w).size == (800, 600) and w.name == "p (800px).jpg"
        (small,) = act(photo(tmp_path / "tiny.png", size=(300, 200)), "act:resize#1280")
        assert Image.open(small).size == (300, 200)                     # never enlarged

    def test_rotate_and_flip(self, tmp_path):
        p = photo(tmp_path / "p.png")
        original = Image.open(p).convert("RGB")
        right = Image.open(act(p, "act:rotate#right")[0])
        assert right.size == (1200, 1600)
        assert right.convert("RGB").getpixel((1200 - 1 - 30, 30)) == original.getpixel((30, 30))   # top-left goes to top-right
        assert Image.open(act(p, "act:rotate#left")[0]).size == (1200, 1600)
        assert Image.open(act(p, "act:rotate#180")[0]).size == (1600, 1200)
        flipped = Image.open(act(p, "act:rotate#flip-h")[0]).convert("RGB")
        assert flipped.getpixel((1600 - 1 - 30, 30)) == original.getpixel((30, 30))

    def test_location_and_camera_data_are_removed(self, tmp_path):
        p = photo(tmp_path / "e.jpg", exif=True)
        assert Image.open(p).getexif().get(0x010F) == "TestCam" and Image.open(p).getexif().get_ifd(0x8825)
        (out,) = act(p, "act:private")
        ex = Image.open(out).getexif()
        assert out.name == "e (private).jpg" and not ex.get(0x010F) and not ex.get_ifd(0x8825)
        assert Image.open(out).size == (1600, 1200)

    def test_orientation_is_applied_not_lost(self, tmp_path):
        im = Image.new("RGB", (200, 100), "blue")
        ex = Image.Exif()
        ex[0x0112] = 6                                            # stored sideways, to be shown rotated
        ex[0x010F] = "Cam"
        im.save(tmp_path / "o.jpg", exif=ex)
        (out,) = act(tmp_path / "o.jpg", "act:private")
        assert Image.open(out).size == (100, 200)

    def test_the_original_follows_the_setting(self, tmp_path):
        p = photo(tmp_path / "p.jpg", size=(400, 300))
        act(p, "act:rotate#180", original="keep")
        assert p.exists()
        act(p, "act:rotate#left", original="delete")
        assert not p.exists()


class TestPdf:
    def make(self, tmp_path, pages=3):
        photo(tmp_path / "big.png", size=(1800, 1200))
        doc = pymupdf.open()
        for i in range(pages):
            page = doc.new_page()
            page.insert_text((72, 80), f"Page {i + 1}", fontsize=20)
            if i == 0:
                page.insert_image(pymupdf.Rect(50, 100, 550, 430), filename=str(tmp_path / "big.png"))
        doc.save(tmp_path / "d.pdf")
        doc.close()
        return tmp_path / "d.pdf"

    def test_offers(self, tmp_path):
        o = opts(self.make(tmp_path))
        assert {"act:pdf-smaller", "act:split", "act:pdf-images", "act:pdf-rotate"} <= set(o)
        one = tmp_path / "one"
        one.mkdir()
        single = pymupdf.open()
        single.new_page().insert_text((72, 80), "x")
        single.save(one / "s.pdf")
        small = opts(one / "s.pdf")
        assert "act:split" not in small and "act:pdf-images" not in small           # one page, no pictures

    def test_smaller(self, tmp_path):
        pdf = self.make(tmp_path)
        (out,) = act(pdf, "act:pdf-smaller#smallest")
        assert out.stat().st_size < pdf.stat().st_size * 0.7
        with pymupdf.open(out) as doc:
            assert len(doc) == 3 and "Page 2" in doc[1].get_text()

    def test_split(self, tmp_path):
        outs = act(self.make(tmp_path), "act:split")
        assert [o.name for o in outs] == ["d - page 1.pdf", "d - page 2.pdf", "d - page 3.pdf"]
        with pymupdf.open(outs[1]) as doc:
            assert len(doc) == 1 and "Page 2" in doc[0].get_text()

    def test_pictures_come_out_at_their_original_quality(self, tmp_path):
        outs = act(self.make(tmp_path), "act:pdf-images")
        assert len(outs) == 1 and outs[0].name.startswith("d - image 001")
        assert Image.open(outs[0]).size == (1800, 1200)

    def test_rotate(self, tmp_path):
        (out,) = act(self.make(tmp_path), "act:pdf-rotate#right")
        with pymupdf.open(out) as doc:
            assert all(p.rotation == 90 for p in doc)


class TestText:
    def test_json(self, tmp_path):
        (tmp_path / "d.json").write_text('{"b": [1,2], "a": {"y": 1, "x": 2}}', encoding="utf-8")
        (pretty,) = act(tmp_path / "d.json", "act:tidy#pretty")
        assert pretty.read_text(encoding="utf-8").startswith('{\n  "b": [')
        (mini,) = act(tmp_path / "d.json", "act:tidy#minify")
        assert mini.read_text(encoding="utf-8") == '{"b":[1,2],"a":{"y":1,"x":2}}'
        (srt,) = act(tmp_path / "d.json", "act:tidy#sorted")
        assert list(json.loads(srt.read_text(encoding="utf-8"))) == ["a", "b"]
        (tmp_path / "bad.json").write_text("{nope", encoding="utf-8")
        with pytest.raises(Exception, match="valid JSON"):
            act(tmp_path / "bad.json", "act:tidy#pretty")

    def test_xml(self, tmp_path):
        (tmp_path / "d.xml").write_text("<a><b>1</b><c/></a>", encoding="utf-8")
        (out,) = act(tmp_path / "d.xml", "act:tidy#pretty")
        assert "  <b>1</b>" in out.read_text(encoding="utf-8")

    def test_encoding_and_line_endings(self, tmp_path):
        (tmp_path / "w.txt").write_bytes("héllo\r\nwörld\r\n".encode("cp1252"))
        (lf,) = act(tmp_path / "w.txt", "act:fix-text#lf")
        assert lf.read_bytes() == "héllo\nwörld\n".encode("utf-8")
        (crlf,) = act(tmp_path / "w.txt", "act:fix-text#crlf")
        assert crlf.read_bytes() == "héllo\r\nwörld\r\n".encode("utf-8")
        (tmp_path / "u.csv").write_bytes("a,b\r\n1,é\r\n".encode("utf-16"))
        assert act(tmp_path / "u.csv", "act:fix-text#lf")[0].read_text(encoding="utf-8") == "a,b\n1,é\n"

    def test_only_offered_for_text_like_files(self, tmp_path):
        (tmp_path / "a.txt").write_text("x")
        assert "act:fix-text" in opts(tmp_path / "a.txt")
        assert "act:fix-text" not in opts(photo(tmp_path / "p.jpg", size=(50, 50)))


@needs_ffmpeg
class TestMedia:
    def video(self, path, seconds=3, audio=True):
        cmd = [media.ffmpeg_path(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=640x480:rate=15"]
        if audio:
            cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac"]
        subprocess.run(cmd + ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return path

    def test_no_sound_keeps_the_picture(self, tmp_path):
        v = self.video(tmp_path / "v.mp4")
        assert "act:mute" in opts(v)
        (out,) = act(v, "act:mute")
        info = media.probe(out)
        assert out.name == "v (no sound).mp4" and info.has_video and not info.has_audio
        assert "act:mute" not in opts(self.video(tmp_path / "q.mp4", audio=False))

    def test_smaller_video(self, tmp_path):
        v = self.video(tmp_path / "v.mp4", seconds=2)
        (out,) = act(v, "act:video-smaller")                           # direct key: small clips aren't offered it
        assert media.probe(out).has_video and out.stat().st_size > 0

    def test_audio(self, tmp_path):
        wav = tmp_path / "t.wav"
        subprocess.run([media.ffmpeg_path(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", str(wav)],
                       check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        mp3 = tmp_path / "t.mp3"
        subprocess.run([media.ffmpeg_path(), "-y", "-loglevel", "error", "-i", str(wav), "-b:a", "256k", str(mp3)], check=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        assert "act:audio-smaller" not in opts(wav)                    # lossless: bit rate doesn't apply
        (small,) = act(mp3, "act:audio-smaller")
        assert small.stat().st_size < mp3.stat().st_size
        (level,) = act(wav, "act:volume")
        assert level.name == "t (even volume).wav" and 2.8 <= media.probe(level).duration <= 3.4


class TestWheel:
    def wheel(self, path):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from shapeshifter.wheel import WheelOverlay
        app = QApplication.instance() or QApplication([])
        return app, WheelOverlay([str(path)], dict(CFG, original="keep", animation_ms=0))

    @staticmethod
    def spin(app, predicate, seconds=30):
        end = time.time() + seconds
        while time.time() < end and not predicate():
            app.processEvents()
            time.sleep(0.02)
        return predicate()

    def test_rotate_asks_which_way_then_does_it(self, tmp_path):
        app, w = self.wheel(photo(tmp_path / "p.jpg", size=(400, 300)))
        assert "act:rotate" in w.targets
        w._commit("act:rotate")
        assert self.spin(app, lambda: not w.analysing)
        assert w.targets == [f"act:rotate#{x}" for x in ("right", "left", "180", "flip-h", "flip-v")]
        w._commit("act:rotate#right")
        assert self.spin(app, lambda: w.state == "done")
        assert Image.open(tmp_path / "p (rotated right).jpg").size == (300, 400)

    def test_typing_finds_actions(self, tmp_path):
        app, w = self.wheel(photo(tmp_path / "p.jpg", size=(400, 300)))
        w.set_query("compress")
        assert [w.targets[i] for i in w.matches()][:1] == ["act:smaller"]
        w.set_query("rotate")
        assert w.targets[w.matches()[0]] == "act:rotate"

    def test_an_action_without_choices_runs_directly(self, tmp_path):
        app, w = self.wheel(photo(tmp_path / "e.jpg", size=(400, 300), exif=True))
        w._commit("act:private")
        assert self.spin(app, lambda: w.state == "done")
        assert (tmp_path / "e (private).jpg").exists()


def test_the_new_facts():
    f = variants.facts_for
    assert callable(f)
