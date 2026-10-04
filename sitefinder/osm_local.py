"""OSM features from a local .osm.pbf extract (e.g. Geofabrik) instead of the Overpass API.

`extract()` reads the country file once and keeps only what the config asks for inside the
area's bounding box → a small JSON. Counting and land-use areas are then computed locally,
so there is no rate limit (Overpass throttles shared CI IPs to a crawl).
"""

import json
import math
import re
from pathlib import Path

import numpy as np
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union
from shapely.strtree import STRtree

from .geo import haversine_m
from .osm import _to_local_m

FILTER_RE = re.compile(r'^\s*(nwr|nw|node|way|relation|rel)\s*\[\s*"([^"]+)"\s*(?:=\s*"([^"]*)"\s*)?\]\s*$')
KIND_LETTERS = {"nwr": "nwr", "nw": "nw", "node": "n", "way": "w", "relation": "r", "rel": "r"}


def parse_filter(text):
    """'nwr["amenity"="school"]' → ('nwr', 'amenity', 'school'); value None = any value."""
    m = FILTER_RE.match(text)
    if not m:
        raise ValueError(f"unsupported OSM filter for local extract: {text!r} (use kind[\"key\"=\"value\"])")
    return KIND_LETTERS[m.group(1)], m.group(2), m.group(3)


def _matches(filters, kind, tags):
    for kinds, key, value in filters:
        if kind in kinds and key in tags and (value is None or tags[key] == value):
            return True
    return False


def extract(pbf_path, bbox, point_features, area_features, campus_features=None, margin_deg=0.05, log=print):
    """Read a .osm.pbf and return {"points": {feature: [[lat, lng], ...]}, "areas": {feature: [ring, ...]}}.

    point_features / area_features: {feature: [overpass-style filter, ...]}.
    Points come from nodes and from areas (closed ways / multipolygons, by centroid).
    Area rings are outer rings as [[lng, lat], ...].
    campus_features: like area features but counted as institutions (see LocalOSM.campus_count):
    polygons go to data["campus"][name]["rings"], nodes to data["campus"][name]["nodes"].
    """
    import osmium

    pf = {k: [parse_filter(f) for f in v] for k, v in point_features.items()}
    af = {k: [parse_filter(f) for f in v] for k, v in area_features.items()}
    cf = {k: [parse_filter(f) for f in v] for k, v in (campus_features or {}).items()}
    keys = sorted({key for fs in list(pf.values()) + list(af.values()) + list(cf.values()) for _, key, _ in fs})
    s, n = bbox["south"] - margin_deg, bbox["north"] + margin_deg
    w, e = bbox["west"] - margin_deg, bbox["east"] + margin_deg
    inside = lambda lat, lng: s <= lat <= n and w <= lng <= e

    points = {k: [] for k in pf}
    areas = {k: [] for k in af}
    campus = {k: {"rings": [], "nodes": []} for k in cf}
    seen = 0
    processor = (
        osmium.FileProcessor(str(pbf_path))
        .with_locations()
        .with_areas(osmium.filter.KeyFilter(*keys))
        .with_filter(osmium.filter.KeyFilter(*keys))
    )
    for obj in processor:
        seen += 1
        tags = {t.k: t.v for t in obj.tags}
        if obj.is_node():
            if not obj.location.valid():
                continue
            lat, lng = obj.location.lat, obj.location.lon
            if inside(lat, lng):
                for name, fs in pf.items():
                    if _matches(fs, "n", tags):
                        points[name].append([round(lat, 6), round(lng, 6)])
                for name, fs in cf.items():
                    if _matches(fs, "n", tags):
                        campus[name]["nodes"].append([round(lat, 6), round(lng, 6)])
        elif obj.is_area():
            kind = "w" if obj.from_way() else "r"
            wanted_p = [name for name, fs in pf.items() if _matches(fs, kind, tags)]
            wanted_a = [name for name, fs in af.items() if _matches(fs, kind, tags)]
            wanted_c = [name for name, fs in cf.items() if _matches(fs, kind, tags)]
            if not wanted_p and not wanted_a and not wanted_c:
                continue
            rings = []
            for outer in obj.outer_rings():
                ring = [[round(nd.lon, 6), round(nd.lat, 6)] for nd in outer if nd.location.valid()]
                if len(ring) >= 4:
                    rings.append(ring)
            if not rings:
                continue
            lats = [p[1] for r in rings for p in r]
            lngs = [p[0] for r in rings for p in r]
            if max(lats) < s or min(lats) > n or max(lngs) < w or min(lngs) > e:
                continue
            for name in wanted_p:
                c = Polygon(rings[0]).representative_point()
                points[name].append([round(c.y, 6), round(c.x, 6)])
            for name in wanted_a:
                areas[name].extend(rings)
            for name in wanted_c:
                campus[name]["rings"].extend(rings)

    log(f"  scanned {seen:,} tagged objects; points: "
        + ", ".join(f"{k}={len(v)}" for k, v in points.items())
        + "; areas: " + ", ".join(f"{k}={len(v)} rings" for k, v in areas.items()))
    if campus:
        log("  campus: " + ", ".join(f"{k}={len(v['rings'])} polygons + {len(v['nodes'])} nodes" for k, v in campus.items()))
    return {"bbox": bbox, "points": points, "areas": areas, "campus": campus}


