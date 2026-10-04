"""Stage 1: find every market in the area with Text Search over a grid of tiles."""

import re

import numpy as np
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
        n_tiles = capped = 0
        while queue:
            rect, depth = queue.pop()
            n_tiles += 1
            places, saturated = search_tile(client, keyword, rect)
            if saturated and depth < max_depth:
                queue.extend((sub, depth + 1) for sub in rect.subdivide())
            elif saturated:
                capped += 1  # still 60 results at the smallest tile: Google may be hiding more here

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
        if capped:
            log(f"    ! {capped} tiles still full (60 results) at max_subdivide_depth={max_depth}"
                " — some markets may be missing there; raise area.max_subdivide_depth to search deeper")

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
    """Add keep (1/0), keep_auto (the suggestion) and note — keep=1 means "this is the market itself".

    Rules in order, first match wins (see discover.* in config.yaml):
      1. never_market_types (lodging, parking, …)               → 0
      2. name starts like a market (market_name_prefix)          → 1
      3. name looks like a shop branch / residence (suspect_name) → 0
      4. "ตลาด" in the middle of a name, after a person/product   → 0  (a stall inside a market)
         unless the name has market words (market_name_words)
      5. a market-like Google type (market_types)                → 1
      otherwise                                                   → 0
    """
    names = df["name"].fillna("")
    types = df["primary_type"].fillna("")
    keep = pd.Series(np.nan, index=df.index)
    notes = pd.Series("", index=df.index)

    def rule(mask, value, note):
        hit = mask & keep.isna()
        keep[hit] = value
        notes[hit] = note if isinstance(note, str) else note[hit]

    def has(pattern):
        return names.str.contains(pattern, case=False, regex=True) if pattern else pd.Series(False, index=df.index)

    never = disc.get("never_market_types") or []
    rule(types.isin(never), 0, "not a market: " + types)
    rule(has(disc.get("market_name_prefix")), 1, "")
    rule(has(disc.get("suspect_name")), 0, "name looks like a shop branch/residence")
    stall = names.str.contains("ตลาด") & ~names.str.match(r"\s*ตลาด") & ~has(disc.get("market_name_words"))
    rule(stall, 0, "looks like a stall/shop inside a market")
    if disc.get("market_types"):
        rule(types.isin(disc["market_types"]), 1, "")
        rule(pd.Series(True, index=df.index), 0, "not a market type: " + types.replace("", "(none)"))
    elif disc.get("suspect_primary_type"):  # older blocklist-only configs
        rule(types.str.contains(disc["suspect_primary_type"], case=False, regex=True), 0, "primary_type=" + types)
    keep = keep.fillna(1)

    df = df.copy()
    df["keep"] = keep.astype(int)
    df["keep_auto"] = df["keep"]
    df["note"] = notes
    return df


def carry_over_keep(new, old):
    """Keep manual keep/note edits from a previous markets.csv for places found again.

    Only rows where the user changed `keep` away from the suggestion (`keep_auto`) are carried over,
    so improving the suggestion rules takes effect on re-runs. Files without `keep_auto` predate
    that column and hold only suggestions, so nothing is carried from them.
    """
    new = new.copy()
    if old is None or "place_id" not in old.columns:
        new["is_new"] = 1
        return new
    prev = old.drop_duplicates("place_id").set_index("place_id")
    new["is_new"] = (~new["place_id"].isin(prev.index)).astype(int)
    if "keep" not in prev.columns or "keep_auto" not in prev.columns:
        return new
    prev_keep = pd.to_numeric(prev["keep"], errors="coerce")
    edited = prev.index[(prev_keep != pd.to_numeric(prev["keep_auto"], errors="coerce")) & prev_keep.notna()]
    hit = new["place_id"].isin(edited)
    new.loc[hit, "keep"] = new.loc[hit, "place_id"].map(prev_keep).astype(int).values
    if "note" in prev.columns:
        new.loc[hit, "note"] = new.loc[hit, "place_id"].map(prev["note"]).fillna("").values
    return new


def apply_review(df, path):
    """Apply review/keep.csv (place_id, keep[, note]) on top of markets.csv.

    The file is meant to be committed: it holds only Google place IDs (which may be stored
    indefinitely) plus your own notes, so cloud runs know which places are not real markets.
    """
    from pathlib import Path

    path = Path(path)
    if not path.exists() or df.empty:
        return df
    review = pd.read_csv(path, dtype=str, encoding="utf-8-sig").dropna(subset=["place_id"])
    review = review.drop_duplicates("place_id", keep="last").set_index("place_id")
    df = df.copy()
    if "keep" not in df.columns:
        df["keep"] = 1
    hit = df["place_id"].isin(review.index)
    if "keep" in review.columns:
        keep = df.loc[hit, "place_id"].map(pd.to_numeric(review["keep"], errors="coerce"))
        df.loc[hit, "keep"] = keep.fillna(df.loc[hit, "keep"]).astype(int).values
    if "note" in review.columns:
        if "note" not in df.columns:
            df["note"] = ""
        note = df.loc[hit, "place_id"].map(review["note"])
        df.loc[hit, "note"] = note.fillna(df.loc[hit, "note"]).values
    return df
