"""Stage 1: find every market in the area with Text Search over a grid of tiles."""

import re

import pandas as pd

from .geo import tiles

MAX_PAGES = 3  # Text Search (New) returns at most 60 results = 3 pages of 20


def search_tile(client, keyword, rect):
    """All pages for one keyword in one rectangle."""
    places, token = [], None
    for _ in range(MAX_PAGES):
        data = client.text_search(keyword, rect, token)
        places.extend(data.get("places", []))
        token = data.get("nextPageToken")
        if not token:
            break
    return places, bool(token) or len(places) >= MAX_PAGES * 20


def plan_tiles(cfg):
    area = cfg["area"]
    return tiles(area["bbox"], area["tile_deg"])


def _row(place):
    loc = place.get("location", {})
    return {
        "place_id": place["id"],
        "name": place.get("displayName", {}).get("text", ""),
        "lat": loc.get("latitude"),
        "lng": loc.get("longitude"),
        "address": place.get("formattedAddress", ""),
        "primary_type": place.get("primaryType", ""),
        "types": ",".join(place.get("types", [])),
        "business_status": place.get("businessStatus", ""),
        "maps_url": place.get("googleMapsUri", ""),
    }


def discover(cfg, client, log=print):
    area, disc = cfg["area"], cfg["discover"]
    must_contain = [s.lower() for s in area.get("address_must_contain", [])]
    name_ok = re.compile(disc.get("name_must_match", "."), re.IGNORECASE)
    name_bad = re.compile(disc["name_exclude"], re.IGNORECASE) if disc.get("name_exclude") else None
    max_depth = area.get("max_subdivide_depth", 2)

    found = {}
    skipped = {"outside_area": 0, "name_filter": 0, "closed": 0}
    base_tiles = plan_tiles(cfg)

    for keyword in disc["keywords"]:
        queue = [(rect, 0) for rect in base_tiles]
        n_tiles = 0
        while queue:
            rect, depth = queue.pop()
            n_tiles += 1
            places, saturated = search_tile(client, keyword, rect)
            if saturated and depth < max_depth:
                queue.extend((sub, depth + 1) for sub in rect.subdivide())

            for place in places:
                row = _row(place)
                if must_contain and not any(s in row["address"].lower() for s in must_contain):
                    skipped["outside_area"] += 1
                    continue
                if not name_ok.search(row["name"]) or (name_bad and name_bad.search(row["name"])):
                    skipped["name_filter"] += 1
                    continue
                if row["business_status"] == "CLOSED_PERMANENTLY":
                    skipped["closed"] += 1
                    continue
                if row["place_id"] in found:
                    found[row["place_id"]]["keywords"].add(keyword)
                else:
                    row["keywords"] = {keyword}
                    found[row["place_id"]] = row
        log(f"  '{keyword}': searched {n_tiles} tiles, {len(found)} unique markets so far")

    log(f"  rejected search hits (a place may be counted more than once): {skipped}")
    rows = []
    for row in found.values():
        row = dict(row)
        row["keywords"] = "|".join(sorted(row["keywords"]))
        rows.append(row)
    columns = ["place_id", "name", "lat", "lng", "address", "primary_type", "types",
               "business_status", "maps_url", "keywords"]
    return pd.DataFrame(rows, columns=columns).sort_values("name").reset_index(drop=True)
