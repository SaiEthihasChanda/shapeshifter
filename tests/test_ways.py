"""Alternative ways of reaching one target: every frame / sheet / page, smaller or silent video, and the
content checks that decide when to ask."""

import subprocess
import sys
import zipfile
from pathlib import Path

import openpyxl
import pymupdf
import pytest
from PIL import Image, ImageDraw

from shapeshifter import engine, planner, variants
from shapeshifter.backends import media
from shapeshifter.core import Context

sys.path.insert(0, str(Path(__file__).parent))
from test_ocr import CFG                                  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not media.ffmpeg_path(), reason="ffmpeg not available")


def run(path, target):
    return engine.convert_inplace(path, target, Context(cfg=dict(CFG, original="keep"))).outputs


def make_gif(path: Path, n: int = 5) -> Path:
    frames = []
    for i in range(n):
        im = Image.new("RGB", (120, 80), (40 * i, 100, 200 - 30 * i))
        ImageDraw.Draw(im).rectangle((10 + i * 15, 20, 30 + i * 15, 50), fill="white")
        frames.append(im)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=200, loop=0)
    return path


def make_book(path: Path, sheets: int = 3) -> Path:
    wb = openpyxl.Workbook()
    wb.active.title = "Sales"
    wb.active.append(["item", "qty"])
    wb.active.append(["a", 1])
    for name in ["Costs", "Notes"][:sheets - 1]:
        wb.create_sheet(name).append([name.lower(), 1])
    wb.save(path)
    return path


def make_pdf(path: Path, pages: int = 3) -> Path:
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page(width=300, height=400).insert_text((40, 100), f"Page number {i + 1}", fontsize=24)
    doc.save(path)
    doc.close()
    return path