def save(data, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class LocalOSM:
    """Same questions as OverpassClient (counts within a radius, land-use hectares) from extract()."""

    def __init__(self, data):
        self.points = {k: np.array(v, dtype=float).reshape(-1, 2) for k, v in data["points"].items()}
        self.areas = {}
        for k, rings in data["areas"].items():
            polys = [p if p.is_valid else p.buffer(0) for p in (Polygon(r) for r in rings)]
            polys = [p for p in polys if not p.is_empty]
            self.areas[k] = (polys, STRtree(polys) if polys else None)

        self.campus = {}
        for k, v in data.get("campus", {}).items():
            polys = [p if p.is_valid else p.buffer(0) for p in (Polygon(r) for r in v["rings"])]
            polys = [p for p in polys if not p.is_empty]
            self.campus[k] = (polys, STRtree(polys) if polys else None, np.array(v["nodes"], dtype=float).reshape(-1, 2))

    def has(self, features):
        return all(f in self.points or f in self.areas or f in self.campus for f in features)

    def campus_count(self, lat, lng, radii_m, feature):
        """Distinct institutions within each radius: touching/overlapping polygons merge into one campus
        (so 40 buildings of one university count once); a node counts unless it lies in a counted polygon."""
        polys, tree, nodes = self.campus.get(feature, ([], None, np.zeros((0, 2))))
        out = {}
        r_max = max(radii_m)
        dlat = r_max / 111_320
        dlng = r_max / (111_320 * max(0.1, math.cos(math.radians(lat))))
        box = Polygon([(lng - dlng, lat - dlat), (lng + dlng, lat - dlat),
                       (lng + dlng, lat + dlat), (lng - dlng, lat + dlat)])
        near = [polys[i] for i in tree.query(box)] if tree is not None else []
        project = _to_local_m(lat, lng)
        local = []
        for p in near:
            q = Polygon([project(y, x) for x, y in p.exterior.coords])
            local.append(q if q.is_valid else q.buffer(0))
        node_d = haversine_m(lat, lng, nodes[:, 0], nodes[:, 1]) if len(nodes) else np.zeros(0)
        node_xy = [project(a, b) for a, b in nodes] if len(nodes) else []
        for r in radii_m:
            circle = Point(0, 0).buffer(r, 64)
            hit = [q for q in local if q.intersects(circle)]
            merged = unary_union(hit) if hit else None
            parts = [] if merged is None else (list(merged.geoms) if hasattr(merged, "geoms") else [merged])
            n_nodes = sum(1 for d, xy in zip(node_d, node_xy)
                          if d <= r and (merged is None or not merged.buffer(1).contains(Point(xy))))
            out[r] = len(parts) + n_nodes
        return out

    # same call shapes as OverpassClient, so surround.osm_counts / osm_areas can use either
    def count(self, lat, lng, radius_m, feature_filters):
        out = {}
        for f in feature_filters:
            pts = self.points.get(f)
            if pts is None or not len(pts):
                out[f] = 0
                continue
            out[f] = int((haversine_m(lat, lng, pts[:, 0], pts[:, 1]) <= radius_m).sum())
        return out

    def area_ha(self, lat, lng, radii_m, filters=None, feature=None):
        polys, tree = self.areas.get(feature, ([], None))
        if tree is None:
            return {r: 0.0 for r in radii_m}
        r_max = max(radii_m)
        dlat = r_max / 111_320
        dlng = r_max / (111_320 * max(0.1, math.cos(math.radians(lat))))
        box = Polygon([(lng - dlng, lat - dlat), (lng + dlng, lat - dlat),
                       (lng + dlng, lat + dlat), (lng - dlng, lat + dlat)])
        near = [polys[i] for i in tree.query(box)]
        if not near:
            return {r: 0.0 for r in radii_m}
        project = _to_local_m(lat, lng)
        local = []
        for p in near:
            ring = [project(y, x) for x, y in p.exterior.coords]
            q = Polygon(ring)
            local.append(q if q.is_valid else q.buffer(0))
        merged = unary_union(local)
        return {r: round(merged.intersection(Point(0, 0).buffer(r, 64)).area / 10_000, 2) for r in radii_m}
