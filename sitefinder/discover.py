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
    df = pd.DataFrame(rows, columns=columns)
    return suggest_keep(df, disc).sort_values(["keep", "name"], ascending=[False, True]).reset_index(drop=True)


def suggest_keep(df, disc):
    """Add keep (1/0) and note columns: 0 = looks like a shop/office rather than a market."""
    bad_type = disc.get("suspect_primary_type")
    bad_name = disc.get("suspect_name")
    notes = pd.Series("", index=df.index)
    if bad_type:
        hit = df["primary_type"].fillna("").str.contains(bad_type, case=False, regex=True)
        notes[hit] = "primary_type=" + df.loc[hit, "primary_type"]
    if bad_name:
        hit = df["name"].fillna("").str.contains(bad_name, case=False, regex=True) & (notes == "")
        notes[hit] = "name looks like a shop/residence"
    df = df.copy()
    df["keep"] = (notes == "").astype(int)
    df["note"] = notes
    return df


def carry_over_keep(new, old):
    """Keep the user's manual keep/note edits from a previous markets.csv for places found again."""
    if old is None or "keep" not in old.columns:
        return new
    prev = old.drop_duplicates("place_id").set_index("place_id")
    known = new["place_id"].isin(prev.index)
    new = new.copy()
    new.loc[known, "keep"] = new.loc[known, "place_id"].map(pd.to_numeric(prev["keep"], errors="coerce")).fillna(1).astype(int).values
    if "note" in prev.columns:
        new.loc[known, "note"] = new.loc[known, "place_id"].map(prev["note"]).fillna("").values
    new["is_new"] = (~known).astype(int)
    return new
