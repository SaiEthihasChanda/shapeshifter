"""3D meshes: OBJ, STL (ASCII and binary), PLY (ASCII and binary), glTF and GLB <-> each other.

Geometry only (vertices + triangles); materials, normals and textures are dropped.
"""

from __future__ import annotations

import base64
import json
import math
import struct
from pathlib import Path

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


# ------------------------------------------------------------------ glTF / GLB

COMPONENT = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2), 5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
TYPE_SIZE = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def _mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _node_matrix(node: dict):
    if "matrix" in node:
        m = node["matrix"]                                           # column-major
        return [[m[c * 4 + r] for c in range(4)] for r in range(4)]
    t, q, sc = node.get("translation", [0, 0, 0]), node.get("rotation", [0, 0, 0, 1]), node.get("scale", [1, 1, 1])
    x, y, z, w = q
    r = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
         [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
         [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    return [[r[i][0] * sc[0], r[i][1] * sc[1], r[i][2] * sc[2], t[i]] for i in range(3)] + [[0, 0, 0, 1]]


def _accessor(gltf: dict, buffers: list[bytes], index: int) -> list:
    acc = gltf["accessors"][index]
    if "sparse" in acc:
        raise ConversionError("This glTF uses sparse accessors, which aren't supported.")
    fmt, size = COMPONENT[acc["componentType"]]
    n = TYPE_SIZE[acc["type"]]
    view = gltf["bufferViews"][acc["bufferView"]]
    data = buffers[view["buffer"]]
    start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    stride = view.get("byteStride") or size * n
    out = []
    for i in range(acc["count"]):
        off = start + i * stride
        out.append(struct.unpack_from(f"<{n}{fmt}", data, off))
    return out


def _gltf_to_mesh(gltf: dict, buffers: list[bytes]) -> Mesh:
    if "KHR_draco_mesh_compression" in " ".join(gltf.get("extensionsRequired", [])):
        raise ConversionError("This model uses Draco compression, which isn't supported.")
    verts: list = []
    faces: list = []

    def add_mesh(mesh_index: int, matrix):
        for prim in gltf["meshes"][mesh_index].get("primitives", []):
            if "POSITION" not in prim.get("attributes", {}) or prim.get("mode", 4) not in (4, 5, 6):
                continue
            pos = _accessor(gltf, buffers, prim["attributes"]["POSITION"])
            base = len(verts)
            for x, y, z in pos:
                verts.append(tuple(matrix[i][0] * x + matrix[i][1] * y + matrix[i][2] * z + matrix[i][3] for i in range(3)))
            idx = [v[0] for v in _accessor(gltf, buffers, prim["indices"])] if "indices" in prim else list(range(len(pos)))
            mode = prim.get("mode", 4)
            if mode == 4:
                tris = [(idx[i], idx[i + 1], idx[i + 2]) for i in range(0, len(idx) - 2, 3)]
            elif mode == 5:
                tris = [((idx[i], idx[i + 1], idx[i + 2]) if i % 2 == 0 else (idx[i + 1], idx[i], idx[i + 2]))
                        for i in range(len(idx) - 2)]
            else:
                tris = [(idx[0], idx[i], idx[i + 1]) for i in range(1, len(idx) - 1)]
            faces.extend((base + a, base + b, base + c) for a, b, c in tris)

    def walk(node_index: int, parent, depth=0):
        if depth > 64:
            return
        node = gltf["nodes"][node_index]
        m = _mat_mul(parent, _node_matrix(node))
        if "mesh" in node:
            add_mesh(node["mesh"], m)
        for child in node.get("children", []):
            walk(child, m, depth + 1)

    identity = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    scenes = gltf.get("scenes") or []
    roots = scenes[gltf.get("scene", 0)].get("nodes", []) if scenes else []
    if roots:
        for r in roots:
            walk(r, identity)
    elif gltf.get("nodes"):
        for i in range(len(gltf["nodes"])):
            walk(i, identity)
    else:
        for i in range(len(gltf.get("meshes", []))):
            add_mesh(i, identity)
    return verts, faces


def read_glb(data: bytes, dirs=()) -> Mesh:
    if data[:4] != b"glTF" or len(data) < 20:
        raise ConversionError("Not a GLB file")
    pos, json_chunk, bin_chunk = 12, None, b""
    while pos + 8 <= len(data):
        length, kind = struct.unpack_from("<II", data, pos)
        body = data[pos + 8:pos + 8 + length]
        if kind == 0x4E4F534A:
            json_chunk = body
        elif kind == 0x004E4942 and not bin_chunk:
            bin_chunk = body
        pos += 8 + length + (-length % 4)
    if json_chunk is None:
        raise ConversionError("The GLB has no scene description")
    gltf = json.loads(json_chunk.decode("utf-8"))
    buffers = [bin_chunk]
    for extra in gltf.get("buffers", [])[1:]:
        buffers.append(_load_uri(extra.get("uri", ""), dirs))
    return _gltf_to_mesh(gltf, buffers)


def _load_uri(uri: str, dirs) -> bytes:
    if uri.startswith("data:"):
        return base64.b64decode(uri.split(",", 1)[1])
    from urllib.parse import unquote
    for d in dirs or []:
        f = Path(d) / unquote(uri)
        if f.is_file():
            return f.read_bytes()
    raise ConversionError(f"The glTF refers to a file that wasn't found next to it: {uri}")


def read_gltf(data: bytes, dirs=()) -> Mesh:
    if data[:4] == b"glTF":
        return read_glb(data, dirs)
    gltf = json.loads(data.decode("utf-8-sig"))
    buffers = [_load_uri(b.get("uri", ""), dirs) for b in gltf.get("buffers", [])]
    return _gltf_to_mesh(gltf, buffers)


def _gltf_parts(mesh: Mesh):
    verts, faces = mesh
    pos = b"".join(struct.pack("<3f", *v) for v in verts)
    idx = b"".join(struct.pack("<3I", *f) for f in faces)
    lo = [min(v[i] for v in verts) for i in range(3)]
    hi = [max(v[i] for v in verts) for i in range(3)]
    blob = pos + idx
    doc = {"asset": {"version": "2.0", "generator": "ShapeShifter"}, "scene": 0, "scenes": [{"nodes": [0]}],
           "nodes": [{"mesh": 0}], "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
           "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(verts), "type": "VEC3", "min": lo, "max": hi},
                         {"bufferView": 1, "componentType": 5125, "count": len(faces) * 3, "type": "SCALAR"}],
           "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(pos), "target": 34962},
                           {"buffer": 0, "byteOffset": len(pos), "byteLength": len(idx), "target": 34963}],
           "buffers": [{"byteLength": len(blob)}]}
    return doc, blob


