"""Stage 3: count what is around each market (Places Aggregate API or OSM) + external POI files."""

import re
from pathlib import Path

import numpy as np
import pandas as pd

from .geo import haversine_m
from .google import GoogleAPIError


def google_counts(markets, client, google_types, radii, log=print):
    rows = []
    broken = set()  # features whose types the API rejected; don't keep paying for errors
    for i, m in enumerate(markets.itertuples(index=False), 1):
        row = {"place_id": m.place_id}
        for feature, types in google_types.items():
            for r in radii:
                col = f"{feature}_{r}"
                if feature in broken:
                    row[col] = np.nan
                    continue
                try:
                    row[col] = client.count_places(m.lat, m.lng, r, types)
                except GoogleAPIError as e:
                    if e.status in (401, 403) or "API key" in str(e):
                        raise  # key/billing/API-not-enabled: every call would fail the same way
                    if e.status == 400:
                        broken.add(feature)
                        log(f"  ! feature '{feature}' {types} rejected, skipping it: {e}")
                    else:
                        log(f"  ! count failed for {m.name} {col}: {e}")
                    row[col] = np.nan
        rows.append(row)
        if i % 25 == 0:
            log(f"  {i}/{len(markets)}")
    return pd.DataFrame(rows)


def osm_counts(markets, overpass, osm_filters, radii, log=print):
    rows = []
    for i, m in enumerate(markets.itertuples(index=False), 1):
        row = {"place_id": m.place_id}
        for r in radii:
            try:
                counts = overpass.count(m.lat, m.lng, r, osm_filters)
            except Exception as e:  # Overpass is a shared free service; keep going on errors
                log(f"  ! overpass failed for {m.name} r={r}: {e}")
                counts = {k: np.nan for k in osm_filters}
            for feature, n in counts.items():
                row[f"{feature}_{r}"] = n
        rows.append(row)
        if i % 25 == 0:
            log(f"  {i}/{len(markets)}")
    return pd.DataFrame(rows)


def _slug(text):
    return re.sub(r"[^0-9a-zA-Zก-๙]+", "_", str(text)).strip("_").lower() or "other"


def load_pois(path):
    df = pd.read_csv(path)
    rename = {c: c.lower().strip() for c in df.columns}
    df = df.rename(columns=rename)
    aliases = {"latitude": "lat", "lon": "lng", "long": "lng", "longitude": "lng"}
    df = df.rename(columns={k: v for k, v in aliases.items() if k in df.columns and v not in df.columns})
    if not {"lat", "lng"} <= set(df.columns):
        raise ValueError(f"{path}: needs lat and lng columns, got {list(df.columns)}")
    return df.dropna(subset=["lat", "lng"])


def external_counts(markets, name, pois, radii):
    """Count POIs from a CSV around each market; split by `category` column if present."""
    lat, lng = pois["lat"].to_numpy(float), pois["lng"].to_numpy(float)
    cats = pois["category"].map(_slug).to_numpy() if "category" in pois.columns else None
    rows = []
    for m in markets.itertuples(index=False):
        dist = haversine_m(m.lat, m.lng, lat, lng)
        row = {"place_id": m.place_id}
        for r in radii:
            within = dist <= r
            row[f"{name}_{r}"] = int(within.sum())
            if cats is not None:
                for cat in np.unique(cats):
                    row[f"{name}_{cat}_{r}"] = int((within & (cats == cat)).sum())
        rows.append(row)
    return pd.DataFrame(rows)


def surround(cfg, markets, google_client=None, overpass=None, log=print):
    sc = cfg["surroundings"]
    radii = sc["radii_m"]
    frames = []

    if sc["source"] == "google_aggregate":
        log("  Places Aggregate counts…")
        frames.append(google_counts(markets, google_client, sc["google_types"], radii, log))
    elif sc["source"] == "osm":
        log("  OSM counts…")
        frames.append(osm_counts(markets, overpass, sc["osm_filters"], radii, log))
    else:
        raise ValueError(f"unknown surroundings.source: {sc['source']}")

    extra = dict(sc.get("osm_extra") or {})
    if sc["source"] == "osm":  # already counted above
        extra = {k: v for k, v in extra.items() if k not in sc["osm_filters"]}
    if extra:
        log(f"  OSM extra features ({', '.join(extra)})…")
        frames.append(osm_counts(markets, overpass, extra, radii, log))

    for ext in cfg.get("external_pois") or []:
        path = Path(ext["path"])
        if not path.exists():
            log(f"  - external '{ext['name']}' not found at {path}, skipping")
            continue
        pois = load_pois(path)
        log(f"  external '{ext['name']}': {len(pois)} points")
        frames.append(external_counts(markets, ext["name"], pois, ext.get("radii_m", radii)))

    out = markets[["place_id"]].copy()
    for f in frames:
        out = out.merge(f, on="place_id", how="left")
    return out
