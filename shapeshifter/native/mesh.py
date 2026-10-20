"""3D meshes: OBJ, STL (ASCII and binary) and PLY (ASCII and binary) <-> each other.

Geometry only (vertices + triangles); materials, normals and textures are dropped.
"""

from __future__ import annotations

import struct

from ..core import ConversionError
from .registry import register

Mesh = tuple[list[tuple[float, float, float]], list[tuple[int, int, int]]]


def read_obj(data: bytes) -> Mesh:
    verts, faces = [], []
    for line in data.decode("utf-8", "replace").splitlines():
        p = line.split()
        if not p:
            continue
        if p[0] == "v" and len(p) >= 4:
            verts.append(tuple(float(x) for x in p[1:4]))
        elif p[0] == "f" and len(p) >= 4:
            idx = []
            for tok in p[1:]:
                i = int(tok.split("/")[0])
                idx.append(i - 1 if i > 0 else len(verts) + i)
            for k in range(1, len(idx) - 1):           # fan-triangulate polygons
                faces.append((idx[0], idx[k], idx[k + 1]))
    return verts, faces


def read_stl(data: bytes) -> Mesh:
    tris = []
    if data[:5].lower() == b"solid" and b"facet" in data[:2048]:
        coords = []
        for line in data.decode("utf-8", "replace").splitlines():
            p = line.split()
            if p and p[0] == "vertex":
                coords.append(tuple(float(x) for x in p[1:4]))
        tris = [coords[i:i + 3] for i in range(0, len(coords) - 2, 3)]
    else:
        n = struct.unpack_from("<I", data, 80)[0]
        if 84 + n * 50 > len(data):
            raise ConversionError("Truncated binary STL")
        for i in range(n):
            vals = struct.unpack_from("<12f", data, 84 + i * 50)
            tris.append([vals[3:6], vals[6:9], vals[9:12]])
    verts, faces, index = [], [], {}
    for tri in tris:
        f = []
        for v in tri:
            key = tuple(round(c, 6) for c in v)
            if key not in index:
                index[key] = len(verts)
                verts.append(key)
            f.append(index[key])
        faces.append(tuple(f))
    return verts, faces


PLY_TYPES = {"char": "b", "int8": "b", "uchar": "B", "uint8": "B", "short": "h", "int16": "h",
             "ushort": "H", "uint16": "H", "int": "i", "int32": "i", "uint": "I", "uint32": "I",
             "float": "f", "float32": "f", "double": "d", "float64": "d"}


def read_ply(data: bytes) -> Mesh:
    end = data.find(b"end_header")
    if end < 0:
        raise ConversionError("Corrupt PLY header")
    header = data[:end].decode("ascii", "replace").splitlines()
    body_start = data.index(b"\n", end) + 1
    fmt = "ascii"
    elements: list[list] = []
    for line in header:
        p = line.split()
        if not p:
            continue
        if p[0] == "format":
            fmt = p[1]
        elif p[0] == "element":
            elements.append([p[1], int(p[2]), []])
        elif p[0] == "property" and elements:
            elements[-1][2].append(p[1:])
    verts, faces = [], []
    if fmt == "ascii":
        lines = iter(data[body_start:].decode("ascii", "replace").split("\n"))
        for name, count, props in elements:
            for _ in range(count):
                vals = next(lines).split()
                if name == "vertex":
                    names = [pr[-1] for pr in props]
                    verts.append(tuple(float(vals[names.index(a)]) for a in "xyz"))
                elif name == "face":
                    n = int(vals[0])
                    idx = [int(v) for v in vals[1:1 + n]]
                    faces += [(idx[0], idx[k], idx[k + 1]) for k in range(1, n - 1)]
    else:
        end_c = "<" if fmt == "binary_little_endian" else ">"
        pos = body_start
        for name, count, props in elements:
            for _ in range(count):
                row = {}
                for pr in props:
                    if pr[0] == "list":
                        cnt_t, item_t = PLY_TYPES[pr[1]], PLY_TYPES[pr[2]]
                        (n,) = struct.unpack_from(end_c + cnt_t, data, pos)
                        pos += struct.calcsize(cnt_t)
                        row[pr[-1]] = struct.unpack_from(f"{end_c}{n}{item_t}", data, pos)
                        pos += n * struct.calcsize(item_t)
                    else:
                        t = PLY_TYPES[pr[0]]
                        (row[pr[-1]],) = struct.unpack_from(end_c + t, data, pos)
                        pos += struct.calcsize(t)
                if name == "vertex":
                    verts.append((row["x"], row["y"], row["z"]))
                elif name == "face":
                    idx = row.get("vertex_indices") or row.get("vertex_index") or ()
                    faces += [(idx[0], idx[k], idx[k + 1]) for k in range(1, len(idx) - 1)]
    return verts, faces


def write_obj(mesh: Mesh) -> bytes:
    verts, faces = mesh
    lines = ["# Exported by ShapeShifter"] + [f"v {x:g} {y:g} {z:g}" for x, y, z in verts]
    lines += [f"f {a + 1} {b + 1} {c + 1}" for a, b, c in faces]
    return ("\n".join(lines) + "\n").encode()


def _normal(a, b, c):
    u = [b[i] - a[i] for i in range(3)]
    v = [c[i] - a[i] for i in range(3)]
    n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
    length = (n[0] ** 2 + n[1] ** 2 + n[2] ** 2) ** 0.5 or 1.0
    return tuple(x / length for x in n)


def write_stl(mesh: Mesh) -> bytes:
    verts, faces = mesh
    out = bytearray(b"ShapeShifter binary STL".ljust(80, b" ") + struct.pack("<I", len(faces)))
    for f in faces:
        a, b, c = (verts[i] for i in f)
        out += struct.pack("<12fH", *_normal(a, b, c), *a, *b, *c, 0)
    return bytes(out)


def write_ply(mesh: Mesh) -> bytes:
    verts, faces = mesh
    head = (f"ply\nformat ascii 1.0\ncomment ShapeShifter\nelement vertex {len(verts)}\n"
            "property float x\nproperty float y\nproperty float z\n"
            f"element face {len(faces)}\nproperty list uchar int vertex_indices\nend_header\n")
    body = "".join(f"{x:g} {y:g} {z:g}\n" for x, y, z in verts)
    body += "".join(f"3 {a} {b} {c}\n" for a, b, c in faces)
    return (head + body).encode()


READERS = {"obj": read_obj, "stl": read_stl, "ply": read_ply}
WRITERS = {"obj": write_obj, "stl": write_stl, "ply": write_ply}


@register(list(READERS), list(WRITERS))
def convert(data: bytes, src, dst, ctx) -> bytes:
    mesh = READERS[src](data)
    if not mesh[1]:
        raise ConversionError("The model has no faces")
    return WRITERS[dst](mesh)
