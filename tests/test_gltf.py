"""glTF / GLB 3D models <-> OBJ, STL and PLY."""

import base64
import json
import math
import struct
import sys
from pathlib import Path

import pytest

from shapeshifter import engine, planner
from shapeshifter.core import Context
from shapeshifter.native import mesh

sys.path.insert(0, str(Path(__file__).parent))
from test_ocr import CFG                                  # noqa: E402

CUBE_V = [(x, y, z) for x in (0, 1) for y in (0, 1) for z in (0, 1)]
CUBE_F = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]


def run(path, target):
    return engine.convert_inplace(path, target, Context(cfg=dict(CFG, original="keep"))).outputs


def obj_text():
    return "\n".join([f"v {x} {y} {z}" for x, y, z in CUBE_V] + [f"f {a + 1} {b + 1} {c + 1}" for a, b, c in CUBE_F]) + "\n"


def handmade_gltf(nodes, buffer: bytes, accessors, views, meshes, uri=None):
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": nodes, "meshes": meshes,
           "accessors": accessors, "bufferViews": views,
           "buffers": [{"byteLength": len(buffer), "uri": uri or "data:application/octet-stream;base64," + base64.b64encode(buffer).decode()}]}
    return json.dumps(doc).encode()


def test_obj_to_glb_and_back_keeps_the_geometry(tmp_path):
    (tmp_path / "cube.obj").write_text(obj_text())
    (glb,) = run(tmp_path / "cube.obj", "glb")
    assert glb.read_bytes()[:4] == b"glTF" and glb.read_bytes()[4:8] == struct.pack("<I", 2)
    verts, faces = mesh.read_glb(glb.read_bytes())
    assert sorted(verts) == sorted(CUBE_V) and sorted(map(sorted, faces)) == sorted(map(sorted, CUBE_F))
    (back,) = run(glb, "obj")
    v2, f2 = mesh.read_obj(back.read_bytes())
    assert len(v2) == 8 and len(f2) == 12


@pytest.mark.parametrize("src,dst", [("stl", "glb"), ("ply", "gltf"), ("glb", "stl"), ("gltf", "ply"), ("glb", "gltf"), ("gltf", "glb")])
def test_every_pair(tmp_path, src, dst):
    (tmp_path / "cube.obj").write_text(obj_text())
    first = run(tmp_path / "cube.obj", src)[0]
    out = run(first, dst)[0]
    assert out.suffix == f".{dst}" and out.stat().st_size > 100
    v, f = mesh.READERS[dst](out.read_bytes())
    assert len(f) == 12 and len(v) in (8, 36) and abs(max(c for p in v for c in p) - 1) < 1e-6


def test_gltf_is_self_contained_json(tmp_path):
    (tmp_path / "cube.obj").write_text(obj_text())
    gltf = run(tmp_path / "cube.obj", "gltf")[0]
    doc = json.loads(gltf.read_text())
    assert doc["asset"]["version"] == "2.0" and doc["buffers"][0]["uri"].startswith("data:")
    assert doc["accessors"][0]["max"] == [1, 1, 1] and doc["accessors"][0]["min"] == [0, 0, 0]


def test_node_transforms_are_applied():
    tri = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    half = math.sqrt(0.5)
    nodes = [{"children": [1], "translation": [10, 0, 0]},
             {"mesh": 0, "rotation": [0, 0, half, half], "scale": [2, 2, 2]}]             # 90 degrees about Z, doubled
    doc = handmade_gltf(nodes, tri, [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"}],
                        [{"buffer": 0, "byteLength": 36}], [{"primitives": [{"attributes": {"POSITION": 0}}]}])
    verts, faces = mesh.read_gltf(doc)
    assert faces == [(0, 1, 2)]
    expected = [(10, 0, 0), (10, 2, 0), (8, 0, 0)]                # (x,y) -> (-2y, 2x) then + (10,0,0)
    for got, want in zip(verts, expected):
        assert got == pytest.approx(want, abs=1e-6)


def test_strips_fans_short_indices_and_interleaved_data():
    # four vertices interleaved with a 1-float padding (stride 16), a triangle strip with 16-bit indices
    verts = struct.pack("<16f", 0, 0, 0, 9, 1, 0, 0, 9, 0, 1, 0, 9, 1, 1, 0, 9)
    idx = struct.pack("<4H", 0, 1, 2, 3)
    buf = verts + idx
    doc = handmade_gltf([{"mesh": 0}], buf,
                        [{"bufferView": 0, "componentType": 5126, "count": 4, "type": "VEC3"},
                         {"bufferView": 1, "componentType": 5123, "count": 4, "type": "SCALAR"}],
                        [{"buffer": 0, "byteLength": 64, "byteStride": 16}, {"buffer": 0, "byteOffset": 64, "byteLength": 8}],
                        [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "mode": 5}]}])
    v, f = mesh.read_gltf(doc)
    assert v[3] == (1, 1, 0) and f == [(0, 1, 2), (2, 1, 3)]                  # the strip's second triangle flips winding


def test_external_buffers_are_found_next_to_the_file(tmp_path):
    tri = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    (tmp_path / "data.bin").write_bytes(tri)
    doc = handmade_gltf([{"mesh": 0}], tri, [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"}],
                        [{"buffer": 0, "byteLength": 36}], [{"primitives": [{"attributes": {"POSITION": 0}}]}], uri="data.bin")
    (tmp_path / "tri.gltf").write_bytes(doc)
    (obj,) = run(tmp_path / "tri.gltf", "obj")
    assert mesh.read_obj(obj.read_bytes())[1] == [(0, 1, 2)]
    (tmp_path / "data.bin").unlink()
    with pytest.raises(Exception, match="wasn't found"):
        run(tmp_path / "tri.gltf", "stl")


def test_unsupported_and_broken_files_say_so(tmp_path):
    doc = json.loads(handmade_gltf([{"mesh": 0}], b"", [], [], [{"primitives": []}]))
    doc["extensionsRequired"] = ["KHR_draco_mesh_compression"]
    with pytest.raises(Exception, match="Draco"):
        mesh.read_gltf(json.dumps(doc).encode())
    with pytest.raises(Exception):
        mesh.read_glb(b"not a glb at all, just text")
    (tmp_path / "empty.gltf").write_bytes(handmade_gltf([{"mesh": 0}], b"", [], [], [{"primitives": []}]))
    with pytest.raises(Exception, match="no faces"):
        run(tmp_path / "empty.gltf", "obj")


def test_the_graph_knows_the_new_pairs():
    for src, dst in (("glb", "stl"), ("obj", "glb"), ("gltf", "ply"), ("stl", "gltf")):
        assert planner.route(src, dst) is not None
    assert "glb" in planner.plan("stl") and "gltf" in planner.plan("obj")
