"""More archive formats through 7-Zip. 7-Zip itself may not be installed, so the parsing and the safety
rules are tested directly; the real round trip runs only when 7z.exe is present."""

import sys
from pathlib import Path

import pytest

from shapeshifter import planner
from shapeshifter.backends import sevenzip
from shapeshifter.backends.base import RiskyFile
from shapeshifter.backends.sevenzip import SevenZipBackend, check_entries, parse_listing
from shapeshifter.core import Context

sys.path.insert(0, str(Path(__file__).parent))
from test_ocr import CFG                                  # noqa: E402

LISTING = """
7-Zip 23.01 (x64) : Copyright (c) 1999-2023 Igor Pavlov : 2023-06-20

Scanning the drive for archives:
1 file, 4096 bytes (4 KiB)

Listing archive: pics.rar

--
Path = pics.rar
Type = Rar5
Physical Size = 4096

----------
Path = a/first.jpg
Folder = -
Size = 1200
Packed Size = 1000
Encrypted = -

Path = a/second ünï.png
Folder = -
Size = 3000
Packed Size = 2500
Encrypted = -

Path = a
Folder = +
Size = 0
Encrypted = -
"""


class TestListing:
    def test_entries_are_parsed(self):
        entries = parse_listing(LISTING)
        assert [e["Path"] for e in entries] == ["a/first.jpg", "a/second ünï.png", "a"]
        assert entries[0]["Size"] == "1200" and entries[0]["Encrypted"] == "-"

    def test_a_normal_archive_passes(self):
        check_entries(parse_listing(LISTING), 4096)

    def test_encrypted_archives_are_refused(self):
        entries = parse_listing(LISTING.replace("Encrypted = -\n\nPath = a/second", "Encrypted = +\n\nPath = a/second"))
        with pytest.raises(RiskyFile, match="password"):
            check_entries(entries, 4096)

    @pytest.mark.parametrize("bad", ["../evil.txt", "/abs/evil.txt", "C:/evil.txt", "a/../../evil.txt"])
    def test_paths_that_escape_the_folder_are_refused(self, bad):
        with pytest.raises(RiskyFile, match="unsafe path"):
            check_entries([{"Path": bad, "Size": "1"}], 100)

    def test_bombs_and_links_are_refused(self):
        with pytest.raises(RiskyFile, match="compresses suspiciously"):
            check_entries([{"Path": "big.bin", "Size": str(5 * 1024 ** 3)}], 1000)
        with pytest.raises(RiskyFile, match="links"):
            check_entries([{"Path": "l", "Size": "1", "Symbolic Link": "x"}], 100)


class TestBackend:
    def test_what_it_offers(self):
        b = SevenZipBackend()
        assert {"rar", "iso", "cab", "wim", "dmg", "vhdx", "squashfs", "rpm"} <= b.sources()
        assert {"extract", "zip", "7z", "tar"} <= b.targets("rar") and "cbz" in b.targets("cbr") and "cbz" not in b.targets("rar")
        assert b.multi_output("rar", "extract") and not b.multi_output("rar", "zip")

    def test_not_available_without_7zip(self, monkeypatch):
        monkeypatch.setattr(sevenzip, "sevenzip_path", lambda: None)
        assert not SevenZipBackend().available()

    def test_extract_and_repack_flow_with_a_stubbed_7zip(self, tmp_path, monkeypatch):
        """The unpack folder is checked and repacked; the archive listing decides whether it runs at all."""
        calls = []

        def fake_run(self, args, ctx, timeout=900):
            calls.append(args[0])
            if args[0] == "l":
                return LISTING
            out = Path(args[-2][2:])
            (out / "a").mkdir(parents=True, exist_ok=True)
            (out / "a" / "first.jpg").write_bytes(b"jpegdata")
            return ""
        monkeypatch.setattr(SevenZipBackend, "_run", fake_run)
        src = tmp_path / "pics.rar"
        src.write_bytes(b"Rar!\x1a\x07\x01\x00" + b"\0" * 100)
        work = tmp_path / "work"
        work.mkdir()
        out = SevenZipBackend().convert(src, work / "pics.zip", Context(cfg=CFG, src_fmt="rar", dst_fmt="zip"))
        assert calls == ["l", "x"]
        import zipfile
        with zipfile.ZipFile(out[0]) as z:
            assert z.namelist() == ["a/", "a/first.jpg"] or "a/first.jpg" in z.namelist()
        folder = SevenZipBackend().convert(src, work / "pics", Context(cfg=CFG, src_fmt="rar", dst_fmt="extract"))[0]
        assert (folder / "a" / "first.jpg").read_bytes() == b"jpegdata"

    def test_planner_routes_when_installed(self, monkeypatch):
        # the graph includes these edges only when the engine is available; the backend still describes them
        assert SevenZipBackend().can("rar", "zip") and SevenZipBackend().can("iso", "extract")

    @pytest.mark.skipif(not sevenzip.sevenzip_path(), reason="7-Zip is not installed")
    def test_a_real_round_trip(self, tmp_path):
        import subprocess
        import zipfile
        (tmp_path / "a.txt").write_text("hello")
        archive = tmp_path / "t.cab"
        # 7-Zip cannot create RAR/CAB; use WIM, which it can both write and read
        wim = tmp_path / "t.wim"
        subprocess.run([sevenzip.sevenzip_path(), "a", str(wim), str(tmp_path / "a.txt")], check=True, capture_output=True)
        del archive
        work = tmp_path / "w"
        work.mkdir()
        out = SevenZipBackend().convert(wim, work / "t.zip", Context(cfg=CFG, src_fmt="wim", dst_fmt="zip"))[0]
        with zipfile.ZipFile(out) as z:
            assert z.read("a.txt") == b"hello"


def test_the_planner_knows_the_formats_but_not_the_edges_without_7zip():
    if sevenzip.sevenzip_path():
        assert planner.route("rar", "zip") is not None
    else:
        assert planner.route("rar", "zip") is None             # honest: nothing offered until 7-Zip is installed