def write_glb(mesh: Mesh) -> bytes:
    doc, blob = _gltf_parts(mesh)
    j = json.dumps(doc, separators=(",", ":")).encode()
    j += b" " * (-len(j) % 4)
    blob += b"\0" * (-len(blob) % 4)
    total = 12 + 8 + len(j) + 8 + len(blob)
    return (b"glTF" + struct.pack("<II", 2, total) + struct.pack("<II", len(j), 0x4E4F534A) + j
            + struct.pack("<II", len(blob), 0x004E4942) + blob)


def write_gltf(mesh: Mesh) -> bytes:
    doc, blob = _gltf_parts(mesh)
    doc["buffers"][0]["uri"] = "data:application/octet-stream;base64," + base64.b64encode(blob).decode()
    return json.dumps(doc, indent=1).encode()


READERS = {"obj": read_obj, "stl": read_stl, "ply": read_ply, "glb": read_glb, "gltf": read_gltf}
WRITERS = {"obj": write_obj, "stl": write_stl, "ply": write_ply, "glb": write_glb, "gltf": write_gltf}


@register(list(READERS), list(WRITERS))
def convert(data: bytes, src, dst, ctx) -> bytes:
    mesh = read_gltf(data, list(getattr(ctx, "resource_dirs", []) or [])) if src in ("glb", "gltf") else READERS[src](data)
    if not mesh[1]:
        raise ConversionError("The model has no faces")
    if not mesh[0]:
        raise ConversionError("The model has no vertices")
    return WRITERS[dst](mesh)
