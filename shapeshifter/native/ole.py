"""A small reader for OLE2 "compound files": the container used by .doc, .xls, .ppt and .msg."""

from __future__ import annotations

import struct

SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
FREE, END, FAT_SECT, DIFAT_SECT = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFC
MAX_SIZE = 600 * 1024 * 1024


class OleError(ValueError):
    pass


def is_ole(head: bytes) -> bool:
    return head[:8] == SIGNATURE


class OleFile:
    def __init__(self, data: bytes):
        if data[:8] != SIGNATURE or len(data) < 512:
            raise OleError("Not an OLE2 file")
        if len(data) > MAX_SIZE:
            raise OleError("File too large")
        self.data = data
        shift = struct.unpack_from("<H", data, 30)[0]
        mini_shift = struct.unpack_from("<H", data, 32)[0]
        if shift not in (9, 12) or mini_shift != 6:
            raise OleError("Unsupported sector size")
        self.ssize, self.msize = 1 << shift, 1 << mini_shift
        (n_fat, first_dir, _, cutoff, first_mini, n_mini, first_difat, n_difat) = struct.unpack_from(
            "<IIIIIIII", data, 44)
        # struct above reads 44.. : n_fat(44) first_dir(48) txn(52) cutoff(56) first_mini(60) n_mini(64) first_difat(68) n_difat(72)
        self.cutoff = cutoff
        difat = list(struct.unpack_from("<109I", data, 76))
        sect = first_difat
        for _ in range(n_difat):
            if sect in (FREE, END):
                break
            block = self._sector(sect)
            vals = struct.unpack_from(f"<{self.ssize // 4}I", block)
            difat += vals[:-1]
            sect = vals[-1]
        self.fat: list[int] = []
        for s in difat[:n_fat] if n_fat else difat:
            if s in (FREE, END):
                continue
            self.fat += struct.unpack_from(f"<{self.ssize // 4}I", self._sector(s))
        self._dir = self._read_chain(first_dir)
        self.entries = self._entries()
        self.root = self.entries[0]
        self._mini = None
        self._mini_fat: list[int] | None = None
        self._first_mini, self._n_mini = first_mini, n_mini

    def _sector(self, n: int) -> bytes:
        start = (n + 1) * self.ssize
        block = self.data[start:start + self.ssize]
        if len(block) < self.ssize:
            raise OleError("Truncated file")
        return block

    def _read_chain(self, first: int, size: int | None = None) -> bytes:
        out, seen, s = [], set(), first
        while s not in (END, FREE) and s < len(self.fat):
            if s in seen:
                raise OleError("Loop in the sector chain")
            seen.add(s)
            out.append(self._sector(s))
            s = self.fat[s]
            if len(out) * self.ssize > MAX_SIZE:
                raise OleError("Stream too large")
        data = b"".join(out)
        return data if size is None else data[:size]

    def _entries(self) -> list[dict]:
        out = []
        for i in range(0, len(self._dir), 128):
            e = self._dir[i:i + 128]
            if len(e) < 128:
                break
            nlen = struct.unpack_from("<H", e, 64)[0]
            name = e[:max(0, nlen - 2)].decode("utf-16-le", errors="replace")
            kind = e[66]
            left, right, child = struct.unpack_from("<III", e, 68)
            start = struct.unpack_from("<I", e, 116)[0]
            size = struct.unpack_from("<Q", e, 120)[0] & 0xFFFFFFFF
            out.append({"name": name, "type": kind, "left": left, "right": right, "child": child,
                        "start": start, "size": size})
        return out

    def _walk(self, idx: int, path: str, out: dict[str, dict], seen: set[int]):
        if idx >= len(self.entries) or idx in seen or self.entries[idx]["type"] == 0:
            return
        seen.add(idx)
        e = self.entries[idx]
        self._walk(e["left"], path, out, seen)
        full = f"{path}/{e['name']}" if path else e["name"]
        out[full] = e
        if e["type"] == 1:                                  # storage
            self._walk(e["child"], full, out, seen)
        self._walk(e["right"], path, out, seen)

    def streams(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        self._walk(self.root["child"], "", out, set())
        return {k: v for k, v in out.items() if v["type"] == 2}

    def names(self) -> list[str]:
        return list(self.streams())

    def _load_mini(self):
        if self._mini is None:
            self._mini = self._read_chain(self.root["start"], self.root["size"])
            raw = self._read_chain(self._first_mini)
            self._mini_fat = list(struct.unpack_from(f"<{len(raw) // 4}I", raw))
        return self._mini

    def read(self, name: str) -> bytes:
        entry = self.streams().get(name)
        if entry is None:
            # case-insensitive and top-level fallbacks
            for k, v in self.streams().items():
                if k.lower() == name.lower() or k.split("/")[-1].lower() == name.lower():
                    entry = v
                    break
        if entry is None:
            raise KeyError(name)
        size = entry["size"]
        if size < self.cutoff:
            mini = self._load_mini()
            out, s, seen = [], entry["start"], set()
            while s not in (END, FREE) and s < len(self._mini_fat) and s not in seen:
                seen.add(s)
                out.append(mini[s * self.msize:(s + 1) * self.msize])
                s = self._mini_fat[s]
            return b"".join(out)[:size]
        return self._read_chain(entry["start"], size)
