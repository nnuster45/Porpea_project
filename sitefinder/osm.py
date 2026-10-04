"""Free POI counts and land-use areas from OpenStreetMap via the Overpass API."""

import math
import time

import requests
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# overpass-api.de rejects generic client User-Agents (python-requests/…) with 406 Not Acceptable
USER_AGENT = "porpea-sitefinder/1.0 (market site selection; +https://github.com/nnuster45/Porpea_project)"


def build_count_query(lat, lng, radius_m, feature_filters):
    """One query that returns one `count` element per feature, in the order given."""
    parts = ["[out:json][timeout:90];"]
    for filters in feature_filters.values():
        union = "".join(f"{f}(around:{int(radius_m)},{lat},{lng});" for f in filters)
        parts.append(f"({union});out count;")
    return "\n".join(parts)


def parse_counts(data, feature_names):
    counts = [el for el in data.get("elements", []) if el.get("type") == "count"]
    if len(counts) != len(feature_names):
        raise ValueError(f"expected {len(feature_names)} count elements, got {len(counts)}")
    return {name: int(el["tags"]["total"]) for name, el in zip(feature_names, counts)}


def build_geom_query(lat, lng, radius_m, filters):
    union = "".join(f"{f}(around:{int(radius_m)},{lat},{lng});" for f in filters)
    return f"[out:json][timeout:120];\n({union});\nout geom;"


def _to_local_m(lat0, lng0):
    """Equirectangular projection to metres around (lat0, lng0) — accurate enough within a few km."""
    k_lat = 111_320.0
    k_lng = 111_320.0 * math.cos(math.radians(lat0))
    return lambda lat, lng: ((lng - lng0) * k_lng, (lat - lat0) * k_lat)


def polygons_from_overpass(data, project):
    """Closed ways and multipolygon relations (outer rings) → shapely polygons in local metres."""
    polys = []
    for el in data.get("elements", []):
        if el.get("type") == "way":
            coords = [project(p["lat"], p["lon"]) for p in el.get("geometry", [])]
            if len(coords) >= 4 and coords[0] == coords[-1]:
                polys.append(Polygon(coords))
        elif el.get("type") == "relation":
            lines = [
                LineString([project(p["lat"], p["lon"]) for p in mem["geometry"]])
                for mem in el.get("members", [])
                if mem.get("role") in ("outer", "") and len(mem.get("geometry") or []) >= 2
            ]
            if lines:
                polys.extend(polygonize(unary_union(lines)))
    return [p if p.is_valid else p.buffer(0) for p in polys if p.area > 0]


def clipped_area_ha(polys, radii_m):
    """Area (hectares) of the union of polygons inside a circle of each radius around the origin."""
    if not polys:
        return {r: 0.0 for r in radii_m}
    merged = unary_union(polys)
    return {r: round(merged.intersection(Point(0, 0).buffer(r, 64)).area / 10_000, 2) for r in radii_m}


class OverpassClient:
    def __init__(self, cache, url=OVERPASS_URL, min_interval_s=1.0, session=None):
        self.cache = cache
        self.url = url
        self.min_interval_s = min_interval_s
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        self.calls = 0
        self._last_call = 0.0

    def fetch(self, query):
        data = self.cache.get("overpass", query)
        if data is not None:
            return data
        for attempt in range(5):
            wait = self.min_interval_s - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            resp = self.session.post(self.url, data={"data": query}, timeout=180)
            if resp.status_code in (429, 504) and attempt < 4:
                time.sleep(10 * (attempt + 1))
                continue
            resp.raise_for_status()
            break
        self.calls += 1
        data = resp.json()
        self.cache.set("overpass", query, data)
        return data

    def count(self, lat, lng, radius_m, feature_filters):
        if not feature_filters:
            return {}
        data = self.fetch(build_count_query(lat, lng, radius_m, feature_filters))
        return parse_counts(data, list(feature_filters))

    def area_ha(self, lat, lng, radii_m, filters, feature=None):
        """Hectares of matching land use within each radius (one query at the largest radius)."""
        data = self.fetch(build_geom_query(lat, lng, max(radii_m), filters))
        return clipped_area_ha(polygons_from_overpass(data, _to_local_m(lat, lng)), radii_m)
