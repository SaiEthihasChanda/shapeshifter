"""Geodata: GPX, KML, GeoJSON <-> each other, and points -> CSV."""

from __future__ import annotations

import csv
import io
import json
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from ..core import ConversionError
from .registry import register

# A feature: {"name", "desc", "kind": point|line, "coords": [(lon, lat, ele?), ...]}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(el, name):
    return [c for c in el.iter() if _local(c.tag) == name]


def _text(el, name) -> str:
    for c in el:
        if _local(c.tag) == name:
            return (c.text or "").strip()
    return ""


def read_gpx(data: bytes) -> list[dict]:
    root = ET.fromstring(data)
    feats = []
    for w in _find(root, "wpt"):
        ele = _text(w, "ele")
        feats.append({"name": _text(w, "name"), "desc": _text(w, "desc"), "kind": "point",
                      "coords": [(float(w.get("lon")), float(w.get("lat"))) + ((float(ele),) if ele else ())]})
    for tag, pt in (("trk", "trkpt"), ("rte", "rtept")):
        for t in _find(root, tag):
            pts = [(float(p.get("lon")), float(p.get("lat")))
                   + ((float(_text(p, "ele")),) if _text(p, "ele") else ()) for p in _find(t, pt)]
            if pts:
                feats.append({"name": _text(t, "name"), "desc": _text(t, "desc"), "kind": "line",
                              "coords": pts})
    return feats


def _kml_coords(s: str):
    out = []
    for tok in s.split():
        parts = [float(v) for v in tok.split(",") if v]
        if len(parts) >= 2:
            out.append(tuple(parts[:3]))
    return out


def read_kml(data: bytes) -> list[dict]:
    root = ET.fromstring(data)
    feats = []
    for pm in _find(root, "Placemark"):
        name, desc = _text(pm, "name"), _text(pm, "description")
        for kind, tag in (("point", "Point"), ("line", "LineString"), ("line", "LinearRing")):
            for geom in _find(pm, tag):
                for c in _find(geom, "coordinates"):
                    coords = _kml_coords(c.text or "")
                    if coords:
                        feats.append({"name": name, "desc": desc, "kind": kind, "coords": coords})
    return feats


def read_geojson(data: bytes) -> list[dict]:
    obj = json.loads(data)
    items = obj.get("features", [obj]) if obj.get("type") == "FeatureCollection" else [obj]
    feats = []
    for f in items:
        geom = f.get("geometry", f) or {}
        props = f.get("properties") or {}
        name = str(props.get("name", props.get("title", "")))
        desc = str(props.get("description", props.get("desc", "")))
        t, c = geom.get("type"), geom.get("coordinates")
        if t == "Point":
            feats.append({"name": name, "desc": desc, "kind": "point", "coords": [tuple(c)]})
        elif t in ("LineString", "MultiPoint"):
            feats.append({"name": name, "desc": desc, "kind": "line" if t == "LineString" else "point",
                          "coords": [tuple(p) for p in c]})
        elif t in ("MultiLineString", "Polygon"):
            for part in c:
                feats.append({"name": name, "desc": desc, "kind": "line", "coords": [tuple(p) for p in part]})
    return feats


def write_geojson(feats) -> bytes:
    out = []
    for f in feats:
        geom = ({"type": "Point", "coordinates": list(f["coords"][0])} if f["kind"] == "point"
                and len(f["coords"]) == 1 else
                {"type": "LineString" if f["kind"] == "line" else "MultiPoint",
                 "coordinates": [list(p) for p in f["coords"]]})
        props = {k: f[k] for k in ("name", "desc") if f.get(k)}
        out.append({"type": "Feature", "properties": props, "geometry": geom})
    return json.dumps({"type": "FeatureCollection", "features": out}, indent=2).encode()


def write_gpx(feats) -> bytes:
    parts = ['<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="ShapeShifter" '
             'xmlns="http://www.topografix.com/GPX/1/1">']
    for f in feats:
        if f["kind"] == "point":
            for p in f["coords"]:
                ele = f"<ele>{p[2]}</ele>" if len(p) > 2 else ""
                parts.append(f'  <wpt lat="{p[1]}" lon="{p[0]}">{ele}<name>{escape(f["name"])}</name></wpt>')
    for f in feats:
        if f["kind"] == "line":
            pts = "".join(f'<trkpt lat="{p[1]}" lon="{p[0]}">' + (f"<ele>{p[2]}</ele>" if len(p) > 2 else "")
                          + "</trkpt>" for p in f["coords"])
            parts.append(f"  <trk><name>{escape(f['name'])}</name><trkseg>{pts}</trkseg></trk>")
    parts.append("</gpx>\n")
    return "\n".join(parts).encode()


def write_kml(feats) -> bytes:
    marks = []
    for f in feats:
        coords = " ".join(",".join(str(v) for v in p) for p in f["coords"])
        geom = (f"<Point><coordinates>{coords}</coordinates></Point>" if f["kind"] == "point"
                and len(f["coords"]) == 1 else
                f"<LineString><coordinates>{coords}</coordinates></LineString>")
        desc = f"<description>{escape(f['desc'])}</description>" if f.get("desc") else ""
        marks.append(f"  <Placemark><name>{escape(f['name'])}</name>{desc}{geom}</Placemark>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2">'
            "<Document>\n" + "\n".join(marks) + "\n</Document></kml>\n").encode()


def write_csv(feats) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "kind", "longitude", "latitude", "elevation", "description"])
    for f in feats:
        for p in f["coords"]:
            w.writerow([f["name"], f["kind"], p[0], p[1], p[2] if len(p) > 2 else "", f["desc"]])
    return buf.getvalue().encode()


READERS = {"gpx": read_gpx, "kml": read_kml, "geojson": read_geojson}
WRITERS = {"gpx": write_gpx, "kml": write_kml, "geojson": write_geojson, "csv": write_csv}


@register(list(READERS), list(WRITERS))
def convert(data: bytes, src, dst, ctx) -> bytes:
    feats = READERS[src](data)
    if not feats:
        raise ConversionError("No points or tracks found")
    return WRITERS[dst](feats)