def make_video(path: Path, seconds: int = 12, audio: bool = True, subs: bool = False) -> Path:
    cmd = [media.ffmpeg_path(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=320x240:rate=15"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    if subs:
        srt = path.with_suffix(".srt")
        srt.write_text("1\n00:00:01,000 --> 00:00:03,000\nHello there\n\n", encoding="utf-8")
        cmd += ["-i", str(srt)]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac"]
    if subs:
        cmd += ["-c:s", "mov_text", "-map", "0:v"] + (["-map", "1:a"] if audio else []) + ["-map", "2:s"]
    cmd += [str(path)]
    subprocess.run(cmd, check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return path


def keys(path):
    return {o.key: o for o in engine.options_for(path)}


class TestSpecs:
    def test_variants_ride_in_the_target_key(self):
        assert variants.split_target("png#frames") == ("png", "frames")
        assert variants.split_target("png") == ("png", "")

    def test_a_gif_offers_ways_to_save_a_picture(self, tmp_path):
        opts = keys(make_gif(tmp_path / "a.gif"))
        assert [v.key for v in opts["jpg"].variants] == ["jpg", "jpg#frames", "jpg#sheet"]
        assert [v.label for v in opts["jpg"].variants] == ["First frame", "Every frame", "Sprite sheet"]
        assert [v.key for v in opts["png"].variants] == ["png", "png#first", "png#frames", "png#sheet"]
        assert opts["png"].variants[0].label == "Keep animation"

    def test_spreadsheets_documents_and_video_offer_theirs(self, tmp_path):
        assert [v.key for v in keys(make_book(tmp_path / "b.xlsx"))["csv"].variants] == ["csv", "csv#sheets"]
        pdf = keys(make_pdf(tmp_path / "d.pdf"))
        assert [v.key for v in pdf["png"].variants] == ["png", "png#first", "png#tall"]
        assert pdf["png"].variants[0].label == "One image per page"

    def test_a_slide_deck_says_slide(self, tmp_path):
        from shapeshifter.native import slides_write
        from shapeshifter.native import docs
        blocks = [docs.Block("h2", "One"), docs.Block("h2", "Two")]
        slides_write.write_pptx(slides_write.deck_from_blocks(blocks, "T"), tmp_path / "s.pptx")
        opts = keys(tmp_path / "s.pptx")
        assert opts["png"].variants[0].label == "One image per slide"

    def test_files_with_nothing_else_to_offer_ask_nothing(self, tmp_path):
        Image.new("RGB", (20, 20)).save(tmp_path / "x.png")
        Image.new("RGB", (20, 20)).save(tmp_path / "x.gif")
        assert not keys(tmp_path / "x.png")["jpg"].variants
        assert not keys(tmp_path / "x.gif")["jpg"].variants
        assert not keys(make_book(tmp_path / "one.xlsx", sheets=1))["csv"].variants
        assert not keys(make_pdf(tmp_path / "one.pdf", pages=1))["png"].variants


class TestResolve:
    def resolve(self, path, key):
        return [v.key for v in engine.resolve_variants([path], keys(path)[key], CFG)]

    def test_single_frame_sheet_and_page_files_do_not_ask(self, tmp_path):
        Image.new("RGB", (20, 20)).save(tmp_path / "still.gif")
        assert self.resolve(tmp_path / "still.gif", "jpg") == ["jpg"]
        assert self.resolve(make_book(tmp_path / "one.xlsx", sheets=1), "csv") == ["csv"]
        assert self.resolve(make_pdf(tmp_path / "one.pdf", pages=1), "png") == ["png"]

    def test_several_frames_sheets_and_pages_ask(self, tmp_path):
        assert self.resolve(make_gif(tmp_path / "a.gif"), "jpg") == ["jpg", "jpg#frames", "jpg#sheet"]
        assert self.resolve(make_book(tmp_path / "b.xlsx"), "csv") == ["csv", "csv#sheets"]
        assert self.resolve(make_pdf(tmp_path / "d.pdf"), "png") == ["png", "png#first", "png#tall"]

    @needs_ffmpeg
    def test_video_questions_depend_on_the_clip(self, tmp_path):
        short_silent = make_video(tmp_path / "short.mp4", seconds=3, audio=False)
        assert self.resolve(short_silent, "gif") == ["gif", "gif#small"]            # too short for "first 10 s"
        assert self.resolve(short_silent, "png") == ["png", "png#first"]            # too short for frames every few seconds
        assert self.resolve(short_silent, "mkv") == ["mkv"]                         # small file, no sound: nothing to ask
        long_clip = make_video(tmp_path / "long.mp4", seconds=14)
        assert self.resolve(long_clip, "gif") == ["gif", "gif#small", "gif#short"]
        assert self.resolve(long_clip, "png") == ["png", "png#first", "png#every", "png#sheet"]
        assert self.resolve(long_clip, "mkv") == ["mkv", "mkv#mute"]                # has sound; too small to ask about size


class TestAnimatedPictures:
    def test_every_frame_and_sprite_sheet(self, tmp_path):
        gif = make_gif(tmp_path / "a.gif")
        outs = run(gif, "png#frames")
        assert [o.name for o in outs] == [f"a-{i}.png" for i in range(1, 6)]
        assert all(Image.open(o).size == (120, 80) for o in outs)
        (sheet,) = run(gif, "jpg#sheet")
        w, h = Image.open(sheet).size
        assert (w, h) == (3 * 120, 2 * 80)                                          # 5 frames: 3 x 2 grid

    def test_first_frame_versus_keeping_the_animation(self, tmp_path):
        gif = make_gif(tmp_path / "a.gif")
        (kept,) = run(gif, "png")
        assert getattr(Image.open(kept), "n_frames", 1) == 5                        # animated PNG
        (first,) = run(gif, "png#first")
        assert getattr(Image.open(first), "n_frames", 1) == 1
        (still,) = run(gif, "jpg")
        assert Image.open(still).format == "JPEG"

    def test_a_still_gif_ignores_the_variant(self, tmp_path):
        Image.new("RGB", (20, 20), "red").save(tmp_path / "s.gif")
        (out,) = run(tmp_path / "s.gif", "png#frames")
        assert out.name == "s.png"


class TestSheets:
    def test_every_sheet_gets_its_own_csv(self, tmp_path):
        outs = run(make_book(tmp_path / "b.xlsx"), "csv#sheets")
        assert sorted(o.name for o in outs) == ["b - Costs.csv", "b - Notes.csv", "b - Sales.csv"]
        assert (tmp_path / "b - Sales.csv").read_text(encoding="utf-8").splitlines() == ["item,qty", "a,1"]

    def test_the_default_is_still_the_first_sheet(self, tmp_path):
        (out,) = run(make_book(tmp_path / "b.xlsx"), "csv")
        assert out.read_text(encoding="utf-8").splitlines()[0] == "item,qty"

    def test_sqlite_tables(self, tmp_path):
        import sqlite3
        con = sqlite3.connect(tmp_path / "s.sqlite")
        con.execute("create table a(x)")
        con.execute("create table b(y)")
        con.execute("insert into a values (1)")
        con.commit()
        con.close()
        outs = run(tmp_path / "s.sqlite", "tsv#sheets")
        assert sorted(o.name for o in outs) == ["s - a.tsv", "s - b.tsv"]            # ".sqlite" is recognised as an extension


class TestPages:
    def test_first_page_only_and_one_tall_image(self, tmp_path):
        pdf = make_pdf(tmp_path / "d.pdf")
        assert len(run(pdf, "png")) == 3
        (first,) = run(pdf, "png#first")
        w1, h1 = Image.open(first).size
        assert first.name == "d.png" and abs(w1 - 625) <= 1 and abs(h1 - 833) <= 1
        (tall,) = run(pdf, "jpg#tall")
        w, h = Image.open(tall).size
        assert abs(w - 625) <= 1 and h > 3 * 830

    def test_works_through_a_longer_route(self, tmp_path):
        from shapeshifter.native import slides_write
        blocks = [__import__("shapeshifter.native.docs", fromlist=["Block"]).Block("h2", f"Slide {i}") for i in range(3)]
        slides_write.write_pptx(slides_write.deck_from_blocks(blocks, "T"), tmp_path / "s.pptx")
        assert len(run(tmp_path / "s.pptx", "png")) == 3                            # slides -> PDF -> pictures
        (tall,) = run(tmp_path / "s.pptx", "png#tall")
        assert Image.open(tall).height > Image.open(tall).width


@needs_ffmpeg
class TestVideo:
    def test_stream_facts(self, tmp_path):
        info = media.probe(make_video(tmp_path / "v.mp4", seconds=4))
        assert info.has_video and info.has_audio and info.subs == 0
        assert (info.width, info.height) == (320, 240) and 3.5 < info.duration < 4.5
        silent = media.probe(make_video(tmp_path / "q.mp4", seconds=2, audio=False))
        assert silent.has_video and not silent.has_audio

    def test_audio_and_subtitle_targets_only_when_present(self, tmp_path):
        silent = keys(make_video(tmp_path / "q.mp4", seconds=2, audio=False))
        assert "mp3" not in silent and "wav" not in silent and "srt" not in silent
        loud = keys(make_video(tmp_path / "v.mp4", seconds=2))
        assert "mp3" in loud and "srt" not in loud
        with_subs = keys(make_video(tmp_path / "s.mp4", seconds=3, subs=True))
        assert "srt" in with_subs and "vtt" in with_subs

    def test_pictures_from_a_video(self, tmp_path):
        v = make_video(tmp_path / "v.mp4", seconds=12)
        (mid,) = run(v, "png")
        assert Image.open(mid).size == (320, 240)
        (first,) = run(v, "jpg#first")
        assert Image.open(first).format == "JPEG"
        frames = run(v, "png#every")
        assert 8 <= len(frames) <= 14 and frames[0].name == "v-001.png"
        (sheet,) = run(v, "webp#sheet")
        assert Image.open(sheet).width == 5 * 320

    def test_extracted_subtitles(self, tmp_path):
        v = make_video(tmp_path / "s.mp4", seconds=4, subs=True)
        (srt,) = run(v, "srt")
        assert "Hello there" in srt.read_text(encoding="utf-8")
        (vtt,) = run(v, "vtt")
        assert vtt.read_text(encoding="utf-8").startswith("WEBVTT")

    def test_smaller_and_silent_versions(self, tmp_path):
        v = make_video(tmp_path / "v.mp4", seconds=6)
        (same,) = run(v, "mkv")
        assert media.probe(same).has_audio
        (mute,) = run(v, "mkv#mute")
        info = media.probe(mute)
        assert info.has_video and not info.has_audio
        (small,) = run(v, "webm#small")
        assert media.probe(small).has_video and small.stat().st_size > 0

    def test_gif_variants(self, tmp_path):
        v = make_video(tmp_path / "v.mp4", seconds=14)
        (std,) = run(v, "gif")
        (small,) = run(v, "gif#small")
        (short,) = run(v, "gif#short")
        assert Image.open(std).width == 320 and Image.open(small).width == 320
        assert getattr(Image.open(short), "n_frames", 1) < getattr(Image.open(std), "n_frames", 1)
        assert small.stat().st_size < std.stat().st_size * 1.1

    def test_frames_cannot_be_chained_into_other_formats(self):
        opt = planner.plan("mp4")["png"]
        assert opt.path[-1].terminal and any("picture" in w for w in opt.warnings)
