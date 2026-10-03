"""Free POI counts from OpenStreetMap via the Overpass API."""

import time

import requests

OVERPASS_URL = "https://overpass-api.de/api/interpreter"


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


class OverpassClient:
    def __init__(self, cache, url=OVERPASS_URL, min_interval_s=1.0, session=None):
        self.cache = cache
        self.url = url
        self.min_interval_s = min_interval_s
        self.session = session or requests.Session()
        self.calls = 0
        self._last_call = 0.0

    def count(self, lat, lng, radius_m, feature_filters):
        if not feature_filters:
            return {}
        query = build_count_query(lat, lng, radius_m, feature_filters)
        data = self.cache.get("overpass", query)
        if data is None:
            for attempt in range(5):
                wait = self.min_interval_s - (time.monotonic() - self._last_call)
                if wait > 0:
                    time.sleep(wait)
                self._last_call = time.monotonic()
                resp = self.session.post(self.url, data={"data": query}, timeout=120)
                if resp.status_code in (429, 504) and attempt < 4:
                    time.sleep(10 * (attempt + 1))
                    continue
                resp.raise_for_status()
                break
            self.calls += 1
            data = resp.json()
            self.cache.set("overpass", query, data)
        return parse_counts(data, list(feature_filters))
